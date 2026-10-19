"""RAG ingestion pipeline — corpus -> chunks -> embeddings -> Chroma.

OWNER: RAG data-pipeline workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §5.4 (pipeline pseudocode),
§5.5 (manifest shape), §5.9 (re-ingestion/versioning). Chunking itself lives
in codebase/models/rag/chunking.py (pure, API-free, independently
unit-tested); this module owns all I/O: reading sources.csv/raw/*.md,
calling the embeddings API, and talking to Chroma.

Run offline, never on the request path:

    python -m codebase.models.rag.ingest

Idempotent (§5.3/§5.9): each tier-2 file's whole-content hash is stored as
`source_content_hash` on every chunk from that file; each tier-1 row's
`content_hash` (of key_content) plays the same role directly, since one row
is one chunk. Re-running with nothing changed costs zero embedding calls —
for every source, a single `collection.get(...)` metadata read decides
whether to skip it. When a source *has* changed, all of its existing chunks
are deleted and the fresh set is embedded and inserted — never diffed
chunk-by-chunk, since chunk boundaries can shift on any edit (§5.3).
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from codebase.backend.config import get_settings
from codebase.models.rag.chunking import (
    CORPUS_VERSION,
    EMBEDDING_MODEL,
    SKIP_FAILED_CRAWL,
    SKIP_NO_LOCAL_FILE,
    TRACK_A_FILES,
    TRACK_B_FILES,
    build_tier1_chunk,
    chunk_track_a,
    chunk_track_b,
    classify_file,
    classify_tier1_skip,
    compute_hash,
    find_page_markers,
)

logger = logging.getLogger("codebase.models.rag.ingest")

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCES_CSV_PATH = REPO_ROOT / "data" / "knowledge_base" / "sources.csv"
RAW_DIR = REPO_ROOT / "data" / "knowledge_base" / "raw"
MANIFEST_PATH = REPO_ROOT / "data" / "knowledge_base" / "ingestion_manifest.json"

COLLECTION_NAME = "knowledge_base"
EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small's native output size
EMBED_BATCH_SIZE = 100  # §5.4: "batched (~100 chunks/request)"


class EmbeddingsClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- client construction ------------------------------------------------------


def get_embeddings_client() -> EmbeddingsClient:
    """Real OpenAI-backed embeddings client. Imports langchain_openai lazily
    so importing this module (e.g. from knowledge_tools.py, or from a test
    that only wants read_sources_csv) never requires it."""
    from langchain_openai import OpenAIEmbeddings

    settings = get_settings()
    return OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=settings.openai_api_key, chunk_size=EMBED_BATCH_SIZE)


def get_chroma_client() -> Any:
    import chromadb

    settings = get_settings()
    return chromadb.PersistentClient(path=settings.chroma_persist_dir)


def get_collection(client: Any | None = None) -> Any:
    """The `knowledge_base` Chroma collection, created with cosine distance
    (OpenAI embeddings are compared by cosine similarity, not Chroma's L2
    default) if it doesn't exist yet."""
    client = client or get_chroma_client()
    return client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


# --- source loading -------------------------------------------------------------


def read_sources_csv(path: Path = SOURCES_CSV_PATH) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_source_url_map(rows: list[dict[str, str]]) -> dict[str, str]:
    """filename -> canonical url, read from sources.csv's local_file/url
    columns. Several tier-1 rows can reference the same raw/*.md file
    (verified: none disagree on url when they do) — used so tier-2 chunks
    get a real source_url even for files like byt-5481 that have no
    '> Nguồn:' header line of their own."""
    mapping: dict[str, str] = {}
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        for part in (row.get("local_file") or "").split(";"):
            part = part.strip()
            if part.startswith("raw/"):
                mapping.setdefault(part[len("raw/") :], url)
    return mapping


# --- chroma upsert helpers -------------------------------------------------------


def _split_chunk(chunk: dict) -> tuple[str, str, dict]:
    chunk_id = chunk["chunk_id"]
    text = chunk["text"]
    metadata = {k: v for k, v in chunk.items() if k not in ("chunk_id", "text") and v is not None}
    return chunk_id, text, metadata


def _upsert_chunks(collection: Any, embeddings_client: EmbeddingsClient, chunks: list[dict]) -> None:
    if not chunks:
        return
    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []
    for chunk in chunks:
        chunk_id, text, metadata = _split_chunk(chunk)
        ids.append(chunk_id)
        texts.append(text)
        metadatas.append(metadata)
    embeddings = embeddings_client.embed_documents(texts)  # batched internally at EMBED_BATCH_SIZE
    collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)


# --- tier 1 ----------------------------------------------------------------------


def _ingest_tier1(
    collection: Any, embeddings_client: EmbeddingsClient, rows: list[dict[str, str]], *, ingested_at: str
) -> dict[str, Any]:
    stats = {
        "total_rows": len(rows),
        "embedded_this_run": 0,
        "unchanged": 0,
        "skipped_no_local_file": 0,
        "skipped_failed_crawl": 0,
    }
    pending: list[dict] = []
    for row_id, row in enumerate(rows):
        skip_reason = classify_tier1_skip(row)
        if skip_reason == SKIP_NO_LOCAL_FILE:
            stats["skipped_no_local_file"] += 1
            continue
        if skip_reason == SKIP_FAILED_CRAWL:
            stats["skipped_failed_crawl"] += 1
            continue

        chunk = build_tier1_chunk(row, row_id, ingested_at=ingested_at)
        existing = collection.get(where={"row_id": row_id}, include=["metadatas"])
        existing_hash = existing["metadatas"][0].get("content_hash") if existing["metadatas"] else None
        if existing_hash == chunk["content_hash"]:
            stats["unchanged"] += 1
            continue
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        pending.append(chunk)

    _upsert_chunks(collection, embeddings_client, pending)
    stats["embedded_this_run"] = len(pending)
    stats["embedded"] = stats["embedded_this_run"] + stats["unchanged"]
    return stats


# --- tier 2 ----------------------------------------------------------------------


def _ingest_tier2(
    collection: Any,
    embeddings_client: EmbeddingsClient,
    source_url_map: dict[str, str],
    *,
    ingested_at: str,
    raw_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    track_stats: dict[str, Any] = {
        "A": {"files": 0, "chunks": 0, "chunks_embedded_this_run": 0, "unchanged_files": 0},
        "B": {
            "files": 0,
            "chunks": 0,
            "chunks_embedded_this_run": 0,
            "unchanged_files": 0,
            "files_with_page_markers": 0,
            "files_without_page_markers": [],
        },
    }
    warnings: list[str] = []

    mapped_files = TRACK_A_FILES | TRACK_B_FILES
    on_disk = {p.name for p in raw_dir.glob("*.md")}
    for missing in sorted(mapped_files - on_disk):
        warnings.append(f"{missing}: listed in chunking.py's track mapping but not found in {raw_dir}, skipped")
    for extra in sorted(on_disk - mapped_files):
        warnings.append(
            f"{extra}: found in {raw_dir} but not in chunking.TRACK_A_FILES/TRACK_B_FILES — "
            "add it to that mapping (§5.4), skipped this run"
        )

    for filename in sorted(mapped_files & on_disk):
        path = raw_dir / filename
        content = path.read_text(encoding="utf-8")
        track = classify_file(filename)
        source_content_hash = compute_hash(content)

        if track == "B":
            has_markers = bool(find_page_markers(content))
            if has_markers:
                track_stats["B"]["files_with_page_markers"] += 1
            else:
                track_stats["B"]["files_without_page_markers"].append(filename)
                warnings.append(
                    f"{filename}: no '--- Trang N ---' markers found, chunked on paragraph "
                    "boundaries only, page_number will be null for all chunks from this file"
                )

        existing = collection.get(where={"source_file": filename}, limit=1, include=["metadatas"])
        existing_hash = existing["metadatas"][0].get("source_content_hash") if existing["metadatas"] else None

        if existing_hash == source_content_hash:
            existing_count = len(collection.get(where={"source_file": filename}, include=[])["ids"])
            track_stats[track]["files"] += 1
            track_stats[track]["chunks"] += existing_count
            track_stats[track]["unchanged_files"] += 1
            continue

        collection.delete(where={"source_file": filename})
        source_url = source_url_map.get(filename)
        if track == "A":
            chunks = chunk_track_a(filename, content, source_url=source_url, ingested_at=ingested_at)
        else:
            chunks = chunk_track_b(filename, content, source_url=source_url, ingested_at=ingested_at)

        _upsert_chunks(collection, embeddings_client, chunks)
        track_stats[track]["files"] += 1
        track_stats[track]["chunks"] += len(chunks)
        track_stats[track]["chunks_embedded_this_run"] += len(chunks)

    return track_stats, warnings


# --- orchestration -----------------------------------------------------------------


def run_ingestion(
    *,
    embeddings_client: EmbeddingsClient | None = None,
    collection: Any | None = None,
    sources_csv_path: Path = SOURCES_CSV_PATH,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Any]:
    """Runs the full pipeline against the given (or default, real) Chroma
    collection and embeddings client, returns the manifest dict. Does not
    write it to disk — see ingest() / write_manifest(). Split out from
    ingest() so tests can pass a fake embeddings_client + a real (ephemeral)
    Chroma collection without touching the network or the real persisted
    store.
    """
    embeddings_client = embeddings_client or get_embeddings_client()
    collection = collection or get_collection()
    ingested_at = _now_iso()

    rows = read_sources_csv(sources_csv_path)
    source_url_map = build_source_url_map(rows)

    tier1_stats = _ingest_tier1(collection, embeddings_client, rows, ingested_at=ingested_at)
    track_stats, tier2_warnings = _ingest_tier2(
        collection, embeddings_client, source_url_map, ingested_at=ingested_at, raw_dir=raw_dir
    )

    total_chunks = tier1_stats["embedded"] + track_stats["A"]["chunks"] + track_stats["B"]["chunks"]

    warnings = list(tier2_warnings)
    if tier1_stats["skipped_no_local_file"] or tier1_stats["skipped_failed_crawl"]:
        warnings.append(
            f"{tier1_stats['skipped_no_local_file']} sources.csv row(s) skipped: no local_file "
            f"(citation-only, kept as metadata but not embedded); "
            f"{tier1_stats['skipped_failed_crawl']} row(s) skipped: notes explicitly flag a failed crawl"
        )
    warnings.append(
        "Track B (long-form PDF-to-text) tables were reconstructed heuristically from blank-line "
        "and 'Bảng N:' boundaries, not verified against the source PDFs page-by-page — §5.2 flags "
        "this as needing a one-time human spot-check before the corpus is treated as fully trusted, "
        "especially byt-5481's Bảng 4/5 treatment-target tables that the mock patient scenario "
        "depends on."
    )

    # Aggregate corpus hash: every tier-1 row's content_hash the collection
    # currently holds, plus every tier-2 file's source_content_hash. Cheap
    # to recompute (metadata-only reads, no embedding calls) and gives a
    # single "did anything in the corpus change" fingerprint for the
    # manifest, independent of chunk boundaries.
    all_hashes: list[str] = []
    tier1_meta = collection.get(where={"tier": 1}, include=["metadatas"])
    all_hashes.extend(m["content_hash"] for m in tier1_meta["metadatas"] if "content_hash" in m)
    for filename in sorted(TRACK_A_FILES | TRACK_B_FILES):
        file_meta = collection.get(where={"source_file": filename}, limit=1, include=["metadatas"])
        if file_meta["metadatas"]:
            all_hashes.append(file_meta["metadatas"][0].get("source_content_hash", ""))
    corpus_content_hash = compute_hash(*sorted(all_hashes))

    manifest = {
        "corpus_version": CORPUS_VERSION,
        "generated_at_utc": ingested_at,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "tier1_sources_csv": tier1_stats,
        "tier2_track_a": track_stats["A"],
        "tier2_track_b": track_stats["B"],
        "total_chunks": total_chunks,
        "corpus_content_hash": corpus_content_hash,
        "warnings": warnings,
    }
    return manifest


def write_manifest(manifest: dict[str, Any], path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


def ingest() -> dict[str, Any]:
    manifest = run_ingestion()
    write_manifest(manifest)
    logger.info(
        "ingestion complete: %s tier-1 chunks, %s tier-2A chunks, %s tier-2B chunks, %s total",
        manifest["tier1_sources_csv"]["embedded"],
        manifest["tier2_track_a"]["chunks"],
        manifest["tier2_track_b"]["chunks"],
        manifest["total_chunks"],
    )
    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = ingest()
    print(json.dumps(result, indent=2, ensure_ascii=False))

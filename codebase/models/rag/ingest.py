"""RAG ingestion pipeline — corpus -> chunks -> local embeddings -> Qdrant.

OWNER: RAG data-pipeline workstream. Do not edit from other workstreams.

Chunking lives in codebase/models/rag/chunking.py (pure, unit-tested); this
module owns the I/O: reading sources.csv and raw/*.md, embedding with the
local model (codebase/models/rag/embeddings.py), writing to the vector store
and the manifest. Run offline, never on the request path:

    python -m codebase.models.rag.ingest            # incremental
    python -m codebase.models.rag.ingest --rebuild  # drop + rebuild the collection

The collection is named per embedding model (`knowledge_base__bge-m3`), so a
model switch builds a fresh collection instead of mixing vector sizes.

Idempotent: every chunk carries a `pipeline_hash` of the file's whole chunker
output (texts and metadata, chunker version). Chunking is cheap and runs every
time; embedding only happens for a file whose output changed — so an edit to
one web file, which also shifts the syllable vocabulary used to repair the PDF
files, re-embeds a PDF file only if its repaired text actually changed. A
changed file has all of its chunks deleted and re-embedded (boundaries can
shift on any edit, so it is never diffed chunk-by-chunk). Files that left raw/
have their chunks removed.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codebase.backend.config import get_settings
from codebase.models.rag.chunking import (
    CHUNKER_VERSION,
    CORPUS_VERSION,
    SKIP_FAILED_CRAWL,
    SKIP_NO_LOCAL_FILE,
    build_syllable_vocab,
    build_tier1_chunk,
    chunk_document,
    classify_tier1_skip,
    compute_hash,
    detect_status,
)
from codebase.models.rag.embeddings import EmbeddingsClient, get_embeddings_client, model_slug
from codebase.models.rag.vector_store import get_vector_client

logger = logging.getLogger("codebase.models.rag.ingest")

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCES_CSV_PATH = REPO_ROOT / "data" / "knowledge_base" / "sources.csv"
RAW_DIR = REPO_ROOT / "data" / "knowledge_base" / "raw"
MANIFEST_PATH = REPO_ROOT / "data" / "knowledge_base" / "ingestion_manifest.json"

COLLECTION_BASE_NAME = "knowledge_base"
EMBED_BATCH_SIZE = 64  # chunks handed to the model per call (it sub-batches itself)

# Payload fields ingest and retrieval filter on.
PAYLOAD_INDEXES = {
    "tier": "integer",
    "row_id": "integer",
    "source_file": "keyword",
    "status": "keyword",
    "doc_type": "keyword",
}

_PDF_MARKER = "--- Trang "


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def collection_name(model_name: str | None = None) -> str:
    return f"{COLLECTION_BASE_NAME}__{model_slug(model_name or get_settings().embedding_model)}"


def get_collection(client: Any | None = None) -> Any:
    """The knowledge-base collection for the configured embedding model."""
    client = client or get_vector_client()
    return client.get_or_create_collection(collection_name(), payload_indexes=PAYLOAD_INDEXES)


# --- source loading -------------------------------------------------------------


def read_sources_csv(path: Path = SOURCES_CSV_PATH) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _raw_files_of(row: dict[str, str]) -> list[str]:
    return [
        part.strip()[len("raw/") :]
        for part in (row.get("local_file") or "").split(";")
        if part.strip().startswith("raw/")
    ]


def build_source_info_map(rows: list[dict[str, str]]) -> dict[str, dict[str, str | None]]:
    """raw filename -> {url, organization, trust_tier, category} from the
    sources.csv rows that reference it (the first row wins; rows that share a
    file agree on these). Gives tier-2 chunks the same provenance fields
    tier-1 rows have — v1 tier-2 chunks carried none of them."""
    info: dict[str, dict[str, str | None]] = {}
    for row in rows:
        for filename in _raw_files_of(row):
            info.setdefault(
                filename,
                {
                    "url": (row.get("url") or "").strip() or None,
                    "organization": (row.get("organization") or "").strip() or None,
                    "trust_tier": (row.get("tier") or "").strip() or None,
                    "category": (row.get("category") or "").strip() or None,
                },
            )
    return info


def build_source_url_map(rows: list[dict[str, str]]) -> dict[str, str]:
    return {name: i["url"] for name, i in build_source_info_map(rows).items() if i["url"]}


# --- vector store helpers ---------------------------------------------------------


def _split_chunk(chunk: dict, embedding_model: str) -> tuple[str, str, dict]:
    metadata = {k: v for k, v in chunk.items() if k not in ("chunk_id", "text") and v is not None}
    metadata["embedding_model"] = embedding_model
    return chunk["chunk_id"], chunk["text"], metadata


def _upsert_chunks(
    collection: Any, embeddings_client: EmbeddingsClient, chunks: list[dict], embedding_model: str
) -> int:
    """Embed and upsert; returns the vector size (0 if nothing was written)."""
    size = 0
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = [_split_chunk(c, embedding_model) for c in chunks[start : start + EMBED_BATCH_SIZE]]
        ids = [b[0] for b in batch]
        texts = [b[1] for b in batch]
        metadatas = [b[2] for b in batch]
        embeddings = embeddings_client.embed_documents(texts)
        size = len(embeddings[0]) if embeddings else size
        collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    return size


def _output_hash(chunks: list[dict]) -> str:
    """Fingerprint of everything a file's chunks would write, except the
    per-run timestamp — equal hash means the stored vectors are still right."""
    stable = [{k: v for k, v in c.items() if k != "ingested_at"} for c in chunks]
    return compute_hash(CHUNKER_VERSION, json.dumps(stable, sort_keys=True, ensure_ascii=False))


def _token_summary(tokens: list[int]) -> dict[str, int]:
    if not tokens:
        return {"min": 0, "median": 0, "max": 0}
    return {"min": min(tokens), "median": int(statistics.median(tokens)), "max": max(tokens)}


# --- tier 1 ------------------------------------------------------------------------


def _ingest_tier1(
    collection: Any,
    embeddings_client: EmbeddingsClient,
    rows: list[dict[str, str]],
    status_by_file: dict[str, str],
    *,
    embedding_model: str,
    ingested_at: str,
) -> tuple[dict[str, Any], int]:
    stats = {
        "total_rows": len(rows),
        "embedded_this_run": 0,
        "unchanged": 0,
        "skipped_no_local_file": 0,
        "skipped_failed_crawl": 0,
    }
    pending: list[dict] = []
    tokens: list[int] = []
    for row_id, row in enumerate(rows):
        skip_reason = classify_tier1_skip(row)
        if skip_reason == SKIP_NO_LOCAL_FILE:
            stats["skipped_no_local_file"] += 1
            continue
        if skip_reason == SKIP_FAILED_CRAWL:
            stats["skipped_failed_crawl"] += 1
            continue

        # A fact card citing a superseded decision is itself superseded.
        statuses = {status_by_file.get(f, "active") for f in _raw_files_of(row)}
        status = "superseded" if statuses == {"superseded"} else "active"
        chunk = build_tier1_chunk(row, row_id, status=status, ingested_at=ingested_at)
        tokens.append(chunk["n_tokens"])
        existing = collection.get(where={"row_id": row_id}, include=["metadatas"])
        existing_hash = existing["metadatas"][0].get("content_hash") if existing["metadatas"] else None
        if existing_hash == chunk["content_hash"]:
            stats["unchanged"] += 1
            continue
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        pending.append(chunk)

    # Rows past the end of a shortened sources.csv.
    stale = [
        chunk_id
        for chunk_id, meta in zip(*_all_tier_rows(collection, tier=1), strict=True)
        if meta.get("row_id", -1) >= len(rows)
    ]
    if stale:
        collection.delete(ids=stale)

    size = _upsert_chunks(collection, embeddings_client, pending, embedding_model)
    stats["embedded_this_run"] = len(pending)
    stats["embedded"] = stats["embedded_this_run"] + stats["unchanged"]
    stats["tokens"] = _token_summary(tokens)
    return stats, size


def _all_tier_rows(collection: Any, *, tier: int) -> tuple[list[str], list[dict]]:
    got = collection.get(where={"tier": tier}, include=["metadatas"])
    return got["ids"], got["metadatas"]


# --- tier 2 ------------------------------------------------------------------------


def _ingest_tier2(
    collection: Any,
    embeddings_client: EmbeddingsClient,
    source_info: dict[str, dict[str, str | None]],
    contents: dict[str, str],
    *,
    embedding_model: str,
    ingested_at: str,
) -> tuple[dict[str, Any], list[str], int]:
    warnings: list[str] = []
    vocab = build_syllable_vocab([c for c in contents.values() if _PDF_MARKER not in c])

    per_file: dict[str, Any] = {}
    totals = {"files": 0, "chunks": 0, "chunks_embedded_this_run": 0, "unchanged_files": 0}
    all_tokens: list[int] = []
    size = 0

    for filename in sorted(contents):
        content = contents[filename]
        is_pdf = _PDF_MARKER in content
        info = source_info.get(filename, {})
        if not info:
            warnings.append(f"{filename}: not referenced by any sources.csv row — no organization/trust_tier")

        file_stats: dict[str, Any] = {}
        chunks = chunk_document(
            filename,
            content,
            source_url=info.get("url"),
            source_info=info,
            syllable_vocab=vocab if is_pdf else None,
            ingested_at=ingested_at,
            stats=file_stats,
        )
        pipeline_hash = _output_hash(chunks)
        for chunk in chunks:
            chunk["pipeline_hash"] = pipeline_hash
        tokens = [c["n_tokens"] for c in chunks]
        all_tokens.extend(tokens)
        file_stats["tokens"] = _token_summary(tokens)
        file_stats["doc_type"] = chunks[0]["doc_type"] if chunks else None
        per_file[filename] = file_stats
        totals["files"] += 1
        totals["chunks"] += len(chunks)

        existing = collection.get(where={"source_file": filename}, limit=1, include=["metadatas"])
        existing_hash = existing["metadatas"][0].get("pipeline_hash") if existing["metadatas"] else None
        if existing_hash == pipeline_hash:
            totals["unchanged_files"] += 1
            continue

        collection.delete(where={"source_file": filename})
        size = _upsert_chunks(collection, embeddings_client, chunks, embedding_model) or size
        totals["chunks_embedded_this_run"] += len(chunks)

    ids, metadatas = _all_tier_rows(collection, tier=2)
    gone = sorted({m.get("source_file") for m in metadatas} - set(contents))
    for filename in gone:
        collection.delete(where={"source_file": filename})
        warnings.append(f"{filename}: no longer in raw/, its chunks were removed")

    totals["tokens"] = _token_summary(all_tokens)
    totals["files_by_status"] = {
        s: sorted(f for f, st in per_file.items() if st.get("status") == s) for s in ("superseded",)
    }
    totals["per_file"] = per_file
    return totals, warnings, size


# --- orchestration -------------------------------------------------------------------


def run_ingestion(
    *,
    embeddings_client: EmbeddingsClient | None = None,
    collection: Any | None = None,
    sources_csv_path: Path = SOURCES_CSV_PATH,
    raw_dir: Path = RAW_DIR,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Run the full pipeline and return the manifest dict (not written — see
    ingest()). Tests pass a fake embeddings client and an in-memory Qdrant
    collection."""
    embedding_model = embedding_model or get_settings().embedding_model
    embeddings_client = embeddings_client or get_embeddings_client()
    collection = collection or get_collection()
    ingested_at = _now_iso()

    rows = read_sources_csv(sources_csv_path)
    source_info = build_source_info_map(rows)
    contents = {p.name: p.read_text(encoding="utf-8") for p in sorted(raw_dir.glob("*.md"))}
    status_by_file = {name: detect_status(content) for name, content in contents.items()}

    missing = sorted(set(source_info) - set(contents))
    warnings = [f"{name}: referenced by sources.csv but not found in {raw_dir.name}/" for name in missing]

    tier1_stats, size1 = _ingest_tier1(
        collection, embeddings_client, rows, status_by_file, embedding_model=embedding_model, ingested_at=ingested_at
    )
    tier2_stats, tier2_warnings, size2 = _ingest_tier2(
        collection, embeddings_client, source_info, contents, embedding_model=embedding_model, ingested_at=ingested_at
    )
    warnings.extend(tier2_warnings)
    if tier1_stats["skipped_no_local_file"] or tier1_stats["skipped_failed_crawl"]:
        warnings.append(
            f"{tier1_stats['skipped_no_local_file']} sources.csv row(s) skipped: no local_file (citation-only); "
            f"{tier1_stats['skipped_failed_crawl']} row(s) skipped: notes flag a failed crawl"
        )
    warnings.append(
        "PDF-extracted tables are reconstructed from line order under 'Bảng N:' captions, not verified "
        "against the source PDFs page by page — spot-check byt-5481's Bảng 4/5 before relying on them."
    )

    embedding_dimensions = size1 or size2 or (collection.vector_size() if hasattr(collection, "vector_size") else None)

    # Corpus fingerprint, independent of chunk boundaries.
    corpus_content_hash = compute_hash(
        *[compute_hash(c) for _, c in sorted(contents.items())], *[r.get("key_content", "") for r in rows]
    )

    return {
        "corpus_version": CORPUS_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "generated_at_utc": ingested_at,
        "embedding_model": embedding_model,
        "embedding_dimensions": embedding_dimensions,
        "collection": getattr(collection, "name", None),
        "tier1_sources_csv": tier1_stats,
        "tier2_documents": tier2_stats,
        "total_chunks": tier1_stats["embedded"] + tier2_stats["chunks"],
        "corpus_content_hash": corpus_content_hash,
        "warnings": warnings,
    }


def write_manifest(manifest: dict[str, Any], path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


def ingest(*, rebuild: bool = False) -> dict[str, Any]:
    if rebuild:
        get_vector_client().delete_collection(collection_name())
    manifest = run_ingestion()
    write_manifest(manifest)
    logger.info(
        "ingestion complete into %s: %s tier-1 + %s tier-2 chunks (%s embedded this run), model %s (%s-dim)",
        manifest["collection"],
        manifest["tier1_sources_csv"]["embedded"],
        manifest["tier2_documents"]["chunks"],
        manifest["tier1_sources_csv"]["embedded_this_run"] + manifest["tier2_documents"]["chunks_embedded_this_run"],
        manifest["embedding_model"],
        manifest["embedding_dimensions"],
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the knowledge base into the vector store.")
    parser.add_argument("--rebuild", action="store_true", help="drop the collection first and re-embed everything")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    manifest = ingest(rebuild=args.rebuild)
    summary = {k: v for k, v in manifest.items() if k != "tier2_documents"}
    summary["tier2_documents"] = {k: v for k, v in manifest["tier2_documents"].items() if k != "per_file"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

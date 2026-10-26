"""Integration-level tests for the ingestion orchestration in
codebase/models/rag/ingest.py — as opposed to test_rag_chunking.py, which
covers the pure chunking logic in isolation.

Runs `run_ingestion()` against a real in-memory Qdrant collection
(qdrant-client `:memory:` mode) and a fake embeddings client, so no Qdrant
server, no model download and no network are needed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid

import pytest

from codebase.models.rag import ingest
from codebase.models.rag.ingest import RAW_DIR, run_ingestion, write_manifest
from codebase.models.rag.vector_store import QdrantVectorClient, VectorSizeMismatchError

FAKE_MODEL = "test/fake-embedder"


class FakeEmbeddingsClient:
    """Deterministic, offline stand-in for the embedding model — same
    interface (embed_documents/embed_query), 8-dim vectors from a hash of the
    text, so identical text always gets an identical vector. `call_count` is
    what the idempotency tests assert on."""

    def __init__(self, dim: int = 8) -> None:
        self.call_count = 0
        self.embedded_texts: list[str] = []
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[: self.dim]
        return [b / 255 + 0.01 for b in digest]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.embedded_texts.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def ephemeral_collection():
    """A fresh in-memory Qdrant collection per test (own client, so isolated)."""
    client = QdrantVectorClient(location=":memory:")
    return client.get_or_create_collection(f"test-{uuid.uuid4().hex}", payload_indexes=ingest.PAYLOAD_INDEXES)


def _run(collection, fake, **kwargs):
    return run_ingestion(embeddings_client=fake, collection=collection, embedding_model=FAKE_MODEL, **kwargs)


def test_run_ingestion_produces_a_well_formed_manifest(ephemeral_collection):
    manifest = _run(ephemeral_collection, FakeEmbeddingsClient())

    assert manifest["corpus_version"] and manifest["chunker_version"]
    assert manifest["embedding_model"] == FAKE_MODEL
    assert manifest["embedding_dimensions"] == 8
    assert manifest["total_chunks"] == ephemeral_collection.count()
    tier1 = manifest["tier1_sources_csv"]
    assert tier1["total_rows"] == 56
    assert tier1["embedded"] + tier1["skipped_no_local_file"] + tier1["skipped_failed_crawl"] == 56
    tier2 = manifest["tier2_documents"]
    assert tier2["files"] == len(list(RAW_DIR.glob("*.md")))
    assert tier2["files_by_status"]["superseded"] == ["byt-3280-qd-byt-full-text.md", "byt-3319-qd-byt-full-text.md"]
    per_file = tier2["per_file"]["byt-5481-qd-byt-full-text.md"]
    assert per_file["is_pdf_extract"] and per_file["syllables_repaired"] > 500
    # The table-reconstruction caveat must survive into the manifest.
    assert any("spot-check" in w for w in manifest["warnings"])


def test_tier2_chunks_carry_sources_csv_provenance(ephemeral_collection):
    _run(ephemeral_collection, FakeEmbeddingsClient())
    got = ephemeral_collection.get(where={"source_file": "byt-5481-qd-byt-full-text.md"}, limit=1)
    meta = got["metadatas"][0]
    assert meta["organization"] == "Bộ Y tế"
    assert meta["trust_tier"] == "Bộ Y tế"
    assert meta["doc_type"] == "national_guideline"
    assert meta["status"] == "active"
    assert meta["embedding_model"] == FAKE_MODEL
    assert meta["source_url"].startswith("http")


def test_tier1_rows_citing_only_superseded_files_are_marked_superseded(ephemeral_collection):
    _run(ephemeral_collection, FakeEmbeddingsClient())
    rows = ingest.read_sources_csv()
    row_id = next(i for i, r in enumerate(rows) if r["local_file"].strip() == "raw/byt-3319-qd-byt-full-text.md")
    meta = ephemeral_collection.get(where={"row_id": row_id})["metadatas"][0]
    assert meta["status"] == "superseded"


def test_reingesting_unchanged_corpus_costs_zero_embedding_calls(ephemeral_collection):
    fake = FakeEmbeddingsClient()
    _run(ephemeral_collection, fake)
    first_call_count = fake.call_count
    assert first_call_count > 0

    second = _run(ephemeral_collection, fake)

    assert fake.call_count == first_call_count
    assert second["tier1_sources_csv"]["embedded_this_run"] == 0
    assert second["tier2_documents"]["chunks_embedded_this_run"] == 0
    assert second["tier1_sources_csv"]["unchanged"] == second["tier1_sources_csv"]["embedded"]


def test_editing_one_file_reembeds_only_that_file(ephemeral_collection, tmp_path):
    raw_copy = tmp_path / "raw"
    shutil.copytree(RAW_DIR, raw_copy)
    fake = FakeEmbeddingsClient()
    _run(ephemeral_collection, fake, raw_dir=raw_copy)

    target = raw_copy / "vade-tong-quan-benh-dtd.md"
    # Words already in the corpus vocabulary, so the PDF files' syllable
    # repair (and therefore their chunks) is unaffected by this edit.
    target.write_text(target.read_text(encoding="utf-8") + "\n\nGhi chú: cập nhật 2026.\n", encoding="utf-8")
    fake.embedded_texts.clear()
    manifest = _run(ephemeral_collection, fake, raw_dir=raw_copy)

    tier2 = manifest["tier2_documents"]
    assert tier2["unchanged_files"] == tier2["files"] - 1
    assert tier2["chunks_embedded_this_run"] == tier2["per_file"]["vade-tong-quan-benh-dtd.md"]["chunks"]
    assert manifest["tier1_sources_csv"]["embedded_this_run"] == 0
    assert any("Ghi chú: cập nhật 2026." in t for t in fake.embedded_texts)


def test_removed_file_has_its_chunks_deleted(ephemeral_collection, tmp_path):
    raw_copy = tmp_path / "raw"
    shutil.copytree(RAW_DIR, raw_copy)
    fake = FakeEmbeddingsClient()
    _run(ephemeral_collection, fake, raw_dir=raw_copy)
    (raw_copy / "who-2020-physical-activity-guidelines.md").unlink()

    manifest = _run(ephemeral_collection, fake, raw_dir=raw_copy)

    assert ephemeral_collection.get(where={"source_file": "who-2020-physical-activity-guidelines.md"})["ids"] == []
    assert any("who-2020-physical-activity-guidelines.md" in w for w in manifest["warnings"])


def test_ingested_chunks_are_retrievable_by_similarity(ephemeral_collection):
    """Not a semantic test (hash vectors) — confirms the upsert -> query
    round-trip returns the metadata knowledge_tools.py depends on."""
    fake = FakeEmbeddingsClient()
    _run(ephemeral_collection, fake)

    result = ephemeral_collection.query(query_embeddings=[fake.embed_query("bất kỳ câu hỏi nào")], n_results=3)

    assert len(result["ids"][0]) == 3
    for metadata in result["metadatas"][0]:
        assert metadata.get("tier") in (1, 2)
        assert metadata.get("embedding_model") == FAKE_MODEL


def test_vector_size_mismatch_is_refused(ephemeral_collection):
    _run(ephemeral_collection, FakeEmbeddingsClient(dim=8))
    with pytest.raises(VectorSizeMismatchError):
        ephemeral_collection.query(query_embeddings=[[0.1] * 16], n_results=1)


def test_collection_name_is_per_model():
    assert ingest.collection_name("BAAI/bge-m3") == "knowledge_base__bge-m3"
    assert ingest.collection_name("text-embedding-3-small") == "knowledge_base__text-embedding-3-small"


def test_write_manifest_roundtrips_through_json(ephemeral_collection, tmp_path):
    manifest = _run(ephemeral_collection, FakeEmbeddingsClient())
    out_path = tmp_path / "ingestion_manifest.json"
    write_manifest(manifest, path=out_path)
    assert json.loads(out_path.read_text(encoding="utf-8"))["total_chunks"] == manifest["total_chunks"]

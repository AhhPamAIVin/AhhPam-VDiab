"""Integration-level tests for the ingestion orchestration in
codebase/models/rag/ingest.py — as opposed to test_rag_chunking.py, which
covers the pure chunking logic in isolation.

Runs `run_ingestion()` against a real (ephemeral, in-memory) Chroma collection
but a fake embeddings client, per that function's own docstring: "Split out
from ingest() so tests can pass a fake embeddings_client + a real (ephemeral)
Chroma collection without touching the network or the real persisted store."
No OpenAI API key is required or used here.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from codebase.models.rag.ingest import run_ingestion, write_manifest


class FakeEmbeddingsClient:
    """Deterministic, offline stand-in for OpenAIEmbeddings — same interface
    (embed_documents/embed_query), fake 8-dim vectors derived from a hash of
    the text so identical text always gets an identical vector (this is what
    lets the idempotency test below assert "zero embedding calls" concretely:
    call_count is what's actually asserted, not the vectors' content)."""

    def __init__(self) -> None:
        self.call_count = 0
        self.embedded_texts: list[str] = []

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
        return [b / 255 for b in digest]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.embedded_texts.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def ephemeral_collection():
    """A fresh, isolated Chroma collection per test.

    chromadb.EphemeralClient() instances are NOT isolated from each other
    within the same process (confirmed directly: two separate instances
    sharing a collection name see each other's data) — so this can't just
    call get_collection(chromadb.EphemeralClient()) with the module's fixed
    COLLECTION_NAME, or tests bleed into each other depending on run order.
    A per-test unique collection name sidesteps that; the fixed COLLECTION_NAME
    constant itself is not what these tests are checking.
    """
    import chromadb

    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(f"test-{uuid.uuid4().hex}", metadata={"hnsw:space": "cosine"})


def test_run_ingestion_produces_a_well_formed_manifest(ephemeral_collection):
    fake = FakeEmbeddingsClient()

    manifest = run_ingestion(embeddings_client=fake, collection=ephemeral_collection)

    assert manifest["corpus_version"]
    assert manifest["embedding_model"] == "text-embedding-3-small"
    assert manifest["total_chunks"] > 0
    # Tier-1: sources.csv has 56 rows; some are skipped (no local_file /
    # failed crawl per design doc §5.1), the rest get embedded.
    tier1 = manifest["tier1_sources_csv"]
    assert tier1["total_rows"] == 56
    assert tier1["embedded"] > 0
    assert tier1["embedded"] + tier1["skipped_no_local_file"] + tier1["skipped_failed_crawl"] == 56
    # Tier-2: both tracks should have picked up real files from data/knowledge_base/raw.
    assert manifest["tier2_track_a"]["files"] > 0
    assert manifest["tier2_track_b"]["files"] > 0
    # byt-3319 has no page markers at all (verified directly with grep — an
    # earlier pass of the design doc mislabeled byt-5481 as the exception
    # here, which was wrong: byt-5481 actually has 77 page markers). The
    # manifest must say so rather than silently treating it like every other
    # Track B file.
    assert "byt-3319-qd-byt-full-text.md" in manifest["tier2_track_b"]["files_without_page_markers"]
    # §5.2's table-reconstruction caveat must survive into the manifest, not
    # just live in a docstring — it's the one thing a human reviewer most
    # needs to see before trusting Track B content.
    assert any("spot-check" in w for w in manifest["warnings"])


def test_reingesting_unchanged_corpus_costs_zero_embedding_calls(ephemeral_collection):
    fake = FakeEmbeddingsClient()
    run_ingestion(embeddings_client=fake, collection=ephemeral_collection)
    first_call_count = fake.call_count
    assert first_call_count > 0

    second_manifest = run_ingestion(embeddings_client=fake, collection=ephemeral_collection)

    # This is the idempotency guarantee design doc §5.9 promises: nothing
    # changed on disk between runs, so nothing should be re-embedded.
    assert fake.call_count == first_call_count
    assert second_manifest["tier1_sources_csv"]["embedded_this_run"] == 0
    assert second_manifest["tier2_track_a"]["chunks_embedded_this_run"] == 0
    assert second_manifest["tier2_track_b"]["chunks_embedded_this_run"] == 0
    assert second_manifest["tier1_sources_csv"]["unchanged"] == second_manifest["tier1_sources_csv"]["embedded"]


def test_editing_one_tier2_file_reembeds_only_that_file(ephemeral_collection, tmp_path):
    """A change to one Track A file must not force re-embedding the rest of
    the corpus — copies the real raw/ dir so this can mutate one file safely."""
    import shutil

    from codebase.models.rag.ingest import RAW_DIR

    raw_copy = tmp_path / "raw"
    shutil.copytree(RAW_DIR, raw_copy)

    fake = FakeEmbeddingsClient()
    run_ingestion(embeddings_client=fake, collection=ephemeral_collection, raw_dir=raw_copy)
    first_call_count = fake.call_count

    target = raw_copy / "vade-tong-quan-benh-dtd.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n\nAppended for the test.\n", encoding="utf-8")

    manifest = run_ingestion(embeddings_client=fake, collection=ephemeral_collection, raw_dir=raw_copy)

    assert fake.call_count > first_call_count  # something was re-embedded...
    # ...but only the one file's worth of chunks, everything else unchanged.
    other_track_a_files = manifest["tier2_track_a"]["files"] - 1
    assert manifest["tier2_track_a"]["unchanged_files"] == other_track_a_files
    assert manifest["tier2_track_b"]["chunks_embedded_this_run"] == 0
    assert manifest["tier1_sources_csv"]["embedded_this_run"] == 0


def test_ingested_chunks_are_retrievable_by_similarity(ephemeral_collection):
    """Not a real semantic-relevance test (the fake embeddings are hash-based,
    not meaningful vectors) — just confirms the upsert -> query round-trip
    works end-to-end: what gets embedded and stored can be queried back out
    with the metadata search_approved_knowledge (codebase/models/agents/tools
    /knowledge_tools.py) depends on."""
    fake = FakeEmbeddingsClient()
    run_ingestion(embeddings_client=fake, collection=ephemeral_collection)

    result = ephemeral_collection.query(
        query_embeddings=[fake.embed_query("bất kỳ câu hỏi nào")],
        n_results=3,
        include=["metadatas", "documents", "distances"],
    )

    assert len(result["ids"][0]) == 3
    for metadata in result["metadatas"][0]:
        assert metadata.get("tier") in (1, 2)
        assert metadata.get("embedding_model") == "text-embedding-3-small"


def test_write_manifest_roundtrips_through_json(ephemeral_collection, tmp_path):
    fake = FakeEmbeddingsClient()
    manifest = run_ingestion(embeddings_client=fake, collection=ephemeral_collection)

    out_path = tmp_path / "ingestion_manifest.json"
    write_manifest(manifest, path=out_path)

    import json

    reloaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert reloaded["total_chunks"] == manifest["total_chunks"]

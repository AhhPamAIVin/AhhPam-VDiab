"""QdrantCollection (codebase/models/rag/vector_store.py) must behave like the
subset of Chroma's collection API that ingest.py / knowledge_tools.py /
insight_tools.py rely on. Runs against qdrant-client's in-process `:memory:`
mode — no Qdrant server, no network.
"""

from __future__ import annotations

import pytest

from codebase.models.rag.ingest import run_ingestion

pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient  # noqa: E402

from codebase.models.rag.vector_store import QdrantCollection  # noqa: E402
from tests.test_models.test_rag_ingest import FakeEmbeddingsClient  # noqa: E402


@pytest.fixture
def collection() -> QdrantCollection:
    return QdrantCollection(QdrantClient(":memory:"), "test_collection")


def _seed(collection: QdrantCollection) -> None:
    collection.upsert(
        ids=["a", "b", "c"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]],
        documents=["doc a", "doc b", "doc c"],
        metadatas=[
            {"patient_id": "p1", "source_kind": "weekly_batch", "tier": 1},
            {"patient_id": "p1", "source_kind": "appointment", "tier": 2},
            {"patient_id": "p2", "source_kind": "weekly_batch", "tier": 2},
        ],
    )


def test_empty_collection_reads_return_empty_shapes(collection):
    assert collection.get(where={"tier": 1}, include=["metadatas"]) == {"ids": [], "metadatas": [], "documents": []}
    raw = collection.query(query_embeddings=[[1.0, 0.0]], n_results=3)
    assert raw["ids"] == [[]] and raw["distances"] == [[]]
    collection.delete(where={"tier": 1})  # no-op, must not raise


def test_query_returns_original_ids_documents_and_cosine_distance(collection):
    _seed(collection)
    raw = collection.query(query_embeddings=[[1.0, 0.0]], n_results=3)
    assert raw["ids"][0][0] == "a"
    assert raw["documents"][0][0] == "doc a"
    assert raw["metadatas"][0][0]["tier"] == 1
    assert raw["distances"][0][0] == pytest.approx(0.0, abs=1e-6)
    assert raw["distances"][0] == sorted(raw["distances"][0])


def test_and_filter_scopes_query(collection):
    _seed(collection)
    raw = collection.query(
        query_embeddings=[[0.0, 1.0]],
        n_results=5,
        where={"$and": [{"patient_id": "p1"}, {"source_kind": "weekly_batch"}]},
    )
    assert raw["ids"] == [["a"]]


def test_get_limit_and_delete_by_ids_and_where(collection):
    _seed(collection)
    assert len(collection.get(where={"tier": 2}, limit=1, include=["metadatas"])["ids"]) == 1
    assert sorted(collection.get(where={"tier": 2}, include=[])["ids"]) == ["b", "c"]

    collection.delete(ids=["b"])
    assert collection.get(where={"tier": 2}, include=[])["ids"] == ["c"]
    collection.delete(where={"patient_id": "p2"})
    assert collection.count() == 1


def test_upsert_replaces_same_id(collection):
    _seed(collection)
    collection.upsert(ids=["a"], embeddings=[[1.0, 0.0]], documents=["new a"], metadatas=[{"tier": 1}])
    assert collection.count() == 3
    assert collection.get(ids=["a"])["documents"] == ["new a"]


def test_ingestion_is_idempotent_on_qdrant(collection):
    embeddings = FakeEmbeddingsClient()
    first = run_ingestion(embeddings_client=embeddings, collection=collection)
    calls_after_first = embeddings.call_count
    assert first["total_chunks"] > 0
    assert collection.count() == first["total_chunks"]

    second = run_ingestion(embeddings_client=embeddings, collection=collection)
    assert embeddings.call_count == calls_after_first
    assert second["total_chunks"] == first["total_chunks"]
    assert second["corpus_content_hash"] == first["corpus_content_hash"]

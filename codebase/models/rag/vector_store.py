"""Vector store backend selection — Chroma (embedded, default) or Qdrant (server).

Every caller (rag/ingest.py, knowledge_tools.py, insight_tools.py,
upsert_insight.py) was written against Chroma's collection API: `get`,
`upsert`, `delete`, `query` with Chroma-style `where` filters and
`{"ids": [[...]], "distances": [[...]]}` result shapes. Rather than fork each
of those call sites per backend, QdrantCollection below implements exactly
that subset on top of qdrant-client, so switching is a single setting:

    VECTOR_STORE=qdrant
    QDRANT_URL=http://localhost:6333

Only the filter shapes those callers actually use are supported: a flat
`{"field": value}` equality, and `{"$and": [{...}, {...}]}` of those.
"""

from __future__ import annotations

import uuid
from typing import Any

from codebase.backend.config import get_settings

# Qdrant point ids must be unsigned ints or UUIDs, while our chunk/insight ids
# are free-form strings ("tier1-row-12", "byt-5481-...") — so the point id is
# a deterministic uuid5 of the string id, and the original id plus the
# document text ride along in the payload under these reserved keys.
_ID_KEY = "_chunk_id"
_DOCUMENT_KEY = "_document"
_ID_NAMESPACE = uuid.UUID("6f1c9d3e-2a4b-4c8e-9f0a-7d5e3b1c2a90")

_SCROLL_PAGE = 256


def get_vector_client() -> Any:
    """Chroma PersistentClient or QdrantChromaClient, per settings.vector_store.
    Both expose `get_or_create_collection(name, metadata=...)`."""
    settings = get_settings()
    if settings.vector_store == "qdrant":
        return QdrantChromaClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)

    import chromadb

    return chromadb.PersistentClient(path=settings.chroma_persist_dir)


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def _build_filter(where: dict[str, Any] | None) -> Any:
    from qdrant_client import models

    if not where:
        return None
    if "$and" in where:
        clauses = where["$and"]
    else:
        clauses = [where]
    must = []
    for clause in clauses:
        for key, value in clause.items():
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
    return models.Filter(must=must)


def _payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in (_ID_KEY, _DOCUMENT_KEY)}


class QdrantChromaClient:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url, api_key=api_key)

    def get_or_create_collection(self, name: str, metadata: dict[str, Any] | None = None) -> QdrantCollection:
        # The collection itself is created lazily on first upsert: Qdrant needs
        # the vector size up front, which is only known once we have a vector.
        return QdrantCollection(self._client, name)


class QdrantCollection:
    def __init__(self, client: Any, name: str) -> None:
        self._client = client
        self.name = name

    def _exists(self) -> bool:
        return self._client.collection_exists(self.name)

    def _ensure(self, vector_size: int) -> None:
        from qdrant_client import models

        if self._exists():
            return
        self._client.create_collection(
            self.name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        from qdrant_client import models

        if not ids:
            return
        self._ensure(len(embeddings[0]))
        points = [
            models.PointStruct(
                id=_point_id(chunk_id),
                vector=list(vector),
                payload={**metadata, _ID_KEY: chunk_id, _DOCUMENT_KEY: document},
            )
            for chunk_id, vector, document, metadata in zip(ids, embeddings, documents, metadatas, strict=True)
        ]
        for start in range(0, len(points), _SCROLL_PAGE):
            self._client.upsert(self.name, points=points[start : start + _SCROLL_PAGE], wait=True)

    def get(
        self,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        include = ["metadatas", "documents"] if include is None else include
        result: dict[str, Any] = {"ids": [], "metadatas": [], "documents": []}
        if not self._exists():
            return result

        if ids is not None:
            records = self._client.retrieve(self.name, ids=[_point_id(i) for i in ids], with_payload=True)
        else:
            records = []
            offset = None
            query_filter = _build_filter(where)
            while True:
                page_size = _SCROLL_PAGE if limit is None else min(_SCROLL_PAGE, limit - len(records))
                page, offset = self._client.scroll(
                    self.name, scroll_filter=query_filter, limit=page_size, offset=offset, with_payload=True
                )
                records.extend(page)
                if offset is None or (limit is not None and len(records) >= limit):
                    break

        for record in records:
            payload = record.payload or {}
            result["ids"].append(payload.get(_ID_KEY, str(record.id)))
            if "metadatas" in include:
                result["metadatas"].append(_payload_metadata(payload))
            if "documents" in include:
                result["documents"].append(payload.get(_DOCUMENT_KEY))
        return result

    def delete(self, *, ids: list[str] | None = None, where: dict[str, Any] | None = None) -> None:
        from qdrant_client import models

        if not self._exists():
            return
        if ids is not None:
            selector: Any = models.PointIdsList(points=[_point_id(i) for i in ids])
        elif where:
            selector = models.FilterSelector(filter=_build_filter(where))
        else:
            return
        self._client.delete(self.name, points_selector=selector, wait=True)

    def count(self) -> int:
        if not self._exists():
            return 0
        return self._client.count(self.name, exact=True).count

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        include = ["metadatas", "documents", "distances"] if include is None else include
        result: dict[str, Any] = {"ids": [], "metadatas": [], "documents": [], "distances": []}
        if not self._exists():
            return {key: [[] for _ in query_embeddings] for key in result}

        query_filter = _build_filter(where)
        for vector in query_embeddings:
            hits = self._client.query_points(
                self.name, query=list(vector), query_filter=query_filter, limit=n_results, with_payload=True
            ).points
            # Chroma cosine space reports distance = 1 - cosine_similarity;
            # Qdrant reports the similarity itself as the score.
            result["ids"].append([(h.payload or {}).get(_ID_KEY, str(h.id)) for h in hits])
            result["metadatas"].append([_payload_metadata(h.payload or {}) for h in hits])
            result["documents"].append([(h.payload or {}).get(_DOCUMENT_KEY) for h in hits])
            result["distances"].append([1.0 - h.score for h in hits])
        return result

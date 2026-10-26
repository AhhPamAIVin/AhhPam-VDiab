"""find_similar_insights / get_patient_insights — the reflect-agent's own
tool layer, internal to it, not exposed to the respond-agent's chat tools.

OWNER: long-term memory workstream (Phase 2, plan §B). See
docs/AI_Summary_Respond_System_Design.md §7.1, §8 and
/Users/phamminh/.claude/plans/warm-crafting-muffin.md §B/§C.

Same vector store and local embedding model as the knowledge base
(codebase/models/rag/vector_store.py, codebase/models/rag/embeddings.py —
insight text is PHI and is embedded on this machine, never sent out) — a
second, separate collection (`patient_insights__<model>`, not the knowledge
base), never mixed into general RAG
retrieval (design doc §7.1: "used only for the similarity check in §7.3,
never surfaced in general retrieval").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.config import get_settings
from codebase.backend.insights.models import InsightStatus, PatientInsight
from codebase.models.rag.embeddings import get_embeddings_client, model_slug
from codebase.models.rag.vector_store import get_vector_client

COLLECTION_NAME = "patient_insights"
# Filtered on every similarity check (find_similar_insights).
PAYLOAD_INDEXES = {"patient_id": "keyword", "source_kind": "keyword"}

__all__ = [
    "COLLECTION_NAME",
    "find_similar_insights",
    "get_insights_collection",
    "get_patient_insights",
]


def get_insights_collection(client: Any | None = None) -> Any:
    """The patient-insights collection (design doc §7.1) for the configured
    embedding model, named `patient_insights__<model>` so vectors of a
    different size never share a collection — one
    embedded vector per ACTIVE insight's `text`, metadata-filtered by BOTH
    `patient_id` and `source_kind` (plan §B/§C's hard partitioning
    invariant — see find_similar_insights below). Cosine space, created on
    first use, same convention as rag/ingest.py's `knowledge_base`
    collection.
    """
    client = client or get_vector_client()
    name = f"{COLLECTION_NAME}__{model_slug(get_settings().embedding_model)}"
    return client.get_or_create_collection(name, payload_indexes=PAYLOAD_INDEXES)


def _distance_to_score(distance: float) -> float:
    """Same conversion as knowledge_tools._distance_to_score — the
    collection is cosine space, so distance = 1 - cosine_similarity.
    Clipped defensively (floating point can push this a hair outside
    [0, 1])."""
    return max(0.0, min(1.0, 1.0 - distance))


def find_similar_insights(
    patient_id: UUID | str,
    source_kind: str,
    candidate_text: str,
    *,
    top_k: int = 1,
    collection: Any | None = None,
    embeddings_client: Any | None = None,
) -> list[dict[str, Any]]:
    """Embed `candidate_text`, similarity-search the patient's ACTIVE
    insights — scoped to BOTH `patient_id` AND `source_kind` (design doc
    §7.3; plan §B/§C: "hard invariant, not optional" — a weekly-batch
    candidate must never match/update an appointment insight or vice versa,
    and one patient's candidate must never match another patient's
    insight). Internal to the reflect-agent, not exposed to the
    respond-agent's chat tools (design doc §8).

    Returns hits ordered by similarity score (highest first, `top_k`
    capped), each `{"insight_id": str, "score": float, "text": str,
    "version": int, "category": str}` — upsert_insight.py compares the top
    hit's score against SIMILARITY_THRESHOLD to decide create vs. update.
    Empty list if the collection has nothing for this (patient_id,
    source_kind) pair yet.
    """
    collection = collection or get_insights_collection()
    embeddings_client = embeddings_client or get_embeddings_client()

    query_vector = embeddings_client.embed_query(candidate_text)
    raw = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where={"$and": [{"patient_id": str(patient_id)}, {"source_kind": str(source_kind)}]},
        include=["metadatas", "documents", "distances"],
    )

    ids = raw["ids"][0] if raw.get("ids") else []
    documents = raw["documents"][0] if raw.get("documents") else []
    metadatas = raw["metadatas"][0] if raw.get("metadatas") else []
    distances = raw["distances"][0] if raw.get("distances") else []

    hits: list[dict[str, Any]] = []
    for insight_id, text, metadata, distance in zip(ids, documents, metadatas, distances, strict=True):
        hits.append(
            {
                "insight_id": insight_id,
                "score": _distance_to_score(distance),
                "text": text,
                "version": metadata.get("version"),
                "category": metadata.get("category"),
            }
        )
    return hits


def get_patient_insights(
    session: Session,
    patient_id: UUID,
    source_kind: str | Sequence[str] | None = None,
    *,
    status: InsightStatus | None = InsightStatus.ACTIVE,
) -> list[dict[str, Any]]:
    """Read-only Postgres read — design doc §8: "used by respond-agent to
    add context and by the doctor-facing insights endpoint."

    Defaults to ACTIVE only — a SUPERSEDED row (see InsightStatus) is kept
    for audit history, not meant to resurface as current context; pass
    `status=None` for the full history including superseded rows.

    `source_kind` accepts a single value (e.g. doctor-facing callers wanting
    just "appointment") or a sequence (e.g. the patient's own summary wanting
    both "weekly_batch" and "chat" — two patient-facing streams, plan Part 3)
    — a plain string is also iterable, so it's special-cased rather than
    silently `.in_()`-matched character by character.
    """
    statement = select(PatientInsight).where(PatientInsight.patient_id == patient_id)
    if status is not None:
        statement = statement.where(PatientInsight.status == status)
    if isinstance(source_kind, str):
        statement = statement.where(PatientInsight.source_kind == source_kind)
    elif source_kind is not None:
        statement = statement.where(col(PatientInsight.source_kind).in_(source_kind))
    statement = statement.order_by(PatientInsight.last_confirmed_at.desc())  # type: ignore[attr-defined]
    rows = session.exec(statement).all()
    return [row.model_dump() for row in rows]

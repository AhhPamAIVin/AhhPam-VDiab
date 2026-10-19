"""upsert_insight — similarity search, create-vs-update decision (design doc
§7.3 steps 3-4).

OWNER: long-term memory workstream (Phase 2, plan §B). See
docs/AI_Summary_Respond_System_Design.md §7.3 and
/Users/phamminh/.claude/plans/warm-crafting-muffin.md §B/§C for the
partitioned-matching invariant this implements.

`upsert_insight()` is the plain, session-taking function nodes/reflect.py's
`reflect()` calls once per proposed candidate. `upsert_insight_node()` is a
thin LangGraph-node wrapper around the same logic for build_reflect_graph()
(state.py's ReflectState) — kept for design doc §3.2's module-layout parity;
no trigger built in this phase actually invokes the compiled reflect graph
(the weekly-cron stub in codebase/backend/insights/scheduler.py calls
`reflect(...)` directly, not `build_reflect_graph().ainvoke(...)`), so this
wrapper is currently unexercised outside its own tests.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from sqlmodel import Session

from codebase.backend.insights.models import InsightConfidence, InsightStatus, PatientInsight
from codebase.models.agents.prompts.reflect import REFLECT_MERGE_TEMPLATE
from codebase.models.agents.state import ReflectState
from codebase.models.agents.tools.insight_tools import find_similar_insights, get_insights_collection
from codebase.models.llm import get_llm
from codebase.models.rag.ingest import get_embeddings_client

logger = logging.getLogger(__name__)

# design doc §7.3/§10, §13 open question #2: "tuning parameter, not a fixed
# constant to guess at." The mock dataset's two appointments are exactly
# built to calibrate this (data/mock/demo_patient/expected_reflections.json,
# EVAL-REFLECT-001/002): appointment 2's weekday/weekend glucose candidate
# MUST match and update appointment 1's insight (same underlying pattern,
# narrower scope), while the lunch-skipping candidate MUST NOT match
# anything (genuinely new, no precedent in period 1). 0.85 cosine similarity
# is a STARTING POINT validated only against that fixture
# (tests/test_models/test_reflect.py) with controlled embedding vectors —
# not a validated constant for real, model-generated candidate phrasing.
# Revisit with real usage data per §13's own recommendation.
SIMILARITY_THRESHOLD = 0.85


def _parse_confidence(value: str | None) -> InsightConfidence:
    try:
        return InsightConfidence(value)
    except ValueError:
        return InsightConfidence.LOW


def _to_dict(insight: PatientInsight) -> dict[str, Any]:
    return insight.model_dump()


def _enum_value(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _embedding_metadata(insight: PatientInsight) -> dict[str, Any]:
    return {
        "patient_id": str(insight.patient_id),
        "source_kind": _enum_value(insight.source_kind),
        "version": insight.version,
        "category": insight.category,
        "status": _enum_value(insight.status),
    }


def _write_embedding(collection: Any, embeddings_client: Any, insight: PatientInsight) -> None:
    """One embedded vector per ACTIVE insight's text (design doc §7.1),
    keyed by the same id as the Postgres row — an update replaces the prior
    vector in place (Chroma upsert) rather than accumulating stale ones."""
    vector = embeddings_client.embed_documents([insight.text])[0]
    collection.upsert(
        ids=[str(insight.id)],
        embeddings=[vector],
        documents=[insight.text],
        metadatas=[_embedding_metadata(insight)],
    )


async def _merge_text(existing: PatientInsight, candidate: dict[str, Any]) -> str:
    """Second, small LLM call (§7.3 step 3, "At or above threshold") —
    merges the existing insight's text with the new candidate's evidence
    about the same pattern into one updated description. Falls back to the
    candidate's own text on an LLM outage/empty reply rather than raising —
    same defensive posture synthesize_summary.py's LLM call takes — an
    update must never silently lose the new period's finding just because
    the merge call failed."""
    prompt = REFLECT_MERGE_TEMPLATE.format(old_text=existing.text, new_text=candidate["text"])
    try:
        llm = get_llm()
        result = await llm.ainvoke([{"role": "user", "content": prompt}])
        merged = (getattr(result, "content", None) or "").strip()
        return merged or candidate["text"]
    except Exception:
        logger.exception("reflect: merge LLM call failed, falling back to candidate text unmerged")
        return candidate["text"]


async def upsert_insight(
    session: Session,
    *,
    patient_id: UUID,
    source_kind: str,
    candidate: dict[str, Any],
    source_note_id: UUID | None = None,
    now: datetime | None = None,
    collection: Any | None = None,
    embeddings_client: Any | None = None,
) -> dict[str, Any]:
    """§7.3 steps 3-4: similarity-match `candidate` (a proposed insight dict
    — {category, text, evidence, confidence}, see nodes/reflect.py) against
    the patient's ACTIVE insights, scoped to `(patient_id, source_kind)`
    (plan §B/§C's hard partitioning invariant — never shared across
    patients or audiences). Below SIMILARITY_THRESHOLD (or no existing
    insights for this scope) -> creates a new PatientInsight, version=1. At
    or above threshold -> updates the matched row IN PLACE: same `id`,
    `version += 1`, `supersedes_version` set, `text` replaced by the merge
    LLM call's output, Chroma embedding for that id replaced — never both,
    never a duplicate row for an already-known pattern.

    Returns the resulting row as a plain dict (`PatientInsight.model_dump()`),
    same convention as patient_tools.py's tool functions.
    """
    now = now or datetime.utcnow()
    collection = collection or get_insights_collection()
    embeddings_client = embeddings_client or get_embeddings_client()

    matches = find_similar_insights(
        patient_id, source_kind, candidate["text"], collection=collection, embeddings_client=embeddings_client
    )
    best = matches[0] if matches else None

    if best is not None and best["score"] >= SIMILARITY_THRESHOLD:
        existing = session.get(PatientInsight, UUID(best["insight_id"]))
        if existing is not None:
            merged_text = await _merge_text(existing, candidate)
            existing.version += 1
            existing.supersedes_version = existing.version - 1
            existing.text = merged_text
            existing.category = candidate.get("category") or existing.category
            existing.evidence = candidate.get("evidence") or existing.evidence
            existing.confidence = _parse_confidence(candidate.get("confidence"))
            existing.last_confirmed_at = now
            if source_note_id is not None:
                existing.source_note_id = source_note_id
            session.add(existing)
            session.commit()
            session.refresh(existing)
            _write_embedding(collection, embeddings_client, existing)
            return _to_dict(existing)
        # Chroma and Postgres disagreed (the vector's id should always be a
        # real insight.id) — shouldn't happen in normal operation, but fail
        # safe by falling through to CREATE rather than raising, same "an
        # insight is additional context, never a hard dependency" posture
        # design doc §7.4 takes for the read side.
        logger.warning(
            "reflect: find_similar_insights matched chroma id %s with no corresponding PatientInsight row, "
            "creating a new row instead",
            best["insight_id"],
        )

    insight = PatientInsight(
        patient_id=patient_id,
        version=1,
        category=candidate.get("category") or "general",
        text=candidate["text"],
        evidence=candidate.get("evidence") or {},
        confidence=_parse_confidence(candidate.get("confidence")),
        status=InsightStatus.ACTIVE,
        supersedes_version=None,
        source_note_id=source_note_id,
        source_kind=source_kind,
        first_observed_at=now,
        last_confirmed_at=now,
    )
    session.add(insight)
    session.commit()
    session.refresh(insight)
    _write_embedding(collection, embeddings_client, insight)
    return _to_dict(insight)


async def upsert_insight_node(state: ReflectState, config: RunnableConfig) -> dict:
    """build_reflect_graph()'s second node — see this module's docstring
    for why this isn't on any live trigger path yet. Session comes from
    config["configurable"]["session"], same convention every other
    graph-invoked node in this codebase follows (resolve_context.py's
    docstring)."""
    session: Session = config["configurable"]["session"]
    results: list[dict[str, Any]] = []
    for candidate in state.get("candidates") or []:
        result = await upsert_insight(
            session,
            patient_id=state["patient_id"],
            source_kind=state["source_kind"],
            candidate=candidate,
            source_note_id=state.get("source_note_id"),
        )
        results.append(result)
    return {"insights": results}

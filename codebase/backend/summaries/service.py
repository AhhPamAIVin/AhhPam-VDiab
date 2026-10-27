"""AI Summary caching — generation core + read-path lookups.

Fixes the "regenerates on every page reset" problem: POST /api/v1/agent/
summary (agent/router.py) used to invoke the full LangGraph respond-agent
(a real LLM call) on every request. Now it only ever reads the latest
AgentSummarySnapshot row via ensure_snapshot(); generation happens
out-of-band, triggered by health-record writes (health/router.py) and by
a one-off historical backfill (scripts/backfill_agent_summaries.py).

generate_and_store_snapshot() is the trigger-agnostic generation core,
directly mirroring codebase/models/agents/nodes/reflect.py::reflect()'s
documented shape: it calls the graph's node functions directly — fetch_data
-> check_data_quality -> synthesize_summary -> guardrail_check — rather than
agent.ainvoke(...) through the compiled graph. Same reason reflect() bypasses
the graph: this is system-triggered, there's no live current_user/permission
context to resolve (resolve_context.py's job), and no chat semantics apply.
The guardrail step is run up to twice (mirroring graph.py's
_MAX_GUARDRAIL_RETRIES == 1 exactly — synthesize once, and on failure
synthesize+guardrail a second time): guardrail_check itself already contains
the "retry budget spent -> ship the safe canned fallback, clear the error"
logic (see its own docstring), so a second call after a first failure
reproduces the live graph's exact safety behavior without duplicating any of
graph.py's routing logic here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.db import engine
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole
from codebase.models.agents.nodes.check_data_quality import check_data_quality
from codebase.models.agents.nodes.fetch_data import fetch_data
from codebase.models.agents.nodes.guardrail import guardrail_check
from codebase.models.agents.nodes.synthesize_summary import synthesize_summary
from codebase.models.agents.state import AgentState

logger = logging.getLogger(__name__)

# The three windows the frontend's period toggle actually offers (7d/14d/1m
# — see components/glucose-trend-chart.tsx's GlucoseRange) and both audience
# roles — what the write-triggered refresh proactively regenerates. Any other
# period_days value still works (ensure_snapshot lazily generates and caches
# it on first read), it's just not proactively kept warm.
_REFRESH_PERIOD_DAYS: tuple[int, ...] = (7, 14, 30)
_REFRESH_ROLES: tuple[SummaryRole, ...] = (SummaryRole.PATIENT, SummaryRole.DOCTOR)


def _same_day(a: datetime, b: datetime) -> bool:
    return a.date() == b.date()


def get_latest_snapshot(
    session: Session, *, patient_id: UUID, role: str, period_days: int
) -> AgentSummarySnapshot | None:
    """Plain read, no LLM — this is all POST /api/v1/agent/summary's
    read path touches in the common (already-cached-today) case."""
    statement = (
        select(AgentSummarySnapshot)
        .where(
            AgentSummarySnapshot.patient_id == patient_id,
            AgentSummarySnapshot.role == SummaryRole(role),
            AgentSummarySnapshot.period_days == period_days,
        )
        .order_by(col(AgentSummarySnapshot.period_end).desc(), col(AgentSummarySnapshot.generated_at).desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _find_snapshot_for_day(
    session: Session, *, patient_id: UUID, role: str, period_days: int, period_end: datetime
) -> AgentSummarySnapshot | None:
    """Exact-day match for the upsert step — a snapshot whose period_end
    falls on the same calendar day as the one just generated. Multiple
    regenerations on the same day (several health-record writes in one
    sitting) land on this same row instead of piling up duplicates; a new
    day's generation finds nothing here and inserts a fresh row, which is
    what gives the historical "trail since the beginning" its actual rows.
    """
    day_start = datetime.combine(period_end.date(), datetime.min.time())
    day_end = day_start + timedelta(days=1)
    statement = (
        select(AgentSummarySnapshot)
        .where(
            AgentSummarySnapshot.patient_id == patient_id,
            AgentSummarySnapshot.role == SummaryRole(role),
            AgentSummarySnapshot.period_days == period_days,
            AgentSummarySnapshot.period_end >= day_start,
            AgentSummarySnapshot.period_end < day_end,
        )
        .order_by(col(AgentSummarySnapshot.period_end).desc())
        .limit(1)
    )
    return session.exec(statement).first()


async def generate_and_store_snapshot(
    session: Session,
    *,
    patient_id: UUID,
    role: str,
    period_days: int,
    period_end: datetime | None = None,
    period_start: datetime | None = None,
) -> AgentSummarySnapshot:
    """The trigger-agnostic generation core — see module docstring.

    `period_end=None` (the live path: write-triggered refresh, or
    ensure_snapshot's lazy fallback) lets fetch_data.py compute its usual
    trailing-N-days-ending-now window. A caller that passes an explicit
    `period_end` (the historical backfill script, reconstructing a past
    week) instead sets state["date_range_override"] so fetch_data.py fetches
    that exact historical window rather than one ending "now" — see that
    node's own handling of date_range_override.

    `period_start` is accepted separately (not always just `period_end -
    period_days`) because the backfill script's chunks aren't all a full
    `period_days` wide — scripts/backfill_patient_insights.py::weekly_periods
    truncates the final chunk to the seeded window's actual end, so deriving
    `period_start` from `period_end - period_days` for that chunk would
    silently pull in days from the *previous*, already-processed chunk.
    Ignored when `period_end` is None (nothing to override).
    """
    state: AgentState = {
        "patient_id": patient_id,
        "user_role": role,
        "intent": "SUMMARY",
        "date_range_days": period_days,
    }
    if period_end is not None:
        resolved_start = period_start if period_start is not None else period_end - timedelta(days=period_days)
        state["date_range_override"] = (resolved_start, period_end)

    config = {"configurable": {"session": session}}
    state.update(await fetch_data(state, config))
    state.update(await check_data_quality(state))
    # End the read transaction before the LLM calls below: otherwise this
    # connection sits idle-in-transaction for their whole duration and the
    # 30s server timeout (db.py) kills it — verified live, the write at the
    # end then failed with "could not receive data from server".
    session.commit()
    state.update(await synthesize_summary(state))
    state.update(await guardrail_check(state))
    if state.get("error") == "guardrail_failed":
        # One retry, exactly mirroring graph.py's _MAX_GUARDRAIL_RETRIES==1 —
        # see module docstring for why this doesn't need its own fallback.
        state.update(await synthesize_summary(state))
        state.update(await guardrail_check(state))

    fallback_end = period_end or datetime.utcnow()
    actual_start, actual_end = state.get("date_range") or (fallback_end - timedelta(days=period_days), fallback_end)

    field_values = {
        "period_start": actual_start,
        "period_end": actual_end,
        "summary": state.get("response", ""),
        "trends": state.get("trends", []),
        "attention_points": state.get("attention_points", []),
        "suggested_doctor_questions": state.get("suggested_doctor_questions", []),
        "limitations": state.get("limitations", []),
        "data_used": state.get("data_used", []),
        "data_completeness": state.get("data_completeness", {}),
        "sections": state.get("summary_sections", {}),
        "generated_at": datetime.utcnow(),
    }

    existing = _find_snapshot_for_day(
        session, patient_id=patient_id, role=role, period_days=period_days, period_end=actual_end
    )
    if existing is not None:
        snapshot = existing
        for field, value in field_values.items():
            setattr(snapshot, field, value)
    else:
        # All of period_start/period_end/summary are required fields with no
        # default (models.py) — must be passed at construction time, not set
        # afterward, or Pydantic raises a validation error on every first-ever
        # generation for a (patient, role, period_days).
        snapshot = AgentSummarySnapshot(
            patient_id=patient_id, role=SummaryRole(role), period_days=period_days, **field_values
        )
        session.add(snapshot)

    session.commit()
    session.refresh(snapshot)
    return snapshot


async def ensure_snapshot(session: Session, *, patient_id: UUID, role: str, period_days: int) -> AgentSummarySnapshot:
    """Read-path entry point (agent/router.py): serve today's cached
    snapshot if one exists, otherwise generate it once, synchronously, and
    cache it — covers a patient's very first-ever call, and a new day
    starting before any write has proactively refreshed it yet. Every
    subsequent read that same day is a pure DB read, no LLM call."""
    existing = get_latest_snapshot(session, patient_id=patient_id, role=role, period_days=period_days)
    if existing is not None and _same_day(existing.period_end, datetime.utcnow()):
        return existing
    return await generate_and_store_snapshot(session, patient_id=patient_id, role=role, period_days=period_days)


async def refresh_patient_summaries(patient_id: UUID) -> None:
    """Write-path trigger (health/router.py, via FastAPI BackgroundTasks) —
    regenerates all 3 windows x both roles after a new health record. Opens
    its own fresh Session(engine) rather than reusing the request's
    Depends(get_session) session, same "background trigger opens its own
    session" pattern already established by reflect()'s _session_scope
    fallback and notifications/scheduler.py's _run_reminder_tick — the
    request's session shouldn't be held open across 6 sequential LLM calls
    after the response has already been sent.

    Each (role, period_days) window is generated independently and a
    failure in one is caught and logged rather than raised, so e.g. a
    transient LLM error on the 30-day/doctor window doesn't prevent the
    7-day/patient window (the one the dashboard actually shows first) from
    being stored — same "an LLM outage must not take down a summary"
    posture synthesize_summary.py already has for the live path.
    """
    with Session(engine) as session:
        for role in _REFRESH_ROLES:
            for period_days in _REFRESH_PERIOD_DAYS:
                try:
                    await generate_and_store_snapshot(
                        session, patient_id=patient_id, role=role.value, period_days=period_days
                    )
                except Exception:
                    # Without this a failed window leaves the session needing
                    # a rollback and every later window fails too
                    # (PendingRollbackError) — verified live.
                    session.rollback()
                    logger.exception(
                        "summaries: failed to refresh patient_id=%s role=%s period_days=%s",
                        patient_id,
                        role.value,
                        period_days,
                    )

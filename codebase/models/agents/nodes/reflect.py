"""reflect — reads a note (optional) + period records, proposes candidate
insights (design doc §7.3 steps 1-2: gather, propose).

OWNER: long-term memory workstream (Phase 2, plan §B). See
docs/AI_Summary_Respond_System_Design.md §7.3 and
/Users/phamminh/.claude/plans/warm-crafting-muffin.md §B.

`reflect()` is the trigger-agnostic entry point the plan calls for —
"callable identically by a future appointment-trigger and a future
weekly-cron-trigger" (neither is built in this phase). It runs the FULL
algorithm (gather -> propose -> match -> create-or-update), calling into
upsert_insight.py for the last two steps. Confirmed against the actual
call site already written by the (separately in-progress) weekly-cron
workstream, codebase/backend/insights/scheduler.py's
`_run_reflection_for_patient` docstring:

    from codebase.models.agents.nodes.reflect import reflect
    await reflect(
        patient_id=patient_id,
        period_start=period_start,
        period_end=period_end,
        note_text=None,
        source_kind="weekly_batch",
    )

Note that call passes NO session — reflect() has to be safely callable from
a background scheduler tick with no FastAPI request-scoped Session, so
`session` is an optional keyword defaulting to None, falling back to a
fresh `Session(engine)` when absent. This mirrors
codebase/models/agents/checkpoint.py's `_session_scope` (used for
`delete_thread`, its own "no request context" case) and
notifications/scheduler.py's `_run_reminder_tick` (`with Session(engine) as
session:`) — NOT a violation of resolve_context.py's "pull session from
config['configurable']['session'], never open Session(engine) directly in a
node" rule: that rule is about request-scoped graph nodes, so tests can
swap in the in-memory SQLite override. reflect() itself is not invoked via
`agent.ainvoke(state, config=...)` in a request path; it's the same shape
as delete_thread's and notifications/scheduler.py's already-established
"standalone/background caller" exception.

`reflect_node()` is a thin LangGraph-node wrapper around the same gather+
propose logic, for build_reflect_graph() (design doc §3.2's module layout)
— not on any live trigger path in this phase (see upsert_insight.py's
module docstring for the same note).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlmodel import Session, select

from codebase.backend.agent.models import ChatSession
from codebase.backend.db import engine
from codebase.models.agents.checkpoint import checkpointer
from codebase.models.agents.json_output import strip_code_fence
from codebase.models.agents.nodes.upsert_insight import upsert_insight
from codebase.models.agents.prompts.reflect import (
    REFLECT_PROPOSE_APPOINTMENT_TEMPLATE,
    REFLECT_PROPOSE_CHAT_TEMPLATE,
    REFLECT_PROPOSE_WEEKLY_TEMPLATE,
)
from codebase.models.agents.state import ReflectState
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics
from codebase.models.agents.tools.patient_tools import get_health_records
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

_VALID_CONFIDENCE = {"low", "medium", "high"}


@contextmanager
def _session_scope(session: Session | None):
    """See module docstring — falls back to a fresh Session(engine) only
    when the caller (a background trigger, not a request) didn't supply
    one, same pattern checkpoint.py's _session_scope already established."""
    if session is not None:
        yield session
    else:
        with Session(engine) as fresh_session:
            yield fresh_session


def _format_metrics_block(metrics: dict) -> str:
    return json.dumps(metrics, ensure_ascii=False, indent=2, default=str)




def _parse_candidates(raw_text: str) -> list[dict[str, Any]]:
    """Parses the propose LLM call's JSON-array response into candidate
    dicts. Tolerant of a ```json fenced block (a common real-model habit),
    but never raises on malformed output — a candidate is only real
    evidence toward a PatientInsight row if it parses cleanly with both a
    category and non-empty text; a malformed/empty response means "propose
    found nothing usable this run" (empty list), not a crash. This is the
    same "silence over fabrication" posture the prompt itself asks the
    model to take (prompts/reflect.py's _JSON_SHAPE_INSTRUCTIONS)."""
    text = strip_code_fence(raw_text)
    if not text:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("reflect: propose response was not valid JSON, treating as zero candidates")
        return []

    if isinstance(data, dict):
        data = data.get("candidates", [])
    if not isinstance(data, list):
        return []

    candidates: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text_value = str(item.get("text") or "").strip()
        category = str(item.get("category") or "").strip()
        if not text_value or not category:
            continue  # no-fabrication rule: an insight with no text/category isn't usable evidence
        confidence = str(item.get("confidence") or "").strip().lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"
        evidence = item.get("evidence")
        candidates.append(
            {
                "category": category,
                "text": text_value,
                "evidence": evidence if isinstance(evidence, dict) else {},
                "confidence": confidence,
            }
        )
    return candidates


async def _propose(
    period_start: datetime,
    period_end: datetime,
    note_text: str | None,
    metrics: dict,
) -> list[dict[str, Any]]:
    """§7.3 step 2 — one LLM call, two prompt variants (plan §B): clinical/
    technical when `note_text` is present (appointment leg), plain
    patient-appropriate language when it's None (weekly-batch leg)."""
    template = REFLECT_PROPOSE_APPOINTMENT_TEMPLATE if note_text else REFLECT_PROPOSE_WEEKLY_TEMPLATE
    prompt = template.format(
        period_start=period_start.date().isoformat(),
        period_end=period_end.date().isoformat(),
        note_text=note_text or "",
        metrics_block=_format_metrics_block(metrics),
    )
    llm = get_llm()
    result = await llm.ainvoke([{"role": "user", "content": prompt}])
    content = getattr(result, "content", None) or str(result) or ""
    return _parse_candidates(content)


def _format_transcript(messages: list) -> str:
    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"Bệnh nhân: {message.content}")
        elif isinstance(message, AIMessage):
            lines.append(f"Trợ lý AI: {message.content}")
    return "\n".join(lines)


async def _gather_chat_transcript(
    session: Session, patient_id: UUID, period_start: datetime, period_end: datetime
) -> str:
    """Plan Part 3's chat-gather step — self-chat ChatSessions only
    (`patient_id == counterpart_id == patient_id`; a doctor's own working
    thread about this patient is a different privacy context and stays out
    of scope for consent-gated chat mining, see agent/models.py's
    ChatSession docstring for the isolation shape this relies on).

    Session-level granularity, not per-message: any self-chat session whose
    `updated_at` falls in the period contributes its FULL transcript — plain
    LangChain messages don't carry their own timestamps in this app, and a
    chat session is typically one sitting, so this is a reasonable
    approximation rather than a precise per-message time slice.
    """
    sessions = session.exec(
        select(ChatSession)
        .where(
            ChatSession.patient_id == patient_id,
            ChatSession.counterpart_id == patient_id,
            ChatSession.updated_at >= period_start,
            ChatSession.updated_at < period_end,
        )
        .order_by(ChatSession.created_at)  # type: ignore[arg-type]
    ).all()

    blocks: list[str] = []
    for chat_session in sessions:
        # Reuses the caller's own `session` (test-overridden in-memory
        # SQLite, or the real engine) rather than letting checkpointer fall
        # back to a fresh Session(codebase.backend.db.engine) — without this
        # a test running against in-memory SQLite would silently try to read
        # checkpoints from the real module-level engine instead.
        checkpoint_tuple = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": chat_session.thread_id, "session": session}}
        )
        if checkpoint_tuple is None:
            continue
        raw_messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
        transcript = _format_transcript(raw_messages)
        if transcript:
            blocks.append(f"[Phiên trò chuyện ngày {chat_session.created_at.date().isoformat()}]\n{transcript}")
    return "\n\n".join(blocks)


async def _propose_from_chat(period_start: datetime, period_end: datetime, transcript: str) -> list[dict[str, Any]]:
    """Chat variant of _propose() (plan Part 3) — same JSON-array contract
    and same tolerant parsing via _parse_candidates, grounded in a chat
    transcript instead of note_text/metrics_block."""
    prompt = REFLECT_PROPOSE_CHAT_TEMPLATE.format(
        period_start=period_start.date().isoformat(),
        period_end=period_end.date().isoformat(),
        transcript=transcript,
    )
    llm = get_llm()
    result = await llm.ainvoke([{"role": "user", "content": prompt}])
    content = getattr(result, "content", None) or str(result) or ""
    return _parse_candidates(content)


async def _gather_and_propose(
    session: Session,
    patient_id: UUID,
    period_start: datetime,
    period_end: datetime,
    note_text: str | None,
    source_kind: str,
) -> tuple[dict, dict, list[dict[str, Any]]]:
    """§7.3 steps 1-2 — same tools the respond-agent uses
    (get_health_records, calculate_health_metrics), same grounding rule: no
    number in a candidate's evidence that didn't come from a tool call.
    period_start/period_end are used exactly as given (no calendar-day
    normalization here) — the weekly-cron trigger's window is an exact
    trailing-N-days timestamp range (codebase/backend/insights/scheduler.py's
    `now - timedelta(days=7)` to `now`), so flooring/ceiling to whole days
    inside this function would silently widen or shift that window. A
    future appointment-trigger converting a ClinicalNote's `date`-typed
    `data_period_start`/`data_period_end` into whole-day datetimes is that
    trigger's own responsibility (and what tests/test_models/test_reflect.py
    does for the mock fixture's date-only periods).

    `source_kind="chat"` (plan Part 3) branches entirely: no structured
    records/metrics are gathered — nothing downstream reads them for this
    leg, so `records`/`metrics` come back empty — the candidate list instead
    comes from _gather_chat_transcript + _propose_from_chat.
    """
    if source_kind == "chat":
        transcript = await _gather_chat_transcript(session, patient_id, period_start, period_end)
        candidates = await _propose_from_chat(period_start, period_end, transcript)
        return {}, {}, candidates

    records = get_health_records(session, patient_id, period_start, period_end)
    metrics = calculate_health_metrics(records)
    candidates = await _propose(period_start, period_end, note_text, metrics)
    return records, metrics, candidates


async def reflect_node(state: ReflectState, config: RunnableConfig) -> dict:
    """LangGraph node wrapper — build_reflect_graph()'s entry node. See
    module docstring for why this isn't on any live trigger path yet."""
    session: Session = config["configurable"]["session"]
    records, metrics, candidates = await _gather_and_propose(
        session,
        state["patient_id"],
        state["period_start"],
        state["period_end"],
        state.get("note_text"),
        state["source_kind"],
    )
    return {"records": records, "metrics": metrics, "candidates": candidates}


async def reflect(
    patient_id: UUID,
    period_start: datetime,
    period_end: datetime,
    note_text: str | None,
    source_kind: str,
    *,
    session: Session | None = None,
    source_note_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Trigger-agnostic entry point (design doc §7.3, plan §B) — runs the
    full gather -> propose -> match -> create-or-update algorithm for one
    (patient_id, period) window and returns the resulting PatientInsight
    rows as plain dicts (one per candidate propose surfaced; zero candidates
    -> empty list, not an error).

    `source_note_id` isn't part of the plan's stated 5-positional-arg
    signature (which — like the design doc's §8 tool table — omits `session`
    too, the actual implementation shape every tool function in this repo
    takes) but is needed to populate PatientInsight.source_note_id for the
    appointment leg once that trigger exists (plan §B's optional-FK change).
    Kept keyword-only with a default so both the future appointment-trigger
    and the weekly-cron-trigger can call this identically — the weekly leg
    simply never passes it, exactly as codebase/backend/insights/scheduler.py's
    stub already does.
    """
    with _session_scope(session) as active_session:
        _, _, candidates = await _gather_and_propose(
            active_session, patient_id, period_start, period_end, note_text, source_kind
        )

        results: list[dict[str, Any]] = []
        for candidate in candidates:
            insight = await upsert_insight(
                active_session,
                patient_id=patient_id,
                source_kind=source_kind,
                candidate=candidate,
                source_note_id=source_note_id,
                # last_confirmed_at must reflect the period this candidate was
                # actually observed in, not wall-clock time — for the live
                # weekly-cron trigger period_end IS ~now, so this changes
                # nothing there, but for scripts/backfill_patient_insights.py
                # replaying historical weeks it's the difference between a
                # correct per-week timestamp and one that falsely marks every
                # later (still-historical) week as "already caught up" —
                # see that script's _already_backfilled().
                now=period_end,
            )
            if insight is not None:  # None: matched a doctor-rejected insight (Epic 4.4)
                results.append(insight)
        return results

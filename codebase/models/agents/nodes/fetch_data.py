"""Patient data retrieval for the SUMMARY path (always) and the QA path (only
when nodes/plan_response.py's LLM judgment set state["needs_health_data"] —
a pure knowledge-base question or a direct/no-tool answer skips this node
entirely, unlike before this field existed, when it ran unconditionally for
every QA turn).

OWNER: AI Summary workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §4.1, §8 (tools). Must call
get_patient_profile / get_health_records / get_medication_summary /
get_assigned_doctor (tools/patient_tools.py, same workstream) for state["patient_id"] over
state["date_range_days"] (default 7, capped 30 per PRD §15.7), and populate
state["tool_results"] + state["date_range"].
Every number that ends up in a summary must trace back to what this node put
in tool_results — no downstream node is allowed to invent one (§2's
no-fabrication rule is the spine of this whole system, not a suggestion).

Needs a DB session to call the patient_tools functions below — comes from
LangGraph's `config["configurable"]["session"]`, same pattern as
resolve_context.py (see that file's docstring for why: it's what makes the
in-memory SQLite test fixture actually reachable). Pass `session` explicitly
into get_patient_profile/get_health_records rather than having those tools
open their own connection.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.runnables import RunnableConfig

from codebase.models.agents.state import AgentState
from codebase.models.agents.tools.patient_tools import (
    get_assigned_doctor,
    get_health_records,
    get_medication_summary,
    get_patient_profile,
)

DEFAULT_DATE_RANGE_DAYS = 7  # PRD §15.7 default
MAX_DATE_RANGE_DAYS = 30  # PRD §15.7 cap


def _resolve_days(requested: int | None) -> int:
    days = requested if requested else DEFAULT_DATE_RANGE_DAYS
    return max(1, min(int(days), MAX_DATE_RANGE_DAYS))


async def fetch_data(state: AgentState, config: RunnableConfig) -> dict:
    """Fetches this patient's profile/health records/medications/assigned
    doctor into state["tool_results"] — always for SUMMARY, only for QA when
    plan_response.py set needs_health_data. See module docstring for the
    date-range resolution rules and why staleness must be explicitly cleared."""
    intent = state.get("intent")
    if intent not in ("SUMMARY", "QA"):
        return {}
    if intent == "QA" and not state.get("needs_health_data"):
        # Explicitly clear rather than no-op: tool_results/date_range are
        # plain dict fields (no reducer) that persist across turns via the
        # checkpointer (graph.py). Before plan_response.py made this
        # conditional, fetch_data ran unconditionally for every QA turn, so
        # tool_results was always freshly overwritten — this staleness case
        # couldn't occur. Now that a QA turn can skip fetching entirely, it
        # must not silently inherit a *previous* turn's fetched records into
        # this turn's synthesize_qa.py prompt.
        return {"tool_results": {}, "date_range": None}

    patient_id = state.get("patient_id")
    if patient_id is None:
        # resolve_context should have already denied the request before this
        # node ever runs (graph.py routes an unresolved patient_id straight to
        # `respond`) — this is a defensive no-op, not the expected path.
        return {"tool_results": {}, "date_range": None}

    session = config["configurable"]["session"]

    # classify_intent.py (AI Consultation workstream) can set this when a QA
    # chat message names an explicit period ("mùng 1 tới mùng 7") — without
    # it, this always fetched a trailing window ending "now", so any question
    # about a past period silently got the wrong days with no signal that
    # they were wrong, and the model would correctly-but-unhelpfully report
    # no data for the period actually asked about. Same MAX_DATE_RANGE_DAYS
    # cap applies either way.
    override = state.get("date_range_override")
    if override is not None:
        start, end = override
        if (end - start) > timedelta(days=MAX_DATE_RANGE_DAYS):
            start = end - timedelta(days=MAX_DATE_RANGE_DAYS)
    else:
        days = _resolve_days(state.get("date_range_days"))
        end = datetime.utcnow()
        start = end - timedelta(days=days)

    profile = get_patient_profile(session, patient_id)
    records = get_health_records(session, patient_id, start, end)
    medications = get_medication_summary(session, patient_id, start, end)
    assigned_doctor = get_assigned_doctor(session, patient_id)

    return {
        "tool_results": {
            "profile": profile,
            "records": records,
            "medications": medications,
            "assigned_doctor": assigned_doctor,
        },
        "date_range": (start, end),
    }

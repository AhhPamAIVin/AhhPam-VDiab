from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SourceCitation(TypedDict, total=False):
    """One cited source in a final response. See design doc §5.6, §10."""

    title: str
    section: str | None
    page_ref: str | None
    source_url: str | None
    tier: str | None
    organization: str | None  # publisher from sources.csv, e.g. "Bộ Y tế"


class RetrievedChunk(TypedDict, total=False):
    """One RAG hit, as returned by tools/knowledge_tools.search_approved_knowledge.

    See design doc §5.3 for the full metadata shape each chunk carries.
    """

    chunk_id: str
    text: str
    title: str
    section: str | None
    page_ref: str | None
    source_url: str | None
    tier: int
    trust_tier: str
    organization: str | None
    score: float


class AgentState(TypedDict, total=False):
    """Shared state for the respond-agent (AI Summary + AI Consultation).

    See docs/AI_Summary_Respond_System_Design.md §4.1. total=False: every node
    only returns the keys it owns; LangGraph merges partial dict returns into
    this state. Do not add fields here without checking which node(s) would
    own them — this file has no single feature owner, it's shared scaffold.
    """

    # identity / permission — set by the router, refined by resolve_context
    user_id: UUID
    user_role: Literal["admin", "doctor", "patient"]
    patient_id: UUID | None
    session_id: str | None

    # short-term (conversation) memory — persisted across calls sharing the same
    # thread_id via SQLModelCheckpointSaver (codebase/models/agents/checkpoint.py).
    # router.py seeds the new HumanMessage before invoke; respond.py appends the
    # final AIMessage after — chat only, never touched on request_kind="summary".
    # See docs/AI_Summary_Respond_System_Design.md's memory-planning addendum.
    messages: Annotated[list[AnyMessage], add_messages]

    # request
    message: str
    request_kind: Literal["summary", "chat"]
    # PRD §15.7: for request_kind="summary", set by router.py from
    # request.days (7 or 30). For request_kind="chat", set instead by
    # classify_intent.py when a QA message names a *relative* trailing
    # period ("2 tuần qua", "trong 1 tháng") — None (fetch_data.py's default
    # 7-day trailing window applies) when no such period is named.
    date_range_days: int | None
    # set by classify_intent.py when a QA chat message names an explicit
    # period ("mùng 1 tới mùng 7", "từ 1/8 đến 7/8") — fetch_data.py uses this
    # instead of defaulting to a trailing window ending "now", which used to
    # silently fetch the wrong days for any question about a past period.
    # Takes priority over date_range_days above when both are somehow set.
    date_range_override: tuple[datetime, datetime] | None

    # routing — HIGH_RISK/JAILBREAK/SUMMARY/provisional-QA set by
    # classify_intent.py (deterministic safety gate; JAILBREAK is ONLY ever
    # set here, never by plan_response.py — see that module's docstring for
    # why an LLM-judgment jailbreak check was tried and reverted). SOCIAL/
    # APPOINTMENT_SUPPORT/OUT_OF_SCOPE are set by nodes/plan_response.py,
    # which also finalizes QA's tool-need fields below (LLM judgment call,
    # replacing what used to be a keyword net).
    intent: Literal[
        "SUMMARY", "QA", "SOCIAL", "APPOINTMENT_SUPPORT", "OUT_OF_SCOPE", "HIGH_RISK", "JAILBREAK"
    ]
    canned_response: str | None  # set on any short-circuit (permission denial, safety refusal, ...)
    # set by plan_response.py when a QA question needs the patient's own
    # health data (records/medications/profile/assigned doctor) — replaces
    # fetch_data.py's old "always fetch for any QA intent" behavior. False
    # for a pure-knowledge question or a direct/no-tool answer.
    needs_health_data: bool
    # set by plan_response.py when a QA message looks like a pure
    # personal-data lookup ("tôi đang dùng thuốc gì") with no knowledge-
    # seeking cue — retrieve_knowledge.py skips the RAG call entirely rather
    # than running it unconditionally for every QA question and forcing
    # synthesize_qa.py to sort out irrelevant chunks afterward.
    skip_knowledge_retrieval: bool
    # set by plan_response.py: a standalone, rewritten search query for
    # retrieve_knowledge.py to use instead of the raw message — matters for
    # short follow-ups, which are a poor embedding query verbatim. None when
    # skip_knowledge_retrieval is True or the raw message is already a fine
    # standalone query.
    knowledge_query: str | None
    # set by plan_response.py, explicitly True/False on every QA branch: True
    # only when plan_response itself produced the final state["response"]
    # (the no-tool-call path for meta/capability questions), never inferred
    # from needs_health_data/skip_knowledge_retrieval being false — that
    # combination is ambiguous (a malformed planner output can reach the same
    # combination while still deferring to synthesize_qa.py for the actual
    # answer). Used by graph.py's routing (skip straight to guardrail_check)
    # and guardrail.py's ungrounded-claim check (nodes/guardrail.py's
    # `_has_unsupported_claim_with_no_grounding`) — both need to know
    # specifically "did plan_response's own untrusted draft ship", not just
    # "were no tools planned".
    answered_directly: bool

    # working data
    tool_results: dict
    data_completeness: dict
    retrieved_chunks: list[RetrievedChunk]
    date_range: tuple[datetime, datetime] | None
    # set by synthesize_qa.py: response is grounded in the patient's own
    # fetched records (tool_results), not just guideline chunks — lets
    # guardrail.py's uncited_retrieval_drift check tell "legitimately
    # answered from personal data, chunks just weren't relevant" apart from
    # "cited nothing at all, drifted/hallucinated" (same retrieved_chunks/
    # sources shape either way, see guardrail.py docstring §4f).
    personal_data_grounded: bool
    # set by synthesize_qa.py on every QA run: True when the drafting call
    # itself returned a parseable used_chunk_ids list, so `sources` already is
    # "declared by the model AND word-overlap grounded" and verify_citations.py
    # can skip its separate LLM pass (Epic 5.4). Explicit False otherwise —
    # state persists across turns.
    citations_self_reported: bool

    # control
    risk_level: Literal["low", "medium", "high"]
    retry_count: int
    error: str | None

    # output
    response: str
    record_candidates: list[dict]
    # Epic 8: the patient agent (nodes/agent_plan.py) already filled
    # tool_results / retrieved_chunks this turn — fetch_data and
    # retrieve_knowledge then do nothing. Reset to False every turn by both
    # planners (state lives on across turns through the checkpointer).
    agent_gathered: bool
    agent_tool_calls: list[str]
    # Validated record drafts from the agent's draft_records tool (Epic 8);
    # reset every turn by both planners, like agent_gathered.
    agent_record_drafts: list[dict]
    # Patient chat: synthesize_qa's evaluation of the data, shipped apart from
    # `response` and labelled as AI-generated (reset every turn by the planners).
    assessment: str | None
    data_used: list[str]
    sources: list[SourceCitation]
    limitations: list[str]
    handoff: bool  # set by guardrail.py — response needs doctor awareness/confirmation, not an AI decision
    trends: list[str]
    attention_points: list[str]
    suggested_doctor_questions: list[str]
    # doctor SUMMARY only (Epic 4.2): pre-visit brief sections, each a list of
    # deterministic lines — "changes", "meals", "medication_adherence".
    summary_sections: dict[str, list[str]]


class ReflectState(TypedDict, total=False):
    """State for the reflect-agent (long-term memory / insight discovery) —
    a SEPARATE graph from AgentState above (design doc §3.1: "two graphs,
    two triggers"; see build_reflect_graph() in graph.py). The reflect-agent
    is system-triggered (appointment note creation, or the weekly cron —
    neither wired up yet, plan §B/§D), never user-triggered, and never reads
    state["messages"]/short-term memory: plan §C's cross-boundary rule
    ("short-term memory never feeds long-term memory") — reflect.py's inputs
    stay structured health records + optional clinical-note text, never a
    chat thread.

    In practice, nodes/reflect.py's `reflect()` — the trigger-agnostic entry
    point plan §B calls for — is called directly by triggers rather than via
    this graph's `.ainvoke()`; ReflectState/build_reflect_graph() exist for
    design doc §3.2's module-layout parity with the respond-agent. See
    nodes/reflect.py (gather + propose, populates records/metrics/candidates)
    and nodes/upsert_insight.py (match + create-or-update, populates
    insights) for the two nodes that read/write this state.
    """

    # identity / period — set by the caller before invoking the graph.
    patient_id: UUID
    period_start: datetime
    period_end: datetime
    note_text: str | None  # present for source_kind="appointment", None for "weekly_batch"/"chat"
    source_kind: Literal["appointment", "weekly_batch", "chat"]
    source_note_id: UUID | None

    # working data — populated by nodes/reflect.py's reflect_node
    records: dict  # get_health_records(...) output
    metrics: dict  # calculate_health_metrics(...) output
    candidates: list[dict]  # proposed insights: {category, text, evidence, confidence}

    # output — populated by nodes/upsert_insight.py's upsert_insight_node
    insights: list[dict]  # resulting PatientInsight rows (as dicts), one per candidate processed
    error: str | None

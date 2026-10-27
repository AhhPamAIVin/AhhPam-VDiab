"""Respond-agent graph wiring.

OWNER: shared scaffold — owns ALL topology/routing (add_node/add_edge/
add_conditional_edges). Node BODIES live in nodes/*.py, one file per owner
(see each module's docstring). This file exists so parallel workstreams never
need to touch it — if a node needs a new edge, flag it rather than editing
this file directly, since every workstream's graph shape assumption depends
on this staying the single source of truth for routing.

See docs/AI_Summary_Respond_System_Design.md §4.2 for the flow this encodes.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from codebase.backend.config import get_settings
from codebase.models.agents.checkpoint import checkpointer
from codebase.models.agents.nodes.agent_plan import agent_plan
from codebase.models.agents.nodes.check_data_quality import check_data_quality
from codebase.models.agents.nodes.classify_intent import classify_intent
from codebase.models.agents.nodes.fetch_data import fetch_data
from codebase.models.agents.nodes.guardrail import guardrail_check
from codebase.models.agents.nodes.meal.analysis import (
    MealAnalysisState,
    aggregate_node,
    entry_step,
    lookup_nutrition_node,
    parse_meal_items_node,
    persist_analysis_node,
)
from codebase.models.agents.nodes.plan_response import plan_response
from codebase.models.agents.nodes.reflect import reflect_node
from codebase.models.agents.nodes.resolve_context import resolve_context
from codebase.models.agents.nodes.respond import respond
from codebase.models.agents.nodes.retrieve_knowledge import retrieve_knowledge
from codebase.models.agents.nodes.summarize_history import summarize_history
from codebase.models.agents.nodes.synthesize_qa import synthesize_qa
from codebase.models.agents.nodes.synthesize_summary import synthesize_summary
from codebase.models.agents.nodes.upsert_insight import upsert_insight_node
from codebase.models.agents.nodes.verify_citations import verify_citations
from codebase.models.agents.state import AgentState, ReflectState
from codebase.models.agents.timing import timed_node

# classify_intent.py now only ever produces these two "must never reach the
# LLM" intents (Layer 1/2, deterministic) plus SUMMARY/QA — see that node's
# docstring. SOCIAL/APPOINTMENT_SUPPORT/OUT_OF_SCOPE are plan_response.py's
# job now (LLM judgment); _PLAN_SHORT_CIRCUIT_INTENTS below is the routing
# set for those. JAILBREAK is intentionally NOT one of plan_response.py's
# possible outputs — see that module's docstring for why an earlier version
# that added it as a second LLM-judgment safety net was reverted after live
# testing found it misclassified a real medical emergency as JAILBREAK.
_SAFETY_SHORT_CIRCUIT_INTENTS = {"HIGH_RISK", "JAILBREAK", "OUT_OF_SCOPE"}
_PLAN_SHORT_CIRCUIT_INTENTS = {"SOCIAL", "APPOINTMENT_SUPPORT", "OUT_OF_SCOPE"}
_MAX_GUARDRAIL_RETRIES = 1


def _after_resolve_context(state: AgentState) -> str:
    return "respond" if state.get("error") else "classify_intent"


# The three gathering branches read disjoint inputs and write disjoint state
# keys (retrieved_chunks / tool_results+date_range+data_completeness /
# messages), so they run as one parallel fan-out and join at
# synthesize_dispatch (Epic 5.2). Each node already no-ops when its intent or
# plan doesn't need it, so the fan-out is the same for SUMMARY and QA.
_GATHER_BRANCHES = ["retrieve_knowledge", "fetch_data", "summarize_history"]


def _after_classify_intent(state: AgentState) -> str | list[str]:
    if state.get("intent") in _SAFETY_SHORT_CIRCUIT_INTENTS:
        return "respond"
    if state.get("intent") == "SUMMARY":
        return _GATHER_BRANCHES
    return "agent_plan" if _uses_patient_agent(state) else "plan_response"


def _uses_patient_agent(state: AgentState) -> bool:
    """Epic 8: patient chat goes through the tool-calling agent; doctors,
    summaries and the flag turned off keep the planner."""
    return (
        get_settings().patient_agent_enabled
        and state.get("user_role") == "patient"
        and state.get("request_kind") == "chat"
        and state.get("patient_id") is not None
    )


def _after_plan_response(state: AgentState) -> str | list[str]:
    if state.get("intent") in _PLAN_SHORT_CIRCUIT_INTENTS:
        return "respond"
    if state.get("answered_directly"):  # plan_response answered directly — no tool call needed
        return "guardrail_check"
    return _GATHER_BRANCHES


async def synthesize_dispatch(state: AgentState) -> dict:
    """Routing only, not feature logic — picks the summary or QA synthesizer
    by intent. Deliberately tiny so it doesn't become a de facto third
    workstream; if it grows beyond this, that's a sign the split is wrong."""
    if state.get("intent") == "SUMMARY":
        return await synthesize_summary(state)
    return await synthesize_qa(state)


def _after_guardrail(state: AgentState) -> str:
    # guardrail_check already incremented retry_count *before* this routing
    # function runs (it returns the post-increment value) — so on the first
    # failure retry_count arrives here as 1, meaning "one attempt has failed
    # so far, one retry still allowed." The comparison must be <=, not <: with
    # <, retry_count(1) < _MAX_GUARDRAIL_RETRIES(1) is already False on the
    # very first failure, so the retry never fires and the still-unsafe draft
    # goes straight to `respond` unrevised — this was a live bug (verified:
    # the LLM was called exactly once and a diagnosis-phrasing response
    # shipped to the user with error="guardrail_failed" left dangling,
    # unconverted to any HTTP-level signal). <= restores the intended "one
    # retry, then fall back" behavior guardrail.py's own docstring describes.
    if state.get("error") == "guardrail_failed" and state.get("retry_count", 0) <= _MAX_GUARDRAIL_RETRIES:
        return "synthesize_dispatch"
    return "respond"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Every node is wrapped for per-node timing logs (Epic 5.1, timing.py).
    for name, fn in (
        ("resolve_context", resolve_context),
        ("classify_intent", classify_intent),
        ("plan_response", plan_response),
        ("agent_plan", agent_plan),
        ("retrieve_knowledge", retrieve_knowledge),
        ("fetch_data", fetch_data),
        ("check_data_quality", check_data_quality),
        ("summarize_history", summarize_history),
        ("synthesize_dispatch", synthesize_dispatch),
        ("verify_citations", verify_citations),
        ("guardrail_check", guardrail_check),
        ("respond", respond),
    ):
        graph.add_node(name, timed_node(name, fn))

    graph.set_entry_point("resolve_context")
    graph.add_conditional_edges("resolve_context", _after_resolve_context)
    graph.add_conditional_edges("classify_intent", _after_classify_intent)
    graph.add_conditional_edges("plan_response", _after_plan_response)
    # Same exits as the planner: reviewed reply -> respond, direct answer ->
    # guardrail, otherwise the gather join (a no-op for what the agent fetched).
    graph.add_conditional_edges("agent_plan", _after_plan_response)
    graph.add_edge("fetch_data", "check_data_quality")
    # Join: synthesize_dispatch waits for all three branches to finish.
    graph.add_edge(["retrieve_knowledge", "check_data_quality", "summarize_history"], "synthesize_dispatch")
    # verify_citations (AI Consultation workstream, QA-only — no-ops for
    # SUMMARY) re-checks synthesize_qa's citation list against the drafted
    # response with a dedicated LLM pass before guardrail_check's
    # uncited-retrieval-drift scan runs, so that scan sees the corrected
    # sources rather than the word-overlap heuristic's own.
    graph.add_edge("synthesize_dispatch", "verify_citations")
    graph.add_edge("verify_citations", "guardrail_check")
    graph.add_conditional_edges("guardrail_check", _after_guardrail)
    graph.add_edge("respond", END)

    return graph.compile(checkpointer=checkpointer)


agent = build_graph()


def build_reflect_graph() -> StateGraph:
    """Reflect-agent graph — long-term memory / insight discovery (design
    doc §3.1/§3.2, plan §B). A SEPARATE graph from build_graph()'s
    respond-agent above: system-triggered (appointment note creation or the
    weekly cron — neither wired up in this phase, plan §B/§D), one-shot per
    (patient_id, period) invocation rather than a persisted conversation
    thread, so — unlike `agent` above — this is compiled WITHOUT a
    checkpointer.

    Not on any live trigger path yet: nodes/reflect.py's `reflect()` (the
    trigger-agnostic entry point the plan actually specifies, and what
    codebase/backend/insights/scheduler.py's weekly-cron stub calls
    directly) runs the same gather -> propose -> match -> create-or-update
    algorithm without going through this compiled graph's `.ainvoke()`.
    This function exists for design doc §3.2's module-layout parity with
    build_graph() ("graph.py — build_graph() -> respond agent;
    build_reflect_graph() -> reflect agent") and so ReflectState's node
    wiring is real, tested, and available if a future caller wants the
    graph shape specifically (e.g. to add branching/retry steps later)
    rather than the plain function.
    """
    graph = StateGraph(ReflectState)

    graph.add_node("reflect", reflect_node)
    graph.add_node("upsert_insight", upsert_insight_node)

    graph.set_entry_point("reflect")
    graph.add_edge("reflect", "upsert_insight")
    graph.add_edge("upsert_insight", END)

    return graph.compile()


def build_meal_analysis_graph() -> StateGraph:
    """Meal analysis graph (Epic 1.3): parse_meal_items -> lookup_nutrition
    -> aggregate -> persist_analysis, one MealRecord per run. Like
    build_reflect_graph(), no checkpointer: each run is one-shot. A patient
    edit enters at lookup_nutrition with its own items (entry_step), so a
    correction never goes back through the model. The DB session comes from
    config["configurable"]["session"], as in the respond graph."""
    graph = StateGraph(MealAnalysisState)

    graph.add_node("parse_meal_items", timed_node("parse_meal_items", parse_meal_items_node))
    graph.add_node("lookup_nutrition", timed_node("lookup_nutrition", lookup_nutrition_node))
    graph.add_node("aggregate", timed_node("aggregate", aggregate_node))
    graph.add_node("persist_analysis", timed_node("persist_analysis", persist_analysis_node))

    graph.set_conditional_entry_point(
        entry_step, {"parse_meal_items": "parse_meal_items", "lookup_nutrition": "lookup_nutrition"}
    )
    graph.add_edge("parse_meal_items", "lookup_nutrition")
    graph.add_edge("lookup_nutrition", "aggregate")
    graph.add_edge("aggregate", "persist_analysis")
    graph.add_edge("persist_analysis", END)

    return graph.compile()

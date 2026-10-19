"""Final assembly node — shared by every path (permission denial, safety
refusal, summary, QA). Shared scaffold, not owned by any one workstream — see
docs/AI_Summary_Respond_System_Design.md §4.2, §10.

Deliberately thin: everything meaningful was already decided by an upstream
node. This just turns whatever ended up in state into the flat fields
codebase/backend/agent/router.py reads to build the HTTP response.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from codebase.models.agents.state import AgentState


def _messages_update(state: AgentState, final_response: str) -> dict:
    """Appends the assistant's reply to short-term memory (state["messages"])
    so the next turn on this thread sees it — chat only (request_kind="chat"),
    never on a summary invoke: summary calls use their own, separate thread_id
    (see router.py) and don't seed a HumanMessage in the first place, so
    there's nothing conversational to append to."""
    if state.get("request_kind") != "chat" or not final_response:
        return {}
    return {"messages": [AIMessage(content=final_response)]}


async def respond(state: AgentState) -> dict:
    """Final assembly node, shared by every path — flattens whatever
    upstream nodes decided (canned refusal, QA answer, or summary) into the
    fields codebase/backend/agent/router.py reads to build the HTTP
    response. See module docstring for why every field is reset explicitly
    on the canned-response branch."""
    if state.get("canned_response"):
        response = state["canned_response"]
        # Reset every other response field explicitly, same reasoning as
        # classify_intent.py's canned_response/handoff reset: state persists
        # across turns on this thread via the checkpointer, so leaving a key
        # out here doesn't mean "empty", it means "whatever a PREVIOUS real
        # QA/SUMMARY turn last put there" silently rides along on this
        # canned refusal forever. Real, live-verified bug: a canned
        # emergency reply (JAILBREAK/HIGH_RISK/OUT_OF_SCOPE) kept showing
        # the prior turn's "Nguồn tham khảo" citation list, which has
        # nothing to do with a fixed refusal string and, on the emergency
        # path specifically, misleadingly dresses up an urgent "call 115"
        # message as if it were grounded in retrieved guideline chunks.
        return {
            "response": response,
            "record_candidates": [],
            "data_used": [],
            "sources": [],
            "limitations": [],
            "trends": [],
            "attention_points": [],
            "suggested_doctor_questions": [],
            "date_range": None,
            "data_completeness": {},
            "handoff": state.get("handoff", False),
            **_messages_update(state, response),
        }

    response = state.get("response", "")
    return {
        "response": response,
        "record_candidates": state.get("record_candidates", []),
        "data_used": state.get("data_used", []),
        "sources": state.get("sources", []),
        "limitations": state.get("limitations", []),
        "trends": state.get("trends", []),
        "attention_points": state.get("attention_points", []),
        "suggested_doctor_questions": state.get("suggested_doctor_questions", []),
        "date_range": state.get("date_range"),
        "data_completeness": state.get("data_completeness", {}),
        "handoff": state.get("handoff", False),
        **_messages_update(state, response),
    }

"""Patient agent (Epic 8): tool-calling replacement for plan_response.py.

Runs for patient chat when settings.patient_agent_enabled, in plan_response's
place in the graph (after classify_intent's deterministic safety gates). The
model calls tools from tools/agent_tools.py — several per round, up to
settings.patient_agent_max_rounds rounds — and sees a short observation for
each. What the tools gather lands in the usual state keys (tool_results,
retrieved_chunks, date_range) with agent_gathered=True, so the parallel
gather nodes become no-ops and synthesize_qa -> verify_citations ->
guardrail_check write and check the answer exactly as before. The agent's own
text never reaches the patient; ending the turn early goes through a terminal
tool that returns plan_response's reviewed replies.

Rounds: by default the turn stops after the first round whose calls all
succeeded — the prompt asks for every needed tool at once, so a turn costs one
LLM call, like the planner it replaces; another round only retries a failed or
invalid call. settings.patient_agent_reflect=true lets the model look at every
round's observations and ask for more (multi-step, one extra call per turn).

Fail-safe, same posture as plan_response: the planner's whole-message fast
path still applies first; an LLM failure before anything was gathered, or a
model that calls no tool at all, falls back to the "gather everything"
default rather than answering a health question with no data. The same goes
for a terminal tool (social reply, out-of-scope, direct answer) chosen for a
message with health content — see _HEALTH_GUARDED_TERMINALS.
"""

from __future__ import annotations

import inspect
import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from codebase.backend.agent.record_actions import requires_safety_response, urgent_value_response
from codebase.backend.config import get_settings
from codebase.models.agents.nodes.plan_response import (
    appointment_plan,
    direct_answer_plan,
    fallback_plan,
    fast_path_plan,
    history_block,
    out_of_scope_plan,
    social_plan,
)
from codebase.models.agents.prompts.agent import AGENT_SYSTEM_PROMPT, AGENT_USER_TEMPLATE
from codebase.models.agents.state import AgentState
from codebase.models.agents.tools.agent_tools import PROGRESS_BY_TOOL, TOOLS, TOOLS_BY_NAME, AgentTool
from codebase.models.agents.tools.nutrition_tools import strip_diacritics
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

NODE_NAME = "agent_plan"


def _openai_tool(tool: AgentTool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_model.model_json_schema(),
        },
    }


def _not_gathered(result: dict) -> dict:
    return {**result, "agent_gathered": False, "agent_tool_calls": [], "agent_record_drafts": [], "assessment": None}


# Terminal tools that end the turn without looking anything up. The model
# reached for them on real health questions (golden eval 2026-10-29: "đang sốt
# có được tự ý ngừng thuốc không?" -> reply_social "Được rồi nhé"; exercise and
# folk-remedy questions -> answer_directly / out_of_scope refusals), so they
# are only accepted for a message with no health content. Rejecting one falls
# back to gather-everything: the guard only ever turns a refusal into an
# answer, never the other way round.
_HEALTH_GUARDED_TERMINALS = {"reply_social", "out_of_scope", "answer_directly"}
_HEALTH_WORD_RE = re.compile(
    r"\b(?:đường huyết|đường máu|tiểu đường|đái tháo đường|hạ đường|insulin|hba1c|huyết áp|thuốc|uống|ăn|"
    r"nhịn|bữa|tinh bột|đường|carb|tập|thể dục|vận động|bệnh|sốt|ốm|cảm cúm|đau|mệt|chóng mặt|run|vã mồ hôi|"
    r"cân nặng|béo|gầy|biến chứng|tim|thận|mắt|chân|vết thương|khám|xét nghiệm|chữa|khỏi|điều trị|"
    r"tụy|thanh lọc|giải độc|thực phẩm|lá|dân gian|đông y|thuốc nam|chế độ)\b"
)
# Typed without diacritics; multi-syllable only, so "an"/"duong" can't hit "bạn"/"đường đi".
_HEALTH_WORD_PLAIN_RE = re.compile(
    r"\b(?:duong huyet|duong mau|tieu duong|dai thao duong|ha duong|insulin|hba1c|huyet ap|the duc|"
    r"van dong|uong thuoc|chong mat|bien chung|xet nghiem|dieu tri|tinh bot|thuc pham)\b"
)


def _mentions_health(message: str) -> bool:
    lowered = message.lower()
    return bool(_HEALTH_WORD_RE.search(lowered) or _HEALTH_WORD_PLAIN_RE.search(strip_diacritics(lowered)))


def _terminal_result(tool: AgentTool, args: dict, message: str) -> dict | None:
    """plan_response's reviewed reply for a terminal tool, or None if its
    args don't validate or it may not end this message (the caller then
    falls back)."""
    if tool.name in _HEALTH_GUARDED_TERMINALS and _mentions_health(message):
        logger.info("agent_plan rejected terminal tool %s on a health message", tool.name)
        return None
    try:
        parsed = tool.args_model.model_validate(args)
    except ValidationError:
        return None
    if tool.name == "reply_social":
        return social_plan(parsed.kind)
    if tool.name == "request_new_appointment":
        return appointment_plan()
    if tool.name == "out_of_scope":
        return out_of_scope_plan()
    if tool.name == "answer_directly":
        return direct_answer_plan(parsed.answer)
    return None


def _stream_writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # called outside a graph run (unit tests)
        return None


async def _run_tool(call: dict, gathered: dict, session, writer) -> tuple[str, bool]:
    """Run one gathering call into `gathered`; returns (observation, ok)."""
    tool = TOOLS_BY_NAME.get(call.get("name") or "")
    if tool is None or tool.terminal:
        return "Không có tool này (hoặc không thể gọi cùng tool khác).", False
    try:
        args = tool.args_model.model_validate(call.get("args") or {})
    except ValidationError as exc:
        return f"Tham số không hợp lệ: {exc.errors()[0].get('msg', 'sai định dạng')}", False
    if writer is not None and tool.name in PROGRESS_BY_TOOL:
        writer({"progress": PROGRESS_BY_TOOL[tool.name]})
    try:
        outcome = tool.handler(gathered, session, args)
        if inspect.isawaitable(outcome):
            outcome = await outcome
    except Exception as exc:
        session.rollback()
        logger.warning("agent tool %s failed: %s", tool.name, type(exc).__name__)
        return "Không lấy được dữ liệu từ tool này.", False
    gathered.update(outcome.state_update)
    gathered["called"].append(tool.name)
    return outcome.observation, True


async def agent_plan(state: AgentState, config: RunnableConfig) -> dict:
    message = state.get("message", "") or ""
    fast = fast_path_plan(message)
    if fast is not None:
        return _not_gathered(fast)

    settings = get_settings()
    local_now = datetime.now(UTC).astimezone(ZoneInfo(settings.reminder_timezone))
    messages: list = [
        SystemMessage(
            content=AGENT_SYSTEM_PROMPT.format(today=local_now.strftime("%d/%m/%Y"), now=local_now.strftime("%H:%M"))
        ),
        HumanMessage(
            content=AGENT_USER_TEMPLATE.format(
                history_block=history_block((state.get("messages") or [])[:-1]), message=message
            )
        ),
    ]
    gathered: dict = {
        "patient_id": state["patient_id"],
        "message": message,
        "date_range_override": state.get("date_range_override"),
        "tool_results": {},
        "retrieved_chunks": [],
        "date_range": None,
        "knowledge_query": None,
        "record_drafts": [],
        "called": [],
    }
    session = config["configurable"]["session"]
    writer = _stream_writer()

    try:
        llm = get_llm(NODE_NAME).bind_tools([_openai_tool(tool) for tool in TOOLS])
    except Exception:
        logger.warning("agent_plan could not bind tools, falling back to the planner default", exc_info=True)
        return _not_gathered(fallback_plan(message))

    for _ in range(settings.patient_agent_max_rounds):
        try:
            reply = await llm.ainvoke(messages)
        except Exception as exc:
            logger.warning("agent_plan LLM call failed: %s", type(exc).__name__)
            break
        calls = list(getattr(reply, "tool_calls", None) or [])
        if not calls:
            break
        terminal = next((c for c in calls if (t := TOOLS_BY_NAME.get(c.get("name") or "")) and t.terminal), None)
        if terminal is not None:
            result = _terminal_result(TOOLS_BY_NAME[terminal["name"]], terminal.get("args") or {}, message)
            if result is None:
                break
            return {
                **result,
                "agent_gathered": False,
                "agent_tool_calls": [*gathered["called"], terminal["name"]],
                "agent_record_drafts": [],
                "assessment": None,
            }
        messages.append(reply)
        all_ok = True
        for call in calls:
            observation, ok = await _run_tool(call, gathered, session, writer)
            all_ok = all_ok and ok
            messages.append(ToolMessage(content=observation, tool_call_id=call.get("id") or call.get("name") or ""))
        if all_ok and not settings.patient_agent_reflect:
            break  # one LLM call per turn unless something needs a retry

    if not gathered["called"]:
        # Nothing gathered (model failure, or it answered without any tool):
        # never let a health question reach synthesis with no data.
        return _not_gathered(fallback_plan(message))

    urgent = next((d for d in gathered["record_drafts"] if requires_safety_response(d)), None)
    if urgent is not None:
        # A reported value outside the safe range: the fixed referral is the
        # whole answer (no model-written text about it), and it is what the
        # conversation history keeps. The card to save the value still comes.
        return {
            **fallback_plan(message),
            "canned_response": urgent_value_response(urgent),
            "handoff": True,
            "agent_gathered": True,
            "agent_tool_calls": gathered["called"],
            "agent_record_drafts": gathered["record_drafts"],
            "assessment": None,
        }

    searched = "search_knowledge" in gathered["called"]
    tool_results = gathered["tool_results"]
    return {
        "intent": "QA",
        "needs_health_data": "records" in tool_results,
        # Retrieval already happened (or wasn't wanted); synthesize_qa reads
        # this to say "no document found" only when a search really ran.
        "skip_knowledge_retrieval": not searched,
        "knowledge_query": gathered["knowledge_query"],
        "canned_response": None,
        "handoff": False,
        "answered_directly": False,
        "response": None,
        "tool_results": tool_results,
        "retrieved_chunks": gathered["retrieved_chunks"],
        "date_range": gathered["date_range"],
        "agent_gathered": True,
        "agent_tool_calls": gathered["called"],
        # Validated drafts (draft_records); agent/router.py turns them into
        # confirmation cards after the guardrail, instead of the regex parser.
        "agent_record_drafts": gathered["record_drafts"],
        "assessment": None,
    }

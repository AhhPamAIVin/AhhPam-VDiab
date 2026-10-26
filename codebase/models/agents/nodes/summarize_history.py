"""Conversation-history compaction — keeps chat threads from growing
unbounded in token cost. Shared scaffold (see graph.py's OWNER-header
convention — this is cross-cutting short-term-memory infra, same category as
resolve_context.py/respond.py, not owned by the AI Consultation workstream).

Runs on every chat turn (check_data_quality -> summarize_history ->
synthesize_dispatch, graph.py) but is a no-op below the turn threshold, so it
costs nothing extra on ordinary short conversations. Once a thread crosses
_MAX_RAW_TURNS, the oldest messages are collapsed into a single summary
message via one LLM call and removed from state via LangGraph's
RemoveMessage sentinel (the standard add_messages-reducer deletion pattern).
The raw turns are gone from state["messages"] and won't be replayed into
future prompts, but the checkpoint rows that produced them are untouched in
Postgres (SQLModelCheckpointSaver never deletes old AgentCheckpoint rows) —
nothing is destroyed, just no longer carried forward.

At most one summary message is ever live: a second summarization pass
re-collapses "prior summary + newer raw turns" (the prior summary message is
itself inside the next to_summarize slice) into one fresh SystemMessage,
removing the old one — progressive, not additive.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage

from codebase.models.agents.state import AgentState
from codebase.models.llm import get_llm

# ~12 messages ~= 6 user/assistant exchanges — small enough to bound prompt
# cost, large enough that recent context never gets summarized away mid-topic.
_MAX_RAW_TURNS = 12
_KEEP_RECENT_TURNS = 6
SUMMARY_MARKER = "[Tóm tắt các lượt trò chuyện trước đó]"

_SUMMARIZE_PROMPT = (
    "Tóm tắt ngắn gọn (dưới 150 từ) nội dung chính của đoạn hội thoại sau giữa "
    "người dùng và trợ lý AI y tế, giữ lại các thông tin/chủ đề quan trọng người "
    "dùng đã đề cập (triệu chứng, câu hỏi đã hỏi, thông tin cá nhân đã chia sẻ). "
    "Không thêm thông tin không có trong hội thoại, không suy diễn.\n\n{transcript}"
)


def _role_label(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "Người dùng"
    if isinstance(message, AIMessage):
        return "Trợ lý"
    if isinstance(message, SystemMessage):
        return "Tóm tắt trước đó"
    return "Khác"


def _format_transcript(messages: list[AnyMessage]) -> str:
    return "\n".join(f"{_role_label(m)}: {m.content}" for m in messages if getattr(m, "content", None))


async def summarize_history(state: AgentState) -> dict:
    """No-op below _MAX_RAW_TURNS; otherwise collapses the oldest messages
    into one LLM-generated summary SystemMessage and removes them from
    state via LangGraph's RemoveMessage, keeping chat threads bounded in
    token cost. See module docstring for the progressive-summary contract."""
    messages = state.get("messages") or []
    if len(messages) <= _MAX_RAW_TURNS:
        return {}

    to_summarize = messages[:-_KEEP_RECENT_TURNS]
    if not to_summarize:
        return {}

    llm = get_llm("summarize_history")
    result = await llm.ainvoke(
        [{"role": "user", "content": _SUMMARIZE_PROMPT.format(transcript=_format_transcript(to_summarize))}]
    )
    summary_text = (getattr(result, "content", None) or str(result) or "").strip()

    removals = [RemoveMessage(id=m.id) for m in to_summarize if m.id]
    summary_message = SystemMessage(content=f"{SUMMARY_MARKER}: {summary_text}")
    return {"messages": [*removals, summary_message]}

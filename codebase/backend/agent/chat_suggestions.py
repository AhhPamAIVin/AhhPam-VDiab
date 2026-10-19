"""Follow-up question suggestions overlay — NOT part of the AI Consultation
agent (synthesize_qa.py / graph.py / qa.py prompt).

A separate, additive endpoint that proposes 3-4 short follow-up questions
given the last Q&A turn, so the chat UI's suggestion chips can react to the
conversation instead of always showing the same static list. Lives beside
`agent/router.py` rather than inside it — never touches the chat graph,
state, or the guarded QA prompt. A generation/parsing failure here must
never break the chat itself, so it degrades to an empty list rather than
raising.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from codebase.backend.agent.router import _can_use_agent
from codebase.backend.auth.models import User
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_SUGGESTIONS = 4

_SYSTEM_PROMPT = (
    "Bạn đề xuất câu hỏi tiếp theo cho một bệnh nhân đái tháo đường đang trò chuyện với "
    "trợ lý AI sức khỏe. Dựa trên câu hỏi và câu trả lời gần nhất, đề xuất 3-4 câu hỏi tiếp "
    "theo NGẮN GỌN (dưới 12 từ mỗi câu), tự nhiên, liên quan trực tiếp đến chủ đề vừa trao "
    "đổi hoặc mối quan tâm sức khỏe liên quan hợp lý. CHỈ trả về một mảng JSON các chuỗi "
    'tiếng Việt, không kèm giải thích, không markdown. Ví dụ: ["Câu hỏi 1", "Câu hỏi 2"]'
)


class ChatSuggestionsRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    response: str = Field(..., min_length=1, max_length=8000)


class ChatSuggestionsResponse(BaseModel):
    suggestions: list[str] = []


def _parse_suggestions(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    return [str(item).strip() for item in parsed if str(item).strip()][:_MAX_SUGGESTIONS]


@router.post("/chat/suggestions", response_model=ChatSuggestionsResponse)
async def chat_suggestions(
    request: ChatSuggestionsRequest,
    current_user: User = Depends(_can_use_agent),
) -> ChatSuggestionsResponse:
    prompt = (
        f"Câu hỏi của bệnh nhân: {request.message}\n\n"
        f"Câu trả lời của trợ lý AI: {request.response}\n\n"
        "Đề xuất 3-4 câu hỏi tiếp theo (mảng JSON chuỗi):"
    )
    try:
        llm = get_llm()
        result = await llm.ainvoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        suggestions = _parse_suggestions(getattr(result, "content", None) or "")
    except Exception:
        logger.exception("chat_suggestions: failed to generate/parse follow-up questions")
        suggestions = []
    return ChatSuggestionsResponse(suggestions=suggestions)

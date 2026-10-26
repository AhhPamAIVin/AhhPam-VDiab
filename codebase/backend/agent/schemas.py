"""Request/response contracts for the respond-agent endpoints.

Shared scaffold — see docs/AI_Summary_Respond_System_Design.md §10. Both
Summary and Consultation workstreams build against this; if a field needs to
change, flag it rather than editing directly, since router.py and both
synthesize_*.py node files all depend on this shape staying stable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from codebase.backend.time_utils import UtcDatetime

_DISCLAIMER = (
    "Thông tin này do AI tổng hợp từ dữ liệu sức khỏe, không thay thế chẩn đoán "
    "hoặc tư vấn y khoa. Vui lòng trao đổi với bác sĩ điều trị."
)


class SourceCitation(BaseModel):
    title: str
    section: str | None = None
    page_ref: str | None = None
    source_url: str | None = None
    tier: str | None = None
    organization: str | None = None


class AgentChoiceOption(BaseModel):
    label: str
    value: str


class AgentInteraction(BaseModel):
    type: Literal["single_choice", "confirm_record", "record_saved"]
    pending_action_id: UUID
    field: str | None = None
    options: list[AgentChoiceOption] = []
    draft: dict[str, Any] = {}
    saved_record_id: UUID | None = None


class AgentSummaryRequest(BaseModel):
    patient_id: UUID | None = None
    days: int = Field(default=7, ge=1, le=30)


class AgentSummaryResponse(BaseModel):
    summary: str
    date_range: tuple[datetime, datetime] | None = None
    data_used: list[str] = []
    trends: list[str] = []
    attention_points: list[str] = []
    data_completeness: dict = {}
    limitations: list[str] = []
    suggested_doctor_questions: list[str] = []
    relevant_insights: list[str] = []
    disclaimer: str = _DISCLAIMER


class AgentChatRequest(BaseModel):
    patient_id: UUID | None = None
    message: str = Field(..., min_length=1, max_length=5000)
    # None = start a new ChatSession (router.py creates it and returns its id
    # below); a given id continues that exact session. See
    # /Users/phamminh/.claude/plans/memoized-churning-patterson.md Part 1.
    session_id: UUID | None = None


class AgentChatResponse(BaseModel):
    response: str
    data_used: list[str] = []
    sources: list[SourceCitation] = []
    limitations: list[str] = []
    handoff: bool = False
    disclaimer: str = _DISCLAIMER
    session_id: UUID
    interaction: AgentInteraction | None = None
    interactions: list[AgentInteraction] = []


class AgentRecordActionRequest(BaseModel):
    action: Literal["provide_field", "confirm", "cancel"]
    field: str | None = None
    value: Any = None


class AgentRecordActionResponse(BaseModel):
    response: str
    interaction: AgentInteraction | None = None


class ChatSessionPublic(BaseModel):
    id: UUID
    patient_id: UUID
    title: str | None = None
    # UtcDatetime, not plain datetime — these are naive-but-UTC values
    # (ChatSession.created_at/updated_at use datetime.utcnow()); without the
    # explicit `Z` this serializer appends, the browser's `new Date(...)`
    # parses the string as local time and every relative-time label in the
    # sidebar is off by the viewer's UTC offset. See time_utils.py's
    # docstring for the exact symptom (verified live: a session created at
    # 11:24 UTC showed "7 giờ trước" immediately, in a UTC+7 browser).
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ChatMessagePublic(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatSessionMessagesResponse(BaseModel):
    session_id: UUID
    messages: list[ChatMessagePublic] = []

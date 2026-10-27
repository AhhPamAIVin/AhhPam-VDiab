"""Tool registry for the patient agent (Epic 8).

Each AgentTool is a typed capability the agent node (nodes/agent_plan.py) can
call. A handler gets the current gather state, the DB session and the
validated args, and returns (state_update, observation):

- state_update merges into the same state keys the rest of the graph already
  reads — tool_results / retrieved_chunks / date_range — so synthesize_qa,
  verify_citations and guardrail_check work unchanged on what the agent found;
- observation is a short text the model sees as the tool result, enough to
  decide whether it needs another tool. Full data never goes back to the
  agent model, only to synthesize_qa.

Rules every tool follows: patient_id comes from the authenticated state, never
from args; ranges are capped (≤ 30 days of records, a fixed number of
knowledge chunks); handlers are deterministic. Terminal tools end the turn
with an already-reviewed reply instead of gathering.

Adding a capability = adding one AgentTool to TOOLS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from codebase.backend.appointments.models import AppointmentStatus
from codebase.backend.appointments.service import list_appointments_for_patient
from codebase.backend.reports.service import list_reports
from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge
from codebase.models.agents.tools.meal_tools import (
    MEAL_RESPONSE_DAYS,
    get_meal_glucose_response,
    get_medication_adherence,
)
from codebase.models.agents.tools.patient_tools import (
    MAX_DATE_RANGE_DAYS,
    get_assigned_doctor,
    get_health_records,
    get_medication_summary,
    get_patient_profile,
)

KNOWLEDGE_K = 10  # same as retrieve_knowledge.py (_RETRIEVAL_K)
_APPOINTMENT_LIMIT = 5
_MEDICATION_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ToolOutcome:
    state_update: dict
    observation: str


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable
    # Ends the turn with a reviewed reply; no synthesis step after it.
    terminal: bool = False


def _merge_tool_results(state: dict, **updates) -> dict:
    return {"tool_results": {**(state.get("tool_results") or {}), **updates}}


# --- data tools -------------------------------------------------------------------


class HealthRecordsArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_DATE_RANGE_DAYS, description="Số ngày gần nhất cần xem (1-30).")
    start_date: date | None = Field(default=None, description="Ngày bắt đầu (YYYY-MM-DD) nếu hỏi một khoảng cụ thể.")
    end_date: date | None = Field(default=None, description="Ngày kết thúc (YYYY-MM-DD), mặc định hôm nay.")


def _resolve_range(state: dict, args: HealthRecordsArgs) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    if args.start_date is not None:
        start = datetime.combine(args.start_date, time.min)
        end = datetime.combine(args.end_date, time.max) if args.end_date else now
    elif state.get("date_range_override") is not None:
        # classify_intent already parsed an explicit period from the message.
        start, end = state["date_range_override"]
    else:
        end = now
        start = end - timedelta(days=args.days)
    if start > end:
        start, end = end, start
    if end - start > timedelta(days=MAX_DATE_RANGE_DAYS):
        start = end - timedelta(days=MAX_DATE_RANGE_DAYS)
    return start, end


def _get_health_records(state: dict, session: Session, args: HealthRecordsArgs) -> ToolOutcome:
    start, end = _resolve_range(state, args)
    patient_id = state["patient_id"]
    records = get_health_records(session, patient_id, start, end)
    update = _merge_tool_results(state, profile=get_patient_profile(session, patient_id), records=records)
    update["date_range"] = (start, end)
    counts = ", ".join(
        f"{label} {len(records.get(key) or [])}"
        for key, label in (("glucose", "đường huyết"), ("blood_pressure", "huyết áp"), ("meals", "bữa ăn"), ("activities", "vận động"))
    )
    return ToolOutcome(update, f"Đã lấy bản ghi {start:%d/%m}–{end:%d/%m}: {counts}.")


class NoArgs(BaseModel):
    pass


def _get_medications(state: dict, session: Session, _args: NoArgs) -> ToolOutcome:
    end = datetime.utcnow()
    medications = get_medication_summary(session, state["patient_id"], end - timedelta(days=_MEDICATION_WINDOW_DAYS), end)
    active = medications.get("active") or []
    notes = medications.get("notes") or []
    return ToolOutcome(
        _merge_tool_results(state, medications=medications),
        f"Đã lấy {len(active)} thuốc đang dùng và {len(notes)} ghi chú thuốc (30 ngày).",
    )


def _get_my_doctor(state: dict, session: Session, _args: NoArgs) -> ToolOutcome:
    doctor = get_assigned_doctor(session, state["patient_id"])
    found = "đã có" if doctor.get("found") else "chưa có"
    return ToolOutcome(_merge_tool_results(state, assigned_doctor=doctor), f"Thông tin bác sĩ phụ trách: {found}.")


_STATUS_VI = {
    AppointmentStatus.SCHEDULED: "đã lên lịch",
    AppointmentStatus.COMPLETED: "đã khám",
    AppointmentStatus.CANCELLED: "đã hủy",
}


def _get_appointments(state: dict, session: Session, _args: NoArgs) -> ToolOutcome:
    now = datetime.utcnow()
    appointments = list_appointments_for_patient(session, state["patient_id"])
    upcoming = [a for a in appointments if a.scheduled_at >= now and a.status == AppointmentStatus.SCHEDULED]
    recent = [a for a in appointments if a.scheduled_at < now][-2:]
    rows = [
        {
            "scheduled_at": a.scheduled_at,
            "reason": a.reason,
            "status": _STATUS_VI.get(a.status, str(a.status)),
            "upcoming": a.scheduled_at >= now,
        }
        for a in [*recent, *upcoming[:_APPOINTMENT_LIMIT]]
    ]
    return ToolOutcome(
        _merge_tool_results(state, appointments=rows),
        f"Có {len(upcoming)} lịch khám sắp tới, {len(recent)} lịch gần đây đã qua.",
    )


def _get_weekly_report(state: dict, session: Session, _args: NoArgs) -> ToolOutcome:
    reports = list_reports(session, state["patient_id"])
    if not reports:
        return ToolOutcome(_merge_tool_results(state, weekly_report=None), "Chưa có báo cáo tuần nào.")
    latest = reports[0]
    report = {
        "week": f"{latest.week_start:%d/%m/%Y}–{latest.week_end:%d/%m/%Y}",
        "stats": latest.stats,
        "tip": latest.tip,
    }
    return ToolOutcome(_merge_tool_results(state, weekly_report=report), f"Đã lấy báo cáo tuần {report['week']}.")


class AdherenceArgs(BaseModel):
    days: int = Field(default=30, ge=7, le=90, description="Số ngày nhìn lại (7-90).")


def _get_medication_adherence(state: dict, session: Session, args: AdherenceArgs) -> ToolOutcome:
    result = get_medication_adherence(session, state["patient_id"], args.days)
    rate = result["day_rate_pct"]
    return ToolOutcome(
        _merge_tool_results(state, medication_adherence=result),
        f"Tuân thủ thuốc {args.days} ngày: {result['days_taken']} ngày đủ, {result['days_missed']} ngày có quên/bỏ qua"
        + (f" ({rate}% ngày đủ thuốc)." if rate is not None else " (chưa đủ dữ liệu)."),
    )


class MealResponseArgs(BaseModel):
    days: int = Field(default=MEAL_RESPONSE_DAYS, ge=7, le=90, description="Số ngày nhìn lại (7-90).")


def _get_meal_glucose_response(state: dict, session: Session, args: MealResponseArgs) -> ToolOutcome:
    result = get_meal_glucose_response(session, state["patient_id"], args.days)
    return ToolOutcome(
        _merge_tool_results(state, meal_glucose_response=result),
        f"{result['meals_paired']} bữa có đo đường huyết sau ăn; {len(result['by_food'])} món đủ dữ liệu "
        f"(từ {result['min_meals_per_group']} bữa).",
    )


class KnowledgeArgs(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=300,
        description="Câu truy vấn độc lập, đầy đủ ngữ cảnh (viết lại câu hỏi nối tiếp nếu cần).",
    )


async def _search_knowledge(state: dict, _session: Session, args: KnowledgeArgs) -> ToolOutcome:
    found = await asyncio.to_thread(search_approved_knowledge, args.query, KNOWLEDGE_K)
    existing = list(state.get("retrieved_chunks") or [])
    seen = {chunk.get("chunk_id") for chunk in existing}
    merged = existing + [chunk for chunk in found if chunk.get("chunk_id") not in seen]
    titles = list(dict.fromkeys(chunk.get("title") or "?" for chunk in found))[:5]
    observation = f"Tìm thấy {len(found)} đoạn tài liệu: " + "; ".join(titles) if found else "Không tìm thấy tài liệu phù hợp."
    return ToolOutcome({"retrieved_chunks": merged, "knowledge_query": args.query}, observation)


# --- terminal tools (reviewed replies, reused from plan_response) ------------------


class SocialArgs(BaseModel):
    kind: Literal["greeting", "thanks", "farewell", "ack"]


class DirectAnswerArgs(BaseModel):
    answer: str = Field(
        min_length=1,
        max_length=1500,
        description="Câu trả lời ngắn cho câu hỏi về chính trợ lý/cách dùng ứng dụng. Không số liệu, thuốc hay khuyến nghị y khoa.",
    )


def _terminal_placeholder(_state: dict, _session: Session, _args: BaseModel) -> ToolOutcome:
    # nodes/agent_plan.py turns a terminal call into the planner's reviewed
    # result shape; nothing is gathered here.
    return ToolOutcome({}, "")


TOOLS: tuple[AgentTool, ...] = (
    AgentTool(
        "get_health_records",
        "Lấy bản ghi của chính bệnh nhân: đường huyết, huyết áp, bữa ăn (kèm ước tính carb), vận động, "
        "cùng hồ sơ cơ bản. Dùng cho mọi câu hỏi về chỉ số/bữa ăn/vận động đã ghi.",
        HealthRecordsArgs,
        _get_health_records,
    ),
    AgentTool("get_medications", "Lấy thuốc đang dùng và ghi chú thuốc (quên liều, tác dụng phụ) của bệnh nhân.", NoArgs, _get_medications),
    AgentTool(
        "get_medication_adherence",
        "Mức tuân thủ dùng thuốc: số ngày đủ thuốc / có quên, thuốc hay quên, đường huyết ngày đủ vs ngày quên.",
        AdherenceArgs,
        _get_medication_adherence,
    ),
    AgentTool("get_my_doctor", "Lấy thông tin bác sĩ phụ trách của bệnh nhân.", NoArgs, _get_my_doctor),
    AgentTool("get_appointments", "Lấy lịch khám/tái khám sắp tới và gần đây của bệnh nhân.", NoArgs, _get_appointments),
    AgentTool(
        "get_weekly_report",
        "Lấy báo cáo tuần gần nhất (tuần Chủ nhật–thứ Bảy đã qua): số liệu so với tuần trước và việc nên làm.",
        NoArgs,
        _get_weekly_report,
    ),
    AgentTool(
        "get_meal_glucose_response",
        "Món ăn / mức carb nào thường đi kèm đường huyết sau ăn cao ở bệnh nhân này (liên quan thống kê).",
        MealResponseArgs,
        _get_meal_glucose_response,
    ),
    AgentTool(
        "search_knowledge",
        "Tìm trong kho tài liệu y khoa đã duyệt (Bộ Y tế, ADA, VADE…) cho câu hỏi kiến thức chung. Có thể gọi "
        "nhiều lần với truy vấn khác nhau.",
        KnowledgeArgs,
        _search_knowledge,
    ),
    AgentTool(
        "reply_social",
        "Kết thúc lượt bằng lời đáp xã giao soạn sẵn — CHỈ cho lời chào/cảm ơn/tạm biệt/xác nhận ngắn không kèm câu hỏi.",
        SocialArgs,
        _terminal_placeholder,
        terminal=True,
    ),
    AgentTool(
        "request_new_appointment",
        "Kết thúc lượt khi người dùng muốn ĐẶT/TẠO lịch khám mới (trợ lý không tạo được lịch).",
        NoArgs,
        _terminal_placeholder,
        terminal=True,
    ),
    AgentTool(
        "out_of_scope",
        "Kết thúc lượt khi tin nhắn hoàn toàn không liên quan tới chăm sóc đái tháo đường/sức khỏe/trợ lý. Khi không chắc, đừng dùng.",
        NoArgs,
        _terminal_placeholder,
        terminal=True,
    ),
    AgentTool(
        "answer_directly",
        "Kết thúc lượt bằng câu trả lời ngắn KHÔNG cần dữ liệu hay tài liệu (trợ lý làm được gì, cách dùng ứng dụng).",
        DirectAnswerArgs,
        _terminal_placeholder,
        terminal=True,
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}

# SSE progress line shown while a gathering tool runs (agent/router.py).
PROGRESS_BY_TOOL = {
    "get_health_records": "Đang xem các chỉ số và bữa ăn bạn đã ghi…",
    "get_medications": "Đang xem thuốc bạn đang dùng…",
    "get_my_doctor": "Đang xem thông tin bác sĩ phụ trách…",
    "get_medication_adherence": "Đang xem việc uống thuốc của bạn…",
    "get_appointments": "Đang xem lịch khám của bạn…",
    "get_weekly_report": "Đang xem báo cáo tuần của bạn…",
    "get_meal_glucose_response": "Đang xem món ăn nào thường đi kèm đường huyết cao…",
    "search_knowledge": "Đang tra cứu tài liệu y khoa đã được duyệt…",
}

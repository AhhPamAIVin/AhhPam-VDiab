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
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from codebase.backend.agent.record_actions import _without_accents
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
    # The report's "việc nên làm" tip is advice: the chat assistant never
    # relays advice, so only the computed figures reach it.
    report = {
        "week": f"{latest.week_start:%d/%m/%Y}–{latest.week_end:%d/%m/%Y}",
        "stats": latest.stats,
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


# --- recording (drafts only; the patient confirms in a card) -----------------------

_RECORD_LABEL = {
    "glucose": "đường huyết",
    "blood_pressure": "huyết áp",
    "meal": "bữa ăn",
    "activity": "vận động",
    "medication_note": "ghi chú thuốc",
}


class RecordProposal(BaseModel):
    """One record the patient says already happened to THEM. Values only as
    stated; anything not said stays empty and the patient is asked."""

    record_type: Literal["glucose", "blood_pressure", "meal", "activity", "medication_note"]
    evidence: str = Field(
        min_length=1,
        max_length=400,
        description="Đoạn trích NGUYÊN VĂN, liền mạch từ tin nhắn mới nhất chứa bản ghi này (giữ nguyên chính tả).",
    )
    time: str | None = Field(
        default=None,
        description="Thời điểm xảy ra, ISO 8601 giờ Việt Nam có +07:00. CHỈ điền khi bệnh nhân nói giờ cụ thể "
        "('7h sáng', '19:30'), 'N phút/giờ trước' hoặc 'vừa/bây giờ'. 'sáng nay' không có giờ → để trống.",
    )
    time_evidence: str | None = Field(default=None, description="Đoạn nguyên văn nói về thời điểm, vd '7h sáng nay'.")
    value: float | None = Field(default=None, description="glucose: chỉ số đường huyết (số thập phân dùng dấu chấm).")
    unit: Literal["mg/dL", "mmol/L"] | None = Field(default=None, description="glucose: CHỈ khi bệnh nhân nói đơn vị.")
    meal_context: Literal["FASTING", "BEFORE_MEAL", "AFTER_MEAL", "BEFORE_SLEEP", "RANDOM"] | None = Field(
        default=None, description="glucose: CHỈ khi bệnh nhân nói rõ (lúc đói/trước ăn/sau ăn/trước khi ngủ)."
    )
    systolic: float | None = Field(default=None, description="blood_pressure: tâm thu.")
    diastolic: float | None = Field(default=None, description="blood_pressure: tâm trương.")
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] | None = Field(
        default=None, description="meal: CHỈ khi bệnh nhân nói rõ bữa nào."
    )
    description: str | None = Field(
        default=None, max_length=500, description="meal: món đã ăn, trích nguyên văn từ tin nhắn."
    )
    activity_type: str | None = Field(
        default=None, max_length=100, description="activity: tên hoạt động chép nguyên văn tiếng Việt (vd 'đi bộ'), KHÔNG dịch."
    )
    duration_minutes: int | None = Field(default=None, ge=1, le=1440, description="activity: số phút.")
    medication_name: str | None = Field(default=None, max_length=200, description="medication_note: tên thuốc, nguyên văn.")
    note_type: Literal["missed_dose", "adverse_reaction", "other"] | None = Field(
        default=None, description="medication_note: missed_dose khi quên/bỏ liều."
    )


class DraftRecordsArgs(BaseModel):
    records: list[RecordProposal] = Field(min_length=1, max_length=5)


def _match_medication(session: Session, patient_id, name: str | None):
    """The active medication the patient named, when exactly one matches."""
    from codebase.backend.medications.service import list_active_medications

    wanted = (name or "").strip().casefold()
    if not wanted:
        return None
    matches = [
        m
        for m in list_active_medications(session, patient_id)
        if wanted in m.drug_name.casefold() or m.drug_name.casefold() in wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _medication_named_in(session: Session, patient_id, text: str) -> str | None:
    from codebase.backend.medications.service import list_active_medications

    folded = _without_accents(text)
    named = [m.drug_name for m in list_active_medications(session, patient_id) if _without_accents(m.drug_name) in folded]
    return named[0] if len(named) == 1 else None


def _describe_draft(draft: dict) -> str:
    kind = draft["record_type"]
    if kind == "glucose":
        return f"đường huyết {draft['value']:g} {draft.get('unit') or '(chưa rõ đơn vị)'}"
    if kind == "blood_pressure":
        return f"huyết áp {draft['systolic']:g}/{draft['diastolic']:g} mmHg"
    if kind == "meal":
        return f"bữa ăn: {draft['description']}"
    if kind == "activity":
        return f"{draft['activity_type']} {draft['duration_minutes']} phút"
    return f"quên/bỏ liều {draft.get('medication_name')}"


def _draft_records(state: dict, session: Session, args: DraftRecordsArgs) -> ToolOutcome:
    from codebase.backend.agent.record_actions import draft_from_agent_proposal

    drafts = list(state.get("record_drafts") or [])
    rejected = 0
    for proposal in args.records:
        values = proposal.model_dump()
        if proposal.record_type == "medication_note" and not proposal.medication_name:
            # The model sometimes omits a drug the quote does name: an active
            # medication of this patient written in the quote fills it.
            values["medication_name"] = _medication_named_in(session, state["patient_id"], proposal.evidence)
        draft = draft_from_agent_proposal(state.get("message") or "", values)
        if draft is None:
            rejected += 1
            continue
        if draft["record_type"] == "medication_note" and not draft.get("medication_id"):
            medication = _match_medication(session, state["patient_id"], draft.get("medication_name"))
            draft["medication_id"] = str(medication.id) if medication else None
        drafts.append(draft)
    summary = [_describe_draft(d) for d in drafts]
    update = _merge_tool_results(state, pending_record_drafts=summary)
    update["record_drafts"] = drafts
    observation = f"Đã soạn {len(drafts)} bản nháp chờ bệnh nhân xác nhận" + (f": {'; '.join(summary)}." if summary else ".")
    if rejected:
        observation += f" {rejected} đề xuất bị loại vì không khớp nguyên văn tin nhắn."
    return ToolOutcome(update, observation)


class DeletionArgs(BaseModel):
    record_type: Literal["glucose", "blood_pressure", "meal", "activity"]
    value: float | None = Field(
        default=None,
        description="Giá trị bị nhập nhầm nếu bệnh nhân nói (đường huyết: chỉ số; huyết áp: tâm thu).",
    )
    evidence: str = Field(min_length=1, max_length=400, description="Đoạn nguyên văn bệnh nhân nói muốn xóa/sửa.")


def _record_summary(record_type: str, record) -> str:
    when = record.created_at
    for field in ("measured_at", "meal_time", "activity_time"):
        when = getattr(record, field, None) or when
    stamp = _vn_clock(when)
    if record_type == "glucose":
        return f"Đường huyết {record.value:g} {record.unit} lúc {stamp}"
    if record_type == "blood_pressure":
        return f"Huyết áp {record.systolic:g}/{record.diastolic:g} mmHg lúc {stamp}"
    if record_type == "meal":
        return f"Bữa ăn \"{record.description}\" lúc {stamp}"
    return f"{record.activity_type} {record.duration_minutes} phút lúc {stamp}"


def _vn_clock(value: datetime) -> str:
    from zoneinfo import ZoneInfo

    from codebase.backend.config import get_settings

    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(get_settings().reminder_timezone)).strftime("%H:%M %d/%m")


def _propose_record_deletion(state: dict, session: Session, args: DeletionArgs) -> ToolOutcome:
    from codebase.backend.agent.record_actions import evidence_supports_value, normalized_span
    from codebase.backend.health.service import self_deletable_records

    message = state.get("message") or ""
    if normalized_span(args.evidence) not in normalized_span(message):
        return ToolOutcome({}, "Đoạn trích không khớp nguyên văn tin nhắn; không soạn yêu cầu xóa.")
    if args.value is not None and not evidence_supports_value(args.evidence, args.value):
        return ToolOutcome({}, "Giá trị không có trong tin nhắn; không soạn yêu cầu xóa.")
    candidates = self_deletable_records(session, patient_id=state["patient_id"], record_type=args.record_type)
    if args.value is not None:
        field = "systolic" if args.record_type == "blood_pressure" else "value"
        candidates = [r for r in candidates if abs(float(getattr(r, field, -1) or -1) - args.value) < 0.05]
    if not candidates:
        note = (
            f"Không tìm thấy bản ghi {_RECORD_LABEL[args.record_type]} phù hợp mà bệnh nhân tự nhập trong 7 ngày qua "
            "(bản ghi do bác sĩ nhập hoặc đã được bác sĩ xem xét thì không tự xóa được)."
        )
        return ToolOutcome(_merge_tool_results(state, record_deletion=note), note)
    target = candidates[0]  # the most recently entered match
    summary = _record_summary(args.record_type, target)
    draft = {"record_type": "delete_record", "target_type": args.record_type, "target_id": str(target.id), "summary": summary}
    update = _merge_tool_results(state, record_deletion=f"Đã soạn yêu cầu xóa, chờ bệnh nhân xác nhận: {summary}")
    update["record_drafts"] = [*(state.get("record_drafts") or []), draft]
    return ToolOutcome(update, f"Đã soạn yêu cầu xóa chờ xác nhận: {summary}.")


# Accent-folded: "quên/chưa/bỏ liều/không uống" is a missed dose, never "taken".
_NOT_TAKEN_RE = re.compile(r"\b(?:quen|chua|bo lieu|bo qua|khong uong|ko uong|k uong)\b")


class MedicationTakenArgs(BaseModel):
    evidence: str = Field(min_length=1, max_length=400, description="Đoạn nguyên văn bệnh nhân nói đã uống thuốc.")


def _mark_medication_taken(state: dict, session: Session, args: MedicationTakenArgs) -> ToolOutcome:
    from codebase.backend.agent.record_actions import normalized_span, open_medication_reminders

    if normalized_span(args.evidence) not in normalized_span(state.get("message") or ""):
        return ToolOutcome({}, "Đoạn trích không khớp nguyên văn tin nhắn.")
    if _NOT_TAKEN_RE.search(_without_accents(args.evidence)):
        # Seen in the eval: "tôi quên uống metformin" routed here. Forgetting
        # is a missed-dose note (draft_records), never "taken".
        return ToolOutcome({}, "Bệnh nhân nói quên/chưa/không uống thuốc — dùng draft_records (medication_note).")
    reminders = open_medication_reminders(session, state["patient_id"])
    if not reminders:
        note = "Hôm nay không có lời nhắc uống thuốc nào đang chờ xác nhận."
        return ToolOutcome(_merge_tool_results(state, medication_taken=note), note)
    times = ", ".join(_vn_clock(r.scheduled_for).split()[0] for r in reminders)
    summary = f"Đã uống thuốc theo lời nhắc hôm nay ({times})"
    draft = {"record_type": "medication_taken", "notification_ids": [str(r.id) for r in reminders], "summary": summary}
    update = _merge_tool_results(state, medication_taken=f"Đã soạn xác nhận chờ bệnh nhân bấm: {summary}")
    update["record_drafts"] = [*(state.get("record_drafts") or []), draft]
    return ToolOutcome(update, f"Đã soạn xác nhận đã uống thuốc ({len(reminders)} lời nhắc đang mở).")


class MedicationReminderArgs(BaseModel):
    evidence: str = Field(
        min_length=1, max_length=400, description="Đoạn nguyên văn bệnh nhân nhờ nhắc uống thuốc, kèm giờ nếu có."
    )


def _set_medication_reminder(state: dict, session: Session, args: MedicationReminderArgs) -> ToolOutcome:
    """A daily medication reminder card. The time is read by code from the
    patient's own words, never taken from the model; unclear morning/evening
    ("lúc 8h") or no time at all → the answer asks instead of drafting."""
    from codebase.backend.agent.record_actions import normalized_span, stated_time_of_day
    from codebase.backend.medications.service import list_active_medications
    from codebase.backend.notifications.service import medication_reminder_at

    message = state.get("message") or ""
    if normalized_span(args.evidence) not in normalized_span(message):
        return ToolOutcome({}, "Đoạn trích không khớp nguyên văn tin nhắn; không soạn lời nhắc.")
    at = stated_time_of_day(args.evidence)
    if not at:
        note = (
            "Chưa rõ giờ nhắc là sáng hay tối — cần hỏi lại bệnh nhân (ví dụ '8 giờ sáng' hay '8 giờ tối')."
            if at is False
            else "Bệnh nhân chưa nói giờ — cần hỏi lại muốn được nhắc uống thuốc lúc mấy giờ."
        )
        return ToolOutcome(_merge_tool_results(state, medication_reminder=note), note)
    clock = at.strftime("%H:%M")
    medications = list_active_medications(session, state["patient_id"])
    existing = medication_reminder_at(session, state["patient_id"], at, has_medications=bool(medications))
    if existing is not None:
        note = f"Đã có lời nhắc uống thuốc lúc {clock} hằng ngày ({existing.label}); không cần đặt thêm."
        return ToolOutcome(_merge_tool_results(state, medication_reminder=note), note)
    folded_message = _without_accents(message)
    named = [m.drug_name for m in medications if _without_accents(m.drug_name) in folded_message]
    label = f"Nhắc uống thuốc {', '.join(named)}" if named else "Nhắc uống thuốc"
    summary = f"{label} lúc {clock} hằng ngày"
    draft = {"record_type": "medication_reminder", "time_of_day": clock, "label": label, "summary": summary}
    update = _merge_tool_results(state, medication_reminder=f"Đã soạn lời nhắc chờ bệnh nhân xác nhận: {summary}")
    update["record_drafts"] = [*(state.get("record_drafts") or []), draft]
    return ToolOutcome(update, f"Đã soạn lời nhắc chờ xác nhận: {summary}.")


class HealthStatsArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="Số ngày gần nhất (1-90).")
    start_date: date | None = Field(default=None, description="Ngày bắt đầu (YYYY-MM-DD) nếu hỏi khoảng cụ thể.")
    end_date: date | None = Field(default=None, description="Ngày kết thúc (YYYY-MM-DD), mặc định hôm nay.")
    compare_previous: bool = Field(default=False, description="So với khoảng liền trước cùng độ dài ('so với tuần trước').")
    above_mg_dl: float | None = Field(default=None, ge=20, le=600, description="Đếm số lần đường huyết TRÊN mức này.")
    below_mg_dl: float | None = Field(default=None, ge=20, le=600, description="Đếm số lần đường huyết DƯỚI mức này.")


_STATS_MAX_DAYS = 90


def _period_stats(session: Session, patient_id, start: datetime, end: datetime, args: HealthStatsArgs) -> dict:
    from codebase.models.agents.tools.metrics_tools import calculate_health_metrics

    records = get_health_records(session, patient_id, start, end, max_days=_STATS_MAX_DAYS)
    metrics = calculate_health_metrics(records)
    values = [float(g["value"]) for g in records["glucose"] if g.get("value") is not None]
    stats = {
        "khoang": f"{_vn_clock(start).split()[1]}–{_vn_clock(end).split()[1]}",
        "duong_huyet": {
            "so_lan_do": metrics["glucose_count"],
            "so_ngay_co_do": metrics["glucose_days_logged"],
            **{k: v for k, v in metrics["glucose_overall"].items() if k != "count"},
            "theo_hoan_canh": metrics["glucose_by_context"],
        },
        "huyet_ap": {
            "so_lan_do": metrics["blood_pressure_count"],
            "tam_thu": metrics["blood_pressure_systolic"],
            "tam_truong": metrics["blood_pressure_diastolic"],
        },
        "so_bua_an": metrics["meal_count"],
        "so_lan_van_dong": metrics["activity_count"],
    }
    if args.above_mg_dl is not None:
        stats["duong_huyet"][f"so_lan_tren_{args.above_mg_dl:g}"] = sum(v > args.above_mg_dl for v in values)
    if args.below_mg_dl is not None:
        stats["duong_huyet"][f"so_lan_duoi_{args.below_mg_dl:g}"] = sum(v < args.below_mg_dl for v in values)
    return stats


def _get_health_stats(state: dict, session: Session, args: HealthStatsArgs) -> ToolOutcome:
    """Numbers computed here, never by the model (counts, averages, min/max,
    threshold counts, period-over-period change) — up to 90 days."""
    now = datetime.utcnow()
    if args.start_date is not None:
        start = datetime.combine(args.start_date, time.min)
        end = datetime.combine(args.end_date, time.max) if args.end_date else now
    elif state.get("date_range_override") is not None:
        start, end = state["date_range_override"]
    else:
        end, start = now, now - timedelta(days=args.days)
    if start > end:
        start, end = end, start
    start = max(start, end - timedelta(days=_STATS_MAX_DAYS))
    current = _period_stats(session, state["patient_id"], start, end, args)
    result = {"hien_tai": current}
    if args.compare_previous:
        previous = _period_stats(session, state["patient_id"], start - (end - start), start, args)
        result["truoc_do"] = previous
        cur_avg, prev_avg = current["duong_huyet"].get("avg"), previous["duong_huyet"].get("avg")
        if cur_avg is not None and prev_avg is not None:
            result["chenh_lech_trung_binh_duong_huyet_mg_dl"] = round(cur_avg - prev_avg, 1)
    update = _merge_tool_results(state, health_stats=result)
    return ToolOutcome(
        update,
        f"Đã tính thống kê {current['khoang']}: {current['duong_huyet']['so_lan_do']} lần đo đường huyết"
        + (" (kèm khoảng trước đó)." if args.compare_previous else "."),
    )


def _get_my_insights(state: dict, session: Session, _args: NoArgs) -> ToolOutcome:
    """Long-term patterns the weekly AI scan found (and, with the patient's
    consent, ones mined from their own chats) — minus any a doctor rejected.
    AI-generated: synthesize_qa may use them only in the assessment box."""
    from codebase.backend.auth.models import User
    from codebase.backend.insights.feedback import rejected_insight_ids
    from codebase.models.agents.tools.insight_tools import get_patient_insights

    patient = session.get(User, state["patient_id"])
    kinds = ["weekly_batch", "chat"] if patient is not None and patient.ai_memory_consent else ["weekly_batch"]
    rejected = rejected_insight_ids(session, state["patient_id"])
    texts = [i["text"] for i in get_patient_insights(session, state["patient_id"], source_kind=kinds) if i["id"] not in rejected]
    return ToolOutcome(
        _merge_tool_results(state, ai_insights=texts[:8]),
        f"Có {len(texts)} nhận xét tự động dài hạn." if texts else "Chưa có nhận xét tự động nào.",
    )


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
        "Lấy báo cáo tuần gần nhất (tuần Chủ nhật–thứ Bảy đã qua): số liệu so với tuần trước.",
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
        "draft_records",
        "Soạn bản nháp ghi vào hồ sơ khi bệnh nhân KỂ chỉ số/bữa ăn/vận động/quên thuốc ĐÃ xảy ra với CHÍNH HỌ "
        "(kể cả viết không dấu, viết tắt, nhiều chỉ số một câu). Không dùng cho câu hỏi, giả định ('nếu…'), kế "
        "hoạch, hay số của người khác. Chỉ điền điều bệnh nhân nói; bệnh nhân sẽ xác nhận trước khi lưu.",
        DraftRecordsArgs,
        _draft_records,
    ),
    AgentTool(
        "propose_record_deletion",
        "Soạn yêu cầu XÓA một bản ghi bệnh nhân tự nhập nhầm gần đây ('lúc nãy ghi nhầm 180', 'xóa bữa trưa vừa "
        "nhập'). Nếu bệnh nhân nói cả giá trị đúng, gọi thêm draft_records cho giá trị đúng. Bệnh nhân xác nhận trước khi xóa.",
        DeletionArgs,
        _propose_record_deletion,
    ),
    AgentTool(
        "mark_medication_taken",
        "Soạn xác nhận ĐÃ uống thuốc theo lời nhắc hôm nay khi bệnh nhân kể đã uống ('tôi uống thuốc rồi'). "
        "Quên/bỏ liều thì dùng draft_records (medication_note).",
        MedicationTakenArgs,
        _mark_medication_taken,
    ),
    AgentTool(
        "set_medication_reminder",
        "Soạn lời nhắc uống thuốc HẰNG NGÀY khi bệnh nhân nhờ nhắc ('nhắc tôi uống thuốc lúc 8h tối'). Giờ do hệ "
        "thống đọc từ đoạn trích; bệnh nhân xác nhận trước khi đặt. Đổi/tắt lời nhắc có sẵn thì hướng dẫn vào mục "
        "Thông báo.",
        MedicationReminderArgs,
        _set_medication_reminder,
    ),
    AgentTool(
        "get_health_stats",
        "Thống kê do hệ thống tính trên tối đa 90 ngày: số lần đo, trung bình/thấp nhất/cao nhất, theo hoàn cảnh đo, "
        "số lần trên/dưới một mức, và so với khoảng liền trước. Dùng cho mọi câu hỏi 'trung bình', 'bao nhiêu lần', "
        "'so với tuần/tháng trước'.",
        HealthStatsArgs,
        _get_health_stats,
    ),
    AgentTool(
        "get_my_insights",
        "Nhận xét tự động dài hạn về xu hướng của bệnh nhân (do AI phát hiện, chưa phải ý kiến bác sĩ). Dùng khi hỏi "
        "'tôi có xu hướng gì', 'AI nhận thấy gì về tôi'.",
        NoArgs,
        _get_my_insights,
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
    "draft_records": "Đang chuẩn bị bản ghi để bạn xác nhận…",
    "propose_record_deletion": "Đang tìm bản ghi bạn muốn xóa…",
    "mark_medication_taken": "Đang xem lời nhắc uống thuốc hôm nay…",
    "set_medication_reminder": "Đang chuẩn bị lời nhắc uống thuốc…",
    "get_health_stats": "Đang tính thống kê từ dữ liệu của bạn…",
    "get_my_insights": "Đang xem các nhận xét tự động về xu hướng của bạn…",
}

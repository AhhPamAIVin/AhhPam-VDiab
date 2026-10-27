"""Epic 8.6 — the agent's next tools: code-computed statistics, removing a
record the patient entered by mistake, "I took my medicine", and the
long-term AI insights (assessment box only)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from codebase.backend.agent.models import ChatSession
from codebase.backend.agent.record_actions import apply_action, create_pending_action
from codebase.backend.agent.schemas import AgentRecordActionRequest
from codebase.backend.auth.models import User, UserRole
from codebase.backend.health.models import (
    CarbLoad,
    DoctorWatchLabel,
    DoctorWatchReview,
    GlucoseRecord,
    MealAnalysis,
    MealAnalysisStatus,
    MealContext,
    MealInputSource,
    MealRecord,
    MealType,
)
from codebase.backend.insights.models import InsightConfidence, InsightSourceKind, InsightStatus, PatientInsight
from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType
from codebase.models.agents.tools.agent_tools import (
    TOOLS_BY_NAME,
    DeletionArgs,
    HealthStatsArgs,
    MedicationTakenArgs,
    NoArgs,
)
from tests.test_backend.test_patient_agent import _user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


@pytest.fixture
def doctor(session: Session) -> User:
    return _user(session, "doctor@diab.example.com", UserRole.DOCTOR)


def _glucose(session: Session, patient: User, value: float, *, days_ago: float = 0, by: User | None = None,
             context=MealContext.FASTING) -> GlucoseRecord:
    when = datetime.utcnow() - timedelta(days=days_ago)
    record = GlucoseRecord(
        patient_id=patient.id, value=value, unit="mg/dL", measured_at=when, meal_context=context,
        created_by=(by or patient).id, created_at=when,
    )
    session.add(record)
    session.commit()
    return record


def _state(patient: User, message: str = "") -> dict:
    return {"patient_id": patient.id, "message": message, "tool_results": {}}


def _confirm(session: Session, patient: User, draft: dict) -> tuple[str, object]:
    chat = ChatSession(thread_id=f"t-{datetime.utcnow().timestamp()}", patient_id=patient.id, counterpart_id=patient.id)
    session.add(chat)
    session.commit()
    action = create_pending_action(session, patient_id=patient.id, chat_session=chat, draft=draft)
    return apply_action(
        session, action_id=action.id, patient_id=patient.id, request=AgentRecordActionRequest(action="confirm")
    )


# --- statistics -------------------------------------------------------------------


def test_stats_are_computed_by_code_with_comparison_and_thresholds(session: Session, patient: User):
    for value, days_ago in ((120, 1), (200, 2), (190, 3), (100, 9), (110, 10)):
        _glucose(session, patient, value, days_ago=days_ago)
    outcome = TOOLS_BY_NAME["get_health_stats"].handler(
        _state(patient), session, HealthStatsArgs(days=7, compare_previous=True, above_mg_dl=180)
    )
    stats = outcome.state_update["tool_results"]["health_stats"]
    current, previous = stats["hien_tai"]["duong_huyet"], stats["truoc_do"]["duong_huyet"]
    assert (current["so_lan_do"], current["avg"], current["max"], current["so_lan_tren_180"]) == (3, 170.0, 200, 2)
    assert (previous["so_lan_do"], previous["avg"]) == (2, 105.0)
    assert stats["chenh_lech_trung_binh_duong_huyet_mg_dl"] == 65.0


def test_stats_reach_back_90_days_not_more(session: Session, patient: User):
    _glucose(session, patient, 150, days_ago=60)
    _glucose(session, patient, 300, days_ago=120)
    outcome = TOOLS_BY_NAME["get_health_stats"].handler(_state(patient), session, HealthStatsArgs(days=90))
    assert outcome.state_update["tool_results"]["health_stats"]["hien_tai"]["duong_huyet"]["so_lan_do"] == 1
    with pytest.raises(ValueError):
        HealthStatsArgs(days=91)


# --- deleting a mistake --------------------------------------------------------------


def test_deletion_targets_the_patients_recent_matching_record_and_deletes_on_confirm(session: Session, patient: User):
    _glucose(session, patient, 108, days_ago=0.5)
    wrong = _glucose(session, patient, 180, days_ago=0.1)
    message = "lúc nãy tôi ghi nhầm 180"
    outcome = TOOLS_BY_NAME["propose_record_deletion"].handler(
        _state(patient, message), session, DeletionArgs(record_type="glucose", value=180, evidence=message)
    )
    draft = outcome.state_update["record_drafts"][0]
    assert draft["record_type"] == "delete_record" and draft["target_id"] == str(wrong.id)

    reply, interaction = _confirm(session, patient, draft)
    assert "xóa" in reply and interaction.type == "record_saved"
    session.expire_all()
    assert session.get(GlucoseRecord, wrong.id) is None
    assert len(session.exec(select(GlucoseRecord)).all()) == 1


def test_records_the_patient_may_not_delete_are_never_offered(session: Session, patient: User, doctor: User):
    _glucose(session, patient, 180, by=doctor)  # entered by the doctor
    _glucose(session, patient, 180, days_ago=8)  # too old
    reviewed = _glucose(session, patient, 180)
    session.add(DoctorWatchReview(glucose_record_id=reviewed.id, patient_id=patient.id, doctor_id=doctor.id,
                                  label=list(DoctorWatchLabel)[0], rationale="ok"))
    session.commit()
    message = "xóa chỉ số 180"
    outcome = TOOLS_BY_NAME["propose_record_deletion"].handler(
        _state(patient, message), session, DeletionArgs(record_type="glucose", value=180, evidence=message)
    )
    assert "record_drafts" not in outcome.state_update
    assert "Không tìm thấy" in outcome.observation


def test_a_forged_deletion_draft_is_refused_at_confirm(session: Session, patient: User, doctor: User):
    other = _user(session, "other@diab.example.com", UserRole.PATIENT)
    theirs = _glucose(session, other, 180)
    draft = {"record_type": "delete_record", "target_type": "glucose", "target_id": str(theirs.id), "summary": "x"}
    with pytest.raises(HTTPException) as exc:
        _confirm(session, patient, draft)
    assert exc.value.status_code == 409
    assert session.get(GlucoseRecord, theirs.id) is not None


def test_deleting_a_meal_takes_its_analysis_with_it(session: Session, patient: User):
    meal = MealRecord(patient_id=patient.id, meal_type=MealType.LUNCH, meal_time=datetime.utcnow(),
                      description="2 bát phở", created_by=patient.id)
    session.add(meal)
    session.commit()
    session.add(
        MealAnalysis(
            meal_id=meal.id, patient_id=patient.id, items=[], carb_load=CarbLoad.UNKNOWN,
            source=MealInputSource.TEXT, status=MealAnalysisStatus.DRAFT,
        )
    )
    session.commit()
    message = "xóa bữa trưa vừa nhập"
    outcome = TOOLS_BY_NAME["propose_record_deletion"].handler(
        _state(patient, message), session, DeletionArgs(record_type="meal", evidence=message)
    )
    _confirm(session, patient, outcome.state_update["record_drafts"][0])
    session.expire_all()
    assert session.exec(select(MealRecord)).all() == [] and session.exec(select(MealAnalysis)).all() == []


def test_deletion_value_must_be_in_the_message(session: Session, patient: User):
    _glucose(session, patient, 180)
    message = "xóa chỉ số lúc nãy"
    outcome = TOOLS_BY_NAME["propose_record_deletion"].handler(
        _state(patient, message), session, DeletionArgs(record_type="glucose", value=180, evidence=message)
    )
    assert "record_drafts" not in outcome.state_update


# --- medicine taken ---------------------------------------------------------------


def _reminder(session: Session, patient: User, *, status=NotificationStatus.PENDING, day: date | None = None):
    today = day or datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    notification = Notification(
        patient_id=patient.id, notification_type=NotificationType.MEDICATION_REMINDER, title="Uống thuốc",
        message="Đến giờ uống thuốc", slot_key=f"med:{today}:{status}", slot_date=today,
        scheduled_for=datetime.utcnow(), status=status,
    )
    session.add(notification)
    session.commit()
    return notification


def test_medication_taken_completes_todays_open_reminder_on_confirm(session: Session, patient: User):
    reminder = _reminder(session, patient)
    _reminder(session, patient, status=NotificationStatus.COMPLETED)
    yesterday = _reminder(session, patient, day=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() - timedelta(days=1))
    message = "tôi uống thuốc rồi"
    outcome = TOOLS_BY_NAME["mark_medication_taken"].handler(
        _state(patient, message), session, MedicationTakenArgs(evidence=message)
    )
    draft = outcome.state_update["record_drafts"][0]
    assert draft["notification_ids"] == [str(reminder.id)]

    reply, _ = _confirm(session, patient, draft)
    assert "uống thuốc" in reply
    session.expire_all()
    assert session.get(Notification, reminder.id).status == NotificationStatus.COMPLETED
    assert session.get(Notification, yesterday.id).status == NotificationStatus.PENDING


def test_medication_taken_without_an_open_reminder_says_so(session: Session, patient: User):
    message = "tôi uống thuốc rồi"
    outcome = TOOLS_BY_NAME["mark_medication_taken"].handler(
        _state(patient, message), session, MedicationTakenArgs(evidence=message)
    )
    assert "record_drafts" not in outcome.state_update
    assert "không có lời nhắc" in outcome.state_update["tool_results"]["medication_taken"]


# --- insights ---------------------------------------------------------------------


def _insight(session: Session, patient: User, kind: InsightSourceKind, text: str) -> None:
    now = datetime.utcnow()
    session.add(
        PatientInsight(
            patient_id=patient.id, category="glucose_pattern", text=text, evidence={},
            confidence=InsightConfidence.MEDIUM, status=InsightStatus.ACTIVE, source_kind=kind,
            first_observed_at=now, last_confirmed_at=now,
        )
    )
    session.commit()


def test_insights_respect_the_chat_memory_consent(session: Session, patient: User, doctor: User):
    _insight(session, patient, InsightSourceKind.WEEKLY_BATCH, "Đường huyết sau bữa tối thường cao hơn.")
    _insight(session, patient, InsightSourceKind.CHAT, "Hay ăn khuya.")
    _insight(session, patient, InsightSourceKind.APPOINTMENT, "Ghi chú từ bác sĩ.")
    tool = TOOLS_BY_NAME["get_my_insights"]

    texts = tool.handler(_state(patient), session, NoArgs()).state_update["tool_results"]["ai_insights"]
    assert texts == ["Đường huyết sau bữa tối thường cao hơn."]

    patient.ai_memory_consent = True
    session.add(patient)
    session.commit()
    texts = tool.handler(_state(patient), session, NoArgs()).state_update["tool_results"]["ai_insights"]
    assert sorted(texts) == ["Hay ăn khuya.", "Đường huyết sau bữa tối thường cao hơn."]


def test_insights_go_to_the_assessment_box_only():
    from codebase.models.agents.nodes.synthesize_qa import _format_patient_data_block

    block = _format_patient_data_block({"ai_insights": ["Hay ăn khuya."]})
    assert "assessment" in block and "CHƯA phải ý kiến bác sĩ" in block


@pytest.mark.parametrize("message", ["Sáng nay tôi quên uống metformin", "toi chua uong thuoc", "hôm nay không uống thuốc"])
def test_forgetting_is_never_marked_as_taken(session: Session, patient: User, message: str):
    _reminder(session, patient)
    outcome = TOOLS_BY_NAME["mark_medication_taken"].handler(
        _state(patient, message), session, MedicationTakenArgs(evidence=message)
    )
    assert "record_drafts" not in outcome.state_update
    assert "draft_records" in outcome.observation

"""Epic 3.1-3.2: medication adherence (day and dose level), its glucose
comparison, the endpoint, and how it reaches the agent."""

from datetime import date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealContext
from codebase.backend.medications.adherence import compute_adherence
from codebase.backend.medications.models import Medication, MedicationNote, MedicationNoteType
from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType
from codebase.models.agents.nodes.fetch_data import fetch_data
from codebase.models.agents.nodes.synthesize_summary import _build_adherence_lines
from codebase.models.agents.tools.agent_tools import TOOLS_BY_NAME, AdherenceArgs

PASSWORD = "AdminPass123"
TODAY = date(2026, 10, 25)
WEEK = (date(2026, 10, 18), date(2026, 10, 24))


@pytest.fixture
def patient(session: Session) -> User:
    user = User(email="p@diab.example.com", full_name="P", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _medication(session: Session, patient: User, name="Metformin", frequency="2 lần/ngày") -> Medication:
    med = Medication(
        patient_id=patient.id, drug_name=name, dose="500mg", frequency=frequency,
        start_date=datetime(2026, 9, 1), created_by=patient.id,
    )
    session.add(med)
    session.commit()
    session.refresh(med)
    return med


def _reminder(session: Session, patient: User, day: date, status: NotificationStatus, slot: str = "glucose_before_breakfast"):
    # 06:00 local = 23:00 UTC the day before.
    scheduled = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
    session.add(
        Notification(
            patient_id=patient.id, notification_type=NotificationType.MEDICATION_REMINDER,
            title="Nhắc uống thuốc", message="Metformin", slot_key=f"medication_{slot}:{day.isoformat()}",
            slot_date=day, scheduled_for=scheduled, status=status,
            responded_at=scheduled + timedelta(minutes=5) if status == NotificationStatus.COMPLETED else None,
        )
    )
    session.commit()


def _missed_note(session: Session, patient: User, med: Medication, day: date):
    session.add(
        MedicationNote(
            medication_id=med.id, patient_id=patient.id, note_type=MedicationNoteType.MISSED_DOSE,
            occurred_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=3), created_by=patient.id,
        )
    )
    session.commit()


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _adherence(session, patient):
    return compute_adherence(session, patient.id, *WEEK, today=TODAY)


def test_regular(session: Session, patient: User):
    _medication(session, patient)
    for day in _days(*WEEK):
        _reminder(session, patient, day, NotificationStatus.COMPLETED)
    result = _adherence(session, patient)
    assert result["days_taken"] == 7 and result["days_missed"] == 0
    assert result["day_rate_pct"] == 100.0 and result["dose_rate_pct"] == 100.0
    assert result["missed_dates"] == []


def test_occasional_misses(session: Session, patient: User):
    med = _medication(session, patient)
    for day in _days(*WEEK):
        _reminder(session, patient, day, NotificationStatus.COMPLETED)
        _reminder(session, patient, day, NotificationStatus.COMPLETED, slot="glucose_before_dinner")
    _missed_note(session, patient, med, date(2026, 10, 20))  # patient says they missed one
    result = _adherence(session, patient)
    assert result["days_taken"] == 6 and result["days_missed"] == 1
    assert result["day_rate_pct"] == pytest.approx(85.7)
    assert result["dose_rate_pct"] == pytest.approx(92.9)  # 13 of 14
    assert result["missed_dates"] == ["2026-10-20"]
    assert result["medications"][0]["missed_notes"] == 1


def test_whole_week_skipped(session: Session, patient: User):
    _medication(session, patient)
    for day in _days(*WEEK):
        _reminder(session, patient, day, NotificationStatus.SKIPPED)
    result = _adherence(session, patient)
    assert result["days_missed"] == 7 and result["day_rate_pct"] == 0.0 and result["dose_rate_pct"] == 0.0


def test_no_reminders(session: Session, patient: User):
    _medication(session, patient)
    result = _adherence(session, patient)
    assert result["days_no_data"] == 7
    assert result["day_rate_pct"] is None and result["dose_rate_pct"] is None


def test_skipped_and_noted_counts_once(session: Session, patient: User):
    med = _medication(session, patient)
    day = date(2026, 10, 20)
    _reminder(session, patient, day, NotificationStatus.SKIPPED)
    _missed_note(session, patient, med, day)
    result = _adherence(session, patient)
    entry = next(d for d in result["daily"] if d["date"] == "2026-10-20")
    assert entry["skipped"] == 1 and entry["missed_notes"] == 1
    assert result["doses_considered"] == 1  # one dose, not two


def test_unanswered_reminders(session: Session, patient: User):
    _medication(session, patient)
    _reminder(session, patient, date(2026, 10, 22), NotificationStatus.PENDING)
    _reminder(session, patient, TODAY, NotificationStatus.PENDING)
    result = compute_adherence(session, patient.id, WEEK[0], TODAY, today=TODAY)
    statuses = {d["date"]: d["status"] for d in result["daily"]}
    assert statuses["2026-10-22"] == "unconfirmed"  # not counted as forgotten
    assert statuses[TODAY.isoformat()] == "no_data"  # today isn't over
    assert result["days_missed"] == 0
    assert result["dose_rate_pct"] == 0.0  # but still not a confirmed dose


def test_as_needed_medication_notes_are_left_out(session: Session, patient: User):
    _medication(session, patient)
    prn = _medication(session, patient, name="Paracetamol", frequency="khi cần")
    _reminder(session, patient, date(2026, 10, 20), NotificationStatus.COMPLETED)
    _missed_note(session, patient, prn, date(2026, 10, 20))
    result = _adherence(session, patient)
    assert result["days_missed"] == 0
    assert next(m for m in result["medications"] if m["drug_name"] == "Paracetamol")["as_needed"] is True


def _glucose(session, patient, day: date, value: float):
    session.add(GlucoseRecord(patient_id=patient.id, value=value, measured_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=2),
                              meal_context=MealContext.FASTING, created_by=patient.id))
    session.commit()


def test_glucose_on_taken_vs_missed_days(session: Session, patient: User):
    _medication(session, patient)
    days = list(_days(*WEEK))
    for day in days[:4]:
        _reminder(session, patient, day, NotificationStatus.COMPLETED)
        _glucose(session, patient, day, 120)
    for day in days[4:]:
        _reminder(session, patient, day, NotificationStatus.SKIPPED)
        _glucose(session, patient, day, 180)
    assert _adherence(session, patient)["glucose_by_adherence"] == {
        "taken_days": 4, "taken_avg_mg_dl": 120.0, "missed_days": 3, "missed_avg_mg_dl": 180.0,
    }


def test_glucose_comparison_needs_three_days_each(session: Session, patient: User):
    _medication(session, patient)
    days = list(_days(*WEEK))
    for day in days[:5]:
        _reminder(session, patient, day, NotificationStatus.COMPLETED)
        _glucose(session, patient, day, 120)
    for day in days[5:]:
        _reminder(session, patient, day, NotificationStatus.SKIPPED)
        _glucose(session, patient, day, 180)
    assert _adherence(session, patient)["glucose_by_adherence"] is None


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_endpoint_and_access(client: AsyncClient, session: Session, patient: User):
    _medication(session, patient)
    response = await client.get("/api/v1/medications/adherence?days=7", headers=await _headers(client, patient))
    assert response.status_code == 200, response.text
    assert response.json()["days"] == 7

    other = User(email="o@diab.example.com", full_name="O", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(other)
    session.commit()
    response = await client.get(f"/api/v1/medications/adherence?patient_id={patient.id}", headers=await _headers(client, other))
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "message", "included"),
    [("SUMMARY", "", True), ("QA", "Tôi có hay quên thuốc không?", True), ("QA", "Đường huyết hôm nay?", False)],
)
async def test_fetch_data_adds_adherence_when_relevant(session: Session, patient: User, intent, message, included):
    result = await fetch_data(
        {"intent": intent, "needs_health_data": True, "patient_id": patient.id, "message": message},
        {"configurable": {"session": session}},
    )
    assert ("medication_adherence" in result["tool_results"]) is included
    if included:
        assert "daily" not in result["tool_results"]["medication_adherence"]


def test_agent_tool(session: Session, patient: User):
    _medication(session, patient)
    outcome = TOOLS_BY_NAME["get_medication_adherence"].handler(
        {"patient_id": patient.id, "tool_results": {}}, session, AdherenceArgs(days=7)
    )
    assert "medication_adherence" in outcome.state_update["tool_results"]
    assert "chưa đủ dữ liệu" in outcome.observation


def test_doctor_summary_lines():
    adherence = {
        "days": 30, "days_taken": 24, "days_missed": 6, "days_unconfirmed": 2, "day_rate_pct": 80.0,
        "medications": [{"drug_name": "Metformin", "missed_notes": 3}, {"drug_name": "Gliclazide", "missed_notes": 0}],
        "glucose_by_adherence": {"taken_days": 20, "taken_avg_mg_dl": 135.0, "missed_days": 5, "missed_avg_mg_dl": 182.0},
    }
    lines = _build_adherence_lines(adherence, "doctor")
    assert lines[0].startswith("Tuân thủ thuốc (30 ngày): 24 ngày đủ thuốc, 6 ngày có quên/bỏ qua (80.0%")
    assert "2 ngày không xác nhận" in lines[0]
    assert lines[1] == "Thuốc bệnh nhân tự ghi nhận quên: Metformin."
    assert "không phải nguyên nhân" in lines[2]
    assert _build_adherence_lines(adherence, "patient") == []
    assert _build_adherence_lines({**adherence, "day_rate_pct": None}, "doctor") == []

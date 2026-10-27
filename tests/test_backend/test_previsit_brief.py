"""Epic 4.1-4.3: the pre-visit brief — window since the last visit, the hourly
job (idempotent, cancelled/rescheduled/new data, failure), the doctor's
sections ("chưa đủ dữ liệu" never left blank) and the API."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.appointments import briefs
from codebase.backend.appointments.models import Appointment, AppointmentStatus, PrevisitBrief, PrevisitBriefStatus
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord
from codebase.backend.patients.models import ClinicalNote, PatientAssignment
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole
from codebase.models.agents.nodes.synthesize_summary import _build_sections, synthesize_summary

PASSWORD = "AdminPass123"
NOW = datetime.utcnow().replace(microsecond=0)
SNAPSHOT = "codebase.backend.appointments.briefs.generate_and_store_snapshot"


def _user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


@pytest.fixture
def doctor(session: Session, patient: User) -> User:
    doctor = _user(session, "doctor@diab.example.com", UserRole.DOCTOR)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    return doctor


def _appointment(session, patient, doctor, at, status=AppointmentStatus.SCHEDULED) -> Appointment:
    appointment = Appointment(
        patient_id=patient.id, doctor_id=doctor.id, scheduled_at=at, status=status, created_by=doctor.id
    )
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


def _snapshot_mock(attention: list[str] | None = None) -> AsyncMock:
    async def _make(session_arg, *, patient_id, role, period_days, period_start, period_end):
        snap = AgentSummarySnapshot(
            patient_id=patient_id, role=SummaryRole(role), period_days=period_days, period_start=period_start,
            period_end=period_end, summary="Tóm tắt cho bác sĩ.", attention_points=attention or ["Điểm cần chú ý."],
            sections={"changes": ["So với giai đoạn trước: ..."], "meals": ["Chưa đủ dữ liệu: ..."]},
        )
        session_arg.add(snap)
        session_arg.commit()
        session_arg.refresh(snap)
        return snap

    return AsyncMock(side_effect=_make)


def _brief(session, appointment) -> PrevisitBrief | None:
    session.expire_all()
    return briefs.get_brief(session, appointment.id)


# --- window since the last visit ---------------------------------------------------


@pytest.mark.parametrize(
    ("previous_days_ago", "expected_days"),
    [(None, 30), (45, 45), (2, 7), (200, 90)],
)
def test_brief_window(previous_days_ago, expected_days):
    previous = None if previous_days_ago is None else NOW - timedelta(days=previous_days_ago)
    start, end, days = briefs.brief_window(previous, NOW)
    assert (end, days) == (NOW, expected_days)
    assert end - start == timedelta(days=expected_days)


def test_previous_visit_is_the_latest_completed_appointment_or_note(session: Session, patient, doctor):
    upcoming = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    assert briefs.previous_visit_at(session, upcoming) is None

    _appointment(session, patient, doctor, NOW - timedelta(days=60), AppointmentStatus.COMPLETED)
    _appointment(session, patient, doctor, NOW - timedelta(days=10), AppointmentStatus.CANCELLED)  # not a visit
    assert briefs.previous_visit_at(session, upcoming) == NOW - timedelta(days=60)

    session.add(ClinicalNote(
        patient_id=patient.id, doctor_id=doctor.id, reason_for_visit="Tái khám", history_of_present_illness="Ổn",
        diagnosis="ĐTĐ típ 2", treatment_plan="Giữ phác đồ", created_at=NOW - timedelta(days=40),
    ))
    session.commit()
    assert briefs.previous_visit_at(session, upcoming) == NOW - timedelta(days=40)


# --- hourly job --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_makes_one_brief_and_is_idempotent(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    _appointment(session, patient, doctor, NOW - timedelta(days=45), AppointmentStatus.COMPLETED)
    snapshot = _snapshot_mock()
    with patch(SNAPSHOT, snapshot):
        first = await briefs.run_previsit_brief_tick(session, now_utc=NOW)
        again = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=1))

    assert first == {"generated": 1, "up_to_date": 0, "skipped": 0, "failed": 0}
    assert again == {"generated": 0, "up_to_date": 1, "skipped": 0, "failed": 0}
    snapshot.assert_awaited_once()
    kwargs = snapshot.await_args.kwargs
    assert kwargs["role"] == "doctor" and kwargs["period_days"] == 45
    assert kwargs["period_end"] - kwargs["period_start"] == timedelta(days=45)

    brief = _brief(session, appointment)
    assert brief.status == PrevisitBriefStatus.READY
    assert brief.summary == "Tóm tắt cho bác sĩ."
    assert brief.sections["changes"] == ["So với giai đoạn trước: ..."]
    assert brief.seen_at is None  # the doctor's "brief mới"


@pytest.mark.asyncio
async def test_tick_leaves_cancelled_far_off_and_unassigned_appointments_alone(session: Session, patient, doctor):
    _appointment(session, patient, doctor, NOW + timedelta(hours=30), AppointmentStatus.CANCELLED)
    _appointment(session, patient, doctor, NOW + timedelta(hours=72))  # outside the 48h lead window
    _appointment(session, patient, doctor, NOW - timedelta(hours=1))  # already started
    stranger = _user(session, "unassigned@diab.example.com", UserRole.DOCTOR)
    _appointment(session, patient, stranger, NOW + timedelta(hours=10))

    with patch(SNAPSHOT, AsyncMock(side_effect=AssertionError("no brief expected"))):
        counts = await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    assert counts == {"generated": 0, "up_to_date": 0, "skipped": 1, "failed": 0}
    assert session.exec(select(PrevisitBrief)).all() == []


@pytest.mark.asyncio
async def test_rescheduled_appointment_gets_a_new_brief(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    first = _brief(session, appointment)
    first_id = first.id

    appointment.scheduled_at = NOW + timedelta(hours=40)
    session.add(appointment)
    session.commit()
    assert briefs.is_stale(appointment, _brief(session, appointment))

    snapshot = _snapshot_mock()
    with patch(SNAPSHOT, snapshot):
        counts = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=1))
    assert counts["generated"] == 1
    brief = _brief(session, appointment)
    assert brief.id == first_id  # same row, regenerated
    assert brief.appointment_scheduled_at == NOW + timedelta(hours=40)


@pytest.mark.asyncio
async def test_cancelling_after_the_brief_stops_regeneration(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    appointment.status = AppointmentStatus.CANCELLED
    appointment.scheduled_at = NOW + timedelta(hours=35)
    session.add(appointment)
    session.commit()
    with patch(SNAPSHOT, AsyncMock(side_effect=AssertionError("cancelled"))):
        counts = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=1))
    assert counts["generated"] == 0


@pytest.mark.asyncio
async def test_new_data_regenerates_only_after_the_refresh_interval(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=47))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    session.add(GlucoseRecord(
        patient_id=patient.id, value=190, measured_at=NOW + timedelta(hours=1), created_by=patient.id,
        created_at=NOW + timedelta(hours=1),
    ))
    session.commit()

    snapshot = _snapshot_mock()
    with patch(SNAPSHOT, snapshot):
        soon = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=2))
        later = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=7))
        after = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=14))
    assert soon["up_to_date"] == 1  # under 6h old
    assert later["generated"] == 1
    assert after["up_to_date"] == 1  # nothing new since the regeneration
    assert _brief(session, appointment).generated_at == NOW + timedelta(hours=7)


@pytest.mark.asyncio
async def test_failure_is_recorded_and_retried(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, AsyncMock(side_effect=RuntimeError("LLM down"))):
        counts = await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    assert counts["failed"] == 1
    assert _brief(session, appointment).status == PrevisitBriefStatus.FAILED

    with patch(SNAPSHOT, _snapshot_mock()):
        counts = await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=1))
    assert counts["generated"] == 1
    assert _brief(session, appointment).status == PrevisitBriefStatus.READY


@pytest.mark.asyncio
async def test_a_failed_regeneration_keeps_the_older_brief_readable(session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.run_previsit_brief_tick(session, now_utc=NOW)
    appointment.scheduled_at = NOW + timedelta(hours=31)
    session.add(appointment)
    session.commit()
    with patch(SNAPSHOT, AsyncMock(side_effect=RuntimeError("LLM down"))):
        await briefs.run_previsit_brief_tick(session, now_utc=NOW + timedelta(hours=1))
    brief = _brief(session, appointment)
    assert brief.status == PrevisitBriefStatus.READY and brief.summary == "Tóm tắt cho bác sĩ."


# --- doctor sections (4.2) ------------------------------------------------------------


def _metrics(count: int, avg: float, **extra) -> dict:
    return {
        "glucose_overall": {"count": count, "avg": avg},
        "glucose_days_logged": count,
        "fasting": {"count": count, "in_range_pct": 50.0},
        "post_prandial": {"count": 0, "over_target_pct": None},
        **extra,
    }


def _previous(metrics: dict) -> dict:
    return {"start": datetime(2026, 8, 1), "end": datetime(2026, 9, 1), "metrics": metrics}


def test_change_lines_compare_with_the_window_before():
    sections = _build_sections(
        _metrics(10, 150.0, low_glucose_count=1),
        {"previous_period": _previous(_metrics(8, 170.0))},
        "doctor",
    )
    changes = sections["changes"]
    assert changes[0].startswith("So với giai đoạn trước (01/08/2026–01/09/2026): ĐH trung bình 170.0 → 150.0 mg/dL")
    assert "(-20.0 mg/dL; n=8 → n=10)" in changes[0]
    assert "Số lần ĐH < 70 mg/dL: 0 → 1." in changes
    assert not any("sau ăn" in line for line in changes)  # no post-meal readings either side


def test_uneven_logging_gets_a_caveat_under_the_headline():
    even = _build_sections(_metrics(10, 150.0), {"previous_period": _previous(_metrics(8, 170.0))}, "doctor")
    assert not any(line.startswith("Lưu ý") for line in even["changes"])

    uneven = _build_sections(_metrics(6, 130.0), {"previous_period": _previous(_metrics(60, 174.0))}, "doctor")
    assert uneven["changes"][1].startswith("Lưu ý: số ngày có đo chênh lệch nhiều giữa hai giai đoạn (60 → 6 ngày)")


def test_every_section_says_when_data_is_missing():
    sections = _build_sections(
        _metrics(10, 150.0),
        {
            "previous_period": _previous(_metrics(1, 170.0)),
            "meal_glucose_response": {"days": 30, "meals_paired": 2, "min_meals_per_group": 3, "by_food": [], "by_carb_load": []},
            "medication_adherence": {"days": 30, "day_rate_pct": None, "medications": [{"drug_name": "Metformin"}]},
        },
        "doctor",
    )
    assert sections["changes"][0].startswith("Chưa đủ dữ liệu để so sánh")
    assert sections["meals"][0].startswith("Chưa đủ dữ liệu: 2 bữa có đo đường huyết sau ăn")
    assert sections["medication_adherence"][0].startswith("Chưa đủ dữ liệu: chưa có ngày nào")
    assert _build_sections(_metrics(10, 150.0), {}, "doctor")["medication_adherence"] == [
        "Chưa có thuốc nào được ghi nhận trong 30 ngày qua."
    ]


def test_patient_summary_has_no_sections():
    assert _build_sections(_metrics(10, 150.0), {"previous_period": _previous(_metrics(8, 170.0))}, "patient") == {}


@pytest.mark.asyncio
async def test_sections_are_returned_even_without_enough_glucose():
    result = await synthesize_summary({
        "intent": "SUMMARY", "user_role": "doctor", "tool_results": {"records": {}},
        "data_completeness": {"sufficient_for_trends": False},
    })
    assert set(result["summary_sections"]) == {"changes", "meals", "medication_adherence"}
    assert all(lines for lines in result["summary_sections"].values())


# --- API (4.3) --------------------------------------------------------------------


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upcoming_list_brief_and_seen(client: AsyncClient, session: Session, patient, doctor):
    with_brief = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    without = _appointment(session, patient, doctor, NOW + timedelta(days=5))
    _appointment(session, patient, doctor, NOW + timedelta(days=9))  # beyond 7 days
    with patch(SNAPSHOT, _snapshot_mock(["A", "B"])):
        await briefs.generate_brief(session, with_brief, now=NOW)
    headers = await _headers(client, doctor)

    upcoming = (await client.get("/api/v1/appointments/upcoming", headers=headers)).json()
    assert [(row["id"], row["brief_state"], row["attention_count"], row["brief_unseen"]) for row in upcoming] == [
        (str(with_brief.id), "ready", 2, True),
        (str(without.id), "pending", None, False),
    ]
    assert upcoming[0]["patient_name"] == "patient"

    detail = await client.get(f"/api/v1/appointments/{with_brief.id}/brief", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["sections"]["meals"] == ["Chưa đủ dữ liệu: ..."]
    assert (await client.get(f"/api/v1/appointments/{without.id}/brief", headers=headers)).status_code == 404

    seen = await client.post(f"/api/v1/appointments/{with_brief.id}/brief/seen", headers=headers)
    assert seen.json()["seen_at"] is not None
    upcoming = (await client.get("/api/v1/appointments/upcoming", headers=headers)).json()
    assert upcoming[0]["brief_unseen"] is False


@pytest.mark.asyncio
async def test_brief_access_is_the_appointments_doctor_only(client: AsyncClient, session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.generate_brief(session, appointment, now=NOW)
    other_doctor = _user(session, "other@diab.example.com", UserRole.DOCTOR)
    url = f"/api/v1/appointments/{appointment.id}/brief"
    assert (await client.get(url, headers=await _headers(client, other_doctor))).status_code == 403
    assert (await client.get(url, headers=await _headers(client, patient))).status_code == 403

    # Reassigned away: the old doctor loses the brief too.
    session.delete(session.exec(select(PatientAssignment)).one())
    session.commit()
    assert (await client.get(url, headers=await _headers(client, doctor))).status_code == 403


@pytest.mark.asyncio
async def test_generate_now_and_not_for_a_finished_appointment(client: AsyncClient, session: Session, patient, doctor):
    far = _appointment(session, patient, doctor, NOW + timedelta(days=6))
    done = _appointment(session, patient, doctor, NOW - timedelta(days=1), AppointmentStatus.COMPLETED)
    headers = await _headers(client, doctor)
    with patch(SNAPSHOT, _snapshot_mock()):
        made = await client.post(f"/api/v1/appointments/{far.id}/brief", headers=headers)
    assert made.status_code == 200, made.text
    assert made.json()["state"] == "ready"
    assert (await client.post(f"/api/v1/appointments/{done.id}/brief", headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_deleting_an_appointment_removes_its_brief(client: AsyncClient, session: Session, patient, doctor):
    appointment = _appointment(session, patient, doctor, NOW + timedelta(hours=30))
    with patch(SNAPSHOT, _snapshot_mock()):
        await briefs.generate_brief(session, appointment, now=NOW)
    # SQLite skips foreign keys unless asked; Postgres enforces them.
    session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    response = await client.delete(f"/api/v1/appointments/{appointment.id}", headers=await _headers(client, doctor))
    assert response.status_code == 204
    assert session.exec(select(PrevisitBrief)).all() == []

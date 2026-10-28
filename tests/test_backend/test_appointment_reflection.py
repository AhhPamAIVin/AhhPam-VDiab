"""Appointment-triggered reflection (Epic 4 open question): a new clinical
note runs reflect(source_kind="appointment") over the visit's period.
reflect() itself is covered in tests/test_models/test_reflect.py; here it is
patched, and these tests check the trigger, the period and the note text."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.appointments.models import Appointment, AppointmentStatus
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.insights import appointment_reflection
from codebase.backend.insights.appointment_reflection import (
    clinical_note_text,
    reflect_on_clinical_note,
    reflection_period,
)
from codebase.backend.patients.models import ClinicalNote, PatientAssignment

PASSWORD = "AdminPass123"
# Taken before tests/conftest.py's autouse fixture swaps it for a no-op.
_BACKGROUND_TASK = appointment_reflection.reflect_on_clinical_note_in_background


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def doctor(session: Session) -> User:
    return _make_user(session, email="doctor@diab.example.com", role=UserRole.DOCTOR)


@pytest.fixture
def patient(session: Session, doctor: User) -> User:
    user = _make_user(session, email="patient@diab.example.com", role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=user.id, assigned_by=doctor.id))
    session.commit()
    return user


def _note(session: Session, patient: User, doctor: User, *, created_at: datetime, **fields) -> ClinicalNote:
    note = ClinicalNote(
        patient_id=patient.id,
        doctor_id=doctor.id,
        reason_for_visit="Tái khám",
        history_of_present_illness="Đường huyết sau ăn còn cao",
        diagnosis="ĐTĐ típ 2",
        treatment_plan="Tiếp tục Metformin",
        created_at=created_at,
        **fields,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def test_note_text_lists_filled_sections_only(session: Session, patient: User, doctor: User):
    note = _note(
        session,
        patient,
        doctor,
        created_at=datetime(2026, 10, 20, 3),
        hba1c_pct=7.8,
        blood_pressure_systolic=135,
        blood_pressure_diastolic=85,
        follow_up_date=date(2026, 11, 20),
    )
    text = clinical_note_text(note)
    assert "Lý do khám: Tái khám" in text
    assert "HbA1c: 7.8%" in text
    assert "Huyết áp tại phòng khám: 135/85 mmHg" in text
    assert "Hẹn tái khám: 2026-11-20" in text
    assert "Tiền sử" not in text and "Cân nặng" not in text


def test_period_uses_the_reviewed_days_in_local_time(session: Session, patient: User, doctor: User):
    note = _note(
        session,
        patient,
        doctor,
        created_at=datetime(2026, 10, 20, 3),
        data_period_start=date(2026, 10, 1),
        data_period_end=date(2026, 10, 19),
    )
    start, end = reflection_period(session, note)
    # Asia/Ho_Chi_Minh midnight = 17:00 UTC the day before.
    assert start == datetime(2026, 9, 30, 17)
    assert end == datetime(2026, 10, 19, 17)


def test_period_runs_from_the_previous_visit(session: Session, patient: User, doctor: User):
    _note(session, patient, doctor, created_at=datetime(2026, 9, 30, 3))
    note = _note(session, patient, doctor, created_at=datetime(2026, 10, 20, 3))
    start, end = reflection_period(session, note)
    assert end == note.created_at
    assert (end - start).days == 20


def test_period_ignores_the_appointment_the_note_documents(session: Session, patient: User, doctor: User):
    previous = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        scheduled_at=datetime(2026, 10, 6, 2),
        status=AppointmentStatus.COMPLETED,
        created_by=doctor.id,
    )
    this_visit = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        scheduled_at=datetime(2026, 10, 20, 2),
        status=AppointmentStatus.COMPLETED,
        created_by=doctor.id,
    )
    session.add_all([previous, this_visit])
    session.commit()
    note = _note(session, patient, doctor, created_at=datetime(2026, 10, 20, 3), appointment_id=this_visit.id)
    start, end = reflection_period(session, note)
    assert (end - start).days == 15  # since 2026-10-06, not the same-day visit (7-day floor would apply)


def test_period_defaults_without_an_earlier_visit(session: Session, patient: User, doctor: User):
    note = _note(session, patient, doctor, created_at=datetime(2026, 10, 20, 3))
    start, end = reflection_period(session, note)
    assert end - start == timedelta(days=30)


async def test_reflect_runs_as_appointment_insight_linked_to_the_note(session: Session, patient: User, doctor: User):
    note = _note(session, patient, doctor, created_at=datetime(2026, 10, 20, 3), hba1c_pct=7.8)
    with patch.object(appointment_reflection, "reflect", AsyncMock(return_value=[])) as fake_reflect:
        await reflect_on_clinical_note(session, note)
    kwargs = fake_reflect.await_args.kwargs
    assert kwargs["source_kind"] == "appointment"
    assert kwargs["source_note_id"] == note.id
    assert kwargs["patient_id"] == patient.id
    assert "HbA1c: 7.8%" in kwargs["note_text"]


async def test_creating_a_note_schedules_the_reflection(
    client: AsyncClient, session: Session, patient: User, doctor: User, monkeypatch
):
    scheduled: list = []

    async def _record(note_id) -> None:
        scheduled.append(note_id)

    monkeypatch.setattr(appointment_reflection, "reflect_on_clinical_note_in_background", _record)
    login = await client.post("/api/v1/auth/login", data={"username": doctor.email, "password": PASSWORD})
    response = await client.post(
        f"/api/v1/doctor/patients/{patient.id}/notes",
        json={
            "reason_for_visit": "Tái khám",
            "history_of_present_illness": "Ổn định",
            "diagnosis": "ĐTĐ típ 2",
            "treatment_plan": "Tiếp tục",
        },
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 201, response.text
    assert [str(note_id) for note_id in scheduled] == [response.json()["id"]]


async def test_background_task_respects_the_flag(monkeypatch):
    from codebase.backend.config import get_settings

    monkeypatch.setattr(get_settings(), "appointment_reflection_enabled", False)
    with patch.object(appointment_reflection, "Session") as fake_session:
        await _BACKGROUND_TASK(uuid4())
    fake_session.assert_not_called()

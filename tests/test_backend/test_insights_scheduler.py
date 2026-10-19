"""Tests for the weekly reflection tick (codebase/backend/insights/
scheduler.py): patient-selection logic (which patients are even due for a
check this week) plus run_weekly_reflection_tick's wiring into the real
reflect() (mocked here — reflect() itself is covered by
tests/test_models/test_reflect.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from codebase.backend.agent.models import ChatSession
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import MealType
from codebase.backend.health.service import create_activity_record, create_glucose_record, create_meal_record
from codebase.backend.insights.scheduler import (
    patients_due_for_chat_reflection,
    patients_due_for_weekly_reflection,
    run_weekly_reflection_tick,
)

PASSWORD = "AdminPass123"


def _make_patient(session: Session, *, email: str, is_active: bool = True, ai_memory_consent: bool = False) -> User:
    user = User(
        email=email,
        full_name=email,
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
        is_active=is_active,
        ai_memory_consent=ai_memory_consent,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_self_chat_session(session: Session, *, patient_id, updated_at) -> ChatSession:
    """A patient's own self-chat session (counterpart_id == patient_id, see
    ChatSession's docstring) touched at `updated_at` — the activity signal
    patients_due_for_chat_reflection checks for."""
    chat_session = ChatSession(
        thread_id=str(uuid.uuid4()), patient_id=patient_id, counterpart_id=patient_id, updated_at=updated_at
    )
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


@pytest.fixture
def period() -> tuple[datetime, datetime]:
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    return now - timedelta(days=7), now


def test_patient_with_new_glucose_record_is_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="p1@diab.example.com")
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=120.0,
        unit="mg/dL",
        measured_at=period_end - timedelta(days=1),
        note=None,
        created_by=patient.id,
    )

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id in due


def test_patient_with_new_meal_record_is_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="p2@diab.example.com")
    create_meal_record(
        session,
        patient_id=patient.id,
        meal_type=MealType.DINNER,
        meal_time=period_end - timedelta(days=1),
        description="Bữa tối",
        note=None,
        created_by=patient.id,
    )

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id in due


def test_patient_with_new_activity_record_is_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="p3@diab.example.com")
    create_activity_record(
        session,
        patient_id=patient.id,
        activity_type="Đi bộ",
        duration_minutes=20,
        activity_time=period_end - timedelta(days=1),
        note=None,
        created_by=patient.id,
    )

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id in due


def test_patient_with_no_records_in_period_is_not_due(session: Session, period: tuple[datetime, datetime]):
    """The whole point of this selection step: skip calling the LLM for a
    patient with nothing new to reflect on."""
    period_start, period_end = period
    patient = _make_patient(session, email="p4@diab.example.com")

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


def test_patient_with_only_records_outside_the_period_is_not_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="p5@diab.example.com")
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=120.0,
        unit="mg/dL",
        measured_at=period_start - timedelta(days=30),  # well before the window
        note=None,
        created_by=patient.id,
    )

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


def test_inactive_patient_never_selected_even_with_new_records(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="p6@diab.example.com", is_active=False)
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=120.0,
        unit="mg/dL",
        measured_at=period_end - timedelta(days=1),
        note=None,
        created_by=patient.id,
    )

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


def test_doctors_are_never_selected(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    doctor = User(
        email="d1@diab.example.com",
        full_name="d1",
        role=UserRole.DOCTOR,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(doctor)
    session.commit()

    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    assert doctor.id not in due


@pytest.mark.asyncio
async def test_run_weekly_reflection_tick_calls_reflect_once_per_due_patient(
    session: Session, period: tuple[datetime, datetime]
):
    _period_start, now = period
    due_patient = _make_patient(session, email="due@diab.example.com")
    create_glucose_record(
        session,
        patient_id=due_patient.id,
        value=120.0,
        unit="mg/dL",
        measured_at=now - timedelta(days=1),
        note=None,
        created_by=due_patient.id,
    )
    not_due_patient = _make_patient(session, email="not-due@diab.example.com")

    with patch("codebase.backend.insights.scheduler.reflect", new_callable=AsyncMock) as mock_reflect:
        result = await run_weekly_reflection_tick(session, now=now)

    assert due_patient.id in result
    assert not_due_patient.id not in result
    mock_reflect.assert_awaited_once()
    call_kwargs = mock_reflect.call_args.kwargs
    assert call_kwargs["note_text"] is None
    assert call_kwargs["source_kind"] == "weekly_batch"
    assert call_kwargs["session"] is session
    assert mock_reflect.call_args.args[0] == due_patient.id


# ---------------------------------------------------------------------------
# source_kind="chat" pass — consent-gated (plan Part 3 of
# /Users/phamminh/.claude/plans/memoized-churning-patterson.md)
# ---------------------------------------------------------------------------


def test_consented_patient_with_chat_activity_is_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="consented-active@diab.example.com", ai_memory_consent=True)
    _make_self_chat_session(session, patient_id=patient.id, updated_at=period_end - timedelta(days=1))

    due = patients_due_for_chat_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id in due


def test_unconsented_patient_with_chat_activity_is_not_due(session: Session, period: tuple[datetime, datetime]):
    """The whole point of the consent gate: a patient who never opted in
    must never be selected for chat mining, even with real chat activity."""
    period_start, period_end = period
    patient = _make_patient(session, email="not-consented@diab.example.com", ai_memory_consent=False)
    _make_self_chat_session(session, patient_id=patient.id, updated_at=period_end - timedelta(days=1))

    due = patients_due_for_chat_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


def test_consented_patient_with_no_chat_activity_is_not_due(session: Session, period: tuple[datetime, datetime]):
    period_start, period_end = period
    patient = _make_patient(session, email="consented-idle@diab.example.com", ai_memory_consent=True)

    due = patients_due_for_chat_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


def test_consented_patient_doctor_thread_activity_does_not_count(session: Session, period: tuple[datetime, datetime]):
    """A doctor's own working thread about this patient (counterpart_id !=
    patient_id) must never count as "this patient chatted this week" —
    consent covers the patient's own self-chat only."""
    period_start, period_end = period
    patient = _make_patient(session, email="consented-doctor-thread@diab.example.com", ai_memory_consent=True)
    doctor = User(
        email="d-chat@diab.example.com",
        full_name="d-chat",
        role=UserRole.DOCTOR,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(doctor)
    session.commit()
    session.add(
        ChatSession(
            thread_id=str(uuid.uuid4()),
            patient_id=patient.id,
            counterpart_id=doctor.id,
            updated_at=period_end - timedelta(days=1),
        )
    )
    session.commit()

    due = patients_due_for_chat_reflection(session, period_start=period_start, period_end=period_end)
    assert patient.id not in due


@pytest.mark.asyncio
async def test_run_weekly_reflection_tick_also_runs_chat_pass_for_consented_patients(
    session: Session, period: tuple[datetime, datetime]
):
    _period_start, now = period
    chat_due_patient = _make_patient(session, email="chat-due@diab.example.com", ai_memory_consent=True)
    _make_self_chat_session(session, patient_id=chat_due_patient.id, updated_at=now - timedelta(days=1))

    with patch("codebase.backend.insights.scheduler.reflect", new_callable=AsyncMock) as mock_reflect:
        await run_weekly_reflection_tick(session, now=now)

    mock_reflect.assert_awaited_once()  # no weekly_batch-due patients this time — only the chat pass fires
    call_kwargs = mock_reflect.call_args.kwargs
    assert call_kwargs["source_kind"] == "chat"
    assert call_kwargs["note_text"] is None
    assert mock_reflect.call_args.args[0] == chat_due_patient.id

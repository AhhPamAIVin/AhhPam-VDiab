"""Appointment-triggered long-term memory (Epic 4, open question): when a
doctor saves a clinical note, run reflect(source_kind="appointment") over
the period that visit reviewed. This is the first of the two PatientInsight
triggers described in insights/scheduler.py — until now only the weekly job
called reflect().

The note text is context for the LLM, never a source of numbers (the
appointment prompt says so; every figure comes from the records gathered for
the period). Runs as a FastAPI BackgroundTask after the note is committed, so
the doctor's save never waits on the LLM, and opens its own session — same
shape as health/meal_analysis.py::analyze_meal_in_background. Only note
creation triggers it: editing a note (a typo fix, an added lab value) does
not cost another LLM run.

Period: the note's own data_period_start/end when the doctor filled both in
(local days, inclusive), otherwise from the previous visit (completed
appointment or earlier note) to the note — the pre-visit brief's window rule
(appointments/briefs.py::brief_window: clamped 7-90 days, default when there
is no earlier visit).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select

from codebase.backend.appointments.briefs import brief_window
from codebase.backend.appointments.models import Appointment, AppointmentStatus
from codebase.backend.config import get_settings
from codebase.backend.db import engine
from codebase.backend.patients.models import ClinicalNote
from codebase.models.agents.nodes.reflect import reflect

logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")


def _local_day_start_utc(day: date) -> datetime:
    tz = ZoneInfo(get_settings().reminder_timezone)
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(_UTC).replace(tzinfo=None)


def _previous_visit_before(session: Session, note: ClinicalNote) -> datetime | None:
    conditions = [
        Appointment.patient_id == note.patient_id,
        Appointment.status == AppointmentStatus.COMPLETED,
        col(Appointment.scheduled_at) < note.created_at,
    ]
    if note.appointment_id is not None:
        # The visit this note documents isn't "the previous visit".
        conditions.append(Appointment.id != note.appointment_id)
    last_appointment = session.exec(select(func.max(Appointment.scheduled_at)).where(*conditions)).one()
    last_note = session.exec(
        select(func.max(ClinicalNote.created_at)).where(
            ClinicalNote.patient_id == note.patient_id,
            ClinicalNote.id != note.id,
            col(ClinicalNote.created_at) < note.created_at,
        )
    ).one()
    visits = [value for value in (last_appointment, last_note) if value is not None]
    return max(visits) if visits else None


def reflection_period(session: Session, note: ClinicalNote) -> tuple[datetime, datetime]:
    """(period_start, period_end), naive UTC like the records' timestamps."""
    if note.data_period_start is not None and note.data_period_end is not None:
        return (
            _local_day_start_utc(note.data_period_start),
            _local_day_start_utc(note.data_period_end + timedelta(days=1)),
        )
    period_start, period_end, _ = brief_window(_previous_visit_before(session, note), note.created_at)
    return period_start, period_end


def clinical_note_text(note: ClinicalNote) -> str:
    """The note's filled-in sections as labelled lines, for the prompt."""
    blood_pressure = (
        f"{note.blood_pressure_systolic}/{note.blood_pressure_diastolic} mmHg"
        if note.blood_pressure_systolic is not None and note.blood_pressure_diastolic is not None
        else None
    )
    sections = [
        ("Lý do khám", note.reason_for_visit),
        ("Bệnh sử", note.history_of_present_illness),
        ("Tiền sử", note.past_medical_history),
        ("Cân nặng", f"{note.weight_kg:g} kg" if note.weight_kg is not None else None),
        ("Huyết áp tại phòng khám", blood_pressure),
        ("Khám lâm sàng", note.clinical_exam_findings),
        ("HbA1c", f"{note.hba1c_pct:g}%" if note.hba1c_pct is not None else None),
        ("Cận lâm sàng khác", note.paraclinical_results),
        ("Chẩn đoán", note.diagnosis),
        ("Hướng điều trị", note.treatment_plan),
        ("Hẹn tái khám", note.follow_up_date.isoformat() if note.follow_up_date else None),
    ]
    return "\n".join(f"{label}: {value}" for label, value in sections if value not in (None, ""))


async def reflect_on_clinical_note(session: Session, note: ClinicalNote) -> list[dict]:
    period_start, period_end = reflection_period(session, note)
    return await reflect(
        patient_id=note.patient_id,
        period_start=period_start,
        period_end=period_end,
        note_text=clinical_note_text(note),
        source_kind="appointment",
        session=session,
        source_note_id=note.id,
    )


async def reflect_on_clinical_note_in_background(note_id: UUID) -> None:
    if not get_settings().appointment_reflection_enabled:
        return
    with Session(engine) as session:
        note = session.get(ClinicalNote, note_id)
        if note is None:  # deleted before the task ran
            return
        try:
            insights = await reflect_on_clinical_note(session, note)
            logger.info("appointment reflection: note_id=%s insights=%d", note_id, len(insights))
        except Exception:
            session.rollback()
            # The id only — the note is PHI.
            logger.exception("appointment reflection failed for note_id=%s", note_id)

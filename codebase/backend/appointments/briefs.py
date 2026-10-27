"""Pre-visit brief generation (Epic 4.1).

run_previsit_brief_tick() is the hourly job (insights/scheduler.py): every
SCHEDULED appointment in the next settings.previsit_brief_lead_hours whose
doctor is still assigned to the patient gets one PrevisitBrief —

1. the window runs from the last visit (latest completed appointment or
   clinical note before this appointment) to now, clamped to 7-90 days;
   settings.previsit_brief_default_days when there is no earlier visit;
2. the doctor AI Summary for exactly that window
   (summaries.service.generate_and_store_snapshot, already guardrailed) is
   copied into the brief, with its "changes"/"meals"/"medication_adherence"
   sections (Epic 4.2);
3. seen_at is cleared, which is what the doctor's dashboard shows as a new
   brief — doctors have no notification inbox of their own.

A brief is regenerated (same row) when the appointment was rescheduled, when
the last attempt failed, or when the patient logged new data since it was
made and it is at least settings.previsit_brief_refresh_hours old. A
cancelled or completed appointment never gets one. One appointment's
failure is logged (no PHI) and the batch carries on.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, func, select

from codebase.backend.appointments.models import (
    Appointment,
    AppointmentStatus,
    PrevisitBrief,
    PrevisitBriefStatus,
)
from codebase.backend.config import get_settings
from codebase.backend.health.models import ActivityRecord, BloodPressureRecord, GlucoseRecord, MealRecord
from codebase.backend.medications.models import MedicationNote
from codebase.backend.patients.models import ClinicalNote
from codebase.backend.patients.service import is_doctor_assigned
from codebase.backend.summaries.service import generate_and_store_snapshot
from codebase.models.agents.nodes.fetch_data import DOCTOR_SUMMARY_MAX_DAYS

logger = logging.getLogger(__name__)

MIN_PERIOD_DAYS = 7
_NEW_DATA_MODELS = (GlucoseRecord, BloodPressureRecord, MealRecord, ActivityRecord, MedicationNote)


def get_brief(session: Session, appointment_id: UUID) -> PrevisitBrief | None:
    return session.exec(select(PrevisitBrief).where(PrevisitBrief.appointment_id == appointment_id)).first()


def previous_visit_at(session: Session, appointment: Appointment) -> datetime | None:
    last_appointment = session.exec(
        select(func.max(Appointment.scheduled_at)).where(
            Appointment.patient_id == appointment.patient_id,
            Appointment.status == AppointmentStatus.COMPLETED,
            Appointment.id != appointment.id,
            col(Appointment.scheduled_at) < appointment.scheduled_at,
        )
    ).one()
    last_note = session.exec(
        select(func.max(ClinicalNote.created_at)).where(
            ClinicalNote.patient_id == appointment.patient_id,
            col(ClinicalNote.created_at) < appointment.scheduled_at,
        )
    ).one()
    visits = [value for value in (last_appointment, last_note) if value is not None]
    return max(visits) if visits else None


def brief_window(previous_visit: datetime | None, now: datetime) -> tuple[datetime, datetime, int]:
    """(period_start, period_end, period_days) — whole days back from now."""
    if previous_visit is None:
        days = get_settings().previsit_brief_default_days
    else:
        days = math.ceil((now - previous_visit).total_seconds() / 86400)
    days = max(MIN_PERIOD_DAYS, min(days, DOCTOR_SUMMARY_MAX_DAYS))
    return now - timedelta(days=days), now, days


def has_new_data_since(session: Session, patient_id: UUID, since: datetime) -> bool:
    for model in _NEW_DATA_MODELS:
        found = session.exec(
            select(model.id).where(model.patient_id == patient_id, col(model.created_at) > since).limit(1)
        ).first()
        if found is not None:
            return True
    return False


def due_reason(session: Session, appointment: Appointment, brief: PrevisitBrief | None, now: datetime) -> str | None:
    """Why this appointment needs a (new) brief now, or None."""
    if appointment.status != AppointmentStatus.SCHEDULED:
        return None
    if brief is None:
        return "new"
    if brief.status == PrevisitBriefStatus.FAILED:
        return "retry"
    if brief.appointment_scheduled_at != appointment.scheduled_at:
        return "rescheduled"
    refresh_after = timedelta(hours=get_settings().previsit_brief_refresh_hours)
    if now - brief.generated_at >= refresh_after and has_new_data_since(session, appointment.patient_id, brief.generated_at):
        return "new_data"
    return None


def is_stale(appointment: Appointment, brief: PrevisitBrief) -> bool:
    """Made for a time the appointment no longer has (rescheduled since)."""
    return brief.appointment_scheduled_at != appointment.scheduled_at


async def generate_brief(session: Session, appointment: Appointment, *, now: datetime) -> PrevisitBrief:
    """Create or regenerate the appointment's brief. On an LLM/DB failure the
    brief is marked FAILED only if it had no content yet — an older ready
    brief stays readable — and the error is re-raised."""
    brief = get_brief(session, appointment.id)
    previous_visit = previous_visit_at(session, appointment)
    period_start, period_end, period_days = brief_window(previous_visit, now)
    try:
        snapshot = await generate_and_store_snapshot(
            session,
            patient_id=appointment.patient_id,
            role="doctor",
            period_days=period_days,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception:
        session.rollback()
        if brief is None:
            session.add(
                PrevisitBrief(
                    appointment_id=appointment.id,
                    patient_id=appointment.patient_id,
                    doctor_id=appointment.doctor_id,
                    appointment_scheduled_at=appointment.scheduled_at,
                    previous_visit_at=previous_visit,
                    period_start=period_start,
                    period_end=period_end,
                    period_days=period_days,
                    status=PrevisitBriefStatus.FAILED,
                    generated_at=now,
                )
            )
            session.commit()
        raise

    if brief is None:
        brief = PrevisitBrief(
            appointment_id=appointment.id,
            patient_id=appointment.patient_id,
            doctor_id=appointment.doctor_id,
            appointment_scheduled_at=appointment.scheduled_at,
            period_start=period_start,
            period_end=period_end,
            period_days=period_days,
            status=PrevisitBriefStatus.READY,
        )
    brief.doctor_id = appointment.doctor_id
    brief.snapshot_id = snapshot.id
    brief.appointment_scheduled_at = appointment.scheduled_at
    brief.previous_visit_at = previous_visit
    brief.period_start, brief.period_end = snapshot.period_start, snapshot.period_end
    brief.period_days = period_days
    brief.summary = snapshot.summary
    brief.trends = list(snapshot.trends or [])
    brief.attention_points = list(snapshot.attention_points or [])
    brief.limitations = list(snapshot.limitations or [])
    brief.sections = dict(snapshot.sections or {})
    brief.data_completeness = dict(snapshot.data_completeness or {})
    brief.status = PrevisitBriefStatus.READY
    brief.generated_at = now
    brief.seen_at = None
    session.add(brief)
    session.commit()
    session.refresh(brief)
    return brief


def upcoming_appointments(session: Session, *, now: datetime, until: datetime, doctor_id: UUID | None = None) -> list[Appointment]:
    statement = select(Appointment).where(
        Appointment.status == AppointmentStatus.SCHEDULED,
        col(Appointment.scheduled_at) > now,
        col(Appointment.scheduled_at) <= until,
    )
    if doctor_id is not None:
        statement = statement.where(Appointment.doctor_id == doctor_id)
    return list(session.exec(statement.order_by(col(Appointment.scheduled_at))).all())


def briefs_for(session: Session, appointment_ids: list[UUID]) -> dict[UUID, PrevisitBrief]:
    if not appointment_ids:
        return {}
    rows = session.exec(select(PrevisitBrief).where(col(PrevisitBrief.appointment_id).in_(appointment_ids))).all()
    return {row.appointment_id: row for row in rows}


async def run_previsit_brief_tick(session: Session, *, now_utc: datetime) -> dict:
    """Prepare briefs for the appointments in the lead window. Returns counts only."""
    until = now_utc + timedelta(hours=get_settings().previsit_brief_lead_hours)
    appointments = upcoming_appointments(session, now=now_utc, until=until)
    briefs = briefs_for(session, [appointment.id for appointment in appointments])
    counts = {"generated": 0, "up_to_date": 0, "skipped": 0, "failed": 0}
    for appointment in appointments:
        if not is_doctor_assigned(session, doctor_id=appointment.doctor_id, patient_id=appointment.patient_id):
            counts["skipped"] += 1  # the doctor could not open it anyway
            continue
        if due_reason(session, appointment, briefs.get(appointment.id), now_utc) is None:
            counts["up_to_date"] += 1
            continue
        try:
            await generate_brief(session, appointment, now=now_utc)
            counts["generated"] += 1
        except Exception:
            session.rollback()
            counts["failed"] += 1
            logger.exception("previsit brief failed for appointment_id=%s", appointment.id)
    logger.info("previsit brief tick %s", counts)
    return counts


def mark_seen(session: Session, brief: PrevisitBrief, now: datetime) -> PrevisitBrief:
    if brief.seen_at is None:
        brief.seen_at = now
        session.add(brief)
        session.commit()
        session.refresh(brief)
    return brief

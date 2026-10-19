from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from codebase.backend.appointments.models import Appointment, AppointmentStatus


def _day_bounds(on_date: date) -> tuple[datetime, datetime]:
    day_start = datetime(on_date.year, on_date.month, on_date.day)
    return day_start, day_start + timedelta(days=1)


def create_appointment(
    session: Session,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    scheduled_at: datetime,
    reason: str | None,
    notes: str | None,
) -> Appointment:
    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        scheduled_at=scheduled_at,
        reason=reason,
        notes=notes,
        created_by=doctor_id,
    )
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


def list_appointments_for_patient(
    session: Session, patient_id: UUID, *, on_date: date | None = None
) -> list[Appointment]:
    statement = select(Appointment).where(Appointment.patient_id == patient_id)
    if on_date is not None:
        day_start, day_end = _day_bounds(on_date)
        statement = statement.where(col(Appointment.scheduled_at) >= day_start, col(Appointment.scheduled_at) < day_end)
    return list(session.exec(statement.order_by(col(Appointment.scheduled_at))).all())


def list_appointments_for_doctor(
    session: Session, doctor_id: UUID, *, on_date: date | None = None
) -> list[Appointment]:
    statement = select(Appointment).where(Appointment.doctor_id == doctor_id)
    if on_date is not None:
        day_start, day_end = _day_bounds(on_date)
        statement = statement.where(col(Appointment.scheduled_at) >= day_start, col(Appointment.scheduled_at) < day_end)
    return list(session.exec(statement.order_by(col(Appointment.scheduled_at))).all())


def get_appointment_or_404(session: Session, appointment_id: UUID) -> Appointment:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    return appointment


def update_status(session: Session, *, appointment: Appointment, new_status: AppointmentStatus) -> Appointment:
    appointment.status = new_status
    appointment.updated_at = datetime.utcnow()
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


def update_appointment(
    session: Session,
    *,
    appointment: Appointment,
    scheduled_at: datetime,
    reason: str | None,
    new_status: AppointmentStatus,
    notes: str | None,
) -> Appointment:
    appointment.scheduled_at = scheduled_at
    appointment.reason = reason
    appointment.status = new_status
    appointment.notes = notes
    appointment.updated_at = datetime.utcnow()
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


def delete_appointment(session: Session, appointment: Appointment) -> None:
    session.delete(appointment)
    session.commit()

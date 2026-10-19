from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from codebase.backend.appointments import service
from codebase.backend.appointments.models import Appointment
from codebase.backend.appointments.schemas import (
    AppointmentCreate,
    AppointmentPublic,
    AppointmentStatusUpdate,
    AppointmentUpdate,
)
from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.patients.access import ensure_patient_access

router = APIRouter()


@router.post("", response_model=AppointmentPublic, status_code=status.HTTP_201_CREATED)
def create_appointment(
    request: AppointmentCreate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    session: Session = Depends(get_session),
) -> Appointment:
    """Schedule a follow-up appointment for a patient assigned to the current doctor."""
    ensure_patient_access(session, current_user, request.patient_id)
    return service.create_appointment(
        session,
        patient_id=request.patient_id,
        doctor_id=current_user.id,
        scheduled_at=request.scheduled_at,
        reason=request.reason,
        notes=request.notes,
    )


@router.get("", response_model=list[AppointmentPublic])
def list_appointments(
    patient_id: UUID | None = Query(default=None),
    on_date: date | None = Query(default=None, description="Only appointments scheduled on this calendar day"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Appointment]:
    """Patient: own appointments. Doctor without patient_id: schedule across all assigned
    patients. Doctor/Admin with patient_id: that patient's appointments (access-checked).
    `on_date` narrows any of the above to a single calendar day, e.g. "who am I seeing today"."""
    if current_user.role == UserRole.PATIENT:
        target_patient_id = patient_id or current_user.id
        ensure_patient_access(session, current_user, target_patient_id)
        return service.list_appointments_for_patient(session, target_patient_id, on_date=on_date)

    if patient_id is not None:
        ensure_patient_access(session, current_user, patient_id)
        return service.list_appointments_for_patient(session, patient_id, on_date=on_date)

    if current_user.role == UserRole.DOCTOR:
        return service.list_appointments_for_doctor(session, current_user.id, on_date=on_date)

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_id is required")


@router.patch("/{appointment_id}/status", response_model=AppointmentPublic)
def update_appointment_status(
    appointment_id: UUID,
    request: AppointmentStatusUpdate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    session: Session = Depends(get_session),
) -> Appointment:
    """Mark an appointment completed/cancelled (only the assigned doctor may update it)."""
    appointment = service.get_appointment_or_404(session, appointment_id)
    if appointment.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your appointment")
    return service.update_status(session, appointment=appointment, new_status=request.status)


@router.put("/{appointment_id}", response_model=AppointmentPublic)
def update_appointment(
    appointment_id: UUID,
    request: AppointmentUpdate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    session: Session = Depends(get_session),
) -> Appointment:
    """Update an appointment owned by the current doctor."""
    appointment = service.get_appointment_or_404(session, appointment_id)
    if appointment.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your appointment")
    return service.update_appointment(
        session,
        appointment=appointment,
        scheduled_at=request.scheduled_at,
        reason=request.reason,
        new_status=request.status,
        notes=request.notes,
    )


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(
    appointment_id: UUID,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    session: Session = Depends(get_session),
) -> None:
    """Delete an appointment owned by the current doctor."""
    appointment = service.get_appointment_or_404(session, appointment_id)
    if appointment.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your appointment")
    service.delete_appointment(session, appointment)

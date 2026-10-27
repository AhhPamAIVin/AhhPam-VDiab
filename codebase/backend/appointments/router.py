from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, col, select

from codebase.backend.appointments import briefs, service
from codebase.backend.appointments.models import Appointment, AppointmentStatus, PrevisitBrief, PrevisitBriefStatus
from codebase.backend.appointments.schemas import (
    AppointmentCreate,
    AppointmentPublic,
    AppointmentStatusUpdate,
    AppointmentUpdate,
    BriefState,
    PrevisitBriefPublic,
    UpcomingAppointmentPublic,
)
from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.patients.access import ensure_patient_access
from codebase.backend.security.rate_limit import limit_agent

router = APIRouter()

_doctor_only = require_roles(UserRole.DOCTOR)


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


# ---- Pre-visit briefs (Epic 4) — the appointment's own doctor only ----


def _brief_state(appointment: Appointment, brief: PrevisitBrief | None) -> BriefState:
    if brief is None:
        return "pending"
    if brief.status == PrevisitBriefStatus.FAILED:
        return "failed"
    return "stale" if briefs.is_stale(appointment, brief) else "ready"


def _brief_public(appointment: Appointment, brief: PrevisitBrief) -> PrevisitBriefPublic:
    return PrevisitBriefPublic(**brief.model_dump(), state=_brief_state(appointment, brief))


def _own_appointment(session: Session, current_user: User, appointment_id: UUID) -> Appointment:
    appointment = service.get_appointment_or_404(session, appointment_id)
    if appointment.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your appointment")
    ensure_patient_access(session, current_user, appointment.patient_id)
    return appointment


@router.get("/upcoming", response_model=list[UpcomingAppointmentPublic])
def list_upcoming_with_briefs(
    days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> list[UpcomingAppointmentPublic]:
    """Scheduled appointments in the next `days`, with each brief's state and
    attention-point count, soonest first; the dashboard ranks them."""
    now = datetime.utcnow()
    appointments = briefs.upcoming_appointments(
        session, now=now, until=now + timedelta(days=days), doctor_id=current_user.id
    )
    by_appointment = briefs.briefs_for(session, [appointment.id for appointment in appointments])
    patient_ids = {appointment.patient_id for appointment in appointments}
    names = dict(session.exec(select(User.id, User.full_name).where(col(User.id).in_(patient_ids))).all()) if patient_ids else {}
    rows = []
    for appointment in appointments:
        brief = by_appointment.get(appointment.id)
        state = _brief_state(appointment, brief)
        has_content = state in ("ready", "stale")
        rows.append(
            UpcomingAppointmentPublic(
                id=appointment.id,
                patient_id=appointment.patient_id,
                patient_name=names.get(appointment.patient_id, ""),
                scheduled_at=appointment.scheduled_at,
                reason=appointment.reason,
                brief_state=state,
                brief_generated_at=brief.generated_at if has_content else None,
                brief_unseen=has_content and brief.seen_at is None,
                attention_count=len(brief.attention_points or []) if has_content else None,
            )
        )
    return rows


@router.get("/{appointment_id}/brief", response_model=PrevisitBriefPublic)
def get_previsit_brief(
    appointment_id: UUID,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> PrevisitBriefPublic:
    appointment = _own_appointment(session, current_user, appointment_id)
    brief = briefs.get_brief(session, appointment.id)
    if brief is None or brief.status == PrevisitBriefStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chưa có brief cho lịch khám này.")
    return _brief_public(appointment, brief)


@router.post("/{appointment_id}/brief", response_model=PrevisitBriefPublic, dependencies=[Depends(limit_agent)])
async def generate_previsit_brief(
    appointment_id: UUID,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> PrevisitBriefPublic:
    """Make (or remake) the brief now — for an appointment further out than
    the hourly job looks, or one the job hasn't reached yet."""
    appointment = _own_appointment(session, current_user, appointment_id)
    if appointment.status != AppointmentStatus.SCHEDULED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chỉ tạo brief cho lịch khám chưa diễn ra.")
    try:
        brief = await briefs.generate_brief(session, appointment, now=datetime.utcnow())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Chưa tạo được brief lúc này, vui lòng thử lại sau."
        ) from None
    return _brief_public(appointment, brief)


@router.post("/{appointment_id}/brief/seen", response_model=PrevisitBriefPublic)
def mark_previsit_brief_seen(
    appointment_id: UUID,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> PrevisitBriefPublic:
    appointment = _own_appointment(session, current_user, appointment_id)
    brief = briefs.get_brief(session, appointment.id)
    if brief is None or brief.status == PrevisitBriefStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chưa có brief cho lịch khám này.")
    return _brief_public(appointment, briefs.mark_seen(session, brief, datetime.utcnow()))


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

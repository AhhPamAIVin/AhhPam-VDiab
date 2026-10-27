from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from codebase.backend.audit.service import record_patient_access
from codebase.backend.auth.models import User, UserRole
from codebase.backend.patients.service import is_doctor_assigned


def ensure_patient_access(session: Session, current_user: User, patient_id: UUID) -> None:
    """Raise 403 unless current_user may read/write this patient's data.

    Admin: always allowed. Patient: only their own data. Doctor: only patients
    currently assigned to them. Single point of truth reused by appointments
    and health records so patient-data isolation isn't reimplemented per route
    — and therefore also where a doctor/admin touching a patient's data, or
    anyone being denied, is written to the audit trail (task 6.1).
    """
    if current_user.role == UserRole.ADMIN:
        allowed, detail = True, None
    elif current_user.role == UserRole.PATIENT:
        allowed, detail = current_user.id == patient_id, "Cannot access another patient's data"
    elif current_user.role == UserRole.DOCTOR:
        allowed = is_doctor_assigned(session, doctor_id=current_user.id, patient_id=patient_id)
        detail = "Patient is not assigned to you"
    else:
        allowed, detail = False, "Not enough permissions"
    record_patient_access(session, current_user, patient_id, allowed=allowed)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

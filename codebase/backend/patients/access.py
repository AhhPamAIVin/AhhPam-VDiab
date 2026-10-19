from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.patients.service import is_doctor_assigned


def ensure_patient_access(session: Session, current_user: User, patient_id: UUID) -> None:
    """Raise 403 unless current_user may read/write this patient's data.

    Admin: always allowed. Patient: only their own data. Doctor: only patients
    currently assigned to them. Single point of truth reused by appointments
    and health records so patient-data isolation isn't reimplemented per route.
    """
    if current_user.role == UserRole.ADMIN:
        return
    if current_user.role == UserRole.PATIENT:
        if current_user.id != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access another patient's data")
        return
    if current_user.role == UserRole.DOCTOR:
        if not is_doctor_assigned(session, doctor_id=current_user.id, patient_id=patient_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Patient is not assigned to you")
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")

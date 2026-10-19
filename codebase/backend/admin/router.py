from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from codebase.backend.auth import service
from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.schemas import ResetPasswordRequest, ResetPasswordResponse, UserCreate, UserPublic
from codebase.backend.db import get_session
from codebase.backend.patients import service as patients_service
from codebase.backend.patients.models import PatientAssignment
from codebase.backend.patients.schemas import AssignmentCreate, AssignmentPublic

router = APIRouter(dependencies=[Depends(require_roles(UserRole.ADMIN))])


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(request: UserCreate, session: Session = Depends(get_session)) -> User:
    """Create an Admin/Doctor/Patient account with a temporary password (Admin only)."""
    return service.create_user(
        session,
        email=request.email,
        full_name=request.full_name,
        role=request.role,
        temporary_password=request.temporary_password,
    )


@router.get("/users", response_model=list[UserPublic])
def list_users(session: Session = Depends(get_session)) -> list[User]:
    """List all accounts (Admin only)."""
    return service.list_users(session)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_password(
    user_id: UUID,
    request: ResetPasswordRequest,
    session: Session = Depends(get_session),
) -> ResetPasswordResponse:
    """Reset a user's password (Admin only); invalidates any tokens already issued to them."""
    user = service.get_user_or_404(session, user_id)
    new_password = service.reset_password(session, user=user, temporary_password=request.temporary_password)
    return ResetPasswordResponse(temporary_password=new_password)


@router.post("/patient-assignments", response_model=AssignmentPublic, status_code=status.HTTP_201_CREATED)
def assign_patient(
    request: AssignmentCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PatientAssignment:
    """Assign a patient to a doctor (Admin only)."""
    return patients_service.assign_patient(
        session, doctor_id=request.doctor_id, patient_id=request.patient_id, assigned_by=current_user.id
    )


@router.get("/patient-assignments", response_model=list[AssignmentPublic])
def list_patient_assignments(
    doctor_id: UUID | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[PatientAssignment]:
    """List all patient-doctor assignments (Admin only), optionally filtered by doctor."""
    return patients_service.list_assignments(session, doctor_id=doctor_id)


@router.delete("/patient-assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_patient(assignment_id: UUID, session: Session = Depends(get_session)) -> None:
    """Remove a patient-doctor assignment (Admin only)."""
    patients_service.unassign_patient(session, assignment_id)

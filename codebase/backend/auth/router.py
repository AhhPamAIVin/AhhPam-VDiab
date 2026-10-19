from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from codebase.backend.auth import service
from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.schemas import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    TokenResponse,
    UpdateMeRequest,
    UserPublic,
)
from codebase.backend.db import get_session

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Authenticate with email (as the OAuth2 'username' field) and password."""
    user = service.authenticate_user(session, email=form_data.username, password=form_data.password)
    return TokenResponse(access_token=service.issue_token_for(user))


@router.get("/me", response_model=UserPublic)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserPublic)
def update_current_user(
    request: UpdateMeRequest,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    session: Session = Depends(get_session),
) -> User:
    """Self-service profile settings — currently just `ai_memory_consent`
    (see PatientInsight source_kind="chat", insights/scheduler.py). Patient-
    only: this is the patient's own consent to grant, not something a doctor
    or admin sets on their behalf."""
    current_user.ai_memory_consent = request.ai_memory_consent
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ChangePasswordResponse:
    """Change the caller's own password. A fresh token is returned since the old one is invalidated."""
    user = service.change_own_password(
        session,
        user=current_user,
        current_password=request.current_password,
        new_password=request.new_password,
    )
    return ChangePasswordResponse(access_token=service.issue_token_for(user))

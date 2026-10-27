from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from codebase.backend.audit import service as audit
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
from codebase.backend.security.rate_limit import limit_login

router = APIRouter()


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(limit_login)])
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> TokenResponse:
    """Authenticate with email (as the OAuth2 'username' field) and password.
    Both outcomes are audited; a failure names the account when the email
    exists (the email itself is never stored), so repeated failures against
    one account are visible."""
    try:
        user = service.authenticate_user(session, email=form_data.username, password=form_data.password)
    except HTTPException:
        known = service.find_user_by_email(session, form_data.username)
        audit.record(
            session,
            "auth.login",
            actor_id=known.id if known else None,
            actor_role=known.role.value if known else "anonymous",
            outcome="failure",
        )
        raise
    audit.record(session, "auth.login", actor_id=user.id, actor_role=user.role.value)
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
    """Self-service consent settings — `ai_memory_consent` (see
    PatientInsight source_kind="chat", insights/scheduler.py) and
    `meal_photo_ai_consent` (POST /health/meals/photo-analysis). Patient-
    only: this is the patient's own consent to grant, not something a doctor
    or admin sets on their behalf."""
    for field, value in request.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
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

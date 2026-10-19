from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import (
    create_access_token,
    generate_temporary_password,
    hash_password,
    verify_password,
)


def authenticate_user(session: Session, *, email: str, password: str) -> User:
    """Verify email/password and return the user, or raise 401/403."""
    user = session.exec(select(User).where(User.email == email.lower())).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    return user


def issue_token_for(user: User) -> str:
    return create_access_token(subject=str(user.id), role=user.role, token_version=user.token_version)


def get_user_or_404(session: Session, user_id: UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def list_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(col(User.created_at))).all())


def create_user(session: Session, *, email: str, full_name: str, role: UserRole, temporary_password: str) -> User:
    """Create a new account with a temporary password the user must change on first login."""
    email = email.lower()
    if session.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=email,
        full_name=full_name,
        role=role,
        hashed_password=hash_password(temporary_password),
        must_change_password=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_password(session: Session, *, user: User, temporary_password: str | None) -> str:
    """Set a new (temp) password for a user and invalidate any tokens already issued to them."""
    new_password = temporary_password or generate_temporary_password()
    user.hashed_password = hash_password(new_password)
    user.must_change_password = True
    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    return new_password


def change_own_password(session: Session, *, user: User, current_password: str, new_password: str) -> User:
    """Change the caller's own password after verifying the current one."""
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

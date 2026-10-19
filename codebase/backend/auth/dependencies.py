from collections.abc import Callable
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import decode_access_token
from codebase.backend.db import get_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Decode the JWT and load the user, rejecting stale tokens invalidated by a password change/reset."""
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
        token_version = int(payload["tv"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _CREDENTIALS_ERROR from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != token_version:
        raise _CREDENTIALS_ERROR
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Build a dependency that only allows the given roles, e.g. Depends(require_roles(UserRole.ADMIN))."""

    def _check_role(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
        return current_user

    return _check_role

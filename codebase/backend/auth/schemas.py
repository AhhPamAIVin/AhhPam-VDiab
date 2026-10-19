from uuid import UUID

from pydantic import EmailStr
from sqlmodel import Field, SQLModel

from codebase.backend.auth.models import UserRole
from codebase.backend.time_utils import UtcDatetime


class TokenResponse(SQLModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type for the Authorization header")


class UserPublic(SQLModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    must_change_password: bool
    ai_memory_consent: bool
    created_at: UtcDatetime


class UpdateMeRequest(SQLModel):
    ai_memory_consent: bool


class UserCreate(SQLModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    role: UserRole
    temporary_password: str = Field(..., min_length=8, max_length=72)


class ChangePasswordRequest(SQLModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=72)


class ChangePasswordResponse(SQLModel):
    access_token: str = Field(..., description="New JWT issued after the password change")
    token_type: str = Field(default="bearer")


class ResetPasswordRequest(SQLModel):
    temporary_password: str | None = Field(
        default=None,
        min_length=8,
        max_length=72,
        description="If omitted, the server generates a random temporary password.",
    )


class ResetPasswordResponse(SQLModel):
    temporary_password: str = Field(..., description="Share this with the user out of band; it is not stored.")

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Boolean, Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    DOCTOR = "doctor"
    PATIENT = "patient"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    hashed_password: str
    full_name: str = Field(max_length=255)
    role: UserRole = Field(sa_column=Column(SAEnum(UserRole, name="user_role"), nullable=False))
    is_active: bool = Field(default=True)
    must_change_password: bool = Field(default=False)
    token_version: int = Field(default=0)
    zalo_chat_id: str | None = Field(default=None, max_length=128)
    # Patient-granted consent to mine their own AI chat sessions for
    # long-term memory (source_kind="chat" on PatientInsight — see
    # insights/scheduler.py's weekly chat pass). Opt-in, patient-facing only,
    # never shown to doctors — /Users/phamminh/.claude/plans/
    # memoized-churning-patterson.md Part 3. NOT NULL needs a server_default
    # since db.py's additive-only auto-migration (no Alembic in this repo)
    # refuses to add a NOT NULL column without one — same precedent as
    # insights/models.py's InsightStatus column.
    ai_memory_consent: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    # Patient's own consent to send meal photos to the external vision model
    # (Epic 1); asked on first use, revocable from the meals page.
    meal_photo_ai_consent: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="false")
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

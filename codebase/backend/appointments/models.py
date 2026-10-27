from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Appointment(SQLModel, table=True):
    __tablename__ = "appointments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    doctor_id: UUID = Field(foreign_key="users.id", index=True)
    scheduled_at: datetime
    reason: str | None = Field(default=None, max_length=500)
    status: AppointmentStatus = Field(
        sa_column=Column(
            SAEnum(AppointmentStatus, name="appointment_status"),
            nullable=False,
            default=AppointmentStatus.SCHEDULED,
        )
    )
    notes: str | None = Field(default=None, max_length=2000)
    created_by: UUID = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PrevisitBriefStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"  # generation failed; the next tick retries it


class PrevisitBrief(SQLModel, table=True):
    """The doctor's summary prepared before one appointment (Epic 4.1), one
    row per appointment, regenerated in place (appointments/briefs.py).

    The content is copied from the doctor AI Summary snapshot it was made
    from (snapshot_id): snapshots are upserted per day and window, so a later
    refresh must not change a brief the doctor may already have read.
    `appointment_scheduled_at` is the appointment time the brief was made
    for — a rescheduled appointment no longer matches it and gets a new one.
    `seen_at` is cleared on every (re)generation: an unseen brief is the
    doctor's in-app "brief mới" signal."""

    __tablename__ = "previsit_briefs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    appointment_id: UUID = Field(foreign_key="appointments.id", unique=True, index=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    doctor_id: UUID = Field(foreign_key="users.id", index=True)
    snapshot_id: UUID | None = Field(default=None, foreign_key="agent_summary_snapshots.id")
    appointment_scheduled_at: datetime
    previous_visit_at: datetime | None = Field(default=None)
    period_start: datetime
    period_end: datetime
    period_days: int
    summary: str = Field(default="")
    trends: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    attention_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    limitations: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    sections: dict = Field(default_factory=dict, sa_column=Column(JSON))
    data_completeness: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: PrevisitBriefStatus = Field(
        sa_column=Column(SAEnum(PrevisitBriefStatus, name="previsit_brief_status"), nullable=False)
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    seen_at: datetime | None = Field(default=None)

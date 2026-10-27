from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlmodel import Field, SQLModel

from codebase.backend.appointments.models import AppointmentStatus
from codebase.backend.time_utils import UtcDatetime


class AppointmentCreate(SQLModel):
    patient_id: UUID
    scheduled_at: datetime
    reason: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentStatusUpdate(SQLModel):
    status: AppointmentStatus


class AppointmentUpdate(SQLModel):
    scheduled_at: datetime
    reason: str | None = Field(default=None, max_length=500)
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentPublic(SQLModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    scheduled_at: UtcDatetime
    reason: str | None
    status: AppointmentStatus
    notes: str | None
    created_by: UUID
    created_at: UtcDatetime
    updated_at: UtcDatetime


# "ready": up to date · "stale": made before the appointment was rescheduled ·
# "failed": last attempt failed (retried hourly) · "pending": none yet.
BriefState = Literal["ready", "stale", "failed", "pending"]


class PrevisitBriefPublic(SQLModel):
    id: UUID
    appointment_id: UUID
    patient_id: UUID
    state: BriefState
    appointment_scheduled_at: UtcDatetime
    previous_visit_at: UtcDatetime | None
    period_start: UtcDatetime
    period_end: UtcDatetime
    period_days: int
    summary: str
    trends: list[str]
    attention_points: list[str]
    limitations: list[str]
    sections: dict[str, list[str]]
    data_completeness: dict
    generated_at: UtcDatetime
    seen_at: UtcDatetime | None


class UpcomingAppointmentPublic(SQLModel):
    """One row of the doctor's "Lịch khám sắp tới" block (Epic 4.3)."""

    id: UUID
    patient_id: UUID
    patient_name: str
    scheduled_at: UtcDatetime
    reason: str | None
    brief_state: BriefState
    brief_generated_at: UtcDatetime | None
    brief_unseen: bool
    attention_count: int | None  # None until a brief exists

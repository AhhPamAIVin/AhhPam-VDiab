from datetime import datetime
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

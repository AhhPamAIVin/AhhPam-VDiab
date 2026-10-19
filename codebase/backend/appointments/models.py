from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Column, Field, SQLModel
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

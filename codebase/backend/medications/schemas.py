from datetime import datetime
from uuid import UUID

from sqlmodel import Field, SQLModel

from codebase.backend.medications.models import MedicationNoteType, MedicationStatus
from codebase.backend.time_utils import UtcDatetime


class MedicationCreate(SQLModel):
    patient_id: UUID
    drug_name: str = Field(min_length=1, max_length=200)
    dose: str = Field(min_length=1, max_length=100)
    frequency: str = Field(min_length=1, max_length=100)
    start_date: datetime
    note: str | None = Field(default=None, max_length=500)


class MedicationChange(SQLModel):
    """New drug/dose/frequency that replaces the current active entry.

    The current entry is discontinued (not overwritten); this data becomes a brand-new row.
    """

    drug_name: str = Field(min_length=1, max_length=200)
    dose: str = Field(min_length=1, max_length=100)
    frequency: str = Field(min_length=1, max_length=100)
    start_date: datetime | None = Field(default=None, description="When the new regimen starts; defaults to now")
    note: str | None = Field(default=None, max_length=500, description="Reason for the change")


class MedicationDiscontinue(SQLModel):
    end_date: datetime | None = Field(default=None, description="When the medication stopped; defaults to now")
    note: str | None = Field(default=None, max_length=500, description="Reason for stopping")


class MedicationPublic(SQLModel):
    id: UUID
    patient_id: UUID
    drug_name: str
    dose: str
    frequency: str
    start_date: UtcDatetime
    end_date: UtcDatetime | None
    status: MedicationStatus
    note: str | None
    discontinued_reason: str | None
    replaces_medication_id: UUID | None
    created_by: UUID
    created_at: UtcDatetime
    updated_at: UtcDatetime


class MedicationNoteCreate(SQLModel):
    note_type: MedicationNoteType
    text: str | None = Field(default=None, max_length=1000)
    occurred_at: datetime | None = Field(default=None, description="When it happened; defaults to now")


class MedicationNotePublic(SQLModel):
    id: UUID
    medication_id: UUID
    patient_id: UUID
    note_type: MedicationNoteType
    text: str | None
    occurred_at: UtcDatetime
    created_by: UUID
    created_at: UtcDatetime

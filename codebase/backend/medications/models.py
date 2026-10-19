from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class MedicationStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"


class MedicationNoteType(StrEnum):
    MISSED_DOSE = "missed_dose"
    ADVERSE_REACTION = "adverse_reaction"
    OTHER = "other"


class Medication(SQLModel, table=True):
    """A single entry in a patient's medication regimen.

    History is preserved by never mutating drug_name/dose/frequency in place: when a doctor
    changes dosage, swaps the drug, or stops it, the current row is discontinued (status +
    end_date set) and, for dosage changes/swaps, a new row is created for the new regimen.
    `replaces_medication_id` links a replacement back to the entry it succeeded so a swap
    chain is explicit, and querying all rows for a patient in chronological order reconstructs
    the full timeline — nothing is ever overwritten or deleted.
    """

    __tablename__ = "medications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    drug_name: str = Field(max_length=200)
    dose: str = Field(max_length=100)
    frequency: str = Field(max_length=100)
    start_date: datetime
    end_date: datetime | None = Field(default=None)
    status: MedicationStatus = Field(
        sa_column=Column(
            SAEnum(MedicationStatus, name="medication_status"),
            nullable=False,
            default=MedicationStatus.ACTIVE,
        )
    )
    note: str | None = Field(default=None, max_length=500, description="Why this entry was prescribed")
    discontinued_reason: str | None = Field(
        default=None, max_length=500, description="Why this entry was stopped or replaced"
    )
    replaces_medication_id: UUID | None = Field(default=None, foreign_key="medications.id")
    created_by: UUID = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MedicationNote(SQLModel, table=True):
    """A patient's self-report against a specific medication entry — a missed dose or an
    adverse reaction, not a doctor's clinical documentation (see ClinicalNote for that).
    `patient_id` is denormalized from the parent Medication so access control and patient-scoped
    listing don't require a join; `medication_id` ties it to the exact drug/dose it's about,
    which matters because that drug/dose may since have been superseded (see Medication's
    history-preservation docstring) — the note stays attached to what was actually taken.
    """

    __tablename__ = "medication_notes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    medication_id: UUID = Field(foreign_key="medications.id", index=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    note_type: MedicationNoteType = Field(
        sa_column=Column(SAEnum(MedicationNoteType, name="medication_note_type"), nullable=False)
    )
    text: str | None = Field(default=None, max_length=1000)
    occurred_at: datetime = Field(default_factory=datetime.utcnow, description="When the patient says it happened")
    created_by: UUID = Field(foreign_key="users.id")
    source_pending_action_id: UUID | None = Field(
        default=None, foreign_key="pending_record_actions.id", unique=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

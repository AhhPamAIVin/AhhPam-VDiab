from datetime import date, datetime
from uuid import UUID

from sqlmodel import Field, SQLModel

from codebase.backend.patients.models import CarePlanTargetStatus, RiskGroup
from codebase.backend.time_utils import UtcDatetime


class AssignmentCreate(SQLModel):
    doctor_id: UUID
    patient_id: UUID


class AssignmentPublic(SQLModel):
    id: UUID
    doctor_id: UUID
    patient_id: UUID
    assigned_by: UUID
    created_at: UtcDatetime


class ClinicalNoteCreate(SQLModel):
    """Request body for authoring a bệnh án entry. See ClinicalNote's docstring
    (models.py) for what each section maps to and why it's required/optional."""

    appointment_id: UUID | None = None

    reason_for_visit: str = Field(min_length=1, max_length=500)
    history_of_present_illness: str = Field(min_length=1, max_length=4000)
    past_medical_history: str | None = Field(default=None, max_length=2000)

    weight_kg: float | None = Field(default=None, gt=0, le=500)
    blood_pressure_systolic: int | None = Field(default=None, gt=0, le=300)
    blood_pressure_diastolic: int | None = Field(default=None, gt=0, le=200)
    clinical_exam_findings: str | None = Field(default=None, max_length=2000)

    data_period_start: date | None = None
    data_period_end: date | None = None
    hba1c_pct: float | None = Field(default=None, gt=0, le=20)
    paraclinical_results: str | None = Field(default=None, max_length=2000)

    diagnosis: str = Field(min_length=1, max_length=1000)
    treatment_plan: str = Field(min_length=1, max_length=2000)

    follow_up_date: date | None = None


class ClinicalNoteUpdate(SQLModel):
    """Partial update: only fields present in the request body are changed.

    Every field is optional here (unlike ClinicalNoteCreate) purely to express
    "not being changed" — the service layer only applies fields the caller
    actually set (`exclude_unset`), so a required section like `diagnosis`
    can't be blanked out by omitting it, only by explicitly sending a new value.
    """

    appointment_id: UUID | None = None

    reason_for_visit: str | None = Field(default=None, min_length=1, max_length=500)
    history_of_present_illness: str | None = Field(default=None, min_length=1, max_length=4000)
    past_medical_history: str | None = Field(default=None, max_length=2000)

    weight_kg: float | None = Field(default=None, gt=0, le=500)
    blood_pressure_systolic: int | None = Field(default=None, gt=0, le=300)
    blood_pressure_diastolic: int | None = Field(default=None, gt=0, le=200)
    clinical_exam_findings: str | None = Field(default=None, max_length=2000)

    data_period_start: date | None = None
    data_period_end: date | None = None
    hba1c_pct: float | None = Field(default=None, gt=0, le=20)
    paraclinical_results: str | None = Field(default=None, max_length=2000)

    diagnosis: str | None = Field(default=None, min_length=1, max_length=1000)
    treatment_plan: str | None = Field(default=None, min_length=1, max_length=2000)

    follow_up_date: date | None = None


class ClinicalNotePublic(SQLModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    appointment_id: UUID | None

    reason_for_visit: str
    history_of_present_illness: str
    past_medical_history: str | None

    weight_kg: float | None
    blood_pressure_systolic: int | None
    blood_pressure_diastolic: int | None
    clinical_exam_findings: str | None

    data_period_start: date | None
    data_period_end: date | None
    hba1c_pct: float | None
    paraclinical_results: str | None

    diagnosis: str
    treatment_plan: str

    follow_up_date: date | None

    created_at: UtcDatetime
    updated_at: UtcDatetime


class CarePlanTargetVersionCreate(SQLModel):
    target_low_mg_dl: float = Field(gt=0, le=1000)
    target_high_mg_dl: float = Field(gt=0, le=1000)
    danger_low_mg_dl: float = Field(gt=0, le=1000)
    danger_high_mg_dl: float = Field(gt=0, le=1000)
    risk_group: RiskGroup
    effective_from: datetime
    source_clinical_note_id: UUID | None = None
    source_appointment_id: UUID | None = None
    change_reason: str = Field(min_length=1, max_length=1000)


class CarePlanTargetVersionPublic(SQLModel):
    id: UUID
    patient_id: UUID
    approved_by_doctor_id: UUID
    version: int
    target_low_mg_dl: float
    target_high_mg_dl: float
    danger_low_mg_dl: float
    danger_high_mg_dl: float
    risk_group: RiskGroup
    status: CarePlanTargetStatus
    effective_from: datetime
    effective_to: datetime | None
    source_clinical_note_id: UUID | None
    source_appointment_id: UUID | None
    change_reason: str | None

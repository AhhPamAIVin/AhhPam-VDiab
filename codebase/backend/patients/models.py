from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, UniqueConstraint, func
from sqlmodel import Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class PatientAssignment(SQLModel, table=True):
    __tablename__ = "patient_assignments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    doctor_id: UUID = Field(foreign_key="users.id", index=True)
    # unique: a patient may have at most one doctor assigned at a time (1 doctor : N patients)
    patient_id: UUID = Field(foreign_key="users.id", index=True, unique=True)
    assigned_by: UUID = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ClinicalNote(SQLModel, table=True):
    """A structured clinical file ("bệnh án") entry documenting one patient visit.

    Modeled on the standard Vietnamese outpatient bệnh án sections (Bộ Y tế
    convention), adapted for this product's T2D follow-up context rather than
    literal inpatient admission paperwork — see
    data/mock/demo_patient/clinical_notes.json and patient_profile.json for the
    concrete reference fixture this design is grounded in:

      - Hành chính (administrative): patient_id / doctor_id / created_at /
        updated_at, already covered by the surrounding fields.
      - Ly do kham (chief complaint): reason_for_visit.
      - Benh su (history of present illness): history_of_present_illness —
        how the condition evolved since the last visit; this replaces the old
        single free-text `text` field.
      - Tien su (past history — comorbidities/personal & family history):
        past_medical_history. Kept as an optional per-visit field rather than
        a dedicated patient-profile table, since no such table exists yet in
        this codebase (see health/models.py) and comorbidities rarely change
        visit-to-visit.
      - Kham lam sang (clinical exam / vitals): weight_kg,
        blood_pressure_systolic/diastolic, clinical_exam_findings.
      - Can lam sang (paraclinical/test results reviewed): data_period_start/
        end (mirrors `covers_period` in the mock fixture — the window of
        glucose/meal/activity records the doctor reviewed), hba1c_pct (the
        key T2D lab marker called out in patient_profile.json), and
        paraclinical_results for any other labs.
      - Chan doan (diagnosis) and Huong dieu tri / Ke hoach (treatment plan):
        their own required structured fields, not buried in free text.
      - follow_up_date: the doctor's recommended next-visit date.

    A clinician signature is intentionally NOT stored here — it's derivable
    from doctor_id (+ the doctor's own profile) at read time, same as
    AppointmentPublic doesn't duplicate the doctor's name.

    Table name and `id` semantics are kept stable: docs/AI_Summary_Respond_System_Design.md
    plans a future PatientInsight.source_note_id FK to clinical_notes.id.
    """

    __tablename__ = "clinical_notes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    doctor_id: UUID = Field(foreign_key="users.id", index=True)

    # Visit linkage. Nullable: a bệnh án entry documents a visit, but this
    # product's appointment scheduling is a separate, newer feature — some
    # notes may be authored without a corresponding Appointment row (ad hoc
    # chart entries, or patients onboarded before scheduling existed). When
    # present, the service layer validates it belongs to the same patient.
    appointment_id: UUID | None = Field(default=None, foreign_key="appointments.id", index=True)

    # Ly do kham
    reason_for_visit: str = Field(min_length=1, max_length=500)

    # Benh su
    history_of_present_illness: str = Field(min_length=1, max_length=4000)

    # Tien su
    past_medical_history: str | None = Field(default=None, max_length=2000)

    # Kham lam sang
    weight_kg: float | None = Field(default=None, gt=0, le=500)
    blood_pressure_systolic: int | None = Field(default=None, gt=0, le=300)
    blood_pressure_diastolic: int | None = Field(default=None, gt=0, le=200)
    clinical_exam_findings: str | None = Field(default=None, max_length=2000)

    # Can lam sang
    data_period_start: date | None = Field(default=None)
    data_period_end: date | None = Field(default=None)
    hba1c_pct: float | None = Field(default=None, gt=0, le=20)
    paraclinical_results: str | None = Field(default=None, max_length=2000)

    # Chan doan
    diagnosis: str = Field(min_length=1, max_length=1000)

    # Huong dieu tri / Ke hoach
    treatment_plan: str = Field(min_length=1, max_length=2000)

    follow_up_date: date | None = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PatientProfile(SQLModel, table=True):
    """Demographics and comorbidity flags used as static input to the
    personalized glucose threshold model (data/NHANES/final/
    train_threshold_regression.py) — see docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md
    section 5. Split out from ClinicalNote (rather than living inside it, or
    as free text in past_medical_history) because these change rarely and the
    threshold model needs them as structured, queryable columns rather than
    prose to parse — the exact gap ClinicalNote's own docstring already flags
    ("no such table exists yet in this codebase").

    Nullable throughout: no onboarding flow collects this data yet, so
    existing patients start with an empty profile. The feature-computation
    layer (health/threshold_features.py) treats a missing profile/field as
    "unknown", not "false" for comorbidity flags — a NULL congestive_heart_
    failure_flag must not silently default to "confirmed absent".
    """

    __tablename__ = "patient_profiles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True, unique=True)

    age_years: float | None = Field(default=None, gt=0, le=130)
    sex: str | None = Field(default=None, max_length=20)
    bmi: float | None = Field(default=None, gt=0, le=100)
    caregiver_required_flag: bool | None = Field(default=None)

    congestive_heart_failure_flag: bool | None = Field(default=None)
    coronary_heart_disease_flag: bool | None = Field(default=None)
    stroke_flag: bool | None = Field(default=None)
    family_history_diabetes_flag: bool | None = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RiskGroup(StrEnum):
    """Matches data/knowledge_structured/patient_cgm_monitoring_profiles.csv's
    risk_group values exactly — STANDARD_TYPE_2 vs OLDER_OR_HIGH_RISK
    stratification, ADA 2026 Section 6/9 sourced (see
    docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md section 3.1)."""

    STANDARD_TYPE_2 = "STANDARD_TYPE_2"
    OLDER_OR_HIGH_RISK = "OLDER_OR_HIGH_RISK"


class CarePlanTargetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class CarePlanTarget(SQLModel, table=True):
    """The doctor-approved glucose thresholds this patient's DANGER gate
    reads from — the live-schema equivalent of patient_clinical_targets.csv +
    patient_cgm_monitoring_profiles.csv in the offline dataset.

    This table's existence, not just its data, is the actual prerequisite
    blocking Giai doan D (docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6):
    before this was added, create_glucose_record() had no doctor-approved
    threshold to gate on at all, live, for any patient — DANGER precedence
    (Lop 1, the system's core safety invariant) literally could not be
    evaluated. A patient with no row here must be treated as "not yet
    evaluable" (abstain), never as "no danger" by default — see
    health/threshold_service.py's evaluate_glucose_reading.

    Rows are immutable clinical versions. Creating a follow-up version closes
    the previous ACTIVE interval; historical glucose readings therefore keep
    resolving against the thresholds that were effective when they occurred.
    """

    __tablename__ = "care_plan_targets"
    __table_args__ = (UniqueConstraint("patient_id", "version", name="uq_care_plan_target_patient_version"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    approved_by_doctor_id: UUID = Field(foreign_key="users.id")

    risk_group: RiskGroup = Field(sa_column=Column(SAEnum(RiskGroup, name="risk_group"), nullable=False))
    target_low_mg_dl: float = Field(gt=0, le=1000)
    target_high_mg_dl: float = Field(gt=0, le=1000)
    danger_low_mg_dl: float = Field(gt=0, le=1000)
    danger_high_mg_dl: float = Field(gt=0, le=1000)

    status: CarePlanTargetStatus = Field(
        default=CarePlanTargetStatus.ACTIVE,
        sa_column=Column(
            SAEnum(CarePlanTargetStatus, name="care_plan_target_status"), nullable=False, server_default="ACTIVE"
        ),
    )

    version: int = Field(sa_column=Column(Integer, nullable=False, server_default="1"))
    effective_from: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, server_default=func.now(), index=True),
    )
    effective_to: datetime | None = Field(default=None, index=True)
    source_clinical_note_id: UUID | None = Field(default=None, foreign_key="clinical_notes.id")
    source_appointment_id: UUID | None = Field(default=None, foreign_key="appointments.id")
    change_reason: str | None = Field(default=None, max_length=1000)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

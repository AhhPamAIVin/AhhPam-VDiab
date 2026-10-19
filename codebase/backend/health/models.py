from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class MealContext(StrEnum):
    """When a glucose reading was taken, relative to food. See PRD §20.2."""

    FASTING = "FASTING"
    BEFORE_MEAL = "BEFORE_MEAL"
    AFTER_MEAL = "AFTER_MEAL"
    BEFORE_SLEEP = "BEFORE_SLEEP"
    RANDOM = "RANDOM"
    # A reading taken during recovery from a flagged glucose episode (e.g. a
    # post-hypo/hyper recheck) — see data/simulator_60d, the 60-day simulator's
    # forced test-episode readings use this context.
    EVENT_RECOVERY = "EVENT_RECOVERY"


class MeasurementSource(StrEnum):
    MANUAL = "MANUAL"
    GLUCOSE_METER = "GLUCOSE_METER"
    CGM = "CGM"
    SIMULATOR = "SIMULATOR"


class GlucoseRecord(SQLModel, table=True):
    __tablename__ = "glucose_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    value: float = Field(gt=0, le=1000)
    unit: str = Field(default="mg/dL", max_length=10)
    measured_at: datetime
    note: str | None = Field(default=None, max_length=500)
    # Additive fields for the AI summary/reflection pipeline — see
    # docs/AI_Summary_Respond_System_Design.md §9. Nullable/defaulted so
    # existing rows and callers that don't set them keep working.
    meal_context: MealContext | None = Field(
        default=None, sa_column=Column(SAEnum(MealContext, name="meal_context"), nullable=True)
    )
    measurement_source: MeasurementSource = Field(
        default=MeasurementSource.MANUAL,
        sa_column=Column(
            SAEnum(MeasurementSource, name="measurement_source"), nullable=False, server_default="MANUAL"
        ),
    )
    symptoms: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id")
    source_pending_action_id: UUID | None = Field(
        default=None, foreign_key="pending_record_actions.id", unique=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Personalized-threshold evaluation output — see health/threshold_service.py
    # and docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6. All nullable:
    # a record with no CarePlanTarget yet (see patients/models.py) is left
    # unevaluated rather than guessed at — threshold_status="UNAVAILABLE" in
    # that case, not a silent NORMAL. threshold_status is a plain string, not
    # an enum column: DANGER/WATCH/NORMAL/UNAVAILABLE mirrors the offline
    # rule engine's status values but this is a live-only concept still
    # being iterated on, not worth a migration-locked enum yet.
    predicted_min_mg_dl: float | None = Field(default=None)
    predicted_max_mg_dl: float | None = Field(default=None)
    threshold_status: str | None = Field(default=None, max_length=20)
    threshold_evaluation_source: str | None = Field(default=None, max_length=30)
    threshold_fallback_reason: str | None = Field(default=None, max_length=500)
    care_plan_target_version: int | None = Field(default=None)


class DoctorWatchLabel(StrEnum):
    CONFIRMED_WATCH = "CONFIRMED_WATCH"
    CONFIRMED_NORMAL = "CONFIRMED_NORMAL"
    UNCERTAIN = "UNCERTAIN"


class DoctorWatchReview(SQLModel, table=True):
    """Clinician ground-truth candidate; never auto-created by the model."""

    __tablename__ = "doctor_watch_reviews"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    glucose_record_id: UUID = Field(foreign_key="glucose_records.id", index=True, unique=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    doctor_id: UUID = Field(foreign_key="users.id", index=True)
    model_status_at_review: str | None = Field(default=None, max_length=20)
    label: DoctorWatchLabel = Field(sa_column=Column(SAEnum(DoctorWatchLabel, name="doctor_watch_label")))
    rationale: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)


class BloodPressureRecord(SQLModel, table=True):
    """Blood pressure reading, logged independently from glucose — see PRD §618.

    Patients often lack a combo device, so this is its own submission rather
    than fields tacked onto GlucoseRecord.
    """

    __tablename__ = "blood_pressure_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    systolic: float = Field(gt=0, le=300)  # huyết áp tâm thu
    diastolic: float = Field(gt=0, le=200)  # huyết áp tâm trương
    unit: str = Field(default="mmHg", max_length=10)
    measured_at: datetime
    note: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id")
    source_pending_action_id: UUID | None = Field(
        default=None, foreign_key="pending_record_actions.id", unique=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MealRecord(SQLModel, table=True):
    __tablename__ = "meal_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    meal_type: MealType = Field(sa_column=Column(SAEnum(MealType, name="meal_type"), nullable=False))
    meal_time: datetime
    description: str = Field(max_length=1000)
    note: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id")
    source_pending_action_id: UUID | None = Field(
        default=None, foreign_key="pending_record_actions.id", unique=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityRecord(SQLModel, table=True):
    __tablename__ = "activity_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    activity_type: str = Field(max_length=100)
    duration_minutes: int = Field(gt=0, le=1440)
    activity_time: datetime
    note: str | None = Field(default=None, max_length=500)
    created_by: UUID = Field(foreign_key="users.id")
    source_pending_action_id: UUID | None = Field(
        default=None, foreign_key="pending_record_actions.id", unique=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

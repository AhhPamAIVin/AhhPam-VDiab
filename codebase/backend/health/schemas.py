from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationInfo, field_validator
from sqlmodel import Field, SQLModel

from codebase.backend.health.models import (
    CarbLoad,
    DoctorWatchLabel,
    MealAnalysis,
    MealAnalysisStatus,
    MealContext,
    MealInputSource,
    MealType,
    MeasurementSource,
)
from codebase.backend.time_utils import UtcDatetime

GLUCOSE_UNITS = ("mg/dL", "mmol/L")
GLUCOSE_MAX_MG_DL = 1000.0
GLUCOSE_MAX_MMOL_L = 55.5
# Standard clinical conversion (glucose 180.16 g/mol / 10), rounded as in
# QĐ 5481's own paired figures (7 mmol/L ~ 126 mg/dL).
MG_DL_PER_MMOL_L = 18.0


class GlucoseRecordCreate(SQLModel):
    patient_id: UUID
    unit: str = Field(max_length=10)
    value: float = Field(gt=0, le=GLUCOSE_MAX_MG_DL)
    measured_at: datetime
    note: str | None = Field(default=None, max_length=500)
    meal_context: MealContext
    measurement_source: MeasurementSource = Field(default=MeasurementSource.MANUAL)
    symptoms: str | None = Field(default=None, max_length=500)

    @field_validator("unit")
    @classmethod
    def _unit_must_be_supported(cls, value: str) -> str:
        if value not in GLUCOSE_UNITS:
            raise ValueError(f"Đơn vị chỉ chấp nhận {' hoặc '.join(GLUCOSE_UNITS)}")
        return value

    @field_validator("value")
    @classmethod
    def _value_must_be_within_unit_range(cls, value: float, info: ValidationInfo) -> float:
        unit = info.data.get("unit")
        max_value = GLUCOSE_MAX_MMOL_L if unit == "mmol/L" else GLUCOSE_MAX_MG_DL
        if value > max_value:
            raise ValueError(f"Giá trị đường huyết vượt quá giới hạn tối đa hợp lệ ({max_value} {unit})")
        return value

    @field_validator("measured_at")
    @classmethod
    def _measured_at_must_not_be_future(cls, value: datetime) -> datetime:
        now = datetime.now(value.tzinfo) if value.tzinfo is not None else datetime.now(UTC).replace(tzinfo=None)
        if value > now:
            raise ValueError("Thời gian đo không được nằm trong tương lai")
        return value

class GlucoseRecordPublic(SQLModel):
    id: UUID
    patient_id: UUID
    value: float
    unit: str
    measured_at: UtcDatetime
    note: str | None
    meal_context: MealContext | None
    measurement_source: MeasurementSource
    symptoms: str | None
    created_by: UUID
    created_at: UtcDatetime
    # Personalized-threshold evaluation output — see health/threshold_service.py.
    # Null for records evaluated before this feature existed, or for a patient
    # with no CarePlanTarget yet (abstain, not a silent "normal").
    predicted_min_mg_dl: float | None = None
    predicted_max_mg_dl: float | None = None
    threshold_status: str | None = None
    threshold_evaluation_source: str | None = None
    threshold_fallback_reason: str | None = None
    care_plan_target_version: int | None = None


class DoctorWatchReviewCreate(SQLModel):
    label: DoctorWatchLabel
    rationale: str = Field(min_length=1, max_length=2000)


class DoctorWatchReviewPublic(SQLModel):
    id: UUID
    glucose_record_id: UUID
    patient_id: UUID
    doctor_id: UUID
    model_status_at_review: str | None
    label: DoctorWatchLabel
    rationale: str
    reviewed_at: UtcDatetime


class BloodPressureRecordCreate(SQLModel):
    patient_id: UUID
    systolic: float = Field(gt=0, le=300)
    diastolic: float = Field(gt=0, le=200)
    unit: str = Field(default="mmHg", max_length=10)
    measured_at: datetime
    note: str | None = Field(default=None, max_length=500)


class BloodPressureRecordPublic(SQLModel):
    id: UUID
    patient_id: UUID
    systolic: float
    diastolic: float
    unit: str
    measured_at: UtcDatetime
    note: str | None
    created_by: UUID
    created_at: UtcDatetime


class MealRecordCreate(SQLModel):
    patient_id: UUID
    meal_type: MealType
    meal_time: datetime
    description: str = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=500)
    portion: str | None = Field(default=None, max_length=100)
    # PHOTO when the form was prefilled by POST /meals/photo-analysis.
    input_source: MealInputSource = Field(default=MealInputSource.TEXT)


class MealAnalysisItemPublic(SQLModel):
    name: str
    matched_food_id: str | None = None
    matched_name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    grams: float | None = None
    carbs_g: float | None = None
    kcal: float | None = None
    gi_level: str | None = None
    match_confidence: float | None = None
    # The unit isn't known for this food; the default serving was assumed.
    portion_estimated: bool = False


class MealAnalysisPublic(SQLModel):
    """Estimate from the nutrition table (Epic 1.3). With `has_unmatched`
    the totals cover only the matched items, so they are lower bounds."""

    id: UUID
    meal_id: UUID
    items: list[MealAnalysisItemPublic]
    total_carbs_g: float | None
    total_kcal: float | None
    carb_load: CarbLoad
    has_unmatched: bool
    source: MealInputSource
    status: MealAnalysisStatus
    model_version: str | None
    nutrition_db_version: str | None
    updated_at: UtcDatetime

    @classmethod
    def from_row(cls, row: MealAnalysis) -> "MealAnalysisPublic":
        return cls(
            **row.model_dump(exclude={"items", "patient_id", "created_at"}),
            items=[MealAnalysisItemPublic(**item) for item in row.items],
            has_unmatched=any(item.get("carbs_g") is None for item in row.items),
        )


class MealItemEdit(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    quantity: float | None = Field(default=None, gt=0, le=2000)
    unit: str | None = Field(default=None, min_length=1, max_length=20)


class MealAnalysisEdit(SQLModel):
    """The patient's own item list; replaces the parsed one (status=edited)."""

    items: list[MealItemEdit] = Field(min_length=1, max_length=10)


class MealRecordPublic(SQLModel):
    id: UUID
    patient_id: UUID
    meal_type: MealType
    meal_time: UtcDatetime
    description: str
    note: str | None
    portion: str | None = None
    input_source: MealInputSource | None = None
    created_by: UUID
    created_at: UtcDatetime
    # Filled in the background after the meal is saved; None until then.
    analysis: MealAnalysisPublic | None = None


class MealFoodResponsePublic(SQLModel):
    food_id: str
    name: str
    n: int
    avg_post_mg_dl: float
    avg_delta_mg_dl: float | None
    n_with_delta: int
    pct_over_target: float


class MealCarbLoadResponsePublic(SQLModel):
    carb_load: CarbLoad
    n: int
    avg_post_mg_dl: float
    avg_delta_mg_dl: float | None
    n_with_delta: int
    pct_over_target: float


class MealGlucoseResponsePublic(SQLModel):
    """Association between meals and post-meal glucose — not cause. Groups
    with fewer than `min_n` paired meals are left out."""

    start: UtcDatetime
    end: UtcDatetime
    days: int
    meals_paired: int
    min_n: int
    post_prandial_target_mg_dl: float
    by_food: list[MealFoodResponsePublic]
    by_carb_load: list[MealCarbLoadResponsePublic]


class MealPhotoItemPublic(SQLModel):
    name: str
    quantity: float | None
    unit: str | None


class MealPhotoAnalysisPublic(SQLModel):
    """Draft for the meal form — nothing is saved, the photo included.
    `recognized=false` means no food was found in the photo."""

    recognized: bool
    description: str | None
    portion: str | None
    items: list[MealPhotoItemPublic]


class ActivityRecordCreate(SQLModel):
    patient_id: UUID
    activity_type: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(gt=0, le=1440)
    activity_time: datetime
    note: str | None = Field(default=None, max_length=500)


class ActivityRecordPublic(SQLModel):
    id: UUID
    patient_id: UUID
    activity_type: str
    duration_minutes: int
    activity_time: UtcDatetime
    note: str | None
    created_by: UUID
    created_at: UtcDatetime

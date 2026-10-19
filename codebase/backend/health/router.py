from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.health import service
from codebase.backend.health.content_safety import is_unsafe_activity_type, is_unsafe_meal_description
from codebase.backend.health.models import (
    ActivityRecord,
    BloodPressureRecord,
    DoctorWatchReview,
    GlucoseRecord,
    MealRecord,
)
from codebase.backend.health.schemas import (
    ActivityRecordCreate,
    ActivityRecordPublic,
    BloodPressureRecordCreate,
    BloodPressureRecordPublic,
    DoctorWatchReviewCreate,
    DoctorWatchReviewPublic,
    GlucoseRecordCreate,
    GlucoseRecordPublic,
    MealRecordCreate,
    MealRecordPublic,
)
from codebase.backend.patients.access import ensure_patient_access
from codebase.backend.summaries.service import refresh_patient_summaries

router = APIRouter()

_can_write = require_roles(UserRole.PATIENT, UserRole.DOCTOR)

_MAX_FILTER_YEARS_PAST = 10
_MAX_FILTER_YEARS_FUTURE = 1


def _resolve_patient_id(current_user: User, patient_id: UUID | None) -> UUID:
    """Patients default to themselves; Doctor/Admin must specify which patient."""
    if patient_id is not None:
        return patient_id
    if current_user.role == UserRole.PATIENT:
        return current_user.id
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_id is required")


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _validate_date_range(start_date: datetime | None, end_date: datetime | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before end_date")

    now = datetime.utcnow()
    earliest = now - timedelta(days=365 * _MAX_FILTER_YEARS_PAST)
    latest = now + timedelta(days=365 * _MAX_FILTER_YEARS_FUTURE)
    for label, value in (("start_date", start_date), ("end_date", end_date)):
        if value is not None and not (earliest <= _to_naive_utc(value) <= latest):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{label} must be within {_MAX_FILTER_YEARS_PAST} years in the past "
                    f"to {_MAX_FILTER_YEARS_FUTURE} year in the future"
                ),
            )


@router.post("/glucose", response_model=GlucoseRecordPublic, status_code=status.HTTP_201_CREATED)
def create_glucose(
    request: GlucoseRecordCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> GlucoseRecord:
    ensure_patient_access(session, current_user, request.patient_id)
    if service.find_duplicate_glucose_record(session, patient_id=request.patient_id, measured_at=request.measured_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bản ghi trùng lặp với số đo đã tồn tại trong cùng thời điểm",
        )
    record = service.create_glucose_record(
        session,
        patient_id=request.patient_id,
        value=request.value,
        unit=request.unit,
        measured_at=request.measured_at,
        note=request.note,
        meal_context=request.meal_context,
        measurement_source=request.measurement_source,
        symptoms=request.symptoms,
        created_by=current_user.id,
    )
    background_tasks.add_task(refresh_patient_summaries, patient_id=request.patient_id)
    return record


@router.get("/glucose", response_model=list[GlucoseRecordPublic])
def list_glucose(
    patient_id: UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None, description="Inclusive lower bound on measured_at"),
    end_date: datetime | None = Query(default=None, description="Inclusive upper bound on measured_at"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[GlucoseRecord]:
    _validate_date_range(start_date, end_date)
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_glucose_records(session, target_patient_id, start_date=start_date, end_date=end_date)


@router.post(
    "/glucose/{glucose_record_id}/watch-review",
    response_model=DoctorWatchReviewPublic,
    status_code=status.HTTP_201_CREATED,
)
def review_watch_label(
    glucose_record_id: UUID,
    request: DoctorWatchReviewCreate,
    current_user: User = Depends(require_roles(UserRole.DOCTOR)),
    session: Session = Depends(get_session),
) -> DoctorWatchReview:
    record = session.get(GlucoseRecord, glucose_record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Glucose record not found")
    ensure_patient_access(session, current_user, record.patient_id)
    return service.create_doctor_watch_review(
        session,
        glucose_record=record,
        doctor_id=current_user.id,
        label=request.label,
        rationale=request.rationale,
    )


def _validate_blood_pressure(systolic: float, diastolic: float) -> None:
    if diastolic >= systolic:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="diastolic (huyết áp tâm trương) must be lower than systolic (huyết áp tâm thu)",
        )


@router.post("/blood-pressure", response_model=BloodPressureRecordPublic, status_code=status.HTTP_201_CREATED)
def create_blood_pressure(
    request: BloodPressureRecordCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> BloodPressureRecord:
    ensure_patient_access(session, current_user, request.patient_id)
    _validate_blood_pressure(request.systolic, request.diastolic)
    record = service.create_blood_pressure_record(
        session,
        patient_id=request.patient_id,
        systolic=request.systolic,
        diastolic=request.diastolic,
        unit=request.unit,
        measured_at=request.measured_at,
        note=request.note,
        created_by=current_user.id,
    )
    background_tasks.add_task(refresh_patient_summaries, patient_id=request.patient_id)
    return record


@router.get("/blood-pressure", response_model=list[BloodPressureRecordPublic])
def list_blood_pressure(
    patient_id: UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None, description="Inclusive lower bound on measured_at"),
    end_date: datetime | None = Query(default=None, description="Inclusive upper bound on measured_at"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[BloodPressureRecord]:
    _validate_date_range(start_date, end_date)
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_blood_pressure_records(session, target_patient_id, start_date=start_date, end_date=end_date)


async def _validate_meal_description(description: str) -> None:
    if await is_unsafe_meal_description(description):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Mô tả bữa ăn này không giống một món ăn hoặc thức uống thực tế. "
                "Vui lòng mô tả lại đúng những gì bạn đã ăn/uống."
            ),
        )


@router.post("/meals", response_model=MealRecordPublic, status_code=status.HTTP_201_CREATED)
async def create_meal(
    request: MealRecordCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> MealRecord:
    ensure_patient_access(session, current_user, request.patient_id)
    await _validate_meal_description(request.description)
    record = service.create_meal_record(
        session,
        patient_id=request.patient_id,
        meal_type=request.meal_type,
        meal_time=request.meal_time,
        description=request.description,
        note=request.note,
        created_by=current_user.id,
    )
    background_tasks.add_task(refresh_patient_summaries, patient_id=request.patient_id)
    return record


@router.get("/meals", response_model=list[MealRecordPublic])
def list_meals(
    patient_id: UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None, description="Inclusive lower bound on meal_time"),
    end_date: datetime | None = Query(default=None, description="Inclusive upper bound on meal_time"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[MealRecord]:
    _validate_date_range(start_date, end_date)
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_meal_records(session, target_patient_id, start_date=start_date, end_date=end_date)


async def _validate_activity_type(activity_type: str) -> None:
    if await is_unsafe_activity_type(activity_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Mô tả hoạt động này không giống một hoạt động thể chất thực tế. "
                "Vui lòng mô tả lại đúng hoạt động bạn đã thực hiện."
            ),
        )


@router.post("/activities", response_model=ActivityRecordPublic, status_code=status.HTTP_201_CREATED)
async def create_activity(
    request: ActivityRecordCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> ActivityRecord:
    ensure_patient_access(session, current_user, request.patient_id)
    await _validate_activity_type(request.activity_type)
    record = service.create_activity_record(
        session,
        patient_id=request.patient_id,
        activity_type=request.activity_type,
        duration_minutes=request.duration_minutes,
        activity_time=request.activity_time,
        note=request.note,
        created_by=current_user.id,
    )
    background_tasks.add_task(refresh_patient_summaries, patient_id=request.patient_id)
    return record


@router.get("/activities", response_model=list[ActivityRecordPublic])
def list_activities(
    patient_id: UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None, description="Inclusive lower bound on activity_time"),
    end_date: datetime | None = Query(default=None, description="Inclusive upper bound on activity_time"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ActivityRecord]:
    _validate_date_range(start_date, end_date)
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_activity_records(session, target_patient_id, start_date=start_date, end_date=end_date)

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.health import meal_analysis, meal_photo, service
from codebase.backend.health.content_safety import (
    find_unsafe_substance_term,
    is_unsafe_activity_type,
    is_unsafe_meal_description,
)
from codebase.backend.health.meal_response import meal_glucose_response
from codebase.backend.health.models import (
    ActivityRecord,
    BloodPressureRecord,
    DoctorWatchReview,
    GlucoseRecord,
    MealAnalysis,
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
    MealAnalysisEdit,
    MealAnalysisPublic,
    MealGlucoseResponsePublic,
    MealPhotoAnalysisPublic,
    MealPhotoItemPublic,
    MealRecordCreate,
    MealRecordPublic,
)
from codebase.backend.patients.access import ensure_patient_access
from codebase.backend.security.rate_limit import limit_agent
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


async def _validate_meal_description(description: str, portion: str | None = None) -> None:
    # The portion is a quantity ("1 bát"), so only the literal denylist runs
    # on it — no second LLM call per meal.
    if await is_unsafe_meal_description(description) or (
        portion and find_unsafe_substance_term(portion) is not None
    ):
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
    await _validate_meal_description(request.description, request.portion)
    record = service.create_meal_record(
        session,
        patient_id=request.patient_id,
        meal_type=request.meal_type,
        meal_time=request.meal_time,
        description=request.description,
        note=request.note,
        portion=request.portion,
        input_source=request.input_source,
        created_by=current_user.id,
    )
    background_tasks.add_task(refresh_patient_summaries, patient_id=request.patient_id)
    # Attribute lookup at call time, so tests can stub it (tests/conftest.py).
    background_tasks.add_task(meal_analysis.analyze_meal_in_background, meal_id=record.id)
    return record


@router.post(
    "/meals/photo-analysis",
    response_model=MealPhotoAnalysisPublic,
    dependencies=[Depends(limit_agent)],
)
async def analyze_meal_photo(
    file: UploadFile = File(...),
    hint: str | None = Form(default=None, max_length=1000),
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
) -> MealPhotoAnalysisPublic:
    """Prefill the meal form from a photo. The photo goes to the vision model
    once and is then dropped — nothing is stored, and the draft only becomes
    a MealRecord when the patient saves the form (input_source=photo).

    Patient-only: the consent to send the photo out is the patient's own.
    Shares the per-user /agent/* rate limit, since every call is an LLM call."""
    recognition = await meal_photo.recognize_upload(file, hint, current_user)
    return MealPhotoAnalysisPublic(
        recognized=recognition.is_food,
        description=recognition.description,
        portion=recognition.portion,
        items=[MealPhotoItemPublic(**item.model_dump()) for item in recognition.items],
    )


@router.get("/meals", response_model=list[MealRecordPublic])
def list_meals(
    patient_id: UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None, description="Inclusive lower bound on meal_time"),
    end_date: datetime | None = Query(default=None, description="Inclusive upper bound on meal_time"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[MealRecordPublic]:
    _validate_date_range(start_date, end_date)
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    meals = service.list_meal_records(session, target_patient_id, start_date=start_date, end_date=end_date)
    analyses = meal_analysis.analyses_by_meal(session, [meal.id for meal in meals])
    return [_meal_public(meal, analyses.get(meal.id)) for meal in meals]


def _meal_public(meal: MealRecord, analysis: MealAnalysis | None) -> MealRecordPublic:
    public = MealRecordPublic.model_validate(meal)
    public.analysis = MealAnalysisPublic.from_row(analysis) if analysis is not None else None
    return public


@router.get("/meals/glucose-response", response_model=MealGlucoseResponsePublic)
def get_meal_glucose_response(
    patient_id: UUID | None = Query(default=None),
    days: int = Query(default=30, ge=7, le=90),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Which foods / carb bands tend to go with high post-meal glucose for
    this patient (Epic 1.4). Association only; groups under min_n meals are
    left out."""
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return meal_glucose_response(session, target_patient_id, days=days)


def _get_meal_for(session: Session, current_user: User, meal_id: UUID) -> MealRecord:
    meal = session.get(MealRecord, meal_id)
    if meal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal record not found")
    ensure_patient_access(session, current_user, meal.patient_id)
    return meal


def _get_analysis_for(session: Session, meal: MealRecord) -> MealAnalysis:
    analysis = meal_analysis.get_meal_analysis(session, meal.id)
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bữa ăn này chưa có phân tích.")
    return analysis


@router.get("/meals/{meal_id}/analysis", response_model=MealAnalysisPublic)
def get_meal_analysis(
    meal_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MealAnalysisPublic:
    meal = _get_meal_for(session, current_user, meal_id)
    return MealAnalysisPublic.from_row(_get_analysis_for(session, meal))


@router.post(
    "/meals/{meal_id}/analysis/rerun",
    response_model=MealAnalysisPublic,
    dependencies=[Depends(limit_agent)],
)
async def rerun_meal_analysis(
    meal_id: UUID,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> MealAnalysisPublic:
    """Analyse again from the meal text (back to draft). May call the LLM,
    hence the /agent/* rate limit."""
    meal = _get_meal_for(session, current_user, meal_id)
    return MealAnalysisPublic.from_row(await meal_analysis.analyze_meal(session, meal))


@router.patch("/meals/{meal_id}/analysis", response_model=MealAnalysisPublic)
async def edit_meal_analysis(
    meal_id: UUID,
    request: MealAnalysisEdit,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> MealAnalysisPublic:
    """Replace the item list with the patient's own and recompute from the
    nutrition table — no LLM involved. Stored as status=edited."""
    meal = _get_meal_for(session, current_user, meal_id)
    for item in request.items:
        if find_unsafe_substance_term(item.name) is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tên món không hợp lệ.")
    edited = [item.model_dump() for item in request.items]
    return MealAnalysisPublic.from_row(await meal_analysis.analyze_meal(session, meal, edited_items=edited))


@router.post("/meals/{meal_id}/analysis/confirm", response_model=MealAnalysisPublic)
def confirm_meal_analysis(
    meal_id: UUID,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> MealAnalysisPublic:
    meal = _get_meal_for(session, current_user, meal_id)
    analysis = meal_analysis.confirm_meal_analysis(session, _get_analysis_for(session, meal))
    return MealAnalysisPublic.from_row(analysis)


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

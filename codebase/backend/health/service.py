import logging
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from codebase.backend.auth.models import User
from codebase.backend.config import get_settings
from codebase.backend.health.models import (
    ActivityRecord,
    BloodPressureRecord,
    DoctorWatchLabel,
    DoctorWatchReview,
    GlucoseRecord,
    MealContext,
    MealInputSource,
    MealRecord,
    MealType,
    MeasurementSource,
)
from codebase.backend.health.schemas import MG_DL_PER_MMOL_L
from codebase.backend.health.threshold_service import evaluate_glucose_reading
from codebase.backend.notifications.models import NotificationType
from codebase.backend.notifications.service import auto_complete_pending, create_glucose_alert_notification

logger = logging.getLogger(__name__)


def _reminder_local_date(value: datetime) -> date:
    """Map a stored measurement instant to the reminder plan's wall-clock day.

    Health timestamps are stored as naive UTC, while reminder slots are keyed
    by the patient's configured local day. Around UTC midnight those dates can
    differ, so comparing ``measured_at.date()`` directly leaves today's local
    reminder pending after a patient has actually logged the reading.
    """
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(ZoneInfo(get_settings().reminder_timezone)).date()


def find_duplicate_glucose_record(session: Session, *, patient_id: UUID, measured_at: datetime) -> GlucoseRecord | None:
    """A second reading logged for the exact same patient+timestamp is almost
    always a duplicate submission (double-tap, retry), not two real readings."""
    statement = select(GlucoseRecord).where(
        GlucoseRecord.patient_id == patient_id, GlucoseRecord.measured_at == measured_at
    )
    return session.exec(statement).first()


def create_glucose_record(
    session: Session,
    *,
    patient_id: UUID,
    value: float,
    unit: str,
    measured_at: datetime,
    note: str | None,
    created_by: UUID,
    meal_context: MealContext | None = None,
    measurement_source: MeasurementSource = MeasurementSource.MANUAL,
    symptoms: str | None = None,
    source_pending_action_id: UUID | None = None,
) -> GlucoseRecord:
    # Everything downstream — the DANGER gate, the ML WATCH band, averages
    # shown as "mg/dL" — compares raw `value` against mg/dL numbers. A reading
    # entered in mmol/L (the API and the chat parser both accept it) used to
    # be stored as-is, so a normal 5.8 mmol/L fasting value was compared as
    # 5.8 mg/dL and raised a false DANGER alert (caught by the rule-engine eval).
    if unit == "mmol/L":
        value = round(value * MG_DL_PER_MMOL_L, 1)
        unit = "mg/dL"
    record = GlucoseRecord(
        patient_id=patient_id,
        value=value,
        unit=unit,
        measured_at=measured_at,
        note=note,
        meal_context=meal_context,
        measurement_source=measurement_source,
        symptoms=symptoms,
        created_by=created_by,
        source_pending_action_id=source_pending_action_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    auto_complete_pending(
        session,
        patient_id=patient_id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        on_date=_reminder_local_date(record.measured_at),
    )
    record_id = record.id  # plain value for the failure logs below

    # Threshold evaluation is best-effort enrichment, not the primary action
    # here — a bug or missing model in this newer path must never stop a
    # patient's reading from being saved. See health/threshold_service.py
    # for the actual DANGER-gated logic; failures here leave the record's
    # threshold_status at its default None, same as "UNAVAILABLE".
    try:
        evaluation = evaluate_glucose_reading(session, record)
        record.predicted_min_mg_dl = evaluation.predicted_min_mg_dl
        record.predicted_max_mg_dl = evaluation.predicted_max_mg_dl
        record.threshold_status = evaluation.status
        record.threshold_evaluation_source = (
            "ML_QUANTILE_V2" if evaluation.ml_called else "DOCTOR_RULE_FALLBACK"
        )
        if evaluation.status == "DANGER":
            record.threshold_evaluation_source = "DOCTOR_DANGER_RULE"
        if evaluation.status == "UNAVAILABLE":
            record.threshold_evaluation_source = "UNAVAILABLE"
        record.threshold_fallback_reason = evaluation.fallback_reason
        record.care_plan_target_version = evaluation.care_plan_target_version
        session.add(record)
        session.commit()
        session.refresh(record)
    except Exception:
        session.rollback()
        logger.exception("threshold evaluation failed for glucose record %s — leaving it unevaluated", record_id)
        return record

    # WATCH and DANGER both notify (NORMAL/UNAVAILABLE never do) — Giai doan
    # E, see notifications/service.py's create_glucose_alert_notification for
    # the cap/idempotency/push logic and how the two severities differ in
    # message content. Its own try/except: a notification failure must not
    # roll back the threshold evaluation that was just committed above.
    if record.threshold_status in ("WATCH", "DANGER"):
        try:
            patient = session.get(User, patient_id)
            if patient is not None:
                create_glucose_alert_notification(
                    session, patient=patient, glucose_record_id=record.id, measured_at=record.measured_at,
                    value=record.value, status=record.threshold_status,
                    predicted_min_mg_dl=record.predicted_min_mg_dl,
                    predicted_max_mg_dl=record.predicted_max_mg_dl,
                    danger_low_mg_dl=evaluation.danger_low_mg_dl,
                    danger_high_mg_dl=evaluation.danger_high_mg_dl,
                )
        except Exception:
            # Roll back first: after a failed flush the session refuses every
            # query, including the attribute load `record.id` would trigger —
            # logging it before the rollback turned this "must not fail the
            # request" path into a 500 (verified live).
            session.rollback()
            logger.exception("glucose alert notification failed for record %s", record_id)

    return record


def list_glucose_records(
    session: Session,
    patient_id: UUID,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[GlucoseRecord]:
    statement = select(GlucoseRecord).where(GlucoseRecord.patient_id == patient_id)
    if start_date is not None:
        statement = statement.where(col(GlucoseRecord.measured_at) >= start_date)
    if end_date is not None:
        statement = statement.where(col(GlucoseRecord.measured_at) <= end_date)
    return list(session.exec(statement.order_by(col(GlucoseRecord.measured_at).desc())).all())


def create_doctor_watch_review(
    session: Session,
    *,
    glucose_record: GlucoseRecord,
    doctor_id: UUID,
    label: DoctorWatchLabel,
    rationale: str,
) -> DoctorWatchReview:
    existing = session.exec(
        select(DoctorWatchReview).where(DoctorWatchReview.glucose_record_id == glucose_record.id)
    ).first()
    if existing is not None:
        existing.doctor_id = doctor_id
        existing.label = label
        existing.rationale = rationale
        existing.model_status_at_review = glucose_record.threshold_status
        existing.reviewed_at = datetime.utcnow()
        review = existing
    else:
        review = DoctorWatchReview(
            glucose_record_id=glucose_record.id,
            patient_id=glucose_record.patient_id,
            doctor_id=doctor_id,
            model_status_at_review=glucose_record.threshold_status,
            label=label,
            rationale=rationale,
        )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def create_blood_pressure_record(
    session: Session,
    *,
    patient_id: UUID,
    systolic: float,
    diastolic: float,
    unit: str,
    measured_at: datetime,
    note: str | None,
    created_by: UUID,
    source_pending_action_id: UUID | None = None,
) -> BloodPressureRecord:
    record = BloodPressureRecord(
        patient_id=patient_id,
        systolic=systolic,
        diastolic=diastolic,
        unit=unit,
        measured_at=measured_at,
        note=note,
        created_by=created_by,
        source_pending_action_id=source_pending_action_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    auto_complete_pending(
        session,
        patient_id=patient_id,
        notification_type=NotificationType.BLOOD_PRESSURE_REMINDER,
        on_date=_reminder_local_date(record.measured_at),
    )
    return record


def list_blood_pressure_records(
    session: Session,
    patient_id: UUID,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[BloodPressureRecord]:
    statement = select(BloodPressureRecord).where(BloodPressureRecord.patient_id == patient_id)
    if start_date is not None:
        statement = statement.where(col(BloodPressureRecord.measured_at) >= start_date)
    if end_date is not None:
        statement = statement.where(col(BloodPressureRecord.measured_at) <= end_date)
    return list(session.exec(statement.order_by(col(BloodPressureRecord.measured_at).desc())).all())


def create_meal_record(
    session: Session,
    *,
    patient_id: UUID,
    meal_type: MealType,
    meal_time: datetime,
    description: str,
    note: str | None,
    created_by: UUID,
    portion: str | None = None,
    input_source: MealInputSource = MealInputSource.TEXT,
    source_pending_action_id: UUID | None = None,
) -> MealRecord:
    record = MealRecord(
        patient_id=patient_id,
        meal_type=meal_type,
        meal_time=meal_time,
        description=description,
        note=note,
        portion=portion,
        input_source=input_source,
        created_by=created_by,
        source_pending_action_id=source_pending_action_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_meal_records(
    session: Session,
    patient_id: UUID,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[MealRecord]:
    statement = select(MealRecord).where(MealRecord.patient_id == patient_id)
    if start_date is not None:
        statement = statement.where(col(MealRecord.meal_time) >= start_date)
    if end_date is not None:
        statement = statement.where(col(MealRecord.meal_time) <= end_date)
    return list(session.exec(statement.order_by(col(MealRecord.meal_time).desc())).all())


def create_activity_record(
    session: Session,
    *,
    patient_id: UUID,
    activity_type: str,
    duration_minutes: int,
    activity_time: datetime,
    note: str | None,
    created_by: UUID,
    source_pending_action_id: UUID | None = None,
) -> ActivityRecord:
    record = ActivityRecord(
        patient_id=patient_id,
        activity_type=activity_type,
        duration_minutes=duration_minutes,
        activity_time=activity_time,
        note=note,
        created_by=created_by,
        source_pending_action_id=source_pending_action_id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_activity_records(
    session: Session,
    patient_id: UUID,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[ActivityRecord]:
    statement = select(ActivityRecord).where(ActivityRecord.patient_id == patient_id)
    if start_date is not None:
        statement = statement.where(col(ActivityRecord.activity_time) >= start_date)
    if end_date is not None:
        statement = statement.where(col(ActivityRecord.activity_time) <= end_date)
    return list(session.exec(statement.order_by(col(ActivityRecord.activity_time).desc())).all())

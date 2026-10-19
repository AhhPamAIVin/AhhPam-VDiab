from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealContext, MeasurementSource
from codebase.backend.health.service import create_glucose_record
from codebase.backend.health.threshold_service import evaluate_glucose_reading
from codebase.backend.patients.models import CarePlanTarget, PatientProfile, RiskGroup

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def doctor_user(session: Session) -> User:
    return _make_user(session, email="doctor@diab.example.com", role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session) -> User:
    return _make_user(session, email="patient@diab.example.com", role=UserRole.PATIENT)


@pytest.fixture
def care_plan(session: Session, patient_user: User, doctor_user: User) -> CarePlanTarget:
    session.add(PatientProfile(patient_id=patient_user.id, age_years=55, sex="MALE", bmi=25))
    target = CarePlanTarget(
        patient_id=patient_user.id,
        approved_by_doctor_id=doctor_user.id,
        risk_group=RiskGroup.STANDARD_TYPE_2,
        target_low_mg_dl=70,
        target_high_mg_dl=180,
        danger_low_mg_dl=54,
        danger_high_mg_dl=250,
        effective_from=datetime.now(UTC) - timedelta(days=365),
    )
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def test_missing_meal_context_uses_doctor_target_fallback(
    session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget
):
    record = GlucoseRecord(
        patient_id=patient_user.id, value=200, unit="mg/dL", measured_at=datetime.now(UTC),
        meal_context=None, measurement_source=MeasurementSource.MANUAL, created_by=doctor_user.id,
    )
    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.status == "WATCH"
    assert evaluation.ml_called is False
    assert evaluation.fallback_reason == "INSUFFICIENT_CONTEXT_BASELINE|MEAL_CONTEXT_MISSING"
    assert (evaluation.predicted_min_mg_dl, evaluation.predicted_max_mg_dl) == (70, 180)


def test_new_patient_without_profile_uses_doctor_target_fallback(
    session: Session, doctor_user: User
):
    patient = _make_user(session, email="new-patient@diab.example.com", role=UserRole.PATIENT)
    session.add(CarePlanTarget(
        patient_id=patient.id, approved_by_doctor_id=doctor_user.id,
        risk_group=RiskGroup.STANDARD_TYPE_2, target_low_mg_dl=70, target_high_mg_dl=180,
        danger_low_mg_dl=54, danger_high_mg_dl=250,
    ))
    session.commit()
    record = GlucoseRecord(
        patient_id=patient.id, value=90, unit="mg/dL", measured_at=datetime.now(UTC),
        meal_context=MealContext.FASTING, measurement_source=MeasurementSource.MANUAL,
        created_by=doctor_user.id,
    )
    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.status == "NORMAL"
    assert evaluation.ml_called is False
    assert "PATIENT_PROFILE_MISSING" in (evaluation.fallback_reason or "")


def test_unscheduled_tightly_spaced_reading_uses_fallback(
    session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget
):
    now = datetime.now(UTC)
    for index in range(5):
        session.add(GlucoseRecord(
            patient_id=patient_user.id,
            value=120 + index,
            unit="mg/dL",
            measured_at=now - timedelta(days=5 - index),
            meal_context=MealContext.FASTING,
            measurement_source=MeasurementSource.MANUAL,
            created_by=doctor_user.id,
        ))
    session.add(GlucoseRecord(
        patient_id=patient_user.id,
        value=130,
        unit="mg/dL",
        measured_at=now - timedelta(minutes=10),
        meal_context=MealContext.RANDOM,
        measurement_source=MeasurementSource.MANUAL,
        created_by=doctor_user.id,
    ))
    session.commit()
    record = GlucoseRecord(
        patient_id=patient_user.id,
        value=200,
        unit="mg/dL",
        measured_at=now,
        meal_context=MealContext.RANDOM,
        measurement_source=MeasurementSource.MANUAL,
        created_by=doctor_user.id,
    )
    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.ml_called is False
    assert "MEASUREMENT_INTERVAL_TOO_SHORT" in (evaluation.fallback_reason or "")


def test_no_care_plan_target_abstains(session: Session, patient_user: User, doctor_user: User):
    """A patient with no doctor-approved CarePlanTarget must never get a
    DANGER/WATCH/NORMAL claim — abstain, per plan section 8's acceptance
    criteria, not "assume not dangerous"."""
    record = create_glucose_record(
        session, patient_id=patient_user.id, value=90, unit="mg/dL",
        measured_at=datetime.now(UTC), note=None, created_by=doctor_user.id,
    )
    assert record.threshold_status == "UNAVAILABLE"
    assert record.predicted_min_mg_dl is None
    assert record.predicted_max_mg_dl is None


def test_danger_reading_never_reaches_ml(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    record = GlucoseRecord(
        patient_id=patient_user.id, value=300, unit="mg/dL", measured_at=datetime.now(UTC),
        meal_context=MealContext.FASTING, measurement_source=MeasurementSource.MANUAL, created_by=doctor_user.id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.status == "DANGER"
    assert evaluation.ml_called is False
    assert evaluation.predicted_min_mg_dl is None
    assert evaluation.predicted_max_mg_dl is None


def test_boundary_value_is_not_danger(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    """Matches the offline rule engine's strict-inequality semantics — see
    data/ml/personalized_watch/serving.py and test_danger_precedence.py."""
    record = GlucoseRecord(
        patient_id=patient_user.id, value=250, unit="mg/dL", measured_at=datetime.now(UTC),
        meal_context=MealContext.FASTING, measurement_source=MeasurementSource.MANUAL, created_by=doctor_user.id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.status != "DANGER"
    assert evaluation.status == "WATCH"
    assert evaluation.ml_called is False
    assert evaluation.fallback_reason == "INSUFFICIENT_CONTEXT_BASELINE"


def test_non_danger_reading_gets_ml_prediction(
    session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget
):
    base_time = datetime.now(UTC) - timedelta(days=10)
    for i in range(6):
        prior = GlucoseRecord(
            patient_id=patient_user.id, value=140 + i, unit="mg/dL",
            measured_at=base_time + timedelta(hours=6 * i), meal_context=MealContext.FASTING,
            measurement_source=MeasurementSource.MANUAL, created_by=doctor_user.id,
        )
        session.add(prior)
    session.commit()

    record = GlucoseRecord(
        patient_id=patient_user.id, value=145, unit="mg/dL", measured_at=datetime.now(UTC),
        meal_context=MealContext.FASTING, measurement_source=MeasurementSource.MANUAL, created_by=doctor_user.id,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    evaluation = evaluate_glucose_reading(session, record)
    assert evaluation.status in ("WATCH", "NORMAL")
    assert evaluation.ml_called is True
    assert evaluation.predicted_min_mg_dl is not None
    assert evaluation.predicted_max_mg_dl is not None
    assert evaluation.predicted_min_mg_dl < evaluation.predicted_max_mg_dl


def test_create_glucose_record_persists_evaluation(
    session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget
):
    record = create_glucose_record(
        session, patient_id=patient_user.id, value=200, unit="mg/dL",
        measured_at=datetime.now(UTC), note=None, created_by=doctor_user.id,
        meal_context=MealContext.AFTER_MEAL,
    )
    assert record.threshold_status is not None
    assert record.id is not None  # the record itself always saves regardless of evaluation outcome

"""Curate one doctor's patient panel into a believable demo showcase: exactly
5 patients, split 3 NORMAL / 1 WATCH / 1 DANGER by their latest glucose
reading's threshold_status (codebase/backend/health/threshold_service.py) —
the same field the doctor dashboard's status badges read
(codebase/frontend/components/pages/doctor-dashboard.tsx).

Run after migrate_nhanes_demo_data.py and backfill_care_plan_targets.py (a
CarePlanTarget is required per patient — created here as a fallback only if
missing) and after the other backfill_* scripts, so this patient's
history-derived features reflect real prior data rather than an empty
history.

Picks doctor + 5 patients deterministically (lowest `id` first) so that two
databases seeded from the identical NHANES CSVs (e.g. the v1 and v2 deploy
stacks) independently converge on the *same* demo doctor/patients without
needing to coordinate. `PatientAssignment.patient_id` is unique (1 doctor : N
patients — patients/models.py), so reassigning is a delete-then-insert of
that patient's single row; safe to do since these are demo accounts, not real
clinical relationships.

Status is forced deterministically, not left to the ML quantile model: every
inserted reading uses `meal_context=None`, which unconditionally triggers
threshold_features.py's MEAL_CONTEXT_MISSING fallback — so NORMAL/WATCH here
are both decided by the same simple doctor-target-bounds comparison DANGER
already is (danger gate is ML-independent by design), never by the trained
model's quantile output. This trades one degree of demo realism (a real
patient's own meal_context) for two: no flakiness across re-runs, and no
silent status drift if the model artifacts are ever retrained.

Usage:
    python -m scripts.seed_demo_doctor_showcase [--dry-run]

Safe to re-run: reassignment is idempotent, CarePlanTarget creation is
guarded, and each run inserts one fresh "latest" reading per patient (which
is the point — it keeps the demo's latest-reading-driven status current).
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, time, timedelta

from sqlmodel import Session, col, select

import codebase.backend.appointments.models as _appointments_models  # noqa: F401  (registers Appointment — CarePlanTarget.source_appointment_id/ClinicalNote.appointment_id FK into it)
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import create_db_and_tables, engine
from codebase.backend.health.models import GlucoseRecord, MealContext, MeasurementSource
from codebase.backend.health.threshold_service import evaluate_glucose_reading
from codebase.backend.medications.models import (
    Medication,  # noqa: F401  (registers medications; threshold_features.py queries it)
)
from codebase.backend.patients.models import CarePlanTarget, PatientAssignment, RiskGroup

logger = logging.getLogger("scripts.seed_demo_doctor_showcase")

PANEL_SIZE = 5
STATUS_MIX = ("NORMAL", "NORMAL", "NORMAL", "WATCH", "DANGER")

# The product's own designated demo accounts — codebase/frontend/components/
# pages/login-page.tsx's "Bác sĩ demo"/"Bệnh nhân demo" one-click buttons
# fall back to these exact emails whenever NEXT_PUBLIC_DEMO_*_EMAIL isn't set
# (true for these self-hosted deploys). Curating any other doctor would leave
# that button pointing at an uncurated account, so this is not an arbitrary
# choice — it's the account the live "demo" buttons actually log into.
DEMO_DOCTOR_EMAIL = "tampn_0101@vdiab.com.vn"
# Included in the panel (not just left alone) so the two demo buttons tell
# one consistent story: the doctor's list includes the same patient the
# patient-demo button logs into.
DEMO_PATIENT_EMAIL = "viethh_0461@gmail.com"

# Only used as a fallback if a picked patient somehow has no CarePlanTarget
# yet (backfill_care_plan_targets.py should already have created one for
# every migrated patient) — standard adult T2D bounds, ADA-ish.
FALLBACK_TARGET_LOW_MG_DL = 70.0
FALLBACK_TARGET_HIGH_MG_DL = 180.0
FALLBACK_DANGER_LOW_MG_DL = 54.0
FALLBACK_DANGER_HIGH_MG_DL = 250.0

DEMO_NOTE = "demo showcase seed (scripts/seed_demo_doctor_showcase.py)"

# migrate_nhanes_demo_data.py's date-shift leaves the last 1-2 days before
# "today" empty for every patient (its ~60-day source window doesn't quite
# reach today — see docs/nhanes_migration_staleness note in project memory).
# For the demo panel specifically we don't want a visible gap right before
# "today" in the glucose trend chart, so scan back this many days and
# backfill any day with zero readings — generous enough to cover the gap
# even if it drifts a bit wider on a later re-run.
GAP_FILL_LOOKBACK_DAYS = 5
# (hour of day, meal context, offset from the patient's target midpoint) —
# a plausible daily pattern, not meant to be clinically precise.
GAP_FILL_READINGS = (
    (7, MealContext.FASTING, -8.0),
    (12, MealContext.AFTER_MEAL, 12.0),
    (18, MealContext.AFTER_MEAL, 18.0),
    (21, MealContext.BEFORE_SLEEP, -3.0),
)


def _pick_doctor_and_patients(session: Session) -> tuple[User, list[User]]:
    doctor = session.exec(select(User).where(User.email == DEMO_DOCTOR_EMAIL)).first()
    if doctor is None:
        logger.warning("demo doctor %s not found — falling back to lowest-id doctor", DEMO_DOCTOR_EMAIL)
        doctor = session.exec(select(User).where(User.role == UserRole.DOCTOR).order_by(col(User.id))).first()
    if doctor is None:
        raise RuntimeError("No doctor users found — run migrate_nhanes_demo_data first.")

    patients: list[User] = []
    demo_patient = session.exec(select(User).where(User.email == DEMO_PATIENT_EMAIL)).first()
    if demo_patient is None:
        logger.warning("demo patient %s not found — panel will be 5 arbitrary patients", DEMO_PATIENT_EMAIL)
    else:
        patients.append(demo_patient)

    exclude_ids = {p.id for p in patients}
    fill_statement = select(User).where(User.role == UserRole.PATIENT).order_by(col(User.id))
    if exclude_ids:
        fill_statement = fill_statement.where(col(User.id).not_in(exclude_ids))
    patients += session.exec(fill_statement.limit(PANEL_SIZE - len(patients))).all()

    if len(patients) < PANEL_SIZE:
        raise RuntimeError(f"Need {PANEL_SIZE} patients, only found {len(patients)}.")
    return doctor, patients


def _reassign_patients(session: Session, doctor: User, patients: list[User], assigned_by: User) -> None:
    # Drop any of the doctor's *other* pre-existing patients first — the demo
    # panel must be exactly these 5, not these 5 plus whatever the random
    # NHANES seed had already assigned to this doctor.
    chosen_ids = {p.id for p in patients}
    other_existing = session.exec(select(PatientAssignment).where(PatientAssignment.doctor_id == doctor.id)).all()
    for row in other_existing:
        if row.patient_id not in chosen_ids:
            session.delete(row)
    session.flush()

    for patient in patients:
        existing = session.exec(select(PatientAssignment).where(PatientAssignment.patient_id == patient.id)).first()
        if existing is not None:
            if existing.doctor_id == doctor.id:
                continue
            session.delete(existing)
            session.flush()
        session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=assigned_by.id))
    session.flush()


def _ensure_care_plan_target(session: Session, patient: User, doctor: User) -> CarePlanTarget:
    target = session.exec(
        select(CarePlanTarget)
        .where(CarePlanTarget.patient_id == patient.id, CarePlanTarget.effective_to.is_(None))
        .order_by(col(CarePlanTarget.version).desc())
    ).first()
    if target is not None:
        return target
    target = CarePlanTarget(
        patient_id=patient.id,
        approved_by_doctor_id=doctor.id,
        risk_group=RiskGroup.STANDARD_TYPE_2,
        target_low_mg_dl=FALLBACK_TARGET_LOW_MG_DL,
        target_high_mg_dl=FALLBACK_TARGET_HIGH_MG_DL,
        danger_low_mg_dl=FALLBACK_DANGER_LOW_MG_DL,
        danger_high_mg_dl=FALLBACK_DANGER_HIGH_MG_DL,
        version=1,
        effective_from=datetime(2026, 1, 1),
        change_reason="Fallback target created by seed_demo_doctor_showcase.py (none existed)",
    )
    session.add(target)
    session.flush()
    return target


def _value_for_status(target: CarePlanTarget, status: str) -> float:
    if status == "NORMAL":
        return (target.target_low_mg_dl + target.target_high_mg_dl) / 2
    if status == "WATCH":
        return target.target_high_mg_dl + (target.danger_high_mg_dl - target.target_high_mg_dl) / 2
    if status == "DANGER":
        return target.danger_high_mg_dl + 20.0
    raise ValueError(status)


def _insert_evaluated_reading(
    session: Session,
    patient: User,
    *,
    value: float,
    measured_at: datetime,
    meal_context: MealContext | None,
) -> GlucoseRecord:
    record = GlucoseRecord(
        patient_id=patient.id,
        value=value,
        measured_at=measured_at,
        note=DEMO_NOTE,
        meal_context=meal_context,
        measurement_source=MeasurementSource.MANUAL,
        created_by=patient.id,
    )
    session.add(record)
    session.flush()

    evaluation = evaluate_glucose_reading(session, record)
    record.predicted_min_mg_dl = evaluation.predicted_min_mg_dl
    record.predicted_max_mg_dl = evaluation.predicted_max_mg_dl
    record.threshold_status = evaluation.status
    record.threshold_evaluation_source = "ML_QUANTILE_V2" if evaluation.ml_called else "DOCTOR_RULE_FALLBACK"
    if evaluation.status == "DANGER":
        record.threshold_evaluation_source = "DOCTOR_DANGER_RULE"
    if evaluation.status == "UNAVAILABLE":
        record.threshold_evaluation_source = "UNAVAILABLE"
    record.threshold_fallback_reason = evaluation.fallback_reason
    record.care_plan_target_version = evaluation.care_plan_target_version
    session.add(record)
    return record


def _seed_reading(session: Session, patient: User, target: CarePlanTarget, desired_status: str) -> GlucoseRecord:
    record = _insert_evaluated_reading(
        session,
        patient,
        value=_value_for_status(target, desired_status),
        measured_at=datetime.utcnow(),
        meal_context=None,  # forces the deterministic doctor-target fallback — see module docstring
    )
    if record.threshold_status != desired_status:
        logger.warning(
            "patient %s: wanted %s but evaluate_glucose_reading returned %s (fallback_reason=%s) — "
            "check CarePlanTarget bounds for this patient",
            patient.email,
            desired_status,
            record.threshold_status,
            record.threshold_fallback_reason,
        )
    return record


def _fill_gap_days(session: Session, patient: User, target: CarePlanTarget, today: datetime) -> int:
    """Backfill any of the last GAP_FILL_LOOKBACK_DAYS days that have zero
    glucose readings for this patient — see the module-level constants'
    docstring for why this gap exists. No-op for days that already have
    real (migrated) data."""
    today_date = today.date()
    window_start = datetime.combine(today_date - timedelta(days=GAP_FILL_LOOKBACK_DAYS), time.min)
    window_end = datetime.combine(today_date, time.min)
    existing_days = {
        m.date()
        for m in session.exec(
            select(GlucoseRecord.measured_at).where(
                GlucoseRecord.patient_id == patient.id,
                GlucoseRecord.measured_at >= window_start,
                GlucoseRecord.measured_at < window_end,
            )
        ).all()
    }

    filled_days = 0
    for offset in range(GAP_FILL_LOOKBACK_DAYS, 0, -1):
        day = today_date - timedelta(days=offset)
        if day in existing_days:
            continue
        midpoint = (target.target_low_mg_dl + target.target_high_mg_dl) / 2
        for hour, meal_context, value_offset in GAP_FILL_READINGS:
            _insert_evaluated_reading(
                session,
                patient,
                value=midpoint + value_offset,
                measured_at=datetime.combine(day, time(hour=hour)),
                meal_context=meal_context,
            )
        filled_days += 1
    return filled_days


def run_seed(*, dry_run: bool = False) -> dict:
    create_db_and_tables()
    with Session(engine) as session:
        doctor, patients = _pick_doctor_and_patients(session)
        admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
        assigned_by = admin or doctor

        _reassign_patients(session, doctor, patients, assigned_by)

        now = datetime.utcnow()
        results = []
        for patient, desired_status in zip(patients, STATUS_MIX, strict=True):
            target = _ensure_care_plan_target(session, patient, doctor)
            gap_days_filled = _fill_gap_days(session, patient, target, now)
            if gap_days_filled:
                logger.info(
                    "patient %s: filled %d gap day(s) with no prior glucose data", patient.email, gap_days_filled
                )
            record = _seed_reading(session, patient, target, desired_status)
            results.append((patient, record.threshold_status))

        if dry_run:
            session.rollback()
        else:
            session.commit()

        return {
            "doctor_email": doctor.email,
            "patients": [(p.email, status) for p, status in results],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    summary = run_seed(dry_run=args.dry_run)
    verb = "would show" if args.dry_run else "now shows"
    logger.info("demo doctor %s %s panel (login password = own email):", summary["doctor_email"], verb)
    for email, status in summary["patients"]:
        logger.info("  %s -> %s", email, status)


if __name__ == "__main__":
    main()

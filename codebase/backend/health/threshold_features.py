"""Builds the 48-value feature row the personalized-threshold quantile
regression models expect, from live Postgres data — the online counterpart
to data/NHANES/final/build_watch_feature_table.py's offline batch job.

See docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6 for what was found
when checking which of those 48 features actually exist in the live schema:
most of the time-series-derived block does (computable from GlucoseRecord
history), the clinical labs are partially there (ClinicalNote has hba1c_pct
and blood pressure; LDL/HDL/triglycerides/fasting glucose have no structured
live source at all), and demographics/comorbidities needed a new table
(patients.models.PatientProfile) that didn't exist before this.

Every feature that is genuinely unavailable live is set to NaN with its
`_is_missing` companion flag set — exactly the missing-data handling the
model was already trained to tolerate (see
data/NHANES/final/build_watch_feature_table.py's CLINICAL_NUMERIC_FIELDS).
The one exception is `overall_data_quality_score`, which has no live
equivalent and no `_is_missing` companion in the trained model (it was a
NHANES-survey-specific concept with no meaning for a live patient) — that one
is imputed with the training-set mean (93.2) rather than left as an
untested NaN, and documented here as a deliberate choice, not an oversight.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.health.models import GlucoseRecord, MealContext
from codebase.backend.medications.models import Medication, MedicationStatus
from codebase.backend.patients.models import (
    CarePlanTarget,
    ClinicalNote,
    PatientProfile,
)

# Must match data/NHANES/final/build_watch_feature_table.py exactly — these
# are the model's trained assumptions, not independent live-system choices.
TREND_MAX_GAP_MINUTES = 180
ROLLING_WINDOW_SIZES = (3, 10)
TIR_WINDOW_SIZE = 10
WATCH_HISTORY_LOOKBACK_HOURS = 24 * 7
MIN_BASELINE_POINTS = 5

# No live equivalent exists for this NHANES-survey-specific feature — see
# module docstring. Computed once from data/ml/personalized_watch/feature_table.csv.
OVERALL_DATA_QUALITY_SCORE_IMPUTED = 93.2

# Substring match against generic + brand names for the three hypoglycemia-
# risk drug classes — mirrors scripts/migrate_nhanes_demo_data.py's own
# _match_drug approach (kept independent rather than imported: that script is
# a one-off migration tool, not a shared library).
HYPOGLYCEMIA_RISK_DRUG_KEYWORDS = (
    "insulin", "glargine", "lispro", "aspart", "detemir", "degludec", "nph",
    "glyburide", "glibenclamide", "glipizide", "gliclazide", "glimepiride",  # sulfonylureas
    "repaglinide", "nateglinide",  # meglitinides
)


class CarePlanMissingError(Exception):
    """No doctor-approved CarePlanTarget exists for this patient yet — the
    caller must abstain from any DANGER/WATCH/NORMAL claim entirely, per
    the plan's acceptance criteria ("có cơ chế abstain/fallback khi thiếu
    baseline, schema sai hoặc dữ liệu không đủ"). Never treat this as
    "assume not dangerous"."""


@dataclass
class FeatureRow:
    numeric: dict[str, float | None]
    categorical: dict[str, str | None]
    danger_low_mg_dl: float
    danger_high_mg_dl: float
    target_low_mg_dl: float
    target_high_mg_dl: float
    inference_ready: bool
    fallback_reason: str | None
    care_plan_target_version: int

    def as_predictor_dict(self) -> dict[str, object]:
        return {**self.numeric, **self.categorical}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _naive_utc(value: datetime) -> datetime:
    """Match the database's timezone-naive TIMESTAMP representation."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _std_or_none(values: list[float]) -> float | None:
    return statistics.pstdev(values) if len(values) >= 2 else None


def has_medication_hypoglycemia_risk(session: Session, patient_id: UUID) -> bool:
    statement = select(Medication).where(
        Medication.patient_id == patient_id,
        Medication.status == MedicationStatus.ACTIVE,
    )
    for medication in session.exec(statement).all():
        name = medication.drug_name.lower()
        if any(keyword in name for keyword in HYPOGLYCEMIA_RISK_DRUG_KEYWORDS):
            return True
    return False


def build_feature_row(
    session: Session,
    *,
    patient_id: UUID,
    meal_context: MealContext | None,
    measured_at: datetime,
) -> FeatureRow:
    """Everything here is computed from data strictly before `measured_at` —
    the live equivalent of Giai doan A's "no feature sees the future"
    guarantee, now also "no feature sees the reading currently being
    evaluated" since that reading is what the model predicts a range for.
    """
    measured_at = _naive_utc(measured_at)
    care_plan = session.exec(
        select(CarePlanTarget)
        .where(
            CarePlanTarget.patient_id == patient_id,
            CarePlanTarget.effective_from <= measured_at,
            (CarePlanTarget.effective_to.is_(None)) | (CarePlanTarget.effective_to > measured_at),
        )
        .order_by(col(CarePlanTarget.version).desc())
    ).first()
    if care_plan is None:
        raise CarePlanMissingError(f"no CarePlanTarget for patient {patient_id}")

    prior_statement = (
        select(GlucoseRecord)
        .where(
            GlucoseRecord.patient_id == patient_id,
            GlucoseRecord.measured_at >= care_plan.effective_from,
            GlucoseRecord.measured_at < measured_at,
        )
        .order_by(col(GlucoseRecord.measured_at))
    )
    prior_records = session.exec(prior_statement).all()

    minutes_since_prev = None
    if prior_records:
        minutes_since_prev = (measured_at - prior_records[-1].measured_at).total_seconds() / 60

    context_key = meal_context or MealContext.RANDOM  # closest live analogue to the offline UNSPECIFIED fallback
    same_context_values = [r.value for r in prior_records if (r.meal_context or MealContext.RANDOM) == context_key]
    baseline_p10 = _percentile(same_context_values, 0.10)
    baseline_median = _percentile(same_context_values, 0.50)
    baseline_p90 = _percentile(same_context_values, 0.90)
    baseline_usable = len(same_context_values) >= MIN_BASELINE_POINTS

    recent_values = [r.value for r in prior_records]
    last3 = recent_values[-3:]
    last10 = recent_values[-10:]

    tir = tbr = tar = None
    if last10:
        below = sum(1 for v in last10 if v < care_plan.target_low_mg_dl)
        above = sum(1 for v in last10 if v > care_plan.target_high_mg_dl)
        n = len(last10)
        tbr, tar = 100 * below / n, 100 * above / n
        tir = 100 - tbr - tar

    lookback_7d = measured_at - timedelta(hours=WATCH_HISTORY_LOOKBACK_HOURS)
    lookback_24h = measured_at - timedelta(hours=24)
    recent_7d = [r for r in prior_records if r.measured_at >= lookback_7d]
    exceed_high_7d = sum(1 for r in recent_7d if r.value > care_plan.target_high_mg_dl)
    exceed_low_7d = sum(1 for r in recent_7d if r.value < care_plan.target_low_mg_dl)
    exceed_high_24h = sum(1 for r in recent_7d if r.measured_at >= lookback_24h and r.value > care_plan.target_high_mg_dl)
    exceed_low_24h = sum(1 for r in recent_7d if r.measured_at >= lookback_24h and r.value < care_plan.target_low_mg_dl)

    watch_records_7d = [r for r in recent_7d if r.threshold_status == "WATCH"]
    watch_count_last_7d = len(watch_records_7d)
    minutes_since_last_watch = (
        (measured_at - max(r.measured_at for r in watch_records_7d)).total_seconds() / 60
        if watch_records_7d else None
    )

    profile = session.exec(select(PatientProfile).where(PatientProfile.patient_id == patient_id)).first()
    latest_note = session.exec(
        select(ClinicalNote)
        .where(
            ClinicalNote.patient_id == patient_id,
            ClinicalNote.created_at <= measured_at,
        )
        .order_by(col(ClinicalNote.created_at).desc())
    ).first()

    numeric: dict[str, float | None] = {
        "minutes_since_prev_measurement": minutes_since_prev,
        "baseline_usable": 1.0 if baseline_usable else 0.0,
        "baseline_p10_mg_dl": baseline_p10,
        "baseline_median_mg_dl": baseline_median,
        "baseline_p90_mg_dl": baseline_p90,
        "rolling_mean_last3": _mean_or_none(last3),
        "rolling_min_last3": min(last3) if last3 else None,
        "rolling_max_last3": max(last3) if last3 else None,
        "rolling_std_last3": _std_or_none(last3),
        "rolling_mean_last10": _mean_or_none(last10),
        "rolling_min_last10": min(last10) if last10 else None,
        "rolling_max_last10": max(last10) if last10 else None,
        "rolling_std_last10": _std_or_none(last10),
        "tir_pct_last10": tir,
        "tbr_pct_last10": tbr,
        "tar_pct_last10": tar,
        "exceed_high_count_24h": float(exceed_high_24h),
        "exceed_high_count_7d": float(exceed_high_7d),
        "exceed_low_count_24h": float(exceed_low_24h),
        "exceed_low_count_7d": float(exceed_low_7d),
        "watch_count_last_7d": float(watch_count_last_7d),
        "minutes_since_last_watch": minutes_since_last_watch,
        "age_years": profile.age_years if profile else None,
        "bmi": profile.bmi if profile else None,
        "caregiver_required_flag": _bool_to_float(profile.caregiver_required_flag if profile else None),
        "overall_data_quality_score": OVERALL_DATA_QUALITY_SCORE_IMPUTED,
        "hba1c_pct": latest_note.hba1c_pct if latest_note else None,
        "fasting_glucose_mg_dl": None,  # no structured live source — see module docstring
        "ldl_mg_dl": None,
        "hdl_mg_dl": None,
        "triglycerides_mg_dl": None,
        "sbp_mean": float(latest_note.blood_pressure_systolic) if latest_note and latest_note.blood_pressure_systolic else None,
        "dbp_mean": float(latest_note.blood_pressure_diastolic) if latest_note and latest_note.blood_pressure_diastolic else None,
        "congestive_heart_failure_flag": _bool_to_float(profile.congestive_heart_failure_flag if profile else None),
        "coronary_heart_disease_flag": _bool_to_float(profile.coronary_heart_disease_flag if profile else None),
        "stroke_flag": _bool_to_float(profile.stroke_flag if profile else None),
        "family_history_diabetes_flag": _bool_to_float(profile.family_history_diabetes_flag if profile else None),
    }
    for field in ("hba1c_pct", "fasting_glucose_mg_dl", "ldl_mg_dl", "hdl_mg_dl", "triglycerides_mg_dl", "sbp_mean", "dbp_mean"):
        numeric[f"{field}_is_missing"] = 1.0 if numeric[field] is None else 0.0

    categorical: dict[str, str | None] = {
        "meal_context": (meal_context.value if meal_context else MealContext.RANDOM.value),
        "sex": profile.sex if profile else None,
        "risk_group": care_plan.risk_group.value,
        "medication_hypoglycemia_risk": (
            "HIGH_REVIEW_PRIORITY" if has_medication_hypoglycemia_risk(session, patient_id)
            else "NO_HIGH_RISK_DRUG_RECORDED"
        ),
    }

    fallback_reasons: list[str] = []
    if not baseline_usable:
        fallback_reasons.append("INSUFFICIENT_CONTEXT_BASELINE")
    if meal_context is None:
        fallback_reasons.append("MEAL_CONTEXT_MISSING")
    if (
        minutes_since_prev is not None
        and 0 <= minutes_since_prev < 30
        and meal_context != MealContext.EVENT_RECOVERY
    ):
        fallback_reasons.append("MEASUREMENT_INTERVAL_TOO_SHORT")
    if profile is None:
        fallback_reasons.append("PATIENT_PROFILE_MISSING")
    elif profile.age_years is None or profile.bmi is None or profile.sex is None:
        fallback_reasons.append("PATIENT_PROFILE_INCOMPLETE")

    return FeatureRow(
        numeric=numeric,
        categorical=categorical,
        danger_low_mg_dl=care_plan.danger_low_mg_dl,
        danger_high_mg_dl=care_plan.danger_high_mg_dl,
        target_low_mg_dl=care_plan.target_low_mg_dl,
        target_high_mg_dl=care_plan.target_high_mg_dl,
        inference_ready=not fallback_reasons,
        fallback_reason="|".join(fallback_reasons) if fallback_reasons else None,
        care_plan_target_version=care_plan.version,
    )


def _bool_to_float(value: bool | None) -> float | None:
    if value is None:
        return None
    return 1.0 if value else 0.0

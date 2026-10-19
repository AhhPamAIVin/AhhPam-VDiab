"""The live, safety-gated counterpart to data/ml/personalized_watch/serving.py:
DANGER is decided from the doctor-approved CarePlanTarget cutoffs alone, and
the quantile-regression models are never even called for a DANGER reading,
let alone allowed to override it. See
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6/7 for the design this
implements and section 8 for why abstaining (not guessing) is required when
a patient has no CarePlanTarget yet.

Model files are loaded once per process (HistGradientBoostingRegressor
inference is fast; the load itself is the only meaningfully slow step) —
see _load_models(). If the artifacts aren't present (e.g. a fresh checkout
that hasn't run data/NHANES/final/train_threshold_regression.py), evaluation
abstains rather than raising into the request path.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import joblib
from sqlmodel import Session

from codebase.backend.health.models import GlucoseRecord
from codebase.backend.health.threshold_features import CarePlanMissingError, build_feature_row

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODEL_DIR = _REPO_ROOT / "artifacts" / "personalized_watch" / "v2_quantile_regression"

_min_model = None
_max_model = None
_models_load_attempted = False
_calibration_margin_mg_dl = 0.0


def _load_models() -> tuple[object, object, float] | None:
    global _min_model, _max_model, _models_load_attempted, _calibration_margin_mg_dl
    if not _models_load_attempted:
        _models_load_attempted = True
        try:
            _min_model = joblib.load(_MODEL_DIR / "min_model.joblib")
            _max_model = joblib.load(_MODEL_DIR / "max_model.joblib")
            calibration = json.loads((_MODEL_DIR / "calibration.json").read_text(encoding="utf-8"))
            _calibration_margin_mg_dl = float(calibration["margin_mg_dl"])
        except FileNotFoundError:
            logger.warning("threshold models not found at %s — evaluation will abstain", _MODEL_DIR)
    if _min_model is None or _max_model is None:
        return None
    return _min_model, _max_model, _calibration_margin_mg_dl


@dataclass
class ThresholdEvaluation:
    status: str  # "DANGER" | "WATCH" | "NORMAL" | "UNAVAILABLE"
    predicted_min_mg_dl: float | None
    predicted_max_mg_dl: float | None
    ml_called: bool
    # The doctor-approved DANGER gate itself (CarePlanTarget.danger_low/high_mg_dl)
    # — None only when UNAVAILABLE (no CarePlanTarget to read it from at all).
    # Carried alongside predicted_min/max so a caller building a notification
    # message can always reference "your doctor's danger threshold is X", not
    # just the AI-predicted WATCH band, which doesn't exist for DANGER readings.
    danger_low_mg_dl: float | None = None
    danger_high_mg_dl: float | None = None
    fallback_reason: str | None = None
    care_plan_target_version: int | None = None


def _doctor_target_fallback(features, glucose_mg_dl: float, reason: str) -> ThresholdEvaluation:
    """Safe deterministic fallback: doctor target range, never an ML guess."""
    status = (
        "WATCH"
        if glucose_mg_dl < features.target_low_mg_dl or glucose_mg_dl > features.target_high_mg_dl
        else "NORMAL"
    )
    return ThresholdEvaluation(
        status=status,
        predicted_min_mg_dl=features.target_low_mg_dl,
        predicted_max_mg_dl=features.target_high_mg_dl,
        ml_called=False,
        danger_low_mg_dl=features.danger_low_mg_dl,
        danger_high_mg_dl=features.danger_high_mg_dl,
        fallback_reason=reason,
        care_plan_target_version=features.care_plan_target_version,
    )


def evaluate_glucose_reading(session: Session, record: GlucoseRecord) -> ThresholdEvaluation:
    """The one function anything must call to get a threshold decision for a
    GlucoseRecord — never call the models directly. Persists nothing itself;
    the caller (health/service.py's create_glucose_record) writes the result
    onto the record.
    """
    try:
        features = build_feature_row(
            session, patient_id=record.patient_id, meal_context=record.meal_context, measured_at=record.measured_at
        )
    except CarePlanMissingError:
        return ThresholdEvaluation(status="UNAVAILABLE", predicted_min_mg_dl=None, predicted_max_mg_dl=None, ml_called=False)

    # Lop 1 — the DANGER gate. Strict inequalities, matching build_personalized_
    # attention_dataset.py's own rule engine exactly: the boundary values
    # themselves are not DANGER.
    if record.value < features.danger_low_mg_dl or record.value > features.danger_high_mg_dl:
        return ThresholdEvaluation(
            status="DANGER", predicted_min_mg_dl=None, predicted_max_mg_dl=None, ml_called=False,
            danger_low_mg_dl=features.danger_low_mg_dl, danger_high_mg_dl=features.danger_high_mg_dl,
            care_plan_target_version=features.care_plan_target_version,
        )

    if not features.inference_ready:
        return _doctor_target_fallback(features, record.value, features.fallback_reason or "FEATURES_NOT_READY")

    models = _load_models()
    if models is None:
        return _doctor_target_fallback(features, record.value, "MODEL_UNAVAILABLE")
    min_model, max_model, margin = models

    import pandas as pd  # local import: keep this heavy dependency off the module's import path

    features_frame = pd.DataFrame([features.as_predictor_dict()])
    for col in features_frame.columns:
        if features_frame[col].dtype == object:
            features_frame[col] = features_frame[col].astype("category")

    predicted_min = float(min_model.predict(features_frame)[0]) - margin
    predicted_max = float(max_model.predict(features_frame)[0]) + margin
    if not math.isfinite(predicted_min) or not math.isfinite(predicted_max):
        return _doctor_target_fallback(features, record.value, "NON_FINITE_MODEL_OUTPUT")
    if predicted_min >= predicted_max:
        return _doctor_target_fallback(features, record.value, "QUANTILE_CROSSING")
    status = "WATCH" if (record.value < predicted_min or record.value > predicted_max) else "NORMAL"
    return ThresholdEvaluation(
        status=status, predicted_min_mg_dl=predicted_min, predicted_max_mg_dl=predicted_max, ml_called=True,
        danger_low_mg_dl=features.danger_low_mg_dl, danger_high_mg_dl=features.danger_high_mg_dl,
        care_plan_target_version=features.care_plan_target_version,
    )

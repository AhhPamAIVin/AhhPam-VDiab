#!/usr/bin/env python3
"""Train the quantile-regression personalized threshold (settled design in
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 7): for each measurement's
context, predict [min, max] — the patient's expected normal glucose range —
directly, conditioned on meal_context, instead of inverting a WATCH/NORMAL
classifier (Lop 3's old approach).

Reuses data/ml/personalized_watch/feature_table.csv as-is — no change to
Giai doan A. What changes is the target (glucose_mg_dl itself, not y_watch)
and, critically, the feature set: anything computed *from* the current
reading (delta, rate, deviation-from-baseline) is excluded, because it would
trivially leak the value we're now trying to predict. Rolling stats,
baseline percentiles, exceedance counts, and the static clinical block are
all computed from *prior* readings only and stay in — same
leakage-safety guarantee Giai doan A already built, just re-applied in the
other direction.

One shared model per quantile (not one model per meal_context) — RANDOM
(0.1%) and EVENT_RECOVERY (~1%) contexts don't have enough rows on their own
to train a separate model each; meal_context is a feature instead so the
model pools statistical strength across contexts while still learning
context-specific splits where the data supports it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

LOW_QUANTILE = 0.10
HIGH_QUANTILE = 0.90
RANDOM_STATE = 20260820
TARGET_COVERAGE = 0.80
MODEL_VERSION = "v2_quantile_regression"

CATEGORICAL_COLUMNS = ["meal_context", "sex", "risk_group", "medication_hypoglycemia_risk"]
BOOL_LIKE_COLUMNS = [
    "baseline_usable", "caregiver_required_flag", "congestive_heart_failure_flag",
    "coronary_heart_disease_flag", "stroke_flag", "family_history_diabetes_flag",
    "hba1c_pct_is_missing", "fasting_glucose_mg_dl_is_missing", "ldl_mg_dl_is_missing",
    "hdl_mg_dl_is_missing", "triglycerides_mg_dl_is_missing", "sbp_mean_is_missing",
    "dbp_mean_is_missing",
]

# Computed *from* the current reading — would leak the regression target
# (glucose_mg_dl) if left in. This is the mirror image of Giai doan A's own
# leakage list, not a new concept — see the module docstring above.
CURRENT_VALUE_DERIVED_COLUMNS = {
    "glucose_mg_dl",  # the target itself
    "delta_glucose_mg_dl",
    "rate_mg_dl_per_min",
    "deviation_from_baseline_median",
    "deviation_from_baseline_p10",
    "deviation_from_baseline_p90",
}


def load_regression_table(feature_table_path: Path) -> tuple[pd.DataFrame, list[str], list[str]]:
    df = pd.read_csv(feature_table_path)
    dictionary = pd.read_csv(feature_table_path.parent / "feature_dictionary.csv")
    all_feature_columns = dictionary.loc[
        dictionary["source"].isin(["time_series_derived", "static_clinical"]), "feature_name"
    ].tolist()
    regression_columns = [c for c in all_feature_columns if c not in CURRENT_VALUE_DERIVED_COLUMNS]

    for col in BOOL_LIKE_COLUMNS:
        if col in df.columns:
            df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0}).astype(float)
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    numeric = [c for c in regression_columns if c not in CATEGORICAL_COLUMNS]
    categorical = [c for c in regression_columns if c in CATEGORICAL_COLUMNS]
    return df, numeric, categorical


def build_quantile_model(quantile: float) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile", quantile=quantile,
        categorical_features="from_dtype", random_state=RANDOM_STATE,
    )


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def evaluate(df: pd.DataFrame, min_pred: np.ndarray, max_pred: np.ndarray) -> dict:
    y = df["glucose_mg_dl"].to_numpy()
    coverage = ((y >= min_pred) & (y <= max_pred)).mean()
    return {
        "n": int(len(df)),
        "pinball_loss_min": pinball_loss(y, min_pred, LOW_QUANTILE),
        "pinball_loss_max": pinball_loss(y, max_pred, HIGH_QUANTILE),
        "coverage_rate": float(coverage),  # target ~0.80 for a 10/90 quantile pair
        "mean_min_mg_dl": float(np.mean(min_pred)),
        "mean_max_mg_dl": float(np.mean(max_pred)),
        "mean_band_width_mg_dl": float(np.mean(max_pred - min_pred)),
    }


def conformal_margin(y: np.ndarray, min_pred: np.ndarray, max_pred: np.ndarray) -> float:
    """Fit a finite-sample split-conformal interval expansion on validation."""
    scores = np.maximum.reduce((min_pred - y, y - max_pred, np.zeros_like(y)))
    level = min(1.0, np.ceil((len(scores) + 1) * TARGET_COVERAGE) / len(scores))
    return float(np.quantile(scores, level, method="higher"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clinical_permutation_importance(
    test_df: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    min_model,
    max_model,
) -> dict[str, float]:
    """Measure holdout pinball-loss increase after permuting each clinical field."""
    clinical = [
        name for name in numeric + categorical
        if name in {
            "age_years", "bmi", "risk_group", "medication_hypoglycemia_risk", "hba1c_pct",
            "congestive_heart_failure_flag", "coronary_heart_disease_flag", "stroke_flag",
            "family_history_diabetes_flag",
        }
    ]
    features_frame = test_df[numeric + categorical]
    y = test_df["glucose_mg_dl"].to_numpy()
    baseline = pinball_loss(y, min_model.predict(features_frame), LOW_QUANTILE) + pinball_loss(
        y, max_model.predict(features_frame), HIGH_QUANTILE
    )
    rng = np.random.default_rng(RANDOM_STATE)
    result: dict[str, float] = {}
    for column in clinical:
        shuffled = features_frame.copy()
        shuffled[column] = rng.permutation(shuffled[column].to_numpy())
        loss = pinball_loss(y, min_model.predict(shuffled), LOW_QUANTILE) + pinball_loss(
            y, max_model.predict(shuffled), HIGH_QUANTILE
        )
        result[column] = float(loss - baseline)
    return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, default=Path("data/ml/personalized_watch/feature_table.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/personalized_watch/v2_quantile_regression"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    df, numeric, categorical = load_regression_table(args.feature_table)
    # Same population the classifier trained on: WATCH+NORMAL, DANGER excluded
    # — matches the original baseline computation's own exclusion of
    # danger-range values ("value never becomes a patient's learned normal").
    eligible = df[df["eligible_for_binary_training"] == True].copy()  # noqa: E712

    train_df = eligible[eligible["split"] == "train"]
    val_df = eligible[eligible["split"] == "validation"]
    test_df = eligible[eligible["split"] == "test"]

    train_features, y_train = train_df[numeric + categorical], train_df["glucose_mg_dl"].to_numpy()
    val_features, test_features = val_df[numeric + categorical], test_df[numeric + categorical]

    min_model = build_quantile_model(LOW_QUANTILE)
    max_model = build_quantile_model(HIGH_QUANTILE)
    min_model.fit(train_features, y_train)
    max_model.fit(train_features, y_train)
    min_path = args.output_dir / "min_model.joblib"
    max_path = args.output_dir / "max_model.joblib"
    joblib.dump(min_model, min_path)
    joblib.dump(max_model, max_path)

    val_min_pred, val_max_pred = min_model.predict(val_features), max_model.predict(val_features)
    test_min_pred, test_max_pred = min_model.predict(test_features), max_model.predict(test_features)

    margin = conformal_margin(val_df["glucose_mg_dl"].to_numpy(), val_min_pred, val_max_pred)
    calibration = {
        "method": "split_conformal_symmetric_interval_expansion",
        "fit_split": "validation",
        "target_coverage": TARGET_COVERAGE,
        "margin_mg_dl": margin,
        "validation_rows": int(len(val_df)),
    }
    (args.output_dir / "calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    val_min_cal, val_max_cal = val_min_pred - margin, val_max_pred + margin
    test_min_cal, test_max_cal = test_min_pred - margin, test_max_pred + margin

    metrics = {
        "model_version": MODEL_VERSION,
        "low_quantile": LOW_QUANTILE,
        "high_quantile": HIGH_QUANTILE,
        "target_coverage": TARGET_COVERAGE,
        "calibration": calibration,
        "validation_raw": evaluate(val_df, val_min_pred, val_max_pred),
        "validation": evaluate(val_df, val_min_cal, val_max_cal),
        "test_patient_holdout_raw": evaluate(test_df, test_min_pred, test_max_pred),
        "test_patient_holdout": evaluate(test_df, test_min_cal, test_max_cal),
        "validation_by_meal_context": {},
        "validation_by_risk_group": {},
        "clinical_feature_permutation_importance_test": clinical_permutation_importance(
            test_df, numeric, categorical, min_model, max_model
        ),
    }

    for context, group in val_df.groupby("meal_context", observed=True):
        idx = val_df.index.get_indexer(group.index)
        metrics["validation_by_meal_context"][str(context)] = evaluate(group, val_min_cal[idx], val_max_cal[idx])
    for risk_group, group in val_df.groupby("risk_group", observed=True):
        idx = val_df.index.get_indexer(group.index)
        metrics["validation_by_risk_group"][str(risk_group)] = evaluate(group, val_min_cal[idx], val_max_cal[idx])

    (args.output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (args.output_dir / "feature_schema.json").write_text(
        json.dumps({"numeric_features": numeric, "categorical_features": categorical}, indent=2), encoding="utf-8"
    )
    manifest = {
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "feature_table": str(args.feature_table),
        "feature_table_sha256": sha256(args.feature_table),
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(val_df)),
        "test_rows_patient_holdout": int(len(test_df)),
        "random_state": RANDOM_STATE,
        "low_quantile": LOW_QUANTILE,
        "high_quantile": HIGH_QUANTILE,
        "target_coverage": TARGET_COVERAGE,
        "calibration_margin_mg_dl": margin,
        "artifacts": {"min_model.joblib": sha256(min_path), "max_model.joblib": sha256(max_path)},
    }
    (args.output_dir / "training_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    test_metrics = metrics["test_patient_holdout"]
    model_card = f"""# Model card — personalized WATCH quantile v2

## Intended use

Predict a patient-context early-attention interval for non-DANGER glucose readings. The doctor-approved DANGER gate always runs first. This model does not change treatment or clinical thresholds.

## Data and split

- Synthetic TYPE_2 dataset only; no real-patient clinical validation.
- Train: {len(train_df):,} rows; validation: {len(val_df):,}; patient-holdout test: {len(test_df):,}.
- Feature table SHA-256: `{manifest['feature_table_sha256']}`.

## Calibration and performance

- Raw quantiles: {LOW_QUANTILE:.2f}/{HIGH_QUANTILE:.2f}; target coverage: {TARGET_COVERAGE:.0%}.
- Split-conformal margin fitted only on validation: {margin:.3f} mg/dL.
- Validation calibrated coverage: {metrics['validation']['coverage_rate']:.4f}.
- Patient-holdout calibrated coverage: {test_metrics['coverage_rate']:.4f}.
- Patient-holdout mean band width: {test_metrics['mean_band_width_mg_dl']:.2f} mg/dL.

## Safety and fallback

- DANGER is decided before ML and ML is not called for DANGER.
- Non-finite output or quantile crossing falls back to the doctor target range.
- Missing model, insufficient baseline, missing meal context, or incomplete patient profile also uses the doctor target range.

## Limitations

- Synthetic measurements and weak rule labels are not evidence of clinical benefit.
- RANDOM/unscheduled measurements are sparse and out-of-distribution.
- Real-patient evaluation and doctor-confirmed WATCH labels are still required.
- Clinical feature importance is reported in `metrics.json`; a small value means the feature adds little predictive information in this synthetic dataset.
"""
    (args.output_dir / "model_card.md").write_text(model_card, encoding="utf-8")

    print("Validation:", json.dumps(metrics["validation"], indent=2))
    print("\nTest (patient-holdout):", json.dumps(metrics["test_patient_holdout"], indent=2))
    print("\nBy meal_context (validation) — does the band actually differ by context?")
    for context, stats in metrics["validation_by_meal_context"].items():
        print(f"  {context:16s} n={stats['n']:6d}  mean_min={stats['mean_min_mg_dl']:6.1f}  "
              f"mean_max={stats['mean_max_mg_dl']:6.1f}  width={stats['mean_band_width_mg_dl']:6.1f}  "
              f"coverage={stats['coverage_rate']:.3f}")
    print("\nBy risk_group (validation):")
    for risk_group, stats in metrics["validation_by_risk_group"].items():
        print(f"  {risk_group:20s} n={stats['n']:6d}  coverage={stats['coverage_rate']:.3f}  "
              f"width={stats['mean_band_width_mg_dl']:6.1f}")


if __name__ == "__main__":
    main()

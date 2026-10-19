#!/usr/bin/env python3
"""Run the production quantile-v2 decision contract in offline shadow mode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from train_threshold_regression import load_regression_table

DANGER_HEAVY_THRESHOLD = 0.50
STATUSES = ("DANGER", "WATCH", "NORMAL", "UNAVAILABLE")


def timing_bucket(gap: float) -> str:
    if pd.isna(gap):
        return "no_prior_measurement"
    if gap < 30:
        return "very_tight_lt_30min"
    if gap <= 180:
        return "normal_30min_3h"
    return "sparse_gt_3h"


def agreement_report(df: pd.DataFrame) -> dict:
    confusion = {
        rule: {
            prediction: int(((df["rule_status"] == rule) & (df["ml_status"] == prediction)).sum())
            for prediction in STATUSES
        }
        for rule in STATUSES
        if (df["rule_status"] == rule).any()
    }
    false_negative = int(((df["rule_status"] == "WATCH") & (df["ml_status"] == "NORMAL")).sum())
    false_positive = int(((df["rule_status"] == "NORMAL") & (df["ml_status"] == "WATCH")).sum())
    return {
        "n": int(len(df)),
        "agreement_rate": float((df["rule_status"] == df["ml_status"]).mean()) if len(df) else None,
        "confusion_rule_rows_vs_ml_columns": confusion,
        "false_negative_watch_missed_by_ml": false_negative,
        "false_positive_normal_flagged_watch_by_ml": false_positive,
        "n_nondanger": int((df["rule_status"] != "DANGER").sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, default=Path("data/ml/personalized_watch/feature_table.csv"))
    parser.add_argument(
        "--monitoring-profiles",
        type=Path,
        default=Path("data/knowledge_structured/patient_cgm_monitoring_profiles.csv"),
    )
    parser.add_argument("--model-dir", type=Path, default=Path("artifacts/personalized_watch/v2_quantile_regression"))
    parser.add_argument("--output", type=Path, default=Path("data/ml/personalized_watch/shadow_mode_report.json"))
    args = parser.parse_args()

    df, numeric, categorical = load_regression_table(args.feature_table)
    profiles = pd.read_csv(
        args.monitoring_profiles,
        usecols=["patient_id", "danger_low_mg_dl", "danger_high_mg_dl"],
    )
    profiles["patient_id"] = profiles["patient_id"].astype(str)
    df["patient_id"] = df["patient_id"].astype(str)
    df = df.merge(profiles, on="patient_id", how="left", validate="many_to_one")
    missing_danger_threshold = df["danger_low_mg_dl"].isna() | df["danger_high_mg_dl"].isna()
    min_model = joblib.load(args.model_dir / "min_model.joblib")
    max_model = joblib.load(args.model_dir / "max_model.joblib")
    calibration = json.loads((args.model_dir / "calibration.json").read_text(encoding="utf-8"))
    margin = float(calibration["margin_mg_dl"])

    is_danger = (df["glucose_mg_dl"] < df["danger_low_mg_dl"]) | (
        df["glucose_mg_dl"] > df["danger_high_mg_dl"]
    )
    eligible = ~is_danger & ~missing_danger_threshold
    predictions_min = pd.Series(np.nan, index=df.index)
    predictions_max = pd.Series(np.nan, index=df.index)
    features_frame = df.loc[eligible, numeric + categorical]
    predictions_min.loc[eligible] = min_model.predict(features_frame) - margin
    predictions_max.loc[eligible] = max_model.predict(features_frame) + margin

    invalid = eligible & (
        ~np.isfinite(predictions_min) | ~np.isfinite(predictions_max) | (predictions_min >= predictions_max)
    )
    df["ml_status"] = "DANGER"
    df.loc[missing_danger_threshold, "ml_status"] = "UNAVAILABLE"
    df.loc[invalid, "ml_status"] = "UNAVAILABLE"
    valid = eligible & ~invalid
    outside = (df["glucose_mg_dl"] < predictions_min) | (df["glucose_mg_dl"] > predictions_max)
    df.loc[valid & outside, "ml_status"] = "WATCH"
    df.loc[valid & ~outside, "ml_status"] = "NORMAL"
    df["predicted_min_mg_dl"] = predictions_min
    df["predicted_max_mg_dl"] = predictions_max
    df["rule_status"] = df["status"]
    df["timing_bucket"] = df["minutes_since_prev_measurement"].apply(timing_bucket)
    df["meal_context_group"] = df["meal_context"].apply(
        lambda value: "structured"
        if value in ("BEFORE_MEAL", "AFTER_MEAL", "FASTING", "BEFORE_SLEEP")
        else "irregular"
    )
    danger_rate = df.groupby("patient_id")["rule_status"].apply(lambda values: (values == "DANGER").mean())
    df["danger_bucket"] = df["patient_id"].map(
        (danger_rate > DANGER_HEAVY_THRESHOLD).map({True: "danger_heavy", False: "danger_light"})
    )

    report = {
        "model_version": "v2_quantile_regression",
        "calibration": calibration,
        "confusion_orientation": "outer keys are rule labels; inner keys are ML labels",
        "overall": agreement_report(df),
        "by_split": {str(k): agreement_report(g) for k, g in df.groupby("split")},
        "by_risk_group": {str(k): agreement_report(g) for k, g in df.groupby("risk_group", observed=True)},
        "by_danger_bucket": {str(k): agreement_report(g) for k, g in df.groupby("danger_bucket")},
        "by_timing_bucket": {str(k): agreement_report(g) for k, g in df.groupby("timing_bucket")},
        "by_meal_context_group": {str(k): agreement_report(g) for k, g in df.groupby("meal_context_group")},
        "invalid_interval_count": int(invalid.sum()),
        "missing_doctor_threshold_count": int(missing_danger_threshold.sum()),
        "danger_rows_ml_called": 0,
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["overall"], indent=2, ensure_ascii=False))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()

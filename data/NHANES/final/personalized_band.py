#!/usr/bin/env python3
"""Derive Lop 3's personalized early-warning band (Giai doan B, step 8 in
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md).

The trained Lop 2 model (train_watch_model.py) predicts P(WATCH) for a
measurement — it was never trained to output a threshold. This script
inverts it along the glucose axis instead of training anything new: for one
patient's *current* context (their baseline stats, recent trend, rolling
history, and static clinical block — everything except the glucose value
itself), it sweeps a grid of hypothetical glucose_mg_dl values through the
already-trained model and reports the two edges of the region around the
patient's baseline where P(WATCH) stays below the operating threshold. Those
edges are the "if your glucose crosses X you're likely entering WATCH"
numbers described in plan section 4 (Lop 3) and section 1 (the original ask).

Only the features that mechanically depend on the hypothetical current
reading are recomputed per candidate (glucose_mg_dl, delta, rate, deviation
from baseline). Every rolling/exceedance/static feature reflects *past*
measurements and is held fixed at the row's real historical values — a
hypothetical new reading cannot rewrite history.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

TREND_MAX_GAP_MINUTES = 180  # must match build_watch_feature_table.py
OPERATING_THRESHOLD = 0.5
SCAN_LOW_MG_DL = 40
SCAN_HIGH_MG_DL = 400
SCAN_STEP_MG_DL = 1

CATEGORICAL_COLUMNS = ["meal_context", "sex", "risk_group", "medication_hypoglycemia_risk"]


def build_candidate_frame(row: pd.Series, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    candidates = np.arange(SCAN_LOW_MG_DL, SCAN_HIGH_MG_DL + 1, SCAN_STEP_MG_DL, dtype=float)
    frame = pd.DataFrame([row[numeric + categorical]] * len(candidates)).reset_index(drop=True)
    frame["glucose_mg_dl"] = candidates

    prev_value = None
    if pd.notna(row.get("delta_glucose_mg_dl")) and row.get("delta_glucose_mg_dl") != "":
        prev_value = float(row["glucose_mg_dl"]) - float(row["delta_glucose_mg_dl"])

    if prev_value is not None:
        frame["delta_glucose_mg_dl"] = candidates - prev_value
        gap = row.get("minutes_since_prev_measurement")
        if pd.notna(gap) and gap != "" and 0 < float(gap) <= TREND_MAX_GAP_MINUTES:
            frame["rate_mg_dl_per_min"] = frame["delta_glucose_mg_dl"] / float(gap)
        else:
            frame["rate_mg_dl_per_min"] = np.nan
    else:
        frame["delta_glucose_mg_dl"] = np.nan
        frame["rate_mg_dl_per_min"] = np.nan

    for col, baseline_col in (
        ("deviation_from_baseline_median", "baseline_median_mg_dl"),
        ("deviation_from_baseline_p10", "baseline_p10_mg_dl"),
        ("deviation_from_baseline_p90", "baseline_p90_mg_dl"),
    ):
        baseline_value = row.get(baseline_col)
        if pd.notna(baseline_value) and baseline_value != "":
            frame[col] = candidates - float(baseline_value)
        else:
            frame[col] = np.nan

    for col in categorical:
        frame[col] = frame[col].astype("category")

    return frame, candidates


def find_band(candidates: np.ndarray, prob: np.ndarray, anchor: float, threshold: float) -> dict:
    """Scans outward from `anchor` (the patient's own baseline median) in
    both directions and reports the first glucose value on each side where
    P(WATCH) crosses `threshold`. Returns None for a side with no crossing
    inside [SCAN_LOW_MG_DL, SCAN_HIGH_MG_DL] — abstain rather than guess,
    per the plan's abstain/fallback principle (section 8)."""
    anchor_idx = int(np.argmin(np.abs(candidates - anchor)))

    if prob[anchor_idx] >= threshold:
        return {
            "anchor_glucose_mg_dl": float(candidates[anchor_idx]),
            "anchor_probability": float(prob[anchor_idx]),
            "low_band_mg_dl": None,
            "high_band_mg_dl": None,
            "note": "P(WATCH) already >= threshold at the patient's own baseline — band not meaningful, abstain",
        }

    low_band = next((float(candidates[i]) for i in range(anchor_idx, -1, -1) if prob[i] >= threshold), None)
    high_band = next((float(candidates[i]) for i in range(anchor_idx, len(candidates)) if prob[i] >= threshold), None)

    note = None
    if low_band is None and high_band is None:
        note = f"no crossing found within [{SCAN_LOW_MG_DL}, {SCAN_HIGH_MG_DL}] mg/dL — abstain"

    return {
        "anchor_glucose_mg_dl": float(candidates[anchor_idx]),
        "anchor_probability": float(prob[anchor_idx]),
        "low_band_mg_dl": low_band,
        "high_band_mg_dl": high_band,
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, default=Path("data/ml/personalized_watch/feature_table.csv"))
    parser.add_argument("--model-dir", type=Path, default=Path("artifacts/personalized_watch/v1_hgb"))
    parser.add_argument("--threshold", type=float, default=OPERATING_THRESHOLD)
    parser.add_argument(
        "--sample-patients", type=int, default=20, help="Compute a band for the latest test-split row of N patients"
    )
    parser.add_argument("--output", type=Path, default=Path("data/ml/personalized_watch/personalized_bands_sample.csv"))
    args = parser.parse_args()

    df = pd.read_csv(args.feature_table)
    dictionary = pd.read_csv(args.feature_table.parent / "feature_dictionary.csv")
    feature_columns = dictionary.loc[
        dictionary["source"].isin(["time_series_derived", "static_clinical"]), "feature_name"
    ].tolist()
    numeric = [c for c in feature_columns if c not in CATEGORICAL_COLUMNS]
    categorical = [c for c in feature_columns if c in CATEGORICAL_COLUMNS]

    bool_like = [c for c in numeric if c.endswith("_is_missing") or c in (
        "baseline_usable", "caregiver_required_flag", "congestive_heart_failure_flag",
        "coronary_heart_disease_flag", "stroke_flag", "family_history_diabetes_flag",
    )]
    for col in bool_like:
        df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0}).astype(float)
    for col in categorical:
        df[col] = df[col].astype("category")

    test_rows = df[(df["split"] == "test") & (df["eligible_for_binary_training"] == True)]  # noqa: E712
    latest_per_patient = test_rows.sort_values("measured_at").groupby("patient_id").tail(1)
    sample = latest_per_patient.head(args.sample_patients)

    calibrators = {
        p.stem.replace("calibrated_", ""): joblib.load(p)
        for p in args.model_dir.glob("calibrated_*.joblib")
    }
    fallback_model = joblib.load(args.model_dir / "model.joblib")

    results = []
    for _, row in sample.iterrows():
        model = calibrators.get(row["risk_group"], fallback_model)
        frame, candidates = build_candidate_frame(row, numeric, categorical)
        prob = model.predict_proba(frame[numeric + categorical])[:, 1]
        anchor = float(row.get("baseline_median_mg_dl")) if pd.notna(row.get("baseline_median_mg_dl")) and row.get("baseline_median_mg_dl") != "" else float(row["glucose_mg_dl"])
        band = find_band(candidates, prob, anchor, args.threshold)
        results.append({
            "patient_id": row["patient_id"],
            "risk_group": row["risk_group"],
            "measured_at": row["measured_at"],
            "actual_glucose_mg_dl": row["glucose_mg_dl"],
            "actual_status": row["status"],
            **band,
        })

    out_df = pd.DataFrame(results)
    out_df.to_csv(args.output, index=False)
    print(out_df.to_string(index=False))
    print(f"\nwrote {len(out_df)} personalized bands -> {args.output}")


if __name__ == "__main__":
    main()

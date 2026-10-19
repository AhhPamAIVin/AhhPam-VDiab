#!/usr/bin/env python3
"""Train and evaluate the personalized-WATCH classifier (Giai doan B in
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md).

Reads data/ml/personalized_watch/feature_table.csv (built by
build_watch_feature_table.py), trains a Logistic Regression baseline and a
HistGradientBoosting main model on the pre-assigned `split` column
(train/validation), evaluates both overall and broken down by risk_group and
by the DANGER-heavy patient cohort, calibrates the HGB model's probabilities
separately per risk_group, and writes everything artifacts/personalized_watch/
expects per the plan's Giai doan B deliverable list.

The `test` split (patient-holdout) is loaded only to report final numbers —
never touched during fitting, imputing, scaling, or calibration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

MODEL_VERSION_LOGREG = "v0_logreg"
MODEL_VERSION_HGB = "v1_hgb"
OPERATING_THRESHOLD = 0.5  # see plan section 4 — tuning this is a listed open decision
DANGER_HEAVY_THRESHOLD = 0.50
GROUP_KFOLD_SPLITS = 5
RANDOM_STATE = 20260820

CATEGORICAL_COLUMNS = ["meal_context", "sex", "risk_group", "medication_hypoglycemia_risk"]
BOOLEAN_LIKE_COLUMNS = [
    "baseline_usable",
    "caregiver_required_flag",
    "congestive_heart_failure_flag",
    "coronary_heart_disease_flag",
    "stroke_flag",
    "family_history_diabetes_flag",
    "hba1c_pct_is_missing",
    "fasting_glucose_mg_dl_is_missing",
    "ldl_mg_dl_is_missing",
    "hdl_mg_dl_is_missing",
    "triglycerides_mg_dl_is_missing",
    "sbp_mean_is_missing",
    "dbp_mean_is_missing",
]


def load_feature_table(path: Path) -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_csv(path)
    dictionary = pd.read_csv(path.parent / "feature_dictionary.csv")
    feature_columns = dictionary.loc[
        dictionary["source"].isin(["time_series_derived", "static_clinical"]), "feature_name"
    ].tolist()
    # Booleans round-trip through CSV as the strings "True"/"False" — turn
    # them into 0/1 now so every downstream model sees plain numerics.
    for col in BOOLEAN_LIKE_COLUMNS:
        if col in df.columns:
            df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0}).astype("float")
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")
    return df, feature_columns


def numeric_and_categorical(feature_columns: list[str]) -> tuple[list[str], list[str]]:
    categorical = [c for c in feature_columns if c in CATEGORICAL_COLUMNS]
    numeric = [c for c in feature_columns if c not in CATEGORICAL_COLUMNS]
    return numeric, categorical


def build_logreg_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    preprocessing = ColumnTransformer([
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), numeric),
        ("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), categorical),
    ])
    return Pipeline([
        ("preprocessing", preprocessing),
        ("model", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE)),
    ])


def build_hgb_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    # HistGradientBoosting handles NaN and categorical columns natively — no
    # imputer/scaler/one-hot needed, unlike the Logistic Regression path.
    # categorical_features="from_dtype" reads pandas 'category' dtype columns
    # directly off the input DataFrame, so this pipeline must always be
    # fit/predicted on a DataFrame slice (not a numpy array) with those
    # columns still cast to 'category' — see load_feature_table().
    model = HistGradientBoostingClassifier(
        categorical_features="from_dtype",
        class_weight="balanced",
        random_state=RANDOM_STATE,
    )
    return Pipeline([("model", model)])


def compute_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = OPERATING_THRESHOLD) -> dict:
    y_pred = (y_prob >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "n": int(len(y_true)),
        "n_watch": int(y_true.sum()),
        "n_normal": int(len(y_true) - y_true.sum()),
        "precision_watch": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall_watch": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1_watch": float(f1_score(y_true, y_pred, zero_division=0)),
        "false_negative_rate_watch": float(fn / (fn + tp)) if (fn + tp) else None,
        "pr_auc": float(average_precision_score(y_true, y_prob)) if len(set(y_true)) > 1 else None,
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(set(y_true)) > 1 else None,
        "brier_score": float(brier_score_loss(y_true, y_prob)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "operating_threshold": threshold,
    }


def metrics_by_group(df: pd.DataFrame, y_prob: np.ndarray, group_col: str) -> dict:
    out = {}
    for value in sorted(df[group_col].dropna().unique(), key=str):
        mask = (df[group_col] == value).to_numpy()
        if mask.sum() == 0:
            continue
        out[str(value)] = compute_metrics(df["y_watch"].to_numpy()[mask], y_prob[mask])
    return out


def add_danger_heavy_flag(full_df: pd.DataFrame) -> pd.DataFrame:
    """full_df must include DANGER rows (eligible_for_binary_training=False)
    so the per-patient danger rate reflects the same population the
    diagnosis in plan section 3.3 was computed from."""
    danger_rate = full_df.groupby("patient_id")["status"].apply(lambda s: (s == "DANGER").mean())
    bucket = (danger_rate > DANGER_HEAVY_THRESHOLD).map({True: "danger_heavy", False: "danger_light"})
    return full_df["patient_id"].map(bucket).rename("danger_bucket")


def group_kfold_stability(
    df_train_pool: pd.DataFrame, numeric: list[str], categorical: list[str]
) -> dict:
    """5-fold GroupKFold by patient_id on the train+validation pool — a
    stability check per plan section 6.3, not a substitute for the
    patient-holdout test split, which is never used here."""
    X = df_train_pool[numeric + categorical]
    y = df_train_pool["y_watch"].to_numpy()
    groups = df_train_pool["patient_id"].to_numpy()
    scores = []
    for train_idx, test_idx in GroupKFold(n_splits=GROUP_KFOLD_SPLITS).split(X, y, groups):
        pipeline = build_hgb_pipeline(numeric, categorical)
        pipeline.fit(X.iloc[train_idx], y[train_idx])
        prob = pipeline.predict_proba(X.iloc[test_idx])[:, 1]
        scores.append(average_precision_score(y[test_idx], prob))
    return {"pr_auc_mean": float(np.mean(scores)), "pr_auc_std": float(np.std(scores)), "folds": GROUP_KFOLD_SPLITS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, default=Path("data/ml/personalized_watch/feature_table.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/personalized_watch"))
    args = parser.parse_args()

    full_df, feature_columns = load_feature_table(args.feature_table)
    numeric, categorical = numeric_and_categorical(feature_columns)
    full_df["danger_bucket"] = add_danger_heavy_flag(full_df)

    eligible = full_df[full_df["eligible_for_binary_training"] == True].copy()  # noqa: E712
    eligible["y_watch"] = eligible["y_watch"].astype(int)

    train_df = eligible[eligible["split"] == "train"]
    val_df = eligible[eligible["split"] == "validation"]
    test_df = eligible[eligible["split"] == "test"]

    X_train, y_train = train_df[numeric + categorical], train_df["y_watch"].to_numpy()
    X_val = val_df[numeric + categorical]
    X_test = test_df[numeric + categorical]

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # ---- v0: Logistic Regression baseline ----
    logreg = build_logreg_pipeline(numeric, categorical)
    logreg.fit(X_train, y_train)
    logreg_dir = args.output_dir / MODEL_VERSION_LOGREG
    logreg_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(logreg, logreg_dir / "model.joblib")

    logreg_val_prob = logreg.predict_proba(X_val)[:, 1]
    logreg_test_prob = logreg.predict_proba(X_test)[:, 1]
    logreg_metrics = {
        "model_version": MODEL_VERSION_LOGREG,
        "validation": compute_metrics(val_df["y_watch"].to_numpy(), logreg_val_prob),
        "validation_by_risk_group": metrics_by_group(val_df, logreg_val_prob, "risk_group"),
        "validation_by_danger_bucket": metrics_by_group(val_df, logreg_val_prob, "danger_bucket"),
        "test_patient_holdout": compute_metrics(test_df["y_watch"].to_numpy(), logreg_test_prob),
    }
    (logreg_dir / "metrics.json").write_text(json.dumps(logreg_metrics, indent=2), encoding="utf-8")

    # ---- v1: HistGradientBoosting main model ----
    hgb = build_hgb_pipeline(numeric, categorical)
    hgb.fit(X_train, y_train)
    hgb_dir = args.output_dir / MODEL_VERSION_HGB
    hgb_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(hgb, hgb_dir / "model.joblib")

    hgb_val_prob = hgb.predict_proba(X_val)[:, 1]
    hgb_test_prob = hgb.predict_proba(X_test)[:, 1]

    # ---- calibrate per risk_group, fit only on validation rows (never train, never test) ----
    calibrated_by_group: dict[str, CalibratedClassifierCV] = {}
    calibration_report: dict[str, dict] = {}
    for risk_group in sorted(val_df["risk_group"].dropna().unique(), key=str):
        group_val = val_df[val_df["risk_group"] == risk_group]
        X_group_val = group_val[numeric + categorical]
        y_group_val = group_val["y_watch"].to_numpy()
        calibrator = CalibratedClassifierCV(FrozenEstimator(hgb), method="isotonic")
        calibrator.fit(X_group_val, y_group_val)
        calibrated_by_group[str(risk_group)] = calibrator
        joblib.dump(calibrator, hgb_dir / f"calibrated_{risk_group}.joblib")

        uncalibrated_prob = hgb.predict_proba(X_group_val)[:, 1]
        calibrated_prob = calibrator.predict_proba(X_group_val)[:, 1]
        calibration_report[str(risk_group)] = {
            "n": int(len(y_group_val)),
            "brier_before": float(brier_score_loss(y_group_val, uncalibrated_prob)),
            "brier_after": float(brier_score_loss(y_group_val, calibrated_prob)),
        }

    stability = group_kfold_stability(pd.concat([train_df, val_df]), numeric, categorical)

    hgb_metrics = {
        "model_version": MODEL_VERSION_HGB,
        "operating_threshold": OPERATING_THRESHOLD,
        "validation": compute_metrics(val_df["y_watch"].to_numpy(), hgb_val_prob),
        "validation_by_risk_group": metrics_by_group(val_df, hgb_val_prob, "risk_group"),
        "validation_by_danger_bucket": metrics_by_group(val_df, hgb_val_prob, "danger_bucket"),
        "test_patient_holdout": compute_metrics(test_df["y_watch"].to_numpy(), hgb_test_prob),
        "test_patient_holdout_by_risk_group": metrics_by_group(test_df, hgb_test_prob, "risk_group"),
        "test_patient_holdout_by_danger_bucket": metrics_by_group(test_df, hgb_test_prob, "danger_bucket"),
        "calibration_by_risk_group": calibration_report,
        "group_kfold_stability_train_pool": stability,
        "vs_logreg_baseline": {
            "logreg_pr_auc_validation": logreg_metrics["validation"]["pr_auc"],
            "hgb_pr_auc_validation": compute_metrics(val_df["y_watch"].to_numpy(), hgb_val_prob)["pr_auc"],
        },
    }
    (hgb_dir / "metrics.json").write_text(json.dumps(hgb_metrics, indent=2), encoding="utf-8")

    feature_schema = {"numeric_features": numeric, "categorical_features": categorical}
    for d in (logreg_dir, hgb_dir):
        (d / "feature_schema.json").write_text(json.dumps(feature_schema, indent=2), encoding="utf-8")
        (d / "training_manifest.json").write_text(json.dumps({
            "feature_table": str(args.feature_table),
            "train_rows": len(train_df),
            "validation_rows": len(val_df),
            "test_rows_patient_holdout": len(test_df),
            "random_state": RANDOM_STATE,
        }, indent=2), encoding="utf-8")

    print("Logistic Regression (v0):")
    print(json.dumps(logreg_metrics["validation"], indent=2))
    print("\nHistGradientBoosting (v1):")
    print(json.dumps(hgb_metrics["validation"], indent=2))
    print("\nHGB by risk_group (validation):")
    print(json.dumps(hgb_metrics["validation_by_risk_group"], indent=2))
    print("\nHGB by danger_bucket (validation):")
    print(json.dumps(hgb_metrics["validation_by_danger_bucket"], indent=2))
    print("\nGroupKFold stability (train_pool, HGB, PR-AUC):", stability)
    print("\nPatient-holdout test (HGB):")
    print(json.dumps(hgb_metrics["test_patient_holdout"], indent=2))


if __name__ == "__main__":
    main()

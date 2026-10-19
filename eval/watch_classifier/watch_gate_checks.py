"""Threshold checks for the personalized-WATCH offline eval/CI gate.

Pure, side-effect-free functions that read already-trained artifacts (never
retrain, never touch codebase/backend/health/*) and return a list of
structured check results:

    {"id": str, "description": str, "actual": Any, "threshold": str, "passed": bool}

Used by two callers that must never disagree about pass/fail:

1. test_v2_quantile_thresholds.py / test_v1_hgb_known_separability.py
   (`pytest eval/watch_classifier/` — asserts on "passed").
2. gate.py (`make eval-watch-classifier` — writes the same
   actual/threshold/passed detail into a JSON report).

Threshold rationale (see also docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md
section 6.5/8 and artifacts/personalized_watch/v2_quantile_regression's own
model_card.md/metrics.json): every bound below is the artifact's own stated
target with a deliberate buffer above the currently-observed value, not an
arbitrary number. Where a bound is tightened for one split (validation, the
conformal calibration fit split) vs. loosened for another (patient-holdout
test, genuinely unseen patients), that mirrors the model card's own
distinction between the two.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
V2_DIR = REPO_ROOT / "artifacts" / "personalized_watch" / "v2_quantile_regression"
V1_DIR = REPO_ROOT / "artifacts" / "personalized_watch" / "v1_hgb"
FEATURE_TABLE = REPO_ROOT / "data" / "ml" / "personalized_watch" / "feature_table.csv"

# Mirrors train_threshold_regression.py's CURRENT_VALUE_DERIVED_COLUMNS —
# duplicated (not imported) so this check still works even if that module's
# import path changes; test_regression_no_leakage.py is the source of truth
# that the two stay in sync (test_exclusion_list_matches_module_constant).
CURRENT_VALUE_DERIVED_COLUMNS = {
    "glucose_mg_dl",
    "delta_glucose_mg_dl",
    "rate_mg_dl_per_min",
    "deviation_from_baseline_median",
    "deviation_from_baseline_p10",
    "deviation_from_baseline_p90",
}

# Minimum row count for a validation_by_meal_context / by_risk_group slice
# to be gated at all — below this a single unlucky reading swings the metric
# too much to be a meaningful CI signal. RANDOM (n=17 in the current table)
# falls under this and is reported but not gated; see v1_hgb/v2 model cards'
# own "RANDOM context sparse and out-of-distribution" caveat.
MIN_SUBGROUP_N = 100


def _check(id_: str, description: str, actual: object, threshold: str, passed: bool) -> dict:
    return {"id": id_, "description": description, "actual": actual, "threshold": threshold, "passed": bool(passed)}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_v2_metrics() -> dict:
    return load_json(V2_DIR / "metrics.json")


def load_v2_feature_schema() -> dict:
    return load_json(V2_DIR / "feature_schema.json")


def load_v1_metrics() -> dict:
    return load_json(V1_DIR / "metrics.json")


# ---- v2_quantile_regression (the live model) --------------------------------


def check_model_identity(metrics: dict) -> list[dict]:
    return [
        _check(
            "v2_model_version",
            "metrics.json identifies itself as the expected model version",
            metrics.get("model_version"),
            "== 'v2_quantile_regression'",
            metrics.get("model_version") == "v2_quantile_regression",
        ),
        _check(
            "v2_quantiles",
            "low/high quantiles match the documented 0.10/0.90 split",
            [metrics.get("low_quantile"), metrics.get("high_quantile")],
            "== [0.1, 0.9]",
            metrics.get("low_quantile") == 0.1 and metrics.get("high_quantile") == 0.9,
        ),
        _check(
            "v2_target_coverage_config",
            "declared target_coverage matches the calibration block's target_coverage",
            [metrics.get("target_coverage"), metrics.get("calibration", {}).get("target_coverage")],
            "both == 0.8",
            metrics.get("target_coverage") == 0.8 and metrics.get("calibration", {}).get("target_coverage") == 0.8,
        ),
    ]


def check_coverage(metrics: dict) -> list[dict]:
    checks = []
    val = metrics.get("validation", {})
    test = metrics.get("test_patient_holdout", {})

    # Validation is the split the conformal margin is fit on — tightest band
    # around the 80% target (target_coverage +/- 0.07).
    checks.append(
        _check(
            "v2_coverage_validation",
            "calibrated coverage on validation (the conformal fit split) stays near the 80% target",
            val.get("coverage_rate"),
            "in [0.73, 0.87]",
            val.get("coverage_rate") is not None and 0.73 <= val["coverage_rate"] <= 0.87,
        )
    )
    # Patient-holdout test is genuinely unseen patients — allow more drift
    # (+/- 0.12) before treating it as a regression, matching the model
    # card's own validation-vs-holdout distinction.
    checks.append(
        _check(
            "v2_coverage_test_patient_holdout",
            "calibrated coverage on the unseen patient-holdout test split stays reasonably near 80%",
            test.get("coverage_rate"),
            "in [0.68, 0.92]",
            test.get("coverage_rate") is not None and 0.68 <= test["coverage_rate"] <= 0.92,
        )
    )

    for group, stats in sorted(metrics.get("validation_by_risk_group", {}).items()):
        checks.append(
            _check(
                f"v2_coverage_risk_group_{group}",
                f"validation coverage for risk_group={group} (per ML_HANDOFF section 6.5 subgroup requirement)",
                stats.get("coverage_rate"),
                "in [0.65, 0.95]",
                stats.get("coverage_rate") is not None and 0.65 <= stats["coverage_rate"] <= 0.95,
            )
        )

    for context, stats in sorted(metrics.get("validation_by_meal_context", {}).items()):
        n = stats.get("n", 0)
        if n < MIN_SUBGROUP_N:
            checks.append(
                _check(
                    f"v2_coverage_meal_context_{context}",
                    f"validation coverage for meal_context={context} (n={n} < {MIN_SUBGROUP_N} — reported, not gated)",
                    stats.get("coverage_rate"),
                    "not gated (insufficient n)",
                    True,
                )
            )
            continue
        checks.append(
            _check(
                f"v2_coverage_meal_context_{context}",
                f"validation coverage for meal_context={context} (per ML_HANDOFF section 6.5 subgroup requirement)",
                stats.get("coverage_rate"),
                "in [0.65, 0.95]",
                stats.get("coverage_rate") is not None and 0.65 <= stats["coverage_rate"] <= 0.95,
            )
        )
    return checks


def check_pinball_loss(metrics: dict) -> list[dict]:
    checks = []
    val = metrics.get("validation", {})
    test = metrics.get("test_patient_holdout", {})

    checks.append(
        _check(
            "v2_pinball_validation",
            "validation pinball loss (min/max quantile) stays under a regression ceiling",
            [val.get("pinball_loss_min"), val.get("pinball_loss_max")],
            "min <= 6.0 and max <= 7.0",
            val.get("pinball_loss_min") is not None
            and val.get("pinball_loss_max") is not None
            and val["pinball_loss_min"] <= 6.0
            and val["pinball_loss_max"] <= 7.0,
        )
    )
    checks.append(
        _check(
            "v2_pinball_test_patient_holdout",
            "patient-holdout pinball loss (min/max quantile) stays under a looser regression ceiling",
            [test.get("pinball_loss_min"), test.get("pinball_loss_max")],
            "min <= 6.5 and max <= 7.5",
            test.get("pinball_loss_min") is not None
            and test.get("pinball_loss_max") is not None
            and test["pinball_loss_min"] <= 6.5
            and test["pinball_loss_max"] <= 7.5,
        )
    )

    for group, stats in sorted(metrics.get("validation_by_risk_group", {}).items()):
        checks.append(
            _check(
                f"v2_pinball_risk_group_{group}",
                f"validation pinball_loss_max for risk_group={group}",
                stats.get("pinball_loss_max"),
                "<= 7.0",
                stats.get("pinball_loss_max") is not None and stats["pinball_loss_max"] <= 7.0,
            )
        )

    for context, stats in sorted(metrics.get("validation_by_meal_context", {}).items()):
        n = stats.get("n", 0)
        if n < MIN_SUBGROUP_N:
            checks.append(
                _check(
                    f"v2_pinball_meal_context_{context}",
                    f"validation pinball_loss_max for meal_context={context} (n={n} < {MIN_SUBGROUP_N} — reported, not gated)",
                    stats.get("pinball_loss_max"),
                    "not gated (insufficient n)",
                    True,
                )
            )
            continue
        checks.append(
            _check(
                f"v2_pinball_meal_context_{context}",
                f"validation pinball_loss_max for meal_context={context}",
                stats.get("pinball_loss_max"),
                "<= 7.0",
                stats.get("pinball_loss_max") is not None and stats["pinball_loss_max"] <= 7.0,
            )
        )
    return checks


def check_band_width(metrics: dict) -> list[dict]:
    val = metrics.get("validation", {})
    test = metrics.get("test_patient_holdout", {})
    return [
        _check(
            "v2_band_width_validation",
            "mean predicted [min, max] band width stays clinically usable (not degenerate) on validation",
            val.get("mean_band_width_mg_dl"),
            "<= 100 mg/dL",
            val.get("mean_band_width_mg_dl") is not None and val["mean_band_width_mg_dl"] <= 100,
        ),
        _check(
            "v2_band_width_test_patient_holdout",
            "mean predicted [min, max] band width stays clinically usable on patient-holdout test",
            test.get("mean_band_width_mg_dl"),
            "<= 100 mg/dL",
            test.get("mean_band_width_mg_dl") is not None and test["mean_band_width_mg_dl"] <= 100,
        ),
    ]


def check_calibration_margin(metrics: dict) -> list[dict]:
    margin = metrics.get("calibration", {}).get("margin_mg_dl")
    return [
        _check(
            "v2_calibration_margin_sane",
            "split-conformal symmetric interval-expansion margin hasn't exploded (would signal validation-split drift)",
            margin,
            "in [0, 15] mg/dL",
            margin is not None and 0 <= margin <= 15,
        )
    ]


def check_v2_feature_schema_no_current_value_leakage(feature_schema: dict) -> list[dict]:
    all_features = set(feature_schema.get("numeric_features", [])) | set(feature_schema.get("categorical_features", []))
    leaked = all_features & CURRENT_VALUE_DERIVED_COLUMNS
    return [
        _check(
            "v2_feature_schema_no_target_leakage",
            "the SHIPPED v2 feature_schema.json excludes columns derived from the current "
            "reading (glucose_mg_dl itself, delta/rate/deviation-from-baseline) — those would "
            "leak the regression target; mirrors test_regression_no_leakage.py but checks the "
            "actual persisted artifact, not just train_threshold_regression.py's logic",
            sorted(leaked) if leaked else "none",
            "no overlap with CURRENT_VALUE_DERIVED_COLUMNS",
            not leaked,
        )
    ]


def run_all_v2_checks() -> list[dict]:
    metrics = load_v2_metrics()
    schema = load_v2_feature_schema()
    return (
        check_model_identity(metrics)
        + check_coverage(metrics)
        + check_pinball_loss(metrics)
        + check_band_width(metrics)
        + check_calibration_margin(metrics)
        + check_v2_feature_schema_no_current_value_leakage(schema)
    )


# ---- v1_hgb known-separability canary ----------------------------------------
#
# v1_hgb is NOT the live model (v2_quantile_regression is) and this is not a
# quality bar — see data/ml/personalized_watch/V1_HGB_LEAKAGE_FINDING.md.
# This reconstructs the WATCH/NORMAL label from a small hand-coded rule using
# only features present in v1_hgb's own feature_schema.json, to pin down
# *why* v1_hgb's precision/recall/PR-AUC sit at ~0.999: the label is a
# deterministic function of features already in X, not a model skill result.
# If this reconstruction's agreement with y_watch ever drops well below the
# threshold below, the documented explanation (this file + the finding doc +
# v1_hgb/model_card.md's own permutation-importance section) is stale and
# must be re-derived — that is the entire point of keeping this as a gate
# check rather than a one-off script.

# Universal ADA default cutoffs from
# data/NHANES/final/build_initial_diabetes_thresholds.py:211-216 — confirmed
# empirically to be the *only* (target_low, target_high, danger_low,
# danger_high) tuple across all 489 patients' clinical_review_events (zero
# UPDATE_TARGETS events exist in data/knowledge_structured/longitudinal_care/
# patient_clinical_review_events.csv; every one is INITIALIZE_TARGETS with
# the same (70, 180, 54, 250)).
TARGET_LOW, TARGET_HIGH, DANGER_LOW, DANGER_HIGH = 70.0, 180.0, 54.0, 250.0


def reconstruct_watch_label(feature_rows) -> object:
    """`feature_rows` is a pandas DataFrame of eligible (WATCH/NORMAL) rows
    from feature_table.csv. Returns a 0/1 Series reconstructing y_watch
    using only glucose_mg_dl, rate_mg_dl_per_min,
    deviation_from_baseline_p90/p10 and baseline_usable — see
    build_personalized_attention_dataset.py:231-246 for the rule branches
    this mirrors (LEVEL_1_LOW, ABOVE_TARGET, NEAR_LOW_FALLING/RISING,
    ABOVE/BELOW_PERSONAL_BASELINE)."""
    import pandas as pd

    g = feature_rows["glucose_mg_dl"]
    rate = pd.to_numeric(feature_rows["rate_mg_dl_per_min"], errors="coerce")
    dev_p90 = pd.to_numeric(feature_rows["deviation_from_baseline_p90"], errors="coerce")
    dev_p10 = pd.to_numeric(feature_rows["deviation_from_baseline_p10"], errors="coerce")
    baseline_usable = feature_rows["baseline_usable"].astype(str) == "True"

    level1_low = (g >= DANGER_LOW) & (g < TARGET_LOW)
    above_target = (g > TARGET_HIGH) & (g <= DANGER_HIGH)
    near_low_falling = (g >= TARGET_LOW) & (g < TARGET_LOW + 10) & (rate <= -0.5)
    near_high_rising = (g >= TARGET_HIGH - 20) & (g <= TARGET_HIGH) & (rate >= 0.5)
    above_personal_baseline = baseline_usable & (dev_p90 > 20)
    below_personal_baseline = baseline_usable & (dev_p10 < -15)

    return (
        level1_low
        | above_target
        | near_low_falling
        | near_high_rising
        | above_personal_baseline
        | below_personal_baseline
    ).astype(int)


def check_v1_hgb_known_separability(feature_table_path: Path = FEATURE_TABLE) -> list[dict]:
    import pandas as pd

    df = pd.read_csv(feature_table_path, low_memory=False)
    eligible = df[df["eligible_for_binary_training"] == True].copy()  # noqa: E712
    eligible["y_watch"] = eligible["y_watch"].astype(int)

    pred = reconstruct_watch_label(eligible)
    y = eligible["y_watch"].to_numpy()
    p = pred.to_numpy()

    tp = int(((p == 1) & (y == 1)).sum())
    fp = int(((p == 1) & (y == 0)).sum())
    fn = int(((p == 0) & (y == 1)).sum())
    tn = int(((p == 0) & (y == 0)).sum())
    accuracy = (tp + tn) / len(y) if len(y) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return [
        _check(
            "v1_hgb_rule_reconstruction_accuracy",
            "hand-coded reconstruction of y_watch from glucose_mg_dl + rate_mg_dl_per_min + "
            "deviation_from_baseline_p90/p10 + baseline_usable alone (no ML) — the near-perfect "
            "value here IS the explanation for v1_hgb's ~0.999 precision/recall, not evidence of "
            "model skill; see V1_HGB_LEAKAGE_FINDING.md",
            {
                "n": int(len(y)),
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            },
            "accuracy >= 0.999 (documents/guards the known-separability explanation; a sizeable "
            "drop means the explanation is stale and must be re-derived)",
            accuracy >= 0.999,
        )
    ]

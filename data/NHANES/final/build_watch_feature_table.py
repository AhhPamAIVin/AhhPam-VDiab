#!/usr/bin/env python3
"""Build the ML feature table for the personalized-WATCH classifier (Giai
doan A in docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md).

Joins the attention-engine assessment table with per-patient glucose
baselines, monitoring profiles, and static clinical/demographic data, then
engineers one flat row per assessment. Every temporal feature is computed
from data strictly at or before that row's own measured_at — see plan
section 5 for why static clinical columns are safe to fuse in this way
(risk_group/medication_hypoglycemia_risk are already rule-engine inputs
that only touch attention_score, not the WATCH/NORMAL boundary; the rest
of the clinical block was never used by the rule engine at all).

BASELINE_LEARNING rows are dropped entirely (the rule engine never assigns
them WATCH/NORMAL, so they carry no label). DANGER rows are kept in the
table with eligible_for_binary_training=False so Giai doan C's safety test
("ML must never fire when DANGER is true") has real DANGER rows to test
against, but they are excluded from the WATCH/NORMAL training pool.

Output:
    <output-dir>/feature_table.csv     one row per non-baseline assessment
    <output-dir>/feature_dictionary.csv   every feature column + its source
    <output-dir>/split_manifest.json      patient-holdout + temporal split
    <output-dir>/build_report.json        counts, label balance, lineage
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import statistics
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

FEATURE_TABLE_VERSION = "personalized_watch_feature_table_v1"

# Same cap the rule engine itself uses for trend_rate (build_personalized_
# attention_dataset.py) — a rate computed across a bigger gap than this
# isn't a meaningful "trend" any more given how sparsely patients are
# measured (median gap ~345 min, see plan section 3.1).
TREND_MAX_GAP_MINUTES = 180

# Rolling-window sizes are counted in *prior measurements*, not hours/days —
# plan section 3.1: with ~3.4 readings/day, an hour-based window would
# almost always contain just the current point.
ROLLING_WINDOW_SIZES = (3, 10)
TIR_WINDOW_SIZE = 10
EXCEEDANCE_LOOKBACKS_HOURS = (24, 24 * 7)
WATCH_HISTORY_LOOKBACK_HOURS = 24 * 7

# Deterministic patient-holdout split — fixed so re-running the build
# reproduces the same split_manifest.json.
SPLIT_SEED = 20260820
HOLDOUT_FRACTION = 0.20
DANGER_HEAVY_THRESHOLD = 0.50  # matches the ">50% DANGER" cohort in the analysis


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mean_or_blank(values: list[float]) -> float | str:
    return round(statistics.fmean(values), 2) if values else ""


def stdev_or_blank(values: list[float]) -> float | str:
    return round(statistics.pstdev(values), 2) if len(values) >= 2 else ""


# ---- static per-patient lookups -------------------------------------------------

CLINICAL_NUMERIC_FIELDS = [
    "hba1c_pct",
    "fasting_glucose_mg_dl",
    "ldl_mg_dl",
    "hdl_mg_dl",
    "triglycerides_mg_dl",
    "sbp_mean",
    "dbp_mean",
]
CLINICAL_FLAG_FIELDS = [
    "congestive_heart_failure_flag",
    "coronary_heart_disease_flag",
    "stroke_flag",
    "family_history_diabetes_flag",
]
PROFILE_NUMERIC_FIELDS = ["age_years", "bmi", "overall_data_quality_score"]


def build_static_lookup(
    profiles: list[dict[str, str]],
    clinical: list[dict[str, str]],
    monitoring: list[dict[str, str]],
) -> dict[str, dict[str, object]]:
    static: dict[str, dict[str, object]] = defaultdict(dict)

    for row in profiles:
        pid = row["patient_id"]
        for field in PROFILE_NUMERIC_FIELDS:
            static[pid][field] = to_float(row.get(field))
        static[pid]["sex"] = row.get("sex") or ""
        static[pid]["caregiver_required_flag"] = row.get("caregiver_required_flag") or "False"

    for row in clinical:
        pid = row["patient_id"]
        for field in CLINICAL_NUMERIC_FIELDS:
            value = to_float(row.get(field))
            static[pid][field] = value
            static[pid][f"{field}_is_missing"] = value is None
        for field in CLINICAL_FLAG_FIELDS:
            static[pid][field] = row.get(field) or "False"

    for row in monitoring:
        pid = row["patient_id"]
        static[pid]["risk_group"] = row.get("risk_group") or ""
        static[pid]["medication_hypoglycemia_risk"] = row.get("medication_hypoglycemia_risk") or ""
        static[pid]["target_low_mg_dl"] = to_float(row.get("normal_low_mg_dl"))
        static[pid]["target_high_mg_dl"] = to_float(row.get("normal_high_mg_dl"))

    return static


# ---- per-row temporal feature engineering ----------------------------------------


def engineer_patient_rows(
    patient_id: str,
    rows: list[dict[str, str]],
    baseline_lookup: dict[tuple[str, str, str], dict[str, object]],
    static: dict[str, object],
) -> list[dict[str, object]]:
    rows.sort(key=lambda r: r["_dt"])

    recent_k: dict[int, deque[float]] = {k: deque(maxlen=k) for k in ROLLING_WINDOW_SIZES}
    exceed_high_history: deque[datetime] = deque()
    exceed_low_history: deque[datetime] = deque()
    watch_history: deque[datetime] = deque()
    prev: dict[str, object] | None = None

    target_low = static.get("target_low_mg_dl")
    target_high = static.get("target_high_mg_dl")

    out: list[dict[str, object]] = []
    for row in rows:
        dt: datetime = row["_dt"]
        status = row["status"]
        value = float(row["glucose_mg_dl"])

        # ---- trend vs. the immediately previous measurement ----
        delta = rate = minutes_since_prev = ""
        if prev is not None:
            gap_minutes = (dt - prev["_dt"]).total_seconds() / 60
            minutes_since_prev = round(gap_minutes, 1)
            delta = round(value - prev["value"], 1)
            if 0 < gap_minutes <= TREND_MAX_GAP_MINUTES:
                rate = round(delta / gap_minutes, 4)

        # ---- deviation from this row's personal baseline ----
        meal_context = row.get("meal_context") or "UNSPECIFIED"
        baseline = baseline_lookup.get((patient_id, row.get("baseline_epoch_id", ""), meal_context))
        p10 = median = p90 = dev_median = dev_p10 = dev_p90 = ""
        baseline_usable = False
        if baseline is not None:
            baseline_usable = bool(baseline["baseline_usable"])
            p10, median, p90 = baseline["p10"], baseline["median"], baseline["p90"]
            if median != "":
                dev_median = round(value - float(median), 1)
            if p10 != "":
                dev_p10 = round(value - float(p10), 1)
            if p90 != "":
                dev_p90 = round(value - float(p90), 1)

        # ---- rolling stats over the K most recent PRIOR measurements ----
        rolling_features: dict[str, object] = {}
        for k in ROLLING_WINDOW_SIZES:
            window = list(recent_k[k])
            rolling_features[f"rolling_mean_last{k}"] = mean_or_blank(window)
            rolling_features[f"rolling_min_last{k}"] = min(window) if window else ""
            rolling_features[f"rolling_max_last{k}"] = max(window) if window else ""
            rolling_features[f"rolling_std_last{k}"] = stdev_or_blank(window)

        # ---- TIR/TBR/TAR over the trailing TIR_WINDOW_SIZE prior measurements ----
        tir_window = list(recent_k[TIR_WINDOW_SIZE]) if TIR_WINDOW_SIZE in recent_k else []
        if tir_window and target_low is not None and target_high is not None:
            below = sum(1 for v in tir_window if v < target_low)
            above = sum(1 for v in tir_window if v > target_high)
            n = len(tir_window)
            tbr_pct = round(100 * below / n, 1)
            tar_pct = round(100 * above / n, 1)
            tir_pct = round(100 - tbr_pct - tar_pct, 1)
        else:
            tir_pct = tbr_pct = tar_pct = ""

        # ---- exceedance counts in trailing 24h / 7d ----
        for history in (exceed_high_history, exceed_low_history):
            while history and history[0] < dt - timedelta(hours=WATCH_HISTORY_LOOKBACK_HOURS):
                history.popleft()
        exceed_high_24h = sum(1 for t in exceed_high_history if t >= dt - timedelta(hours=24))
        exceed_high_7d = len(exceed_high_history)
        exceed_low_24h = sum(1 for t in exceed_low_history if t >= dt - timedelta(hours=24))
        exceed_low_7d = len(exceed_low_history)

        # ---- recent WATCH history ----
        while watch_history and watch_history[0] < dt - timedelta(hours=WATCH_HISTORY_LOOKBACK_HOURS):
            watch_history.popleft()
        watch_count_last_7d = len(watch_history)
        minutes_since_last_watch = (
            round((dt - watch_history[-1]).total_seconds() / 60, 1) if watch_history else ""
        )

        eligible = status in ("WATCH", "NORMAL")
        y_watch = 1 if status == "WATCH" else (0 if status == "NORMAL" else "")

        # BASELINE_LEARNING rows are real measurements and must still update
        # the history trackers below (a rolling window or delta computed for
        # a row right after the baseline window closes needs to see them) —
        # they are just never labeled WATCH/NORMAL, so they are excluded
        # only from the *emitted* feature table, not from anyone else's history.
        if status != "BASELINE_LEARNING":
            out.append({
            # identifiers — join/audit only, never features
            "attention_assessment_id": row["attention_assessment_id"],
            "measurement_id": row["measurement_id"],
            "patient_id": patient_id,
            "care_plan_id": row.get("care_plan_id", ""),
            "care_plan_version": row.get("care_plan_version", ""),
            "baseline_epoch_id": row.get("baseline_epoch_id", ""),
            "measured_at": row["measured_at"],
            "meal_context": meal_context,
            # time-series features
            "glucose_mg_dl": value,
            "minutes_since_prev_measurement": minutes_since_prev,
            "delta_glucose_mg_dl": delta,
            "rate_mg_dl_per_min": rate,
            "baseline_usable": baseline_usable,
            "baseline_p10_mg_dl": p10,
            "baseline_median_mg_dl": median,
            "baseline_p90_mg_dl": p90,
            "deviation_from_baseline_median": dev_median,
            "deviation_from_baseline_p10": dev_p10,
            "deviation_from_baseline_p90": dev_p90,
            **rolling_features,
            "tir_pct_last10": tir_pct,
            "tbr_pct_last10": tbr_pct,
            "tar_pct_last10": tar_pct,
            "exceed_high_count_24h": exceed_high_24h,
            "exceed_high_count_7d": exceed_high_7d,
            "exceed_low_count_24h": exceed_low_24h,
            "exceed_low_count_7d": exceed_low_7d,
            "watch_count_last_7d": watch_count_last_7d,
            "minutes_since_last_watch": minutes_since_last_watch,
            # static clinical/demographic features (broadcast per patient)
            "age_years": static.get("age_years", ""),
            "sex": static.get("sex", ""),
            "bmi": static.get("bmi", ""),
            "caregiver_required_flag": static.get("caregiver_required_flag", ""),
            "overall_data_quality_score": static.get("overall_data_quality_score", ""),
            "risk_group": static.get("risk_group", ""),
            "medication_hypoglycemia_risk": static.get("medication_hypoglycemia_risk", ""),
            **{field: static.get(field, "") for field in CLINICAL_NUMERIC_FIELDS},
            **{f"{field}_is_missing": static.get(f"{field}_is_missing", True) for field in CLINICAL_NUMERIC_FIELDS},
            **{field: static.get(field, "") for field in CLINICAL_FLAG_FIELDS},
            # label / meta — kept for audit, must never be used as X
            "status": status,
            "eligible_for_binary_training": eligible,
            "y_watch": y_watch,
        })

        # Only now does this row become part of the history the *next* row
        # can see — this is what keeps every feature above look-back-only.
        if target_high is not None and value > target_high:
            exceed_high_history.append(dt)
        if target_low is not None and value < target_low:
            exceed_low_history.append(dt)
        if status == "WATCH":
            watch_history.append(dt)
        for k in ROLLING_WINDOW_SIZES:
            recent_k[k].append(value)
        if TIR_WINDOW_SIZE not in recent_k:
            recent_k[TIR_WINDOW_SIZE] = deque(maxlen=TIR_WINDOW_SIZE)
        recent_k[TIR_WINDOW_SIZE].append(value)
        prev = {"_dt": dt, "value": value}

    return out


# ---- patient-holdout + temporal split ---------------------------------------------


def assign_splits(
    feature_rows: list[dict[str, object]],
    all_assessment_rows_by_patient: dict[str, list[dict[str, str]]],
    static_by_patient: dict[str, dict[str, object]],
) -> tuple[dict[str, str], dict[str, object]]:
    """Returns (patient_id -> 'holdout'/'train_pool', manifest dict)."""
    danger_rate_by_patient: dict[str, float] = {}
    for pid, rows in all_assessment_rows_by_patient.items():
        total = len(rows)
        danger = sum(1 for r in rows if r["status"] == "DANGER")
        danger_rate_by_patient[pid] = danger / total if total else 0.0

    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid in all_assessment_rows_by_patient:
        risk_group = static_by_patient.get(pid, {}).get("risk_group", "UNKNOWN")
        bucket = "danger_heavy" if danger_rate_by_patient[pid] > DANGER_HEAVY_THRESHOLD else "danger_light"
        strata[(risk_group, bucket)].append(pid)

    rng = random.Random(SPLIT_SEED)
    holdout_patients: set[str] = set()
    for key, patients in strata.items():
        patients = sorted(patients)  # deterministic order before shuffling
        rng.shuffle(patients)
        n_holdout = max(1, round(len(patients) * HOLDOUT_FRACTION)) if patients else 0
        holdout_patients.update(patients[:n_holdout])

    patient_split = {
        pid: ("holdout" if pid in holdout_patients else "train_pool")
        for pid in all_assessment_rows_by_patient
    }

    manifest = {
        "seed": SPLIT_SEED,
        "holdout_fraction_target": HOLDOUT_FRACTION,
        "danger_heavy_threshold": DANGER_HEAVY_THRESHOLD,
        "strata_sizes": {f"{k[0]}|{k[1]}": len(v) for k, v in strata.items()},
        "holdout_patient_count": len(holdout_patients),
        "train_pool_patient_count": len(all_assessment_rows_by_patient) - len(holdout_patients),
        "holdout_patient_ids": sorted(holdout_patients),
    }
    return patient_split, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessments", type=Path, required=True)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--monitoring-profiles", type=Path, required=True)
    parser.add_argument("--patient-profiles", type=Path, required=True)
    parser.add_argument("--patient-clinical", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--validation-split-day",
        type=int,
        default=45,
        help="Day offset (from the dataset's earliest measured_at) where the "
        "train/validation cutoff falls inside the non-holdout patient pool. "
        "Rows on/after this day go to validation; holdout patients go to "
        "test regardless of date.",
    )
    args = parser.parse_args()

    assessment_rows = read_csv(args.assessments)
    baseline_rows = read_csv(args.baselines)
    monitoring_rows = read_csv(args.monitoring_profiles)
    profile_rows = read_csv(args.patient_profiles)
    clinical_rows = read_csv(args.patient_clinical)

    baseline_lookup: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in baseline_rows:
        key = (row["patient_id"], row["baseline_epoch_id"], row["meal_context"])
        baseline_lookup[key] = {
            "p10": row.get("p10_mg_dl", ""),
            "median": row.get("median_mg_dl", ""),
            "p90": row.get("p90_mg_dl", ""),
            "baseline_usable": row.get("baseline_usable") == "True",
        }

    static_by_patient = build_static_lookup(profile_rows, clinical_rows, monitoring_rows)

    by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in assessment_rows:
        row["_dt"] = datetime.fromisoformat(row["measured_at"])
        by_patient[row["patient_id"]].append(row)

    feature_rows: list[dict[str, object]] = []
    for patient_id, rows in sorted(by_patient.items()):
        feature_rows.extend(
            engineer_patient_rows(patient_id, rows, baseline_lookup, static_by_patient.get(patient_id, {}))
        )

    patient_split, split_manifest = assign_splits(feature_rows, by_patient, static_by_patient)

    all_dates = [datetime.fromisoformat(r["measured_at"]) for r in feature_rows]
    earliest = min(all_dates)
    validation_cutoff = earliest + timedelta(days=args.validation_split_day)
    split_manifest["earliest_measured_at"] = earliest.isoformat(sep=" ")
    split_manifest["validation_cutoff"] = validation_cutoff.isoformat(sep=" ")

    split_counts: dict[str, int] = defaultdict(int)
    for row in feature_rows:
        pid = row["patient_id"]
        if patient_split[pid] == "holdout":
            split = "test"
        else:
            split = "validation" if datetime.fromisoformat(row["measured_at"]) >= validation_cutoff else "train"
        row["split"] = split
        split_counts[split] += 1
    split_manifest["row_counts_by_split"] = dict(split_counts)

    fieldnames = list(feature_rows[0].keys()) if feature_rows else []
    feature_table_path = args.output_dir / "feature_table.csv"
    write_csv(feature_table_path, feature_rows, fieldnames)

    # meal_context is deliberately NOT in this set — it is a real predictive
    # feature (the doc's own feature list names it explicitly), not just a
    # join key. It was originally miscategorized as an identifier here,
    # which silently trained v0/v1 without it; fixed after a review question
    # about whether the model actually conditions on measurement timing —
    # see docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 0.
    non_feature_columns = {
        "attention_assessment_id", "measurement_id", "patient_id", "care_plan_id",
        "care_plan_version", "baseline_epoch_id", "measured_at",
        "status", "eligible_for_binary_training", "y_watch", "split",
    }
    static_field_names = (
        {"age_years", "sex", "bmi", "caregiver_required_flag", "overall_data_quality_score",
         "risk_group", "medication_hypoglycemia_risk"}
        | set(CLINICAL_NUMERIC_FIELDS)
        | {f"{f}_is_missing" for f in CLINICAL_NUMERIC_FIELDS}
        | set(CLINICAL_FLAG_FIELDS)
    )
    dictionary_rows = []
    for name in fieldnames:
        if name in non_feature_columns:
            source = "identifier" if name != "status" and name != "y_watch" else "label"
            if name in ("status", "eligible_for_binary_training", "y_watch"):
                source = "label"
        elif name in static_field_names:
            source = "static_clinical"
        else:
            source = "time_series_derived"
        dictionary_rows.append({"feature_name": name, "source": source})
    write_csv(args.output_dir / "feature_dictionary.csv", dictionary_rows, ["feature_name", "source"])

    (args.output_dir / "split_manifest.json").write_text(
        json.dumps(split_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    n_watch = sum(1 for r in feature_rows if r["y_watch"] == 1)
    n_normal = sum(1 for r in feature_rows if r["y_watch"] == 0)
    n_danger = sum(1 for r in feature_rows if r["status"] == "DANGER")
    build_report = {
        "feature_table_version": FEATURE_TABLE_VERSION,
        "row_count": len(feature_rows),
        "patient_count": len(by_patient),
        "label_counts": {"WATCH": n_watch, "NORMAL": n_normal, "DANGER_excluded_from_binary": n_danger},
        "row_counts_by_split": dict(split_counts),
        "source_file_lineage": {
            str(path): {"rows": len(rows), "sha256": file_sha256(path)}
            for path, rows in (
                (args.assessments, assessment_rows),
                (args.baselines, baseline_rows),
                (args.monitoring_profiles, monitoring_rows),
                (args.patient_profiles, profile_rows),
                (args.patient_clinical, clinical_rows),
            )
        },
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(build_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"wrote {len(feature_rows)} rows -> {feature_table_path}")
    print(f"label balance: WATCH={n_watch} NORMAL={n_normal} (excluded DANGER={n_danger})")
    print(f"split row counts: {dict(split_counts)}")
    print(f"holdout patients: {split_manifest['holdout_patient_count']}/{len(by_patient)}")


if __name__ == "__main__":
    main()

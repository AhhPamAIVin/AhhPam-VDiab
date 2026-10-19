"""calculate_health_metrics — pure Python over already-fetched records, no DB
and no LLM call.

OWNER: AI Summary workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §8. Must stay a pure function
(no I/O) — its output is what check_data_quality.py and synthesize_summary.py
treat as ground truth, so it needs to be trivially testable in isolation.
"""

from __future__ import annotations

from datetime import date, datetime

# BYT-5481 treatment targets (design doc §0, data/mock/demo_patient/README.md):
# post-prandial (after-meal) < 180 mg/dL, fasting 80-130 mg/dL. These are the
# only thresholds baked in here — anything more specific (per-patient targets)
# would need a real PatientProfile, which doesn't exist yet (§1).
POST_PRANDIAL_TARGET_MG_DL = 180.0
FASTING_TARGET_LOW_MG_DL = 80.0
FASTING_TARGET_HIGH_MG_DL = 130.0
HYPOGLYCEMIA_THRESHOLD_MG_DL = 70.0


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_date(value: object) -> date | None:
    dt = _as_datetime(value)
    return dt.date() if dt else None


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5  # Monday=0 ... Saturday=5, Sunday=6


def _round(value: float | None, ndigits: int = 1) -> float | None:
    return round(value, ndigits) if value is not None else None


def _bucket_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": _round(sum(values) / len(values)),
        "min": min(values),
        "max": max(values),
    }


def _distinct_days(items: list[dict], field: str) -> int:
    days = {_as_date(item.get(field)) for item in items}
    days.discard(None)
    return len(days)


def calculate_health_metrics(records: dict) -> dict:
    """Aggregate stats over already-fetched records.

    `records` is whatever get_health_records (patient_tools.py, same
    workstream) returned: {"glucose": [...], "blood_pressure": [...],
    "meals": [...], "activities": [...]}, one dict per row with the same
    field names as GlucoseRecord/BloodPressureRecord/MealRecord/ActivityRecord
    (value, unit, measured_at, meal_context, measurement_source, symptoms /
    systolic, diastolic, measured_at / meal_type, meal_time, description /
    activity_type, duration_minutes, activity_time).

    Every number returned here is meant to be the sole source of truth for any
    trend/attention-point text downstream nodes produce — it's all arithmetic
    over what was actually fetched, nothing invented (design doc §2, §8).
    Missing/malformed fields are skipped rather than raising, since real
    logging data has independent gaps (data/mock/demo_patient/README.md).
    """
    glucose = records.get("glucose") or []
    blood_pressure = records.get("blood_pressure") or []
    meals = records.get("meals") or []
    activities = records.get("activities") or []

    all_values = [float(g["value"]) for g in glucose if g.get("value") is not None]
    systolic_values = [float(bp["systolic"]) for bp in blood_pressure if bp.get("systolic") is not None]
    diastolic_values = [float(bp["diastolic"]) for bp in blood_pressure if bp.get("diastolic") is not None]

    metrics: dict = {
        "glucose_count": len(glucose),
        "blood_pressure_count": len(blood_pressure),
        "meal_count": len(meals),
        "activity_count": len(activities),
        "glucose_days_logged": _distinct_days(glucose, "measured_at"),
        "blood_pressure_days_logged": _distinct_days(blood_pressure, "measured_at"),
        "meal_days_logged": _distinct_days(meals, "meal_time"),
        "activity_days_logged": _distinct_days(activities, "activity_time"),
        "glucose_overall": _bucket_stats(all_values),
        # No BYT/ADA blood-pressure target constant exists anywhere else in
        # this codebase (unlike POST_PRANDIAL_TARGET_MG_DL/FASTING_TARGET_*
        # above) — plain avg/min/max only here, no in-range/over-target rate,
        # so this stays arithmetic-only rather than asserting a clinical
        # threshold nobody has actually specified.
        "blood_pressure_systolic": _bucket_stats(systolic_values),
        "blood_pressure_diastolic": _bucket_stats(diastolic_values),
    }

    # Per meal-context breakdown (FASTING/BEFORE_MEAL/AFTER_MEAL/BEFORE_SLEEP/RANDOM).
    by_context: dict[str, list[float]] = {}
    for g in glucose:
        if g.get("value") is None:
            continue
        ctx = str(g.get("meal_context") or "UNSPECIFIED")
        by_context.setdefault(ctx, []).append(float(g["value"]))
    metrics["glucose_by_context"] = {ctx: _bucket_stats(vals) for ctx, vals in by_context.items()}

    # Post-prandial (after-meal) over-target rate — BYT-5481 < 180 mg/dL.
    after_meal = by_context.get("AFTER_MEAL", [])
    over_target = [v for v in after_meal if v > POST_PRANDIAL_TARGET_MG_DL]
    metrics["post_prandial"] = {
        "count": len(after_meal),
        "avg": _round(sum(after_meal) / len(after_meal)) if after_meal else None,
        "over_target_count": len(over_target),
        "over_target_pct": _round(100 * len(over_target) / len(after_meal)) if after_meal else None,
        "target_mg_dl": POST_PRANDIAL_TARGET_MG_DL,
    }

    # Fasting in-range rate — BYT-5481 80-130 mg/dL.
    fasting = by_context.get("FASTING", [])
    in_range = [v for v in fasting if FASTING_TARGET_LOW_MG_DL <= v <= FASTING_TARGET_HIGH_MG_DL]
    metrics["fasting"] = {
        "count": len(fasting),
        "avg": _round(sum(fasting) / len(fasting)) if fasting else None,
        "in_range_count": len(in_range),
        "in_range_pct": _round(100 * len(in_range) / len(fasting)) if fasting else None,
        "target_range_mg_dl": [FASTING_TARGET_LOW_MG_DL, FASTING_TARGET_HIGH_MG_DL],
    }

    # Weekday vs weekend split (overall glucose) — the mock patient's headline
    # pattern (design doc §0). Included generically, not tied to any one
    # meal-context, so it's useful even for sparser real data.
    weekday_values: list[float] = []
    weekend_values: list[float] = []
    for g in glucose:
        if g.get("value") is None:
            continue
        dt = _as_datetime(g.get("measured_at"))
        if dt is None:
            continue
        (weekend_values if _is_weekend(dt) else weekday_values).append(float(g["value"]))
    metrics["weekday_vs_weekend"] = {
        "weekday": _bucket_stats(weekday_values),
        "weekend": _bucket_stats(weekend_values),
    }

    # Possible hypoglycemia signal (< 70 mg/dL) and symptom-flagged readings —
    # counts only; downstream nodes decide whether/how to surface these, this
    # stays a pure count so it can't drift from what's actually in the data.
    metrics["low_glucose_count"] = sum(1 for v in all_values if v < HYPOGLYCEMIA_THRESHOLD_MG_DL)
    metrics["symptomatic_count"] = sum(1 for g in glucose if g.get("symptoms"))

    # Personalized-threshold evaluation (health/threshold_service.py) — a
    # doctor-approved DANGER gate plus an AI-predicted "normal for this
    # patient" WATCH band, both computed live per reading and stored on
    # GlucoseRecord. Distinct from HYPOGLYCEMIA_THRESHOLD_MG_DL/low_glucose_count
    # above: those are a fixed population cutoff, this is per-patient and can
    # fire on a high reading too, not just a low one. threshold_status is None
    # for records predating this feature or a patient with no CarePlanTarget
    # yet — never counted as either WATCH or DANGER.
    metrics["watch_count"] = sum(1 for g in glucose if g.get("threshold_status") == "WATCH")
    metrics["danger_count"] = sum(1 for g in glucose if g.get("threshold_status") == "DANGER")
    band_readings = [
        g for g in glucose
        if g.get("predicted_min_mg_dl") is not None and g.get("predicted_max_mg_dl") is not None
    ]
    latest_band = max(band_readings, key=lambda g: _as_datetime(g.get("measured_at")) or datetime.min, default=None)
    metrics["personalized_band"] = (
        {
            "predicted_min_mg_dl": _round(latest_band["predicted_min_mg_dl"]),
            "predicted_max_mg_dl": _round(latest_band["predicted_max_mg_dl"]),
            "as_of": latest_band.get("measured_at"),
        }
        if latest_band else None
    )

    metrics["activity_total_minutes"] = sum(int(a.get("duration_minutes") or 0) for a in activities)

    return metrics

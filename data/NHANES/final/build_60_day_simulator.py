from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median


GENERATOR_VERSION = "diabetes_simulator_60d_v1"
DEFAULT_SEED = 20260810
DEFAULT_START_DATE = date(2026, 1, 1)
SIMULATOR_GLUCOSE_MIN = 40
SIMULATOR_GLUCOSE_MAX = 600
MEAL_TYPES = ("BREAKFAST", "LUNCH", "DINNER", "SNACK")
MAIN_MEALS = ("BREAKFAST", "LUNCH", "DINNER")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate traceable 60-day synthetic diabetes demo data"
    )
    parser.add_argument("--nhanes", type=Path, required=True)
    parser.add_argument("--cgmacros", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit-patients", type=int)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(str(value).strip())
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.strip())


def minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def timestamp_on(day: date, source: datetime, jitter_minutes: int = 0) -> datetime:
    return datetime.combine(day, source.time().replace(second=0, microsecond=0)) + timedelta(
        minutes=jitter_minutes
    )


def nearest_values(
    readings: list[tuple[datetime, float]], start: datetime, end: datetime
) -> list[float]:
    return [value for measured_at, value in readings if start <= measured_at <= end]


def safe_scale(value: str, factor: float) -> str:
    parsed = number(value)
    return "" if parsed is None else f"{parsed * factor:.1f}"


def fit_fasting_from_a1c(subjects: list[dict[str, str]]) -> tuple[float, float]:
    pairs = [
        (number(row.get("a1c_pct")), number(row.get("fasting_glucose_mg_dl")))
        for row in subjects
    ]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - mean_x) ** 2 for x, _ in pairs)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in pairs) / denominator
        if denominator
        else 0.0
    )
    return mean_y - slope * mean_x, slope


def patient_group(profile: dict[str, str], clinical: dict[str, str]) -> str:
    if profile["project_diabetes_label"] == "PREDIABETES":
        return "PREDIABETES"
    if truthy(clinical.get("taking_insulin_flag")):
        return "TYPE_2_INSULIN"
    return "TYPE_2_NON_INSULIN"


def donor_score(
    profile: dict[str, str], clinical: dict[str, str], donor: dict[str, str]
) -> float:
    score = 0.0
    components = 0
    for patient_value, donor_value, scale in (
        (clinical.get("hba1c_pct"), donor.get("a1c_pct"), 2.0),
        (profile.get("bmi"), donor.get("bmi"), 10.0),
        (profile.get("age_years"), donor.get("age_years"), 30.0),
    ):
        left, right = number(patient_value), number(donor_value)
        if left is not None and right is not None:
            score += abs(left - right) / scale
            components += 1
    return score / components if components else 999.0


def select_donor(
    profile: dict[str, str],
    clinical: dict[str, str],
    donors: list[dict[str, str]],
    rng: random.Random,
) -> dict[str, str]:
    ranked = sorted(donors, key=lambda donor: donor_score(profile, clinical, donor))
    return rng.choice(ranked[: min(3, len(ranked))])


def measurement_schedule(group: str, day_index: int, weekday: int) -> dict[str, object]:
    if group == "PREDIABETES":
        return {
            "adherence": 0.85,
            "fasting": True,
            "paired_meal": MAIN_MEALS[(day_index // 2) % 3] if weekday in {0, 2, 4} else None,
            "bedtime": False,
            "pre_lunch": False,
            "pre_dinner": False,
        }
    if group == "TYPE_2_NON_INSULIN":
        return {
            "adherence": 0.90,
            "fasting": True,
            "paired_meal": MAIN_MEALS[day_index % 3],
            "bedtime": weekday in {0, 2, 4, 6},
            "pre_lunch": False,
            "pre_dinner": False,
        }
    return {
        "adherence": 0.96,
        "fasting": True,
        "paired_meal": MAIN_MEALS[day_index % 3],
        "bedtime": True,
        "pre_lunch": True,
        "pre_dinner": True,
    }


def observed_glucose_for_time(
    readings: list[tuple[datetime, float]], target_minute: int
) -> float | None:
    candidates = [
        (abs(minute_of_day(measured_at) - target_minute), value)
        for measured_at, value in readings
    ]
    return min(candidates)[1] if candidates else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    if args.days <= 0:
        raise ValueError("--days must be positive")
    nhanes = args.nhanes.resolve()
    cgmacros = args.cgmacros.resolve()
    output = args.output.resolve()
    if output in nhanes.parents or output in cgmacros.parents:
        raise ValueError("Output cannot be a parent of an input directory")

    profiles = read_csv(nhanes / "patient_profiles.csv")
    if args.limit_patients:
        profiles = profiles[: args.limit_patients]
    clinical_by_patient = {
        row["patient_id"]: row
        for row in read_csv(nhanes / "patient_baseline_clinical.csv")
    }
    donor_profiles = read_csv(cgmacros / "cgmacros_subject_profiles.csv")
    source_meals = read_csv(cgmacros / "meal_events.csv")
    donor_by_id = {row["source_subject_id"]: row for row in donor_profiles}

    donors_by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for donor in donor_profiles:
        donors_by_group[donor["source_glycemic_group"]].append(donor)

    meals_by_subject_day: dict[tuple[str, date], list[dict[str, object]]] = defaultdict(list)
    meals_by_subject_type: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for source_meal in source_meals:
        enriched: dict[str, object] = dict(source_meal)
        enriched["source_datetime"] = parse_timestamp(source_meal["meal_started_at"])
        key = (source_meal["source_subject_id"], enriched["source_datetime"].date())
        meals_by_subject_day[key].append(enriched)
        meals_by_subject_type[(source_meal["source_subject_id"], source_meal["meal_type"])].append(enriched)

    readings_by_subject: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    readings_by_subject_day: dict[tuple[str, date], list[tuple[datetime, float]]] = defaultdict(list)
    for donor_id in donor_by_id:
        participant = cgmacros / f"CGMacros-{int(donor_id):03d}"
        path = participant / f"CGMacros-{int(donor_id):03d}.csv"
        for row in read_csv(path):
            glucose = number(row.get("Libre GL"))
            if glucose is not None:
                readings_by_subject[donor_id].append(
                    (parse_timestamp(row["Timestamp"]), glucose)
                )
        readings_by_subject[donor_id].sort()
        for measured_at, glucose in readings_by_subject[donor_id]:
            readings_by_subject_day[(donor_id, measured_at.date())].append(
                (measured_at, glucose)
            )

    donor_fasting: dict[str, float] = {}
    source_extremes: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for donor_id, readings in readings_by_subject.items():
        morning = [value for ts, value in readings if 5 * 60 <= minute_of_day(ts) <= 9 * 60]
        donor_fasting[donor_id] = median(morning or [value for _, value in readings])
        group = donor_by_id[donor_id]["source_glycemic_group"]
        for ts, value in readings:
            if value > 180:
                source_extremes[(group, "HIGH")].append((ts, value))
            if value < 70:
                source_extremes[(group, "LOW")].append((ts, value))

    meal_response: dict[str, dict[str, float]] = {}
    for meal in source_meals:
        donor_id = meal["source_subject_id"]
        started = parse_timestamp(meal["meal_started_at"])
        readings = readings_by_subject[donor_id]
        pre = nearest_values(readings, started - timedelta(minutes=30), started)
        post = nearest_values(
            readings, started + timedelta(minutes=90), started + timedelta(minutes=150)
        )
        meal_response[meal["meal_id"]] = {
            "pre": median(pre) if pre else donor_fasting[donor_id],
            "post": median(post) if post else donor_fasting[donor_id],
        }

    intercept, slope = fit_fasting_from_a1c(donor_profiles)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    run_fields = [
        "simulation_run_id", "patient_id", "patient_group", "start_date", "end_date",
        "days", "random_seed", "source_subject_id", "donor_match_score",
        "fasting_anchor_mg_dl", "fasting_anchor_source", "generator_version",
        "is_synthetic",
    ]
    meal_fields = [
        "meal_id", "simulation_run_id", "patient_id", "meal_started_at", "meal_type",
        "calories_kcal", "carbohydrate_g", "protein_g", "fat_g", "fiber_g",
        "amount_consumed_pct", "source_subject_id", "source_meal_id",
        "source_day", "macro_scale_factor", "generation_method", "is_synthetic",
        "generator_version",
    ]
    glucose_fields = [
        "measurement_id", "simulation_run_id", "patient_id", "measured_at",
        "glucose_mg_dl", "raw_generated_glucose_mg_dl", "value_adjustment_flag",
        "timestamp_adjustment_minutes", "unit", "meal_context", "measurement_source", "meal_id",
        "minutes_from_meal_start", "source_subject_id", "source_meal_id",
        "source_observed_glucose_mg_dl", "fasting_anchor_mg_dl",
        "fasting_anchor_source", "generation_method", "episode_id",
        "is_anomaly_scenario", "is_synthetic", "generator_version",
    ]
    episode_fields = [
        "episode_id", "simulation_run_id", "patient_id", "scenario_type",
        "started_at", "peak_at", "resolved_at", "trigger_measurement_id",
        "source_subject_id", "source_observed_glucose_mg_dl", "scenario_policy",
        "is_forced_test_scenario", "is_synthetic", "generator_version",
    ]

    paths = {
        "runs": output / "simulation_runs.csv",
        "meals": output / "synthetic_meal_events.csv",
        "glucose": output / "synthetic_glucose_measurements.csv",
        "episodes": output / "simulation_episodes.csv",
    }
    handles = {name: path.open("w", encoding="utf-8-sig", newline="") for name, path in paths.items()}
    writers = {
        "runs": csv.DictWriter(handles["runs"], fieldnames=run_fields),
        "meals": csv.DictWriter(handles["meals"], fieldnames=meal_fields),
        "glucose": csv.DictWriter(handles["glucose"], fieldnames=glucose_fields),
        "episodes": csv.DictWriter(handles["episodes"], fieldnames=episode_fields),
    }
    for writer in writers.values():
        writer.writeheader()

    counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    context_counts: Counter[str] = Counter()
    episode_counts: Counter[str] = Counter()
    glucose_min = math.inf
    glucose_max = -math.inf

    try:
        for patient_index, profile in enumerate(profiles, start=1):
            patient_id = profile["patient_id"]
            clinical = clinical_by_patient[patient_id]
            group = patient_group(profile, clinical)
            donor_group = "PREDIABETES_RANGE" if group == "PREDIABETES" else "DIABETES_RANGE"
            rng = random.Random(args.seed + int(patient_id) * 1009)
            donor = select_donor(profile, clinical, donors_by_group[donor_group], rng)
            donor_id = donor["source_subject_id"]
            patient_fasting = number(clinical.get("fasting_glucose_mg_dl"))
            if patient_fasting is not None:
                anchor = patient_fasting
                anchor_source = "NHANES_OBSERVED_FASTING_GLUCOSE"
            else:
                a1c = number(clinical.get("hba1c_pct"))
                if a1c is not None:
                    anchor = intercept + slope * a1c
                    anchor_source = "CGMACROS_A1C_FASTING_REGRESSION"
                else:
                    anchor = donor_fasting[donor_id]
                    anchor_source = "CGMACROS_DONOR_FASTING_FALLBACK"
            run_id = f"SIM60-{int(patient_id):06d}"
            patient_seed = args.seed + int(patient_id) * 1009
            group_counts[group] += 1
            writers["runs"].writerow(
                {
                    "simulation_run_id": run_id,
                    "patient_id": patient_id,
                    "patient_group": group,
                    "start_date": args.start_date.isoformat(),
                    "end_date": (args.start_date + timedelta(days=args.days - 1)).isoformat(),
                    "days": args.days,
                    "random_seed": patient_seed,
                    "source_subject_id": donor_id,
                    "donor_match_score": f"{donor_score(profile, clinical, donor):.4f}",
                    "fasting_anchor_mg_dl": f"{anchor:.1f}",
                    "fasting_anchor_source": anchor_source,
                    "generator_version": GENERATOR_VERSION,
                    "is_synthetic": "True",
                }
            )
            counts["runs"] += 1

            source_days = sorted(
                day for subject, day in meals_by_subject_day if subject == donor_id
            )
            generated_days: list[dict[str, object]] = []
            meal_sequence = 0
            measurement_sequence = 0
            episode_sequence = 0
            used_measurement_times: set[datetime] = set()

            def add_measurement(
                measured_at: datetime,
                value: float,
                context: str,
                source_value: float,
                generation_method: str,
                meal: dict[str, object] | None = None,
                minutes_from_meal: int | str = "",
                episode_id: str = "",
                anomaly: bool = False,
            ) -> str:
                nonlocal measurement_sequence, glucose_min, glucose_max
                measurement_sequence += 1
                measurement_id = f"{run_id}-GLU-{measurement_sequence:04d}"
                original_time = measured_at
                while measured_at in used_measurement_times:
                    measured_at += timedelta(minutes=1)
                used_measurement_times.add(measured_at)
                timestamp_adjustment = round(
                    (measured_at - original_time).total_seconds() / 60
                )
                raw_rounded = round(value)
                rounded = min(
                    SIMULATOR_GLUCOSE_MAX,
                    max(SIMULATOR_GLUCOSE_MIN, raw_rounded),
                )
                if rounded != raw_rounded:
                    adjustment_flag = "BOUNDED_TO_SIMULATOR_SENSOR_RANGE"
                else:
                    adjustment_flag = ""
                glucose_min = min(glucose_min, rounded)
                glucose_max = max(glucose_max, rounded)
                writers["glucose"].writerow(
                    {
                        "measurement_id": measurement_id,
                        "simulation_run_id": run_id,
                        "patient_id": patient_id,
                        "measured_at": measured_at.isoformat(sep=" "),
                        "glucose_mg_dl": rounded,
                        "raw_generated_glucose_mg_dl": raw_rounded,
                        "value_adjustment_flag": adjustment_flag,
                        "timestamp_adjustment_minutes": timestamp_adjustment,
                        "unit": "mg/dL",
                        "meal_context": context,
                        "measurement_source": "SIMULATOR",
                        "meal_id": meal["meal_id"] if meal else "",
                        "minutes_from_meal_start": minutes_from_meal,
                        "source_subject_id": donor_id,
                        "source_meal_id": meal["source_meal_id"] if meal else "",
                        "source_observed_glucose_mg_dl": f"{source_value:.1f}",
                        "fasting_anchor_mg_dl": f"{anchor:.1f}",
                        "fasting_anchor_source": anchor_source,
                        "generation_method": generation_method,
                        "episode_id": episode_id,
                        "is_anomaly_scenario": str(anomaly),
                        "is_synthetic": "True",
                        "generator_version": GENERATOR_VERSION,
                    }
                )
                counts["glucose"] += 1
                context_counts[context] += 1
                return measurement_id

            for day_index in range(args.days):
                synthetic_day = args.start_date + timedelta(days=day_index)
                source_day = rng.choice(source_days)
                source_day_meals = meals_by_subject_day[(donor_id, source_day)]
                day_meals: dict[str, list[dict[str, object]]] = defaultdict(list)
                for source_meal in source_day_meals:
                    meal_sequence += 1
                    scale_factor = rng.choice((0.90, 0.95, 1.00, 1.05, 1.10))
                    source_dt = source_meal["source_datetime"]
                    meal_started = timestamp_on(synthetic_day, source_dt, rng.randint(-15, 15))
                    meal_id = f"{run_id}-MEAL-{meal_sequence:04d}"
                    generated = {
                        "meal_id": meal_id,
                        "meal_started_at": meal_started,
                        "meal_type": source_meal["meal_type"],
                        "source_meal_id": source_meal["meal_id"],
                        "source_day": source_day,
                        "response": meal_response[source_meal["meal_id"]],
                    }
                    day_meals[source_meal["meal_type"]].append(generated)
                    writers["meals"].writerow(
                        {
                            "meal_id": meal_id,
                            "simulation_run_id": run_id,
                            "patient_id": patient_id,
                            "meal_started_at": meal_started.isoformat(sep=" "),
                            "meal_type": source_meal["meal_type"],
                            "calories_kcal": safe_scale(source_meal["calories_kcal"], scale_factor),
                            "carbohydrate_g": safe_scale(source_meal["carbohydrate_g"], scale_factor),
                            "protein_g": safe_scale(source_meal["protein_g"], scale_factor),
                            "fat_g": safe_scale(source_meal["fat_g"], scale_factor),
                            "fiber_g": safe_scale(source_meal["fiber_g"], scale_factor),
                            "amount_consumed_pct": source_meal["amount_consumed_pct"],
                            "source_subject_id": donor_id,
                            "source_meal_id": source_meal["meal_id"],
                            "source_day": source_day.isoformat(),
                            "macro_scale_factor": f"{scale_factor:.2f}",
                            "generation_method": "CGMACROS_DAY_BLOCK_BOOTSTRAP",
                            "is_synthetic": "True",
                            "generator_version": GENERATOR_VERSION,
                        }
                    )
                    counts["meals"] += 1

                schedule = measurement_schedule(group, day_index, synthetic_day.weekday())
                adherence = float(schedule["adherence"])
                fasting_source = observed_glucose_for_time(
                    readings_by_subject_day[(donor_id, source_day)], 7 * 60
                ) or donor_fasting[donor_id]
                if schedule["fasting"] and rng.random() <= adherence:
                    fasting_time = datetime.combine(synthetic_day, time(7, 0)) + timedelta(
                        minutes=rng.randint(-20, 20)
                    )
                    value = anchor + (fasting_source - donor_fasting[donor_id])
                    add_measurement(
                        fasting_time, value, "FASTING", fasting_source,
                        "DONOR_TIME_OF_DAY_OFFSET_FROM_PATIENT_FASTING_ANCHOR",
                    )

                contexts = []
                paired_type = schedule["paired_meal"]
                if paired_type:
                    contexts.append((str(paired_type), True))
                if schedule["pre_lunch"] and paired_type != "LUNCH":
                    contexts.append(("LUNCH", False))
                if schedule["pre_dinner"] and paired_type != "DINNER":
                    contexts.append(("DINNER", False))
                for meal_type, include_post in contexts:
                    candidates = day_meals.get(meal_type, [])
                    if not candidates:
                        continue
                    meal = rng.choice(candidates)
                    response = meal["response"]
                    if rng.random() <= adherence:
                        pre_time = meal["meal_started_at"] - timedelta(minutes=rng.randint(5, 15))
                        pre_value = anchor + (response["pre"] - donor_fasting[donor_id])
                        add_measurement(
                            pre_time, pre_value, "BEFORE_MEAL", response["pre"],
                            "CGMACROS_MEAL_RESPONSE_OFFSET_FROM_PATIENT_FASTING_ANCHOR",
                            meal, -round((meal["meal_started_at"] - pre_time).total_seconds() / 60),
                        )
                    if include_post and rng.random() <= adherence:
                        post_time = meal["meal_started_at"] + timedelta(minutes=120 + rng.randint(-10, 10))
                        post_value = anchor + (response["post"] - donor_fasting[donor_id])
                        add_measurement(
                            post_time, post_value, "AFTER_MEAL", response["post"],
                            "CGMACROS_MEAL_RESPONSE_OFFSET_FROM_PATIENT_FASTING_ANCHOR",
                            meal, round((post_time - meal["meal_started_at"]).total_seconds() / 60),
                        )

                if schedule["bedtime"] and rng.random() <= adherence:
                    bedtime_source = observed_glucose_for_time(
                        readings_by_subject_day[(donor_id, source_day)], 22 * 60
                    ) or donor_fasting[donor_id]
                    bedtime = datetime.combine(synthetic_day, time(22, 0)) + timedelta(
                        minutes=rng.randint(-20, 20)
                    )
                    bedtime_value = anchor + (bedtime_source - donor_fasting[donor_id])
                    add_measurement(
                        bedtime, bedtime_value, "BEFORE_SLEEP", bedtime_source,
                        "DONOR_TIME_OF_DAY_OFFSET_FROM_PATIENT_FASTING_ANCHOR",
                    )
                generated_days.append({"date": synthetic_day, "meals": day_meals})

            scenarios: list[str] = []
            if group == "PREDIABETES":
                if rng.random() < 0.30:
                    scenarios.append("POSTPRANDIAL_HIGH")
            elif group == "TYPE_2_NON_INSULIN":
                scenarios.extend(["POSTPRANDIAL_HIGH", "SUSTAINED_HIGH"])
            else:
                scenarios.extend(["POSTPRANDIAL_HIGH", "HYPOGLYCEMIA"])

            used_day_indexes: set[int] = set()
            for scenario in scenarios:
                available = [index for index in range(3, args.days - 2) if index not in used_day_indexes]
                if not available:
                    continue
                selected_index = rng.choice(available)
                used_day_indexes.add(selected_index)
                episode_sequence += 1
                episode_id = f"{run_id}-EP-{episode_sequence:02d}"
                episode_day = args.start_date + timedelta(days=selected_index)
                extreme_kind = "LOW" if scenario == "HYPOGLYCEMIA" else "HIGH"
                pool = source_extremes[(donor_group, extreme_kind)]
                source_ts, source_value = rng.choice(pool)
                if scenario == "HYPOGLYCEMIA":
                    event_time = datetime.combine(episode_day, time(16, 30))
                    context = "RANDOM"
                else:
                    event_time = datetime.combine(episode_day, time(20, 30))
                    context = "AFTER_MEAL"
                trigger_id = add_measurement(
                    event_time,
                    source_value,
                    context,
                    source_value,
                    "CGMACROS_OBSERVED_EXTREME_REPLAY_FOR_TEST_SCENARIO",
                    episode_id=episode_id,
                    anomaly=True,
                )
                recovery_time = event_time + timedelta(minutes=60 if scenario != "SUSTAINED_HIGH" else 150)
                add_measurement(
                    recovery_time,
                    anchor,
                    "EVENT_RECOVERY",
                    donor_fasting[donor_id],
                    "PATIENT_FASTING_ANCHOR_RECOVERY",
                    episode_id=episode_id,
                    anomaly=True,
                )
                writers["episodes"].writerow(
                    {
                        "episode_id": episode_id,
                        "simulation_run_id": run_id,
                        "patient_id": patient_id,
                        "scenario_type": scenario,
                        "started_at": event_time.isoformat(sep=" "),
                        "peak_at": event_time.isoformat(sep=" "),
                        "resolved_at": recovery_time.isoformat(sep=" "),
                        "trigger_measurement_id": trigger_id,
                        "source_subject_id": donor_id,
                        "source_observed_glucose_mg_dl": f"{source_value:.1f}",
                        "scenario_policy": "TEST_COVERAGE_NOT_EPISODE_PREVALENCE",
                        "is_forced_test_scenario": "True",
                        "is_synthetic": "True",
                        "generator_version": GENERATOR_VERSION,
                    }
                )
                counts["episodes"] += 1
                episode_counts[scenario] += 1

            if patient_index % 500 == 0:
                print(f"generated {patient_index}/{len(profiles)} patients", flush=True)
    finally:
        for handle in handles.values():
            handle.close()

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator_version": GENERATOR_VERSION,
        "seed": args.seed,
        "start_date": args.start_date.isoformat(),
        "end_date": (args.start_date + timedelta(days=args.days - 1)).isoformat(),
        "days": args.days,
        "patient_count": counts["runs"],
        "patient_groups": dict(sorted(group_counts.items())),
        "row_counts": dict(sorted(counts.items())),
        "measurement_context_counts": dict(sorted(context_counts.items())),
        "episode_counts": dict(sorted(episode_counts.items())),
        "glucose_min_mg_dl": glucose_min,
        "glucose_max_mg_dl": glucose_max,
        "fasting_fallback_model": {
            "method": "ordinary least squares fitted only on retained CGMacros source profiles",
            "intercept": intercept,
            "a1c_slope": slope,
        },
        "simulator_glucose_bounds_mg_dl": {
            "minimum": SIMULATOR_GLUCOSE_MIN,
            "maximum": SIMULATOR_GLUCOSE_MAX,
            "policy": "Raw generated value is retained; bounded display value is flagged",
        },
        "provenance_policy": (
            "Every generated meal and glucose row stores donor source_subject_id; meal-linked "
            "rows also store source_meal_id. Forced anomalies are explicit test scenarios, "
            "not estimates of clinical prevalence."
        ),
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in paths.values()
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output / "README.md").write_text(
        """# Synthetic 60-day diabetes simulator output

This folder is fully synthetic application-test data derived from NHANES baseline
anchors and observed CGMacros donor patterns. It is not a clinical forecast and must
not be used to recommend treatment or estimate event prevalence.

Patient groups:

- `PREDIABETES`
- `TYPE_2_NON_INSULIN`
- `TYPE_2_INSULIN`

All rows include reproducibility/provenance fields. Forced anomaly episodes are marked
`is_forced_test_scenario=True` and use the policy
`TEST_COVERAGE_NOT_EPISODE_PREVALENCE`.

Large generated CSV files should normally remain outside Git. Commit the generator,
schema, README, and manifest; regenerate the CSV files with the recorded seed.
""",
        encoding="utf-8",
    )
    print(json.dumps(manifest["row_counts"], sort_keys=True))


if __name__ == "__main__":
    main()

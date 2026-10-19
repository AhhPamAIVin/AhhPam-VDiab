#!/usr/bin/env python3
"""Build an explainable, dataset-only personalized glucose attention layer.

The engine learns a per-patient, per-context baseline from the first 14 days,
then labels later synthetic measurements as NORMAL, WATCH, or DANGER. WATCH
suggestions are automatic and limited to self-monitoring/data-capture actions;
they never change medication or a clinician-authored care plan.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ENGINE_VERSION = "personalized_attention_engine_v1.1"
BASELINE_DAYS = 14
MIN_BASELINE_POINTS = 5
TREND_MAX_GAP_MINUTES = 180
SUGGESTION_COOLDOWN_HOURS = 12


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return prefix + hashlib.sha1(payload.encode()).hexdigest()[:16].upper()


def file_metadata(path: Path, rows: int) -> dict[str, object]:
    payload = path.read_bytes()
    return {"rows": rows, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def suggestion(reason: str, side: str) -> tuple[str, str]:
    if side == "LOW":
        return (
            "RECHECK_AND_RECORD_LOW_CONTEXT",
            "Đo lại theo kế hoạch cá nhân và ghi nhận bữa ăn, vận động, triệu chứng cùng thời điểm dùng thuốc.",
        )
    if reason == "REPEATED_ABOVE_TARGET":
        return (
            "REVIEW_REPEATED_HIGH_CONTEXT",
            "Tiếp tục theo dõi và ghi nhận bữa ăn, vận động, thuốc đã dùng để làm rõ mẫu tăng lặp lại.",
        )
    return (
        "RECHECK_AND_RECORD_HIGH_CONTEXT",
        "Đo lại theo kế hoạch cá nhân và ghi nhận bữa ăn, vận động cùng thời điểm dùng thuốc.",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--monitoring-profiles", type=Path, required=True)
    parser.add_argument("--care-plan-versions", type=Path)
    parser.add_argument("--baseline-epochs", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    static_profiles = {row["patient_id"]: row for row in read_csv(args.monitoring_profiles)}
    version_rows = read_csv(args.care_plan_versions) if args.care_plan_versions else []
    versions_by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in version_rows:
        versions_by_patient[row["patient_id"]].append(row)
    for rows in versions_by_patient.values():
        rows.sort(key=lambda item: item["effective_from"])

    def effective_profile(patient_id: str, at: datetime) -> dict[str, str]:
        versions = versions_by_patient.get(patient_id, [])
        applicable = [row for row in versions if date.fromisoformat(row["effective_from"]) <= at.date()]
        return applicable[-1] if applicable else static_profiles[patient_id]

    measurements = read_csv(args.measurements)
    by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in measurements:
        if row.get("patient_id") in static_profiles:
            row["_dt"] = datetime.fromisoformat(row["measured_at"])
            by_patient[row["patient_id"]].append(row)

    epoch_rows = read_csv(args.baseline_epochs) if args.baseline_epochs else []
    epochs_by_patient: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in epoch_rows:
        epochs_by_patient[row["patient_id"]].append(row)
    for patient_id, rows in by_patient.items():
        if not epochs_by_patient.get(patient_id):
            epochs_by_patient[patient_id].append({
                "baseline_epoch_id": f"BL-{patient_id}-LEGACY",
                "patient_id": patient_id,
                "care_plan_id": effective_profile(patient_id, rows[0]["_dt"]).get("care_plan_id", ""),
                "care_plan_version": effective_profile(patient_id, rows[0]["_dt"]).get("care_plan_version", "1"),
                "started_at": rows[0]["_dt"].date().isoformat(), "ended_at": "", "status": "LEARNING",
            })
        epochs_by_patient[patient_id].sort(key=lambda item: item["started_at"])

    def effective_epoch(patient_id: str, at: datetime) -> dict[str, str] | None:
        applicable = [
            row for row in epochs_by_patient.get(patient_id, [])
            if date.fromisoformat(row["started_at"]) <= at.date()
            and (not row.get("ended_at") or at.date() < date.fromisoformat(row["ended_at"][:10]))
        ]
        return applicable[-1] if applicable else None

    baseline_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for patient_id, rows in by_patient.items():
        rows.sort(key=lambda item: item["_dt"])
        for row in rows:
            epoch = effective_epoch(patient_id, row["_dt"])
            if epoch is None:
                continue
            epoch_start = datetime.combine(date.fromisoformat(epoch["started_at"][:10]), datetime.min.time())
            baseline_end = epoch_start + timedelta(days=BASELINE_DAYS)
            if row["_dt"] >= baseline_end:
                continue
            value = float(row["glucose_mg_dl"])
            baseline_key = (patient_id, epoch["baseline_epoch_id"], row.get("meal_context") or "UNSPECIFIED")
            # Materialize the context even when every baseline value is unsafe;
            # absence then remains explicit as baseline_usable=False.
            baseline_values[baseline_key]
            # Danger values never become a patient's learned "normal."
            if 54 <= value <= 250:
                baseline_values[baseline_key].append(value)

    baseline_rows: list[dict[str, object]] = []
    baseline_lookup: dict[tuple[str, str, str], dict[str, float | int | None]] = {}
    for (patient_id, epoch_id, context), values in sorted(baseline_values.items()):
        stats = {
            "count": len(values),
            "p10": percentile(values, 0.10),
            "median": percentile(values, 0.50),
            "p90": percentile(values, 0.90),
        }
        baseline_lookup[(patient_id, epoch_id, context)] = stats
        epoch = next(row for row in epochs_by_patient[patient_id] if row["baseline_epoch_id"] == epoch_id)
        epoch_start = datetime.combine(date.fromisoformat(epoch["started_at"][:10]), datetime.min.time())
        baseline_rows.append({
            "patient_id": patient_id,
            "baseline_epoch_id": epoch_id,
            "care_plan_id": epoch.get("care_plan_id", ""),
            "care_plan_version": epoch.get("care_plan_version", ""),
            "meal_context": context,
            "baseline_start": epoch_start.isoformat(sep=" "),
            "baseline_end_exclusive": (epoch_start + timedelta(days=BASELINE_DAYS)).isoformat(sep=" "),
            "baseline_point_count": stats["count"],
            "p10_mg_dl": round(stats["p10"], 1) if stats["p10"] is not None else "",
            "median_mg_dl": round(stats["median"], 1) if stats["median"] is not None else "",
            "p90_mg_dl": round(stats["p90"], 1) if stats["p90"] is not None else "",
            "baseline_usable": str(stats["count"] >= MIN_BASELINE_POINTS),
            "engine_version": ENGINE_VERSION,
        })

    assessments: list[dict[str, object]] = []
    attention_events: list[dict[str, object]] = []
    last_event_at: dict[tuple[str, str], datetime] = {}
    status_counts: Counter[str] = Counter()
    patient_status_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for patient_id, rows in sorted(by_patient.items(), key=lambda item: int(item[0])):
        recent_above: deque[datetime] = deque()
        previous: dict[str, str] | None = None

        for row in rows:
            dt: datetime = row["_dt"]
            profile = effective_profile(patient_id, dt)
            target_low = float(profile.get("target_low_mg_dl") or profile.get("normal_low_mg_dl") or 70)
            target_high = float(profile.get("target_high_mg_dl") or profile.get("normal_high_mg_dl") or 180)
            danger_low = float(profile["danger_low_mg_dl"])
            danger_high = float(profile["danger_high_mg_dl"])
            medication_high_risk = profile.get("medication_hypoglycemia_risk") == "HIGH_REVIEW_PRIORITY"
            epoch = effective_epoch(patient_id, dt)
            epoch_id = epoch["baseline_epoch_id"] if epoch else ""
            epoch_start = (
                datetime.combine(date.fromisoformat(epoch["started_at"][:10]), datetime.min.time())
                if epoch else None
            )
            baseline_end = epoch_start + timedelta(days=BASELINE_DAYS) if epoch_start else None
            value = float(row["glucose_mg_dl"])
            context = row.get("meal_context") or "UNSPECIFIED"
            baseline = baseline_lookup.get((patient_id, epoch_id, context), {})
            baseline_usable = int(baseline.get("count") or 0) >= MIN_BASELINE_POINTS
            trend_rate = None
            if previous is not None:
                gap = (dt - previous["_dt"]).total_seconds() / 60
                if 0 < gap <= TREND_MAX_GAP_MINUTES:
                    trend_rate = (value - float(previous["glucose_mg_dl"])) / gap

            while recent_above and recent_above[0] < dt - timedelta(hours=24):
                recent_above.popleft()
            if target_high < value <= danger_high:
                recent_above.append(dt)

            status = "NORMAL"
            score = 0
            reason = "WITHIN_PERSONAL_TARGET"
            side = "NONE"
            if value < danger_low:
                status, score, reason, side = "DANGER", 100, "DANGER_LOW", "LOW"
            elif value > danger_high:
                status, score, reason, side = "DANGER", 100, "DANGER_HIGH", "HIGH"
            elif baseline_end is not None and dt < baseline_end:
                status, reason = "BASELINE_LEARNING", "BASELINE_WINDOW"
            elif danger_low <= value < target_low:
                status, score, reason, side = "WATCH", 75, "LEVEL_1_LOW", "LOW"
            elif target_high < value <= danger_high:
                repeated = len(recent_above) >= 3
                status, score, reason, side = (
                    "WATCH", 70 if repeated else 60,
                    "REPEATED_ABOVE_TARGET" if repeated else "ABOVE_TARGET_NOT_DANGER", "HIGH"
                )
            elif target_low <= value < target_low + 10 and trend_rate is not None and trend_rate <= -0.5:
                status, score, reason, side = "WATCH", 45, "NEAR_LOW_AND_FALLING", "LOW"
            elif target_high - 20 <= value <= target_high and trend_rate is not None and trend_rate >= 0.5:
                status, score, reason, side = "WATCH", 40, "NEAR_HIGH_AND_RISING", "HIGH"
            elif baseline_usable and baseline.get("p90") is not None and value > float(baseline["p90"]) + 20:
                status, score, reason, side = "WATCH", 35, "ABOVE_PERSONAL_BASELINE", "HIGH"
            elif baseline_usable and baseline.get("p10") is not None and value < float(baseline["p10"]) - 15:
                status, score, reason, side = "WATCH", 35, "BELOW_PERSONAL_BASELINE", "LOW"

            modifiers: list[str] = []
            if status == "WATCH" and side == "LOW" and medication_high_risk:
                score = min(89, score + 10)
                modifiers.append("HYPOGLYCEMIA_RISK_MEDICATION_REPORTED")
            if status == "WATCH" and side == "LOW" and profile["risk_group"] == "OLDER_OR_HIGH_RISK":
                score = min(89, score + 5)
                modifiers.append("OLDER_OR_HIGH_RISK_PROFILE")

            suggestion_code = ""
            suggestion_text = ""
            suggestion_status = "NOT_APPLICABLE"
            event_allowed = False
            if status == "WATCH":
                suggestion_code, suggestion_text = suggestion(reason, side)
                cooldown_key = (patient_id, reason)
                previous_event_at = last_event_at.get(cooldown_key)
                event_allowed = (
                    previous_event_at is None
                    or dt - previous_event_at >= timedelta(hours=SUGGESTION_COOLDOWN_HOURS)
                )
                suggestion_status = "AUTO_GENERATED" if event_allowed else "SUPPRESSED_BY_COOLDOWN"

            assessment_id = stable_id("ATT-ASMT-", row["measurement_id"], ENGINE_VERSION)
            evidence = {
                "value_mg_dl": value,
                "target_range_mg_dl": [target_low, target_high],
                "danger_boundaries_mg_dl": [danger_low, danger_high],
                "trend_mg_dl_per_min": round(trend_rate, 3) if trend_rate is not None else None,
                "baseline_p10_mg_dl": round(float(baseline["p10"]), 1) if baseline_usable else None,
                "baseline_p90_mg_dl": round(float(baseline["p90"]), 1) if baseline_usable else None,
                "above_target_readings_24h": len(recent_above),
            }
            assessment = {
                "attention_assessment_id": assessment_id,
                "measurement_id": row["measurement_id"],
                "patient_id": patient_id,
                "care_plan_id": profile.get("care_plan_id", ""),
                "care_plan_version": profile.get("care_plan_version", "1"),
                "baseline_epoch_id": epoch_id,
                "measured_at": row["measured_at"],
                "meal_context": context,
                "glucose_mg_dl": int(value) if value.is_integer() else value,
                "status": status,
                "attention_score": score,
                "primary_reason_code": reason,
                "risk_modifiers": "|".join(modifiers),
                "evidence_snapshot": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                "suggestion_code": suggestion_code,
                "suggestion_text": suggestion_text,
                "suggestion_status": suggestion_status,
                "requires_doctor_approval": "False",
                "automated_medication_change_allowed": "False",
                "is_synthetic": "True",
                "engine_version": ENGINE_VERSION,
            }
            assessments.append(assessment)
            status_counts[status] += 1
            patient_status_counts[patient_id][status] += 1
            if status == "WATCH" and event_allowed:
                attention_events.append({
                    "attention_event_id": stable_id("ATT-EVT-", assessment_id),
                    **assessment,
                    "event_state": "OPEN_AUTO_SUGGESTION",
                    "reviewed_by_doctor_id": "",
                    "reviewed_at": "",
                })
                last_event_at[(patient_id, reason)] = dt
            previous = row

    summary_rows: list[dict[str, object]] = []
    for patient_id, counts in sorted(patient_status_counts.items(), key=lambda item: int(item[0])):
        summary_rows.append({
            "patient_id": patient_id,
            "baseline_learning_count": counts["BASELINE_LEARNING"],
            "normal_count": counts["NORMAL"],
            "watch_count": counts["WATCH"],
            "danger_count": counts["DANGER"],
            "has_auto_attention_suggestion": str(counts["WATCH"] > 0),
            "engine_version": ENGINE_VERSION,
        })

    output_dir = args.output_dir
    rule_rows = [
        {"priority": 1, "rule_code": "DANGER_LOW", "condition": "glucose < patient.danger_low", "result": "DANGER", "base_score": 100, "automatic_suggestion": "False"},
        {"priority": 2, "rule_code": "DANGER_HIGH", "condition": "glucose > patient.danger_high", "result": "DANGER", "base_score": 100, "automatic_suggestion": "False"},
        {"priority": 3, "rule_code": "LEVEL_1_LOW", "condition": "danger_low <= glucose < target_low", "result": "WATCH", "base_score": 75, "automatic_suggestion": "True"},
        {"priority": 4, "rule_code": "ABOVE_TARGET", "condition": "target_high < glucose <= danger_high", "result": "WATCH", "base_score": 60, "automatic_suggestion": "True"},
        {"priority": 5, "rule_code": "NEAR_LOW_FALLING", "condition": "target_low <= glucose < target_low+10 and trend <= -0.5 mg/dL/min", "result": "WATCH", "base_score": 45, "automatic_suggestion": "True"},
        {"priority": 6, "rule_code": "NEAR_HIGH_RISING", "condition": "target_high-20 <= glucose <= target_high and trend >= 0.5 mg/dL/min", "result": "WATCH", "base_score": 40, "automatic_suggestion": "True"},
        {"priority": 7, "rule_code": "ABOVE_PERSONAL_BASELINE", "condition": "glucose > context_p90+20 within safety boundaries", "result": "WATCH", "base_score": 35, "automatic_suggestion": "True"},
        {"priority": 8, "rule_code": "BELOW_PERSONAL_BASELINE", "condition": "glucose < context_p10-15 within safety boundaries", "result": "WATCH", "base_score": 35, "automatic_suggestion": "True"},
        {"priority": 9, "rule_code": "WITHIN_TARGET", "condition": "no higher-priority rule matched", "result": "NORMAL", "base_score": 0, "automatic_suggestion": "False"},
    ]
    for rule in rule_rows:
        rule["requires_doctor_approval"] = "False"
        rule["automated_medication_change_allowed"] = "False"
        rule["engine_version"] = ENGINE_VERSION
    write_csv(output_dir / "attention_rule_config.csv", rule_rows, list(rule_rows[0]))
    write_csv(output_dir / "patient_glucose_baselines.csv", baseline_rows, list(baseline_rows[0]))
    write_csv(output_dir / "patient_attention_assessments.csv", assessments, list(assessments[0]))
    write_csv(output_dir / "patient_attention_events.csv", attention_events, list(attention_events[0]))
    write_csv(output_dir / "patient_attention_summary.csv", summary_rows, list(summary_rows[0]))

    report = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "engine_version": ENGINE_VERSION,
        "scope": "Dataset-only synthetic TYPE_2 personalized attention analysis",
        "care_plan_version_aware": bool(version_rows),
        "baseline_epoch_aware": bool(epoch_rows),
        "baseline_days": BASELINE_DAYS,
        "minimum_baseline_points_per_context": MIN_BASELINE_POINTS,
        "suggestion_cooldown_hours_per_patient_reason": SUGGESTION_COOLDOWN_HOURS,
        "patient_count": len(by_patient),
        "measurement_count": len(assessments),
        "attention_event_count": len(attention_events),
        "status_counts": dict(sorted(status_counts.items())),
        "state_precedence": ["DANGER", "WATCH", "NORMAL"],
        "watch_suggestion_requires_doctor_approval": False,
        "automated_medication_change_allowed": False,
        "safety_note": (
            "WATCH suggestions are automatic self-monitoring prompts over synthetic data. "
            "They are not diagnoses or treatment orders and never override DANGER classification."
        ),
    }
    (output_dir / "attention_engine_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_files = {
        "attention_rule_config.csv": len(rule_rows),
        "patient_glucose_baselines.csv": len(baseline_rows),
        "patient_attention_assessments.csv": len(assessments),
        "patient_attention_events.csv": len(attention_events),
        "patient_attention_summary.csv": len(summary_rows),
        "attention_engine_report.json": 1,
    }
    manifest = {
        "generated_at_utc": report["generated_at_utc"],
        "engine_version": ENGINE_VERSION,
        "files": {
            name: file_metadata(output_dir / name, rows)
            for name, rows in output_files.items()
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    parent_manifest_path = output_dir.parent / "manifest.json"
    if parent_manifest_path.exists():
        parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8-sig"))
        parent_manifest["personalized_attention_extension"] = {
            "engine_version": ENGINE_VERSION,
            "scope": report["scope"],
            "generated_at_utc": report["generated_at_utc"],
            "patient_count": report["patient_count"],
            "measurement_count": report["measurement_count"],
            "watch_assessment_count": report["status_counts"].get("WATCH", 0),
            "attention_event_count": report["attention_event_count"],
            "manifest": file_metadata(output_dir / "manifest.json", 1),
        }
        parent_manifest_path.write_text(
            json.dumps(parent_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

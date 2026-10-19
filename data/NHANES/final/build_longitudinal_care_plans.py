#!/usr/bin/env python3
"""Build versioned patient care plans from clinician-confirmed review events.

This is a dataset-only event processor. A confirmed UPDATE_TARGETS event closes
the previous version and activates a new one. NO_CHANGE never creates a fake
version. AI-authored values are rejected for normal/danger target changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PIPELINE_VERSION = "longitudinal_personalized_care_v1.0"
TARGET_SOURCE_DOI = "10.2337/dc26-S006"


EVENT_FIELDS = [
    "clinical_review_event_id", "appointment_id", "patient_id", "appointment_date",
    "effective_from", "doctor_id", "decision", "doctor_confirmed",
    "target_low_mg_dl", "target_high_mg_dl", "danger_low_mg_dl", "danger_high_mg_dl",
    "tir_70_180_min_pct", "tbr_below_70_max_pct", "tbr_below_54_max_pct",
    "tar_above_180_max_pct", "tar_above_250_max_pct", "medication_plan_changed",
    "reset_baseline", "reason_code", "clinical_note", "is_synthetic",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def number(value: str | None, fallback: float) -> float:
    return fallback if value is None or value.strip() == "" else float(value)


def display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def checksum(path: Path, rows: int) -> dict[str, object]:
    payload = path.read_bytes()
    return {"rows": rows, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def validate_thresholds(values: dict[str, float]) -> None:
    dl = values["danger_low_mg_dl"]
    tl = values["target_low_mg_dl"]
    th = values["target_high_mg_dl"]
    dh = values["danger_high_mg_dl"]
    if not dl < tl < th < dh:
        raise ValueError(f"Invalid threshold order: {dl} < {tl} < {th} < {dh} is required")
    if dl <= 0 or dh > 1000:
        raise ValueError("Thresholds must stay within 0-1000 mg/dL")


def initial_events(profiles: list[dict[str, str]], effective_from: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profile in sorted(profiles, key=lambda item: int(item["patient_id"])):
        patient_id = profile["patient_id"]
        rows.append({
            "clinical_review_event_id": f"CRE-{patient_id}-V1",
            "appointment_id": f"APT-{patient_id}-INITIAL",
            "patient_id": patient_id,
            "appointment_date": effective_from,
            "effective_from": effective_from,
            "doctor_id": profile["approved_by_doctor_id"],
            "decision": "INITIALIZE_TARGETS",
            "doctor_confirmed": "True",
            "target_low_mg_dl": profile["normal_low_mg_dl"],
            "target_high_mg_dl": profile["normal_high_mg_dl"],
            "danger_low_mg_dl": profile["danger_low_mg_dl"],
            "danger_high_mg_dl": profile["danger_high_mg_dl"],
            "tir_70_180_min_pct": profile["tir_70_180_min_pct"],
            "tbr_below_70_max_pct": profile["tbr_below_70_max_pct"],
            "tbr_below_54_max_pct": profile["tbr_below_54_max_pct"],
            "tar_above_180_max_pct": profile["tar_above_180_max_pct"],
            "tar_above_250_max_pct": profile["tar_above_250_max_pct"],
            "medication_plan_changed": "False",
            "reset_baseline": "True",
            "reason_code": "SYNTHETIC_INITIALIZATION",
            "clinical_note": "Initial synthetic clinician-confirmed target set.",
            "is_synthetic": "True",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitoring-profiles", type=Path, required=True)
    parser.add_argument("--review-events", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-effective-from", default="2026-01-01")
    args = parser.parse_args()

    profiles = read_csv(args.monitoring_profiles)
    profile_by_patient = {row["patient_id"]: row for row in profiles}
    if not args.review_events.exists():
        write_csv(
            args.review_events,
            initial_events(profiles, args.initial_effective_from),
            EVENT_FIELDS,
        )
    events = read_csv(args.review_events)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_event_ids: set[str] = set()
    for event in events:
        event_id = event["clinical_review_event_id"]
        if event_id in seen_event_ids:
            raise ValueError(f"Duplicate clinical_review_event_id: {event_id}")
        seen_event_ids.add(event_id)
        if event["patient_id"] not in profile_by_patient:
            raise ValueError(f"Unknown TYPE_2 patient: {event['patient_id']}")
        grouped[event["patient_id"]].append(event)

    care_versions: list[dict[str, object]] = []
    target_versions: list[dict[str, object]] = []
    profile_versions: list[dict[str, object]] = []
    change_log: list[dict[str, object]] = []
    baseline_epochs: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    result_counts: Counter[str] = Counter()

    for patient_id in sorted(profile_by_patient, key=int):
        base = profile_by_patient[patient_id]
        patient_events = sorted(
            grouped.get(patient_id, []),
            key=lambda item: (item["effective_from"], item["clinical_review_event_id"]),
        )
        current: dict[str, float] | None = None
        current_version = 0
        last_event_date: date | None = None
        patient_care_rows: list[dict[str, object]] = []
        patient_profile_rows: list[dict[str, object]] = []
        patient_epoch_rows: list[dict[str, object]] = []

        for event in patient_events:
            decision = event["decision"].strip().upper()
            event_date = date.fromisoformat(event["effective_from"])
            if last_event_date is not None and event_date <= last_event_date:
                raise ValueError(f"Non-increasing effective date for patient {patient_id}")
            last_event_date = event_date
            if decision == "NO_THRESHOLD_CHANGE":
                results.append({
                    "clinical_review_event_id": event["clinical_review_event_id"],
                    "patient_id": patient_id, "processing_status": "NO_CHANGE_RECORDED",
                    "created_care_plan_id": "", "message": "Existing active targets carried forward.",
                    "pipeline_version": PIPELINE_VERSION,
                })
                result_counts["NO_CHANGE_RECORDED"] += 1
                continue
            if decision not in {"INITIALIZE_TARGETS", "UPDATE_TARGETS"}:
                raise ValueError(f"Unsupported decision {decision} for {event['clinical_review_event_id']}")
            if not truthy(event.get("doctor_confirmed")) or not event.get("doctor_id"):
                raise ValueError(f"Clinician confirmation required for {event['clinical_review_event_id']}")

            fallback = current or {
                "target_low_mg_dl": float(base["normal_low_mg_dl"]),
                "target_high_mg_dl": float(base["normal_high_mg_dl"]),
                "danger_low_mg_dl": float(base["danger_low_mg_dl"]),
                "danger_high_mg_dl": float(base["danger_high_mg_dl"]),
                "tir_70_180_min_pct": float(base["tir_70_180_min_pct"]),
                "tbr_below_70_max_pct": float(base["tbr_below_70_max_pct"]),
                "tbr_below_54_max_pct": float(base["tbr_below_54_max_pct"]),
                "tar_above_180_max_pct": float(base["tar_above_180_max_pct"]),
                "tar_above_250_max_pct": float(base["tar_above_250_max_pct"]),
            }
            new_values = {field: number(event.get(field), fallback[field]) for field in fallback}
            validate_thresholds(new_values)
            current_version += 1
            care_plan_id = f"CP-CGM-{patient_id}-V{current_version}"
            baseline_reset = current is None or truthy(event.get("reset_baseline")) or truthy(event.get("medication_plan_changed"))

            if patient_care_rows:
                patient_care_rows[-1]["effective_to"] = (event_date - timedelta(days=1)).isoformat()
                patient_care_rows[-1]["status"] = "SUPERSEDED"
                patient_profile_rows[-1]["effective_to"] = (event_date - timedelta(days=1)).isoformat()
                patient_profile_rows[-1]["status"] = "SUPERSEDED"
            if patient_epoch_rows and baseline_reset:
                patient_epoch_rows[-1]["ended_at"] = event["effective_from"]
                patient_epoch_rows[-1]["status"] = "CLOSED"

            patient_care_rows.append({
                "care_plan_id": care_plan_id, "patient_id": patient_id,
                "care_plan_version": current_version,
                "source_clinical_review_event_id": event["clinical_review_event_id"],
                "appointment_id": event["appointment_id"],
                "effective_from": event["effective_from"], "effective_to": "",
                "status": "ACTIVE", "approved_by_doctor_id": event["doctor_id"],
                "approval_timestamp": f"{event['appointment_date']}T00:00:00+07:00",
                "reason_code": event["reason_code"], "clinical_note": event["clinical_note"],
                "is_synthetic": event["is_synthetic"], "pipeline_version": PIPELINE_VERSION,
            })
            patient_profile_rows.append({
                "monitoring_profile_version_id": f"CGM-MON-{patient_id}-V{current_version}",
                "care_plan_id": care_plan_id, "patient_id": patient_id,
                "care_plan_version": current_version,
                "effective_from": event["effective_from"], "effective_to": "", "status": "ACTIVE",
                "risk_group": base["risk_group"],
                "medication_hypoglycemia_risk": base["medication_hypoglycemia_risk"],
                **{key: display_number(value) for key, value in new_values.items()},
                "approved_by_doctor_id": event["doctor_id"],
                "source_clinical_review_event_id": event["clinical_review_event_id"],
                "is_synthetic": event["is_synthetic"], "pipeline_version": PIPELINE_VERSION,
            })
            metric_specs = [
                ("CGM_TARGET_RANGE", "BETWEEN", f"{display_number(new_values['target_low_mg_dl'])}|{display_number(new_values['target_high_mg_dl'])}"),
                ("CGM_DANGER_LOW", "<", str(display_number(new_values["danger_low_mg_dl"]))),
                ("CGM_DANGER_HIGH", ">", str(display_number(new_values["danger_high_mg_dl"]))),
            ]
            for metric, operator, value in metric_specs:
                target_versions.append({
                    "patient_target_version_id": f"PT-{patient_id}-{metric}-V{current_version}",
                    "care_plan_id": care_plan_id, "patient_id": patient_id,
                    "care_plan_version": current_version, "metric_code": metric,
                    "operator": operator, "target_value": value, "unit": "mg/dL",
                    "effective_from": event["effective_from"], "effective_to": "", "status": "ACTIVE",
                    "approved_by_doctor_id": event["doctor_id"],
                    "source_clinical_review_event_id": event["clinical_review_event_id"],
                    "source_doi": TARGET_SOURCE_DOI, "is_synthetic": event["is_synthetic"],
                    "pipeline_version": PIPELINE_VERSION,
                })
            if baseline_reset:
                patient_epoch_rows.append({
                    "baseline_epoch_id": f"BL-{patient_id}-V{current_version}",
                    "patient_id": patient_id, "care_plan_id": care_plan_id,
                    "care_plan_version": current_version, "started_at": event["effective_from"],
                    "ended_at": "", "status": "LEARNING", "learning_days_required": 14,
                    "reset_reason": "INITIAL_PLAN" if current is None else (
                        "MEDICATION_PLAN_CHANGED" if truthy(event.get("medication_plan_changed")) else "CLINICIAN_REQUESTED_RESET"
                    ),
                    "pipeline_version": PIPELINE_VERSION,
                })
            if current is not None:
                for field, new_value in new_values.items():
                    old_value = current[field]
                    if old_value != new_value:
                        change_log.append({
                            "change_id": f"CHG-{patient_id}-V{current_version}-{field}",
                            "patient_id": patient_id, "care_plan_id": care_plan_id,
                            "care_plan_version": current_version,
                            "clinical_review_event_id": event["clinical_review_event_id"],
                            "field_name": field, "old_value": display_number(old_value),
                            "new_value": display_number(new_value), "changed_by_doctor_id": event["doctor_id"],
                            "reason_code": event["reason_code"], "changed_at": event["effective_from"],
                            "pipeline_version": PIPELINE_VERSION,
                        })
            results.append({
                "clinical_review_event_id": event["clinical_review_event_id"],
                "patient_id": patient_id, "processing_status": "VERSION_CREATED",
                "created_care_plan_id": care_plan_id,
                "message": "Clinician-confirmed targets activated; prior version closed when present.",
                "pipeline_version": PIPELINE_VERSION,
            })
            result_counts["VERSION_CREATED"] += 1
            current = new_values

        # Synchronize target effective_to/status after all care versions are known.
        care_by_id = {row["care_plan_id"]: row for row in patient_care_rows}
        for target in target_versions:
            if target["patient_id"] == patient_id:
                care = care_by_id[target["care_plan_id"]]
                target["effective_to"] = care["effective_to"]
                target["status"] = care["status"]
        care_versions.extend(patient_care_rows)
        profile_versions.extend(patient_profile_rows)
        baseline_epochs.extend(patient_epoch_rows)

    output_files = {
        "patient_clinical_review_events.csv": (events, EVENT_FIELDS),
        "patient_care_plan_versions.csv": (care_versions, list(care_versions[0])),
        "patient_target_versions.csv": (target_versions, list(target_versions[0])),
        "patient_cgm_profile_versions.csv": (profile_versions, list(profile_versions[0])),
        "patient_care_plan_change_log.csv": (
            change_log,
            list(change_log[0]) if change_log else [
                "change_id", "patient_id", "care_plan_id", "care_plan_version",
                "clinical_review_event_id", "field_name", "old_value", "new_value",
                "changed_by_doctor_id", "reason_code", "changed_at", "pipeline_version",
            ],
        ),
        "patient_baseline_epochs.csv": (baseline_epochs, list(baseline_epochs[0])),
        "clinical_review_processing_results.csv": (results, list(results[0])),
    }
    for filename, (rows, fields) in output_files.items():
        write_csv(args.output_dir / filename, rows, fields)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "patient_count": len(profile_by_patient), "clinical_review_event_count": len(events),
        "care_plan_version_count": len(care_versions), "target_version_count": len(target_versions),
        "baseline_epoch_count": len(baseline_epochs), "change_log_count": len(change_log),
        "processing_result_counts": dict(sorted(result_counts.items())),
        "invariants": [
            "One ACTIVE care plan per patient", "Strictly increasing effective dates",
            "danger_low < target_low < target_high < danger_high",
            "Clinician confirmation required for target changes",
            "AI may adapt WATCH but may not author normal/danger target changes",
        ],
    }
    report_path = args.output_dir / "longitudinal_care_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_files = {name: checksum(args.output_dir / name, len(rows)) for name, (rows, _) in output_files.items()}
    manifest_files[report_path.name] = checksum(report_path, 1)
    (args.output_dir / "manifest.json").write_text(json.dumps({
        "generated_at_utc": report["generated_at_utc"], "pipeline_version": PIPELINE_VERSION,
        "files": manifest_files,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    parent_manifest_path = args.output_dir.parent / "manifest.json"
    if parent_manifest_path.exists():
        parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8-sig"))
        parent_manifest["longitudinal_care_extension"] = {
            "pipeline_version": PIPELINE_VERSION,
            "generated_at_utc": report["generated_at_utc"],
            "patient_count": report["patient_count"],
            "care_plan_version_count": report["care_plan_version_count"],
            "clinical_review_event_count": report["clinical_review_event_count"],
            "manifest": checksum(args.output_dir / "manifest.json", 1),
        }
        parent_manifest_path.write_text(
            json.dumps(parent_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

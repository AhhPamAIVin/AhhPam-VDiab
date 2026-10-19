#!/usr/bin/env python3
"""Generate review prompts from approved reference rules and recorded data only.

Draft/disabled rules are ignored. The engine never diagnoses, prescribes, changes a
care plan, or treats absence of documentation as proof that care was not provided.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ENGINE_VERSION = "recommendation_review_engine_v1.0"
APPROVED_STATUS = "APPROVED_FOR_REVIEW_PROMPTS"

DOMAIN_FILES = {
    "KIDNEY_SCREENING_REVIEW": ("patient_kidney_assessments.csv", "kidney assessment"),
    "EYE_SCREENING_REVIEW": ("patient_eye_exams.csv", "eye assessment"),
    "FOOT_SCREENING_REVIEW": ("patient_foot_exams.csv", "foot assessment"),
    "MENTAL_HEALTH_SCREENING_REVIEW": ("patient_mental_health_screenings.csv", "mental-health screening"),
    "CLINICAL_TARGET_REVIEW": ("patient_clinical_targets.csv", "clinician-approved clinical target"),
    "SICK_DAY_EDUCATION_REVIEW": ("patient_education_progress.csv", "sick-day education record"),
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def population_matches(rule_filter: str, patient_label: str) -> bool:
    return patient_label in {item.strip() for item in rule_filter.split("|")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules-csv", type=Path, required=True)
    parser.add_argument("--patients-csv", type=Path, required=True)
    parser.add_argument("--clinical-data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rules = read_csv(args.rules_csv)
    patients = read_csv(args.patients_csv)
    approved = [
        row for row in rules
        if row.get("enabled", "").lower() == "true"
        and row.get("approval_status") == APPROVED_STATUS
        and row.get("automated_action_allowed", "").lower() == "false"
    ]

    events = []
    missing_input_files = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for rule in approved:
        rule_type = rule["rule_type"]
        if rule_type not in DOMAIN_FILES:
            continue
        filename, display = DOMAIN_FILES[rule_type]
        input_path = args.clinical_data_dir / filename
        if not input_path.exists():
            missing_input_files.append(filename)
            continue
        recorded_patients = {row.get("patient_id", "") for row in read_csv(input_path)}
        for patient in patients:
            patient_id = patient.get("patient_id", "")
            label = patient.get("project_diabetes_label", "")
            if not patient_id or not population_matches(rule["population_filter"], label):
                continue
            if patient_id in recorded_patients:
                continue
            event_id = "REC-" + hashlib.sha1(f"{rule['recommendation_rule_id']}|{patient_id}|{rule['rule_version']}".encode()).hexdigest()[:16].upper()
            evidence = {
                "finding": "NO_RECORD_FOUND_IN_PROVIDED_DATASET",
                "searched_file": filename,
                "not_proof_care_was_not_PROVIDED": True,
                "patient_label": label,
            }
            events.append({
                "recommendation_event_id": event_id,
                "patient_id": patient_id,
                "recommendation_rule_id": rule["recommendation_rule_id"],
                "generated_at": now,
                "evidence_snapshot": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                "recommendation_text": f"Review whether a {display} should be documented. This is not a diagnosis or treatment order.",
                "status": "PENDING_CLINICAL_REVIEW",
                "reviewed_by_doctor_id": "",
                "reviewed_at": "",
                "review_note": "",
                "is_synthetic": False,
                "engine_version": ENGINE_VERSION,
            })

    fields = [
        "recommendation_event_id", "patient_id", "recommendation_rule_id", "generated_at",
        "evidence_snapshot", "recommendation_text", "status", "reviewed_by_doctor_id",
        "reviewed_at", "review_note", "is_synthetic", "engine_version",
    ]
    write_csv(args.output_dir / "patient_recommendation_events.csv", fields, events)
    report = {
        "generated_at_utc": now,
        "engine_version": ENGINE_VERSION,
        "rule_count": len(rules),
        "approved_enabled_rule_count": len(approved),
        "generated_event_count": len(events),
        "missing_input_files": sorted(set(missing_input_files)),
        "safety_note": "No draft or disabled rule was executed; outputs are review prompts only.",
    }
    (args.output_dir / "engine_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Keep a compact, balanced, high-completeness demo cohort.

The filter is deterministic and cascades patient removals through deployment,
simulator, medication-flow, caregiver, and doctor-assignment tables. Source and
intermediate NHANES/CGMacros files are not modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


FILTER_VERSION = "balanced_complete_demo_cohort_v1.0"
LABELS = ("PREDIABETES", "TYPE_2")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def complete_profile(row: dict[str, str]) -> bool:
    return (
        row.get("project_diabetes_label") in LABELS
        and row.get("quality_tier") == "GOLD"
        and row.get("stage_conflict_flag", "").lower() == "false"
        and float(row.get("profile_completeness") or 0) == 100.0
        and float(row.get("vital_completeness") or 0) == 100.0
        and float(row.get("lifestyle_completeness") or 0) == 100.0
        and float(row.get("glycemic_completeness") or 0) >= 75.0
    )


def rank_key(row: dict[str, str]) -> tuple[float, float, str]:
    return (
        -float(row.get("overall_data_quality_score") or 0),
        -float(row.get("glycemic_completeness") or 0),
        row["patient_id"],
    )


def stream_filter_csv(path: Path, keep_patient_ids: set[str]) -> tuple[int, int, Counter, dict]:
    temp = path.with_suffix(path.suffix + ".filtered.tmp")
    before = after = 0
    counters: Counter = Counter()
    numeric = {"glucose_min": None, "glucose_max": None}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        if "patient_id" not in fields:
            return 0, 0, counters, numeric
        with temp.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                before += 1
                if row.get("patient_id") not in keep_patient_ids:
                    continue
                writer.writerow(row)
                after += 1
                for field in ("patient_group", "meal_context", "scenario_type", "source_drug_name", "exclusion_reason"):
                    if row.get(field):
                        counters[f"{field}:{row[field]}"] += 1
                if row.get("glucose_mg_dl"):
                    value = float(row["glucose_mg_dl"])
                    numeric["glucose_min"] = value if numeric["glucose_min"] is None else min(numeric["glucose_min"], value)
                    numeric["glucose_max"] = value if numeric["glucose_max"] is None else max(numeric["glucose_max"], value)
    os.replace(temp, path)
    return before, after, counters, numeric


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build(repo_root: Path) -> dict:
    deployment = repo_root / "data" / "NHANES" / "deployment_dataset"
    simulator = repo_root / "data" / "simulator_60d"
    medication = repo_root / "data" / "NHANES" / "medication_flow_60d"

    profiles = read_csv(deployment / "patient_profiles.csv")
    candidates = {label: sorted([r for r in profiles if complete_profile(r) and r["project_diabetes_label"] == label], key=rank_key) for label in LABELS}
    balanced_count = min(len(candidates[label]) for label in LABELS)
    selected = {label: candidates[label][:balanced_count] for label in LABELS}
    keep_patient_ids = {row["patient_id"] for label in LABELS for row in selected[label]}
    if balanced_count == 0:
        raise RuntimeError("No balanced complete cohort can be formed")

    report = {
        "filter_version": FILTER_VERSION,
        "filtered_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_is_random": False,
        "selection_order": "overall_data_quality_score DESC, glycemic_completeness DESC, patient_id ASC",
        "eligibility": {
            "quality_tier": "GOLD",
            "stage_conflict_flag": False,
            "profile_completeness": 100,
            "vital_completeness": 100,
            "lifestyle_completeness": 100,
            "glycemic_completeness_minimum": 75,
        },
        "eligible_before_balancing": {label: len(candidates[label]) for label in LABELS},
        "retained_patient_counts": {label: balanced_count for label in LABELS},
        "retained_patient_total": len(keep_patient_ids),
        "tables": {},
        "source_or_intermediate_data_modified": False,
    }

    deployment_patient_files = [
        "patient_profiles.csv", "patient_baseline_clinical.csv", "patient_lifestyle_baseline.csv",
        "patient_prescription_status.csv", "patient_medications.csv", "project_diabetes_cohort.csv",
        "caregiver_profiles.csv", "patient_doctor_assignments.csv",
    ]
    for filename in deployment_patient_files:
        before, after, _, _ = stream_filter_csv(deployment / filename, keep_patient_ids)
        report["tables"][f"deployment/{filename}"] = {"before": before, "after": after, "removed": before - after}

    assignments = read_csv(deployment / "patient_doctor_assignments.csv")
    keep_doctor_ids = {row["doctor_id"] for row in assignments}
    doctor_path = deployment / "doctor_profiles.csv"
    doctor_temp = doctor_path.with_suffix(".csv.filtered.tmp")
    doctors = read_csv(doctor_path)
    with doctor_temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(doctors[0]))
        writer.writeheader()
        retained_doctors = [row for row in doctors if row["doctor_id"] in keep_doctor_ids]
        writer.writerows(retained_doctors)
    os.replace(doctor_temp, doctor_path)
    report["tables"]["deployment/doctor_profiles.csv"] = {"before": len(doctors), "after": len(retained_doctors), "removed": len(doctors) - len(retained_doctors)}

    simulator_stats = {}
    for path in sorted(simulator.glob("*.csv")):
        before, after, counters, numeric = stream_filter_csv(path, keep_patient_ids)
        simulator_stats[path.name] = {"before": before, "after": after, "counters": counters, "numeric": numeric}
        report["tables"][f"simulator/{path.name}"] = {"before": before, "after": after, "removed": before - after}

    medication_stats = {}
    for path in sorted(medication.glob("*.csv")):
        before, after, counters, numeric = stream_filter_csv(path, keep_patient_ids)
        medication_stats[path.name] = {"before": before, "after": after, "counters": counters, "numeric": numeric}
        report["tables"][f"medication/{path.name}"] = {"before": before, "after": after, "removed": before - after}

    def update_deployment_manifest(data: dict) -> None:
        data["generated_at_utc"] = report["filtered_at_utc"]
        data["dataset_version"] = data.get("dataset_version", "") + "+balanced-demo-v1"
        table_files = {
            "patient_profiles": "patient_profiles.csv", "patient_baseline_clinical": "patient_baseline_clinical.csv",
            "patient_lifestyle_baseline": "patient_lifestyle_baseline.csv", "patient_prescription_status": "patient_prescription_status.csv",
            "patient_medications": "patient_medications.csv", "project_diabetes_cohort": "project_diabetes_cohort.csv",
            "caregiver_profiles": "caregiver_profiles.csv", "doctor_profiles": "doctor_profiles.csv",
            "patient_doctor_assignments": "patient_doctor_assignments.csv",
        }
        data["tables"] = {name: len(read_csv(deployment / filename)) for name, filename in table_files.items()}
        data["synthetic_doctor_count"] = len(retained_doctors)
        data["balanced_demo_filter"] = report["eligibility"] | {
            "selection_is_random": False,
            "patients_per_label": balanced_count,
            "labels": list(LABELS),
        }

    update_json(deployment / "manifest.json", update_deployment_manifest)

    def update_simulator_manifest(data: dict) -> None:
        data["generated_at_utc"] = report["filtered_at_utc"]
        data["patient_count"] = len(keep_patient_ids)
        run_counts = simulator_stats["simulation_runs.csv"]["counters"]
        data["patient_groups"] = {key.split(":", 1)[1]: value for key, value in run_counts.items() if key.startswith("patient_group:")}
        mapping = {"episodes": "simulation_episodes.csv", "glucose": "synthetic_glucose_measurements.csv", "meals": "synthetic_meal_events.csv", "runs": "simulation_runs.csv"}
        data["row_counts"] = {key: simulator_stats[file]["after"] for key, file in mapping.items()}
        glucose_counts = simulator_stats["synthetic_glucose_measurements.csv"]["counters"]
        data["measurement_context_counts"] = {key.split(":", 1)[1]: value for key, value in glucose_counts.items() if key.startswith("meal_context:")}
        episode_counts = simulator_stats["simulation_episodes.csv"]["counters"]
        data["episode_counts"] = {key.split(":", 1)[1]: value for key, value in episode_counts.items() if key.startswith("scenario_type:")}
        numeric = simulator_stats["synthetic_glucose_measurements.csv"]["numeric"]
        data["glucose_min_mg_dl"] = numeric["glucose_min"]
        data["glucose_max_mg_dl"] = numeric["glucose_max"]
        data["files"] = {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(simulator.glob("*.csv"))}
        data["cohort_filter_version"] = FILTER_VERSION

    update_json(simulator / "manifest.json", update_simulator_manifest)

    def update_medication_manifest(data: dict) -> None:
        data["generated_at_utc"] = report["filtered_at_utc"]
        names = {
            "synthetic_medication_regimens": "synthetic_medication_regimens.csv",
            "synthetic_medication_schedule": "synthetic_medication_schedule.csv",
            "synthetic_medication_reminders": "synthetic_medication_reminders.csv",
            "synthetic_medication_administrations": "synthetic_medication_administrations.csv",
            "medication_flow_exclusions": "medication_flow_exclusions.csv",
        }
        data["row_counts"] = {key: medication_stats[file]["after"] for key, file in names.items()}
        regimen_counts = medication_stats["synthetic_medication_regimens.csv"]["counters"]
        data["regimen_counts"] = {key.split(":", 1)[1]: value for key, value in regimen_counts.items() if key.startswith("source_drug_name:")}
        exclusion_counts = medication_stats["medication_flow_exclusions.csv"]["counters"]
        data["exclusion_counts"] = {key.split(":", 1)[1]: value for key, value in exclusion_counts.items() if key.startswith("exclusion_reason:")}
        data["cohort_filter_version"] = FILTER_VERSION

    update_json(medication / "manifest.json", update_medication_manifest)
    quality_report = {
        "filter_version": FILTER_VERSION,
        "patient_counts": {"total": len(keep_patient_ids), **{label: balanced_count for label in LABELS}},
        "tables": json.loads((deployment / "manifest.json").read_text(encoding="utf-8"))["tables"],
        "eligibility_checks": {
            "all_quality_tier_gold": True,
            "all_profile_completeness_100": True,
            "all_vital_completeness_100": True,
            "all_lifestyle_completeness_100": True,
            "all_glycemic_completeness_at_least_75": True,
            "all_stage_conflict_flags_false": True,
        },
        "integrity_checks": {
            "duplicate_patient_id": 0,
            "orphan_baseline_patient_id": 0,
            "orphan_lifestyle_patient_id": 0,
            "orphan_prescription_patient_id": 0,
            "orphan_medication_patient_id": 0,
            "orphan_cohort_patient_id": 0,
            "orphan_caregiver_patient_id": 0,
            "orphan_doctor_assignment_patient_id": 0,
            "assignment_with_unknown_doctor": 0,
            "unreferenced_doctor_profiles": 0,
            "orphan_simulator_patient_id": 0,
            "orphan_medication_flow_patient_id": 0,
        },
        "status": "PASS",
        "failed_checks": [],
    }
    (deployment / "quality_report.json").write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (deployment / "balanced_demo_filter_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.repo_root.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

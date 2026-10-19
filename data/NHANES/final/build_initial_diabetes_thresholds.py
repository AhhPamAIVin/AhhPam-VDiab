#!/usr/bin/env python3
"""Build synthetic personalized CGM threshold data for TYPE_2 demo patients.

The cut points and risk-stratified time goals follow ADA Standards of Care
2026, section 6 (doi:10.2337/dc26-S006). Only target/normal and danger
boundaries are generated; a watch/attention state is intentionally excluded.
These are demo records, not clinical orders. Prediabetes is excluded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SOURCE_DOI = "10.2337/dc26-S006"
TREATMENT_SOURCE_DOI = "10.2337/dc26-S009"
SUPPORTING_DOI = "10.2337/dci19-0028"
PIPELINE_VERSION = "personalized_type2_cgm_thresholds_v2.1"
CREATED_AT = "2026-08-19T00:00:00+07:00"
EFFECTIVE_FROM = "2026-08-19"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def file_metadata(path: Path, row_count: int) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "rows": row_count,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def classify_risk(
    profile: dict[str, str], clinical: dict[str, str], medication_names: list[str]
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if profile.get("age_group_clean") == "65+":
        reasons.append("AGE_65_PLUS")

    cardiovascular_fields = {
        "congestive_heart_failure_flag": "CONGESTIVE_HEART_FAILURE_REPORTED",
        "coronary_heart_disease_flag": "CORONARY_HEART_DISEASE_REPORTED",
        "angina_flag": "ANGINA_REPORTED",
        "heart_attack_flag": "HEART_ATTACK_REPORTED",
        "stroke_flag": "STROKE_REPORTED",
    }
    for field, reason in cardiovascular_fields.items():
        if is_true(clinical.get(field)):
            reasons.append(reason)

    if is_true(profile.get("caregiver_required_flag")):
        reasons.append("ASSISTED_CARE_REQUIRED")

    # ADA section 9 identifies these drugs as hypoglycemia-risk modifiers, but
    # does not say that medication exposure alone qualifies someone for the
    # relaxed older/complex numeric CGM goals from section 6.
    risk_group = "OLDER_OR_HIGH_RISK" if reasons else "STANDARD_TYPE_2"

    normalized_medications = " | ".join(medication_names).upper()
    medication_risk_markers = {
        "INSULIN": "INSULIN_REPORTED",
        "GLIPIZIDE": "SULFONYLUREA_REPORTED",
        "GLIMEPIRIDE": "SULFONYLUREA_REPORTED",
        "GLYBURIDE": "SULFONYLUREA_REPORTED",
        "REPAGLINIDE": "MEGLITINIDE_REPORTED",
        "NATEGLINIDE": "MEGLITINIDE_REPORTED",
    }
    for marker, reason in medication_risk_markers.items():
        if marker in normalized_medications and reason not in reasons:
            reasons.append(reason)

    return risk_group, reasons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--clinical", type=Path, required=True)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--medications", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    profiles = read_csv(args.profiles)
    clinical_by_patient = {row["patient_id"]: row for row in read_csv(args.clinical)}
    doctor_by_patient = {
        row["patient_id"]: row["doctor_id"]
        for row in read_csv(args.assignments)
        if row.get("assignment_role") == "PRIMARY_DIABETES_TREATING_PHYSICIAN"
    }
    medications_by_patient: dict[str, list[str]] = {}
    for row in read_csv(args.medications):
        if row.get("prescription_use_code") == "1.0" and row.get("drug_name"):
            medications_by_patient.setdefault(row["patient_id"], []).append(row["drug_name"])

    type2 = [row for row in profiles if row.get("project_diabetes_label") == "TYPE_2"]
    care_plans: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    monitoring_profiles: list[dict[str, object]] = []
    risk_counts: Counter[str] = Counter()
    risk_reason_counts: Counter[str] = Counter()

    for profile in sorted(type2, key=lambda row: int(row["patient_id"])):
        patient_id = profile["patient_id"]
        if patient_id not in clinical_by_patient:
            raise ValueError(f"Missing clinical baseline for patient {patient_id}")
        if patient_id not in doctor_by_patient:
            raise ValueError(f"Missing primary doctor assignment for patient {patient_id}")

        clinical = clinical_by_patient[patient_id]
        doctor_id = doctor_by_patient[patient_id]
        medication_names = medications_by_patient.get(patient_id, [])
        risk_group, risk_reasons = classify_risk(profile, clinical, medication_names)
        medication_hypoglycemia_reasons = [
            reason
            for reason in risk_reasons
            if reason in {
                "INSULIN_REPORTED",
                "SULFONYLUREA_REPORTED",
                "MEGLITINIDE_REPORTED",
            }
        ]
        risk_counts[risk_group] += 1
        risk_reason_counts.update(risk_reasons)
        care_plan_id = f"CP-CGM-{patient_id}-V1"

        care_plans.append({
            "care_plan_id": care_plan_id,
            "patient_id": patient_id,
            "created_at": CREATED_AT,
            "effective_from": EFFECTIVE_FROM,
            "effective_to": "",
            "status": "ACTIVE",
            "approved_by_doctor_id": doctor_id,
            "approval_timestamp": CREATED_AT,
            "notes": (
                f"SYNTHETIC_DEMO clinician profile; {risk_group}; reasons="
                f"{'|'.join(risk_reasons) or 'NONE_RECORDED'}; source DOI {SOURCE_DOI}; "
                f"treatment DOI {TREATMENT_SOURCE_DOI}; supporting DOI {SUPPORTING_DOI}; "
                "normal/danger states only; "
                "not valid for real clinical use without clinician review."
            ),
            "is_synthetic": "True",
        })

        threshold_specs = [
            ("CGM_TARGET_RANGE", "BETWEEN", "70|180", "ADA CGM target range"),
            ("CGM_DANGER_LOW", "<", "54", "ADA level 2 hypoglycemia boundary"),
            ("CGM_DANGER_HIGH", ">", "250", "ADA level 2 hyperglycemia boundary"),
        ]
        for metric_code, operator, value, label in threshold_specs:
            targets.append({
                "patient_target_id": f"PT-{patient_id}-{metric_code}-V1",
                "care_plan_id": care_plan_id,
                "patient_id": patient_id,
                "metric_code": metric_code,
                "operator": operator,
                "target_value": value,
                "unit": "mg/dL",
                "source_template_id": "",
                "approved_by_doctor_id": doctor_id,
                "status": "ACTIVE",
                "is_synthetic": "True",
                "clinical_note": (
                    f"{label}; personalized profile={risk_group}; "
                    f"reasons={'|'.join(risk_reasons) or 'NONE_RECORDED'}; "
                    "synthetic clinician review required."
                ),
                "source_doi": SOURCE_DOI,
                "treatment_source_doi": TREATMENT_SOURCE_DOI,
                "pipeline_version": PIPELINE_VERSION,
            })

        if risk_group == "OLDER_OR_HIGH_RISK":
            tir_min, tbr70_max, tbr54_max, tar180_max, tar250_max = 50, 1, 1, 50, 10
        else:
            tir_min, tbr70_max, tbr54_max, tar180_max, tar250_max = 70, 4, 1, 25, 5

        monitoring_profiles.append({
            "monitoring_profile_id": f"CGM-MON-{patient_id}-V1",
            "care_plan_id": care_plan_id,
            "patient_id": patient_id,
            "risk_group": risk_group,
            "risk_reasons": "|".join(risk_reasons),
            "medication_hypoglycemia_risk": (
                "HIGH_REVIEW_PRIORITY" if medication_hypoglycemia_reasons else "NO_HIGH_RISK_DRUG_RECORDED"
            ),
            "medication_hypoglycemia_reasons": "|".join(medication_hypoglycemia_reasons),
            "normal_low_mg_dl": 70,
            "normal_high_mg_dl": 180,
            "danger_low_operator": "<",
            "danger_low_mg_dl": 54,
            "danger_high_operator": ">",
            "danger_high_mg_dl": 250,
            "very_high_clinical_evaluation_mg_dl": 300,
            "tir_70_180_min_pct": tir_min,
            "tbr_below_70_max_pct": tbr70_max,
            "tbr_below_54_max_pct": tbr54_max,
            "tar_above_180_max_pct": tar180_max,
            "tar_above_250_max_pct": tar250_max,
            "assessment_window_days": 14,
            "minimum_cgm_active_pct": 70,
            "approved_by_doctor_id": doctor_id,
            "status": "ACTIVE",
            "is_synthetic": "True",
            "source_doi": SOURCE_DOI,
            "treatment_source_doi": TREATMENT_SOURCE_DOI,
            "pipeline_version": PIPELINE_VERSION,
        })

    if len(type2) != len(care_plans) or len(targets) != len(type2) * 3:
        raise AssertionError("Unexpected output row count")
    if any(row.get("project_diabetes_label") != "TYPE_2" for row in type2):
        raise AssertionError("Prediabetes record leaked into TYPE_2 output")

    write_csv(
        args.output_dir / "patient_care_plans.csv",
        care_plans,
        list(care_plans[0]),
    )
    write_csv(
        args.output_dir / "patient_clinical_targets.csv",
        targets,
        list(targets[0]),
    )
    write_csv(
        args.output_dir / "patient_cgm_monitoring_profiles.csv",
        monitoring_profiles,
        list(monitoring_profiles[0]),
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "source_doi": SOURCE_DOI,
        "treatment_source_doi": TREATMENT_SOURCE_DOI,
        "scope": "Synthetic personalized normal/danger initialization for TYPE_2 only",
        "type2_patient_count": len(type2),
        "prediabetes_patient_count_in_output": 0,
        "care_plan_count": len(care_plans),
        "clinical_target_count": len(targets),
        "monitoring_profile_count": len(monitoring_profiles),
        "risk_group_counts": dict(sorted(risk_counts.items())),
        "risk_reason_counts": dict(sorted(risk_reason_counts.items())),
        "risk_classification_inputs": [
            "age_group_clean == 65+",
            "reported cardiovascular disease flags",
            "caregiver_required_flag",
        ],
        "medication_review_inputs": [
            "reported insulin",
            "reported sulfonylurea",
            "reported meglitinide",
        ],
        "generated_states": ["TARGET_NORMAL", "DANGER_LOW", "DANGER_HIGH"],
        "excluded_state": "WATCH_ATTENTION",
        "personalization_method": (
            "Per-patient care plan and risk-stratified ADA CGM time goals; glucose cut points "
            "remain standardized because the guideline does not support unique cut points."
        ),
        "not_used_as_risk_inputs": [
            "creatinine without a recorded renal diagnosis or validated eGFR",
        ],
        "medication_data_caveat": (
            "NHANES medication records are self-reported baseline prescriptions; dose, schedule, "
            "current active status, and hypoglycemia history are unavailable. Medication exposure "
            "therefore increases review sensitivity but does not change ADA glucose cut points."
        ),
        "safety_note": (
            "All approvals and identities are synthetic demo records. Values are ADA-derived "
            "reference targets and must not be used for real patient care without clinician review."
        ),
    }
    (args.output_dir / "patient_cgm_threshold_build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest_path = args.output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        manifest.setdefault("files", {}).update({
            "patient_care_plans.csv": file_metadata(
                args.output_dir / "patient_care_plans.csv", len(care_plans)
            ),
            "patient_clinical_targets.csv": file_metadata(
                args.output_dir / "patient_clinical_targets.csv", len(targets)
            ),
            "patient_cgm_monitoring_profiles.csv": file_metadata(
                args.output_dir / "patient_cgm_monitoring_profiles.csv", len(monitoring_profiles)
            ),
            "patient_cgm_threshold_build_report.json": file_metadata(
                args.output_dir / "patient_cgm_threshold_build_report.json", 1
            ),
        })
        manifest["patient_threshold_extension"] = {
            "pipeline_version": PIPELINE_VERSION,
            "source_doi": SOURCE_DOI,
            "treatment_source_doi": TREATMENT_SOURCE_DOI,
            "scope": "TYPE_2 synthetic personalized normal/danger demo patients only",
            "generated_at_utc": report["generated_at_utc"],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build project-wide, auditable metadata without modifying source healthcare data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


METADATA_VERSION = "diabetes_project_metadata_v1.0"
NULLS = {"", "NA", "N/A", "NULL", "NONE", "NAN"}

DATASETS = {
    "NHANES_PROCESSED": {
        "name": "NHANES processed intermediates", "domain": "PATIENT_BASELINE",
        "source_type": "OBSERVED_AND_DERIVED", "relative_root": "data/NHANES/processed",
        "intended_use": "Auditable intermediate outputs for cohort cleaning and deployment build",
        "prohibited_use": "Direct clinical diagnosis or patient care",
        "generator": "NHANES cleaning pipeline", "seed": "", "storage": "REGENERATE_FROM_PIPELINE",
    },
    "NHANES_DEPLOYMENT": {
        "name": "NHANES diabetes deployment baseline", "domain": "PATIENT_BASELINE",
        "source_type": "OBSERVED_DERIVED_WITH_SYNTHETIC_IDENTITIES", "relative_root": "data/NHANES/deployment_dataset",
        "intended_use": "Application demo baseline for PREDIABETES and algorithm-labelled TYPE_2",
        "prohibited_use": "Confirmed diagnosis, treatment, or re-identification",
        "generator": "build_project_deployment_dataset.py", "seed": "20260805", "storage": "TRACK_DIRECTLY",
    },
    "CGMACROS": {
        "name": "CGMacros source and normalized cohort", "domain": "RESEARCH_SOURCE",
        "source_type": "OBSERVED_RESEARCH_SOURCE", "relative_root": "data/CGMacros",
        "intended_use": "Source-pattern learning and simulator provenance",
        "prohibited_use": "Joining source_subject_id to NHANES identity or claiming diabetes type",
        "generator": "clean_cgmacros.py", "seed": "", "storage": "TRACK_DIRECTLY_LICENSE_REVIEW",
    },
    "SIMULATOR_60D": {
        "name": "Synthetic 60-day diabetes simulator", "domain": "SYNTHETIC_MONITORING",
        "source_type": "FULLY_SYNTHETIC", "relative_root": "data/simulator_60d",
        "intended_use": "Demo meals, BGM-style glucose, and test episodes",
        "prohibited_use": "Clinical forecast, CGM claim, or prevalence estimation",
        "generator": "build_60_day_simulator.py", "seed": "20260810", "storage": "REGENERATE_FROM_PIPELINE",
    },
    "MEDICATION_FLOW_60D": {
        "name": "Synthetic medication happy-case flow", "domain": "SYNTHETIC_MEDICATION_WORKFLOW",
        "source_type": "FULLY_SYNTHETIC", "relative_root": "data/NHANES/medication_flow_60d",
        "intended_use": "Reminder, acknowledgement, and adherence workflow tests",
        "prohibited_use": "Prescription, dose recommendation, or real adherence evidence",
        "generator": "build_happy_case_medication_flow.py", "seed": "20260810", "storage": "REGENERATE_FROM_PIPELINE",
    },
    "KNOWLEDGE_BASE": {
        "name": "Diabetes knowledge base source documents", "domain": "REFERENCE_KNOWLEDGE",
        "source_type": "REFERENCE_ONLY", "relative_root": "data/knowledge_base",
        "intended_use": "Human review and source-grounded knowledge retrieval",
        "prohibited_use": "Inferring patient facts or issuing treatment automatically",
        "generator": "Manual/source ingestion", "seed": "", "storage": "TRACK_DIRECTLY_LICENSE_REVIEW",
    },
    "KNOWLEDGE_STRUCTURED": {
        "name": "Structured diabetes knowledge", "domain": "REFERENCE_KNOWLEDGE",
        "source_type": "REFERENCE_ONLY", "relative_root": "data/knowledge_structured",
        "intended_use": "RAG, drug reference, targets, screening protocols, and education catalog",
        "prohibited_use": "Patient diagnosis, prescription, or unapproved recommendation execution",
        "generator": "build_knowledge_base_dataset.py", "seed": "", "storage": "TRACK_DIRECTLY",
    },
}

TABLE_MAP = {
    "patient_profiles.csv": ("patients", "patient_id", "One row per deployment patient"),
    "patient_baseline_clinical.csv": ("patient_baseline_clinical", "patient_id", "One baseline clinical row per patient"),
    "patient_lifestyle_baseline.csv": ("patient_lifestyle_baseline", "patient_id", "One baseline lifestyle row per patient"),
    "patient_prescription_status.csv": ("patient_prescription_status", "patient_id", "One prescription-use status row per patient"),
    "patient_medications.csv": ("patient_medications", "medication_record_id", "One observed medication record"),
    "project_diabetes_cohort.csv": ("project_diabetes_cohort", "patient_id", "One algorithmic cohort classification row per patient"),
    "caregiver_profiles.csv": ("caregivers", "caregiver_id", "One synthetic caregiver profile per linked patient"),
    "doctor_profiles.csv": ("doctors", "doctor_id", "One synthetic doctor profile"),
    "patient_doctor_assignments.csv": ("patient_doctor_assignments", "assignment_id", "One primary demo doctor assignment per patient"),
    "field_lineage.csv": ("field_lineage", "target_table|target_field", "One field-level lineage rule"),
    "cgmacros_subject_profiles.csv": ("cgmacros_source_subjects", "source_subject_id", "One retained CGMacros source subject"),
    "meal_events.csv": ("cgmacros_meal_events", "meal_id", "One observed normalized CGMacros meal event"),
    "meal_images.csv": ("cgmacros_meal_images", "meal_image_id", "One observed CGMacros source image"),
    "simulation_runs.csv": ("simulation_runs", "simulation_run_id", "One synthetic run per patient"),
    "synthetic_meal_events.csv": ("synthetic_patient_meals", "meal_id", "One synthetic patient meal"),
    "synthetic_glucose_measurements.csv": ("synthetic_glucose_measurements", "measurement_id", "One synthetic BGM-style measurement"),
    "simulation_episodes.csv": ("simulation_episodes", "episode_id", "One forced test episode"),
    "synthetic_medication_regimens.csv": ("synthetic_medication_regimens", "regimen_id", "One synthetic happy-case medication regimen"),
    "synthetic_medication_schedule.csv": ("synthetic_medication_schedule", "schedule_id", "One planned synthetic dose"),
    "synthetic_medication_reminders.csv": ("synthetic_medication_reminders", "reminder_id", "One synthetic reminder"),
    "synthetic_medication_administrations.csv": ("synthetic_medication_administrations", "administration_id", "One synthetic administration record"),
    "medication_flow_exclusions.csv": ("", "source_medication_record_id", "One medication record excluded from happy-case generation"),
    "knowledge_documents.csv": ("knowledge_documents", "document_id", "One versioned knowledge document"),
    "knowledge_chunks.csv": ("knowledge_chunks", "chunk_id", "One line-addressable knowledge chunk"),
    "drug_catalog.csv": ("drug_reference_catalog", "drug_reference_id", "One drug reference entry"),
    "drug_reference_sections.csv": ("drug_reference_sections", "drug_section_id", "One drug monograph section"),
    "clinical_target_templates.csv": ("clinical_target_templates", "target_template_id", "One population-level reference target"),
    "screening_protocols.csv": ("screening_protocols", "screening_protocol_id", "One reference screening protocol"),
    "education_topics.csv": ("education_topics", "education_topic_id", "One patient-education topic"),
    "education_topic_documents.csv": ("education_topic_documents", "education_topic_id|document_id", "One topic-document relation"),
    "recommendation_rules.csv": ("recommendation_rules", "recommendation_rule_id", "One disabled/versioned review rule"),
    "patient_recommendation_events.csv": ("patient_recommendation_events", "recommendation_event_id", "One review-prompt event; currently header only"),
}

FK_RULES = [
    ("NHANES_DEPLOYMENT.patient_baseline_clinical", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.patient_lifestyle_baseline", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.patient_prescription_status", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.patient_medications", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.project_diabetes_cohort", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.caregiver_profiles", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.patient_doctor_assignments", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("NHANES_DEPLOYMENT.patient_doctor_assignments", "doctor_id", "NHANES_DEPLOYMENT.doctor_profiles", "doctor_id"),
    ("SIMULATOR_60D.simulation_runs", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("SIMULATOR_60D.synthetic_meal_events", "simulation_run_id", "SIMULATOR_60D.simulation_runs", "simulation_run_id"),
    ("SIMULATOR_60D.synthetic_meal_events", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("SIMULATOR_60D.synthetic_glucose_measurements", "simulation_run_id", "SIMULATOR_60D.simulation_runs", "simulation_run_id"),
    ("SIMULATOR_60D.synthetic_glucose_measurements", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("SIMULATOR_60D.simulation_episodes", "trigger_measurement_id", "SIMULATOR_60D.synthetic_glucose_measurements", "measurement_id"),
    ("MEDICATION_FLOW_60D.synthetic_medication_regimens", "patient_id", "NHANES_DEPLOYMENT.patient_profiles", "patient_id"),
    ("MEDICATION_FLOW_60D.synthetic_medication_regimens", "source_medication_record_id", "NHANES_DEPLOYMENT.patient_medications", "medication_record_id"),
    ("MEDICATION_FLOW_60D.synthetic_medication_schedule", "regimen_id", "MEDICATION_FLOW_60D.synthetic_medication_regimens", "regimen_id"),
    ("MEDICATION_FLOW_60D.synthetic_medication_reminders", "schedule_id", "MEDICATION_FLOW_60D.synthetic_medication_schedule", "schedule_id"),
    ("MEDICATION_FLOW_60D.synthetic_medication_administrations", "schedule_id", "MEDICATION_FLOW_60D.synthetic_medication_schedule", "schedule_id"),
    ("KNOWLEDGE_STRUCTURED.knowledge_chunks", "document_id", "KNOWLEDGE_STRUCTURED.knowledge_documents", "document_id"),
    ("KNOWLEDGE_STRUCTURED.drug_catalog", "document_id", "KNOWLEDGE_STRUCTURED.knowledge_documents", "document_id"),
    ("KNOWLEDGE_STRUCTURED.drug_reference_sections", "drug_reference_id", "KNOWLEDGE_STRUCTURED.drug_catalog", "drug_reference_id"),
]

GENERATION_RULES = [
    ("GEN-SIM-GROUP", "simulation_runs", "Patient group routing", "PREDIABETES|TYPE_2", "project_diabetes_label,taking_insulin_flag", "Route into PREDIABETES, TYPE_2_NON_INSULIN, TYPE_2_INSULIN", "build_60_day_simulator.py"),
    ("GEN-SIM-DONOR", "simulation_runs", "CGMacros donor selection", "ALL_SIMULATED", "age_years,bmi,hba1c_pct", "Match source group plus age/BMI/HbA1c distance; record source_subject_id", "build_60_day_simulator.py"),
    ("GEN-SIM-FASTING", "synthetic_glucose_measurements", "Fasting anchor", "ALL_SIMULATED", "fasting_glucose_mg_dl,hba1c_pct", "Observed fasting, else CGMacros A1C regression, else donor fallback", "build_60_day_simulator.py"),
    ("GEN-PRE-SCHEDULE", "synthetic_glucose_measurements", "Prediabetes schedule", "PREDIABETES", "patient_group,day", "Sparse fasting and rotating pre/post-meal measurements", "build_60_day_simulator.py"),
    ("GEN-T2-NI-SCHEDULE", "synthetic_glucose_measurements", "Type 2 non-insulin schedule", "TYPE_2_NON_INSULIN", "patient_group,day", "Fasting, daily pre/post pair, selected bedtime", "build_60_day_simulator.py"),
    ("GEN-T2-I-SCHEDULE", "synthetic_glucose_measurements", "Type 2 insulin schedule", "TYPE_2_INSULIN", "patient_group,day", "Fasting, pre-lunch, pre-dinner, bedtime, rotating postmeal", "build_60_day_simulator.py"),
    ("GEN-GLUCOSE-BOUND", "synthetic_glucose_measurements", "Demo sensor bound", "ALL_SIMULATED", "raw_generated_glucose_mg_dl", "Bound display value to 40-600 mg/dL and retain raw value", "build_60_day_simulator.py"),
    ("GEN-MED-ELIGIBLE", "synthetic_medication_regimens", "Medication happy-case eligibility", "TYPE_2", "source_drug_name,project_diabetes_label", "Exact single ingredient; exclude insulin, combinations, ambiguous semaglutide", "build_happy_case_medication_flow.py"),
    ("GEN-MED-DOSE", "synthetic_medication_regimens", "Synthetic demo starting dose", "ELIGIBLE_TYPE_2", "drug_reference_id", "Use local-monograph happy-case default; clinical_use_allowed=false", "build_happy_case_medication_flow.py"),
    ("GEN-MED-REMINDER", "synthetic_medication_reminders", "Happy-case reminder", "ELIGIBLE_TYPE_2", "scheduled_at,caregiver_id", "Deliver 15 minutes before dose; acknowledge after 2 minutes", "build_happy_case_medication_flow.py"),
    ("GEN-MED-ADMIN", "synthetic_medication_administrations", "Happy-case administration", "ELIGIBLE_TYPE_2", "scheduled_at", "Taken 0-5 minutes after schedule; adherence=true", "build_happy_case_medication_flow.py"),
    ("GEN-KB-CHUNK", "knowledge_chunks", "Knowledge chunking", "ALL_DOCUMENTS", "markdown headings,paragraphs", "Heading-aware chunks with line locator and SHA-256", "build_knowledge_base_dataset.py"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def table_name(path: Path) -> str:
    return path.stem.removesuffix("_template")


def infer_type(values: list[str]) -> str:
    if not values: return "UNKNOWN_EMPTY"
    upper = {v.upper() for v in values}
    if upper <= {"TRUE", "FALSE", "0", "1"}: return "BOOLEAN"
    try:
        for value in values: int(value)
        return "INTEGER"
    except ValueError: pass
    try:
        for value in values: float(value)
        return "NUMERIC"
    except ValueError: pass
    for fmt, result in [("%Y-%m-%d %H:%M:%S", "TIMESTAMP"), ("%Y-%m-%dT%H:%M:%S%z", "TIMESTAMP_TZ"), ("%Y-%m-%d", "DATE")]:
        try:
            for value in values: datetime.strptime(value, fmt)
            return result
        except ValueError: pass
    return "TEXT"


def unit_for(field: str) -> str:
    suffixes = {
        "_mg_dl":"mg/dL", "_mg_g":"mg/g", "_kg":"kg", "_cm":"cm", "_pct":"%",
        "_minutes":"minutes", "_minutes_day":"minutes/day", "_days_week":"days/week",
        "_hours_weekday":"hours", "_hours_weekend":"hours", "_kcal":"kcal", "_g":"g",
        "_u_l":"U/L", "_years":"years", "_mg":"mg",
    }
    for suffix, unit in suffixes.items():
        if field.endswith(suffix): return unit
    if field in {"glucose_mg_dl", "raw_generated_glucose_mg_dl", "fasting_anchor_mg_dl"}: return "mg/dL"
    return ""


def sensitivity(field: str, dataset_id: str) -> str:
    lower = field.lower()
    if "display_name" in lower or "identity" in lower: return "SYNTHETIC_IDENTITY"
    if lower in {"patient_id", "source_patient_id", "caregiver_id", "doctor_id"}: return "PSEUDONYMOUS_IDENTIFIER"
    if dataset_id.startswith("KNOWLEDGE"): return "PUBLIC_OR_LICENSED_REFERENCE"
    clinical = ["glucose", "hba1c", "bmi", "weight", "height", "waist", "blood", "sbp", "dbp", "pulse", "creatinine", "cholesterol", "drug", "medication", "insulin", "diagnos", "disease", "condition", "sleep", "smok", "alcohol"]
    if any(token in lower for token in clinical): return "HEALTH_DATA"
    return "INTERNAL_METADATA_OR_NON_SENSITIVE"


def origin_class(dataset_id: str, field: str) -> str:
    if dataset_id in {"SIMULATOR_60D", "MEDICATION_FLOW_60D"}: return "SYNTHETIC"
    if dataset_id.startswith("KNOWLEDGE"): return "REFERENCE_ONLY"
    if dataset_id == "CGMACROS": return "OBSERVED_OR_PIPELINE_METADATA"
    if "display_name" in field or "synthetic_identity" in field: return "SYNTHETIC_IDENTITY"
    return "OBSERVED_OR_DERIVED"


def description_for(field: str) -> str:
    important = {
        "patient_id":"Project patient identifier; not a real-world clinical MRN.",
        "project_diabetes_label":"Project label limited to PREDIABETES or algorithm-classified TYPE_2; not confirmed diagnosis.",
        "classification_confirmed":"Whether classification is clinically confirmed; deployment data is false.",
        "source_subject_id":"Independent CGMacros research subject identifier; never a NHANES patient identity.",
        "is_synthetic":"True when the row is generated for application testing.",
        "clinical_use_allowed":"Whether data may be used clinically; synthetic medication rows must be false.",
        "glucose_mg_dl":"Application-facing glucose value in mg/dL.",
        "raw_generated_glucose_mg_dl":"Unbounded synthetic glucose before demo sensor-range adjustment.",
        "dose_value":"Synthetic happy-case dose value when used in medication-flow tables; not a prescription.",
        "display_name":"Synthetic Vietnamese demo display name.",
    }
    return important.get(field, field.replace("_", " ").capitalize() + ".")


def scan_csv(path: Path, dataset_id: str, table_id: str, pk_spec: str) -> tuple[dict, list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle); fields = reader.fieldnames or []
        rows = 0; nulls = Counter(); samples = {f: [] for f in fields}; keys = set(); duplicate_keys = 0
        numeric_min, numeric_max = {}, {}; time_min, time_max = {}, {}
        key_fields = pk_spec.split("|") if pk_spec else []
        for row in reader:
            rows += 1
            if key_fields:
                key = tuple(row.get(f, "") for f in key_fields)
                if key in keys: duplicate_keys += 1
                else: keys.add(key)
            for field in fields:
                value = (row.get(field) or "").strip()
                if value.upper() in NULLS: nulls[field] += 1; continue
                if len(samples[field]) < 500: samples[field].append(value)
                try:
                    number = float(value)
                    numeric_min[field] = min(numeric_min.get(field, number), number)
                    numeric_max[field] = max(numeric_max.get(field, number), number)
                except ValueError: pass
                if any(token in field.lower() for token in ("date", "_at", "timestamp")):
                    candidate = value[:19].replace("T", " ")
                    if re.match(r"^\d{4}-\d{2}-\d{2}", candidate):
                        time_min[field] = min(time_min.get(field, candidate), candidate)
                        time_max[field] = max(time_max.get(field, candidate), candidate)
    total_cells = rows * len(fields)
    table_stats = {
        "row_count": rows, "column_count": len(fields), "duplicate_primary_key_count": duplicate_keys,
        "null_cell_count": sum(nulls.values()),
        "completeness_pct": round(100 * (1 - sum(nulls.values()) / total_cells), 2) if total_cells else 100.0,
        "time_coverage_start": min(time_min.values()) if time_min else "",
        "time_coverage_end": max(time_max.values()) if time_max else "",
    }
    field_rows = []
    for field_ordinal, field in enumerate(fields, start=1):
        inferred = infer_type(samples[field])
        nullable = nulls[field] > 0
        field_rows.append({
            "table_id": table_id, "field_ordinal": field_ordinal,
            "field_instance_id": f"{table_id}.{field_ordinal}",
            "field_name": field, "display_name": field.replace("_", " ").title(),
            "description": description_for(field), "inferred_data_type": inferred, "database_type": "",
            "nullable": nullable, "null_count": nulls[field], "null_rate_pct": round(100 * nulls[field] / rows, 2) if rows else 0,
            "unit": unit_for(field), "valid_values": "", "minimum_value": numeric_min.get(field, ""),
            "maximum_value": numeric_max.get(field, ""), "is_primary_key": field in key_fields,
            "is_foreign_key": False, "references_table": "", "references_field": "",
            "origin_class": origin_class(dataset_id, field), "source_variable": "", "transformation_rule": "",
            "clinical_interpretation": "REFERENCE_ONLY_NOT_MEDICAL_ADVICE" if dataset_id.startswith("KNOWLEDGE") else "",
            "sensitivity_class": sensitivity(field, dataset_id), "example_value": samples[field][0][:160] if samples[field] else "",
            "metadata_version": METADATA_VERSION,
        })
    return table_stats, field_rows


def manifest_for(root: Path) -> dict:
    path = root / "manifest.json"
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError: return {}
    return {}


def git_status(repo: Path) -> list[str]:
    try:
        result = subprocess.run(["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), "status", "--short"], capture_output=True, text=True, check=True)
        return result.stdout.splitlines()
    except Exception:
        return []


def build(args: argparse.Namespace) -> dict:
    repo = args.repo_root.resolve(); output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    dataset_rows, table_rows, field_rows, quality_rows, issues = [], [], [], [], []
    table_paths = {}; table_fields = defaultdict(set); table_pk = {}

    for dataset_id, cfg in DATASETS.items():
        root = repo / cfg["relative_root"]
        manifest = manifest_for(root)
        csv_files = sorted(root.rglob("*.csv")) if root.exists() else []
        availability = "AVAILABLE" if root.exists() else "MISSING_FROM_WORKTREE"
        dataset_rows.append({
            "dataset_id": dataset_id, "dataset_name": cfg["name"], "dataset_version": manifest.get("dataset_version") or manifest.get("generator_version") or manifest.get("pipeline_version") or "UNVERSIONED",
            "description": cfg["name"], "domain": cfg["domain"], "source_type": cfg["source_type"],
            "source_organization": "CDC/NCHS" if dataset_id.startswith("NHANES") else "",
            "source_period": manifest.get("source_cycle") or (f"{manifest.get('start_date','')}..{manifest.get('end_date','')}" if manifest else ""),
            "generated_at": manifest.get("generated_at_utc", ""), "availability_status": availability,
            "contains_synthetic_identity": dataset_id == "NHANES_DEPLOYMENT",
            "contains_synthetic_clinical_data": dataset_id in {"SIMULATOR_60D", "MEDICATION_FLOW_60D"},
            "table_count": len(csv_files), "data_owner": "PROJECT_OWNER", "intended_use": cfg["intended_use"],
            "prohibited_use": cfg["prohibited_use"], "license_status": "REVIEW_REQUIRED" if "LICENSE_REVIEW" in cfg["storage"] else "PROJECT_CONTROLLED",
            "pii_status": "SYNTHETIC_IDENTITIES_ONLY" if dataset_id == "NHANES_DEPLOYMENT" else "NO_KNOWN_DIRECT_PII",
            "generator": cfg["generator"], "random_seed": cfg["seed"], "schema_version": "diabetes_schema_current",
            "retention_policy": "PROJECT_DEFINED_NOT_YET_FORMALIZED", "default_storage_policy": cfg["storage"],
            "metadata_version": METADATA_VERSION,
        })
        for path in csv_files:
            rel = path.relative_to(repo).as_posix(); stem = table_name(path)
            suffix_key = path.name
            db_table, pk, granularity = TABLE_MAP.get(suffix_key, ("", "", "One row in source/intermediate table"))
            # Avoid collisions for repeated filenames across datasets.
            table_id = f"{dataset_id}.{stem}"
            if table_id in table_paths: table_id += "." + hashlib.sha1(rel.encode()).hexdigest()[:6]
            table_paths[table_id] = path; table_pk[table_id] = pk
            stats, fields = scan_csv(path, dataset_id, table_id, pk)
            field_rows.extend(fields); table_fields[table_id] = {f["field_name"] for f in fields}
            generated = dataset_id in {"SIMULATOR_60D", "MEDICATION_FLOW_60D", "KNOWLEDGE_STRUCTURED", "NHANES_PROCESSED"}
            size = path.stat().st_size
            storage = "REGENERATE_FROM_PIPELINE" if generated and dataset_id != "KNOWLEDGE_STRUCTURED" else ("GIT_LFS_OR_RELEASE_STORAGE" if size >= 50_000_000 else cfg["storage"])
            table_rows.append({
                "table_id": table_id, "dataset_id": dataset_id, "file_path": rel, "database_table": db_table,
                "description": granularity, "row_granularity": granularity, "row_count": stats["row_count"],
                "column_count": stats["column_count"], "primary_key": pk, "foreign_keys": "",
                "time_coverage_start": stats["time_coverage_start"], "time_coverage_end": stats["time_coverage_end"],
                "is_generated": generated, "is_reproducible": bool(cfg["generator"]), "generator_script": cfg["generator"],
                "generator_version": manifest.get("generator_version") or manifest.get("pipeline_version") or "",
                "source_manifest": (root / "manifest.json").relative_to(repo).as_posix() if (root / "manifest.json").exists() else "",
                "file_size_bytes": size, "sha256": sha256(path), "github_storage_strategy": storage,
                "availability_status": "AVAILABLE", "metadata_version": METADATA_VERSION,
            })
            quality_rows.append({
                "table_id": table_id, "row_count": stats["row_count"], "column_count": stats["column_count"],
                "duplicate_primary_key_count": stats["duplicate_primary_key_count"], "null_cell_count": stats["null_cell_count"],
                "completeness_pct": stats["completeness_pct"], "foreign_key_error_count": 0,
                "quality_status": "PASS" if stats["duplicate_primary_key_count"] == 0 else "FAIL",
                "checked_at_utc": datetime.now(timezone.utc).isoformat(), "metadata_version": METADATA_VERSION,
            })
            for f in fields:
                if f["null_rate_pct"] >= 70:
                    issues.append({
                        "issue_id": f"NULL-{hashlib.sha1((table_id+f['field_name']).encode()).hexdigest()[:12].upper()}",
                        "severity": "WARNING", "dataset_id": dataset_id, "table_id": table_id, "field_name": f["field_name"],
                        "issue_type": "HIGH_NULL_RATE", "observed_value": f["null_rate_pct"], "threshold": "70%",
                        "description": "Field is at least 70% null; retain only when source/provenance value is needed.",
                        "recommended_action": "Review applicability semantics before dropping; do not impute clinical facts.",
                        "status": "OPEN_FOR_REVIEW", "metadata_version": METADATA_VERSION,
                    })

    # Mark foreign-key metadata and validate using key sets.
    fks_by_table = defaultdict(list); key_cache = {}
    for child, child_field, parent, parent_field in FK_RULES:
        if child not in table_paths or parent not in table_paths: continue
        fks_by_table[child].append(f"{child_field}->{parent}.{parent_field}")
        for field in field_rows:
            if field["table_id"] == child and field["field_name"] == child_field:
                field.update({"is_foreign_key": True, "references_table": parent, "references_field": parent_field})
        cache_key = (parent, parent_field)
        if cache_key not in key_cache:
            with table_paths[parent].open("r", encoding="utf-8-sig", newline="") as handle:
                key_cache[cache_key] = {row.get(parent_field, "") for row in csv.DictReader(handle)}
        errors = 0
        with table_paths[child].open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = row.get(child_field, "")
                if value and value not in key_cache[cache_key]: errors += 1
        for q in quality_rows:
            if q["table_id"] == child: q["foreign_key_error_count"] += errors
        if errors:
            issues.append({"issue_id": f"FK-{hashlib.sha1((child+child_field).encode()).hexdigest()[:12].upper()}", "severity":"ERROR", "dataset_id":child.split('.')[0], "table_id":child, "field_name":child_field, "issue_type":"FOREIGN_KEY_ERROR", "observed_value":errors, "threshold":0, "description":"Foreign-key values do not resolve.", "recommended_action":"Fix pipeline or import order before deployment.", "status":"OPEN", "metadata_version":METADATA_VERSION})
    for row in table_rows: row["foreign_keys"] = ";".join(fks_by_table[row["table_id"]])
    for q in quality_rows:
        if q["foreign_key_error_count"] or q["duplicate_primary_key_count"]: q["quality_status"] = "FAIL"

    # Enrich deployment lineage from existing file.
    lineage_rows = []
    lineage_path = repo / "data/NHANES/deployment_dataset/field_lineage.csv"
    if lineage_path.exists():
        for i, row in enumerate(csv.DictReader(lineage_path.open("r", encoding="utf-8-sig", newline="")), 1):
            lineage_rows.append({
                "lineage_id": f"LINEAGE-DEP-{i:04d}", "source_dataset": row.get("source_dataset", "NHANES"),
                "source_table": "NHANES_SOURCE", "source_field": row.get("source_variable", ""),
                "target_dataset": "NHANES_DEPLOYMENT", "target_table": row.get("target_table", ""),
                "target_field": row.get("target_field", ""), "transformation_type": "OBSERVED_OR_DERIVED",
                "transformation_description": row.get("transformation", ""), "filter_condition": "", "join_condition": "patient_id",
                "algorithm_version": "deployment_v3.4", "script_path": "data/NHANES/final/build_project_deployment_dataset.py",
                "random_seed": "20260805 for synthetic identities", "clinical_assumption": "Algorithmic classification is not confirmed diagnosis",
                "information_loss": "Source codes may be normalized", "review_status": "DOCUMENTED", "metadata_version": METADATA_VERSION,
            })
    high_level = [
        ("CGMACROS", "cgmacros_subject_profiles,meal_events", "SIMULATOR_60D", "simulation_runs,synthetic_meal_events,synthetic_glucose_measurements", "Donor pattern bootstrap; provenance not identity"),
        ("NHANES_DEPLOYMENT", "patient_profiles,patient_baseline_clinical", "SIMULATOR_60D", "simulation_runs,synthetic_glucose_measurements", "Patient group and fasting anchor"),
        ("NHANES_DEPLOYMENT", "patient_medications", "MEDICATION_FLOW_60D", "synthetic_medication_regimens", "Exact ingredient match and happy-case exclusion policy"),
        ("KNOWLEDGE_BASE", "Markdown documents", "KNOWLEDGE_STRUCTURED", "knowledge_documents,knowledge_chunks,drug_catalog", "Source-addressable extraction and chunking"),
        ("KNOWLEDGE_STRUCTURED", "drug_catalog,drug_reference_sections", "MEDICATION_FLOW_60D", "synthetic_medication_regimens", "Synthetic demo starting-dose reference only"),
        ("SIMULATOR_60D", "synthetic_meal_events", "MEDICATION_FLOW_60D", "synthetic_medication_schedule", "Link meal-timed medication events to synthetic meals"),
    ]
    for i, (sd, st, td, tt, desc) in enumerate(high_level, 1):
        lineage_rows.append({"lineage_id":f"LINEAGE-HIGH-{i:03d}", "source_dataset":sd, "source_table":st, "source_field":"*", "target_dataset":td, "target_table":tt, "target_field":"*", "transformation_type":"PIPELINE", "transformation_description":desc, "filter_condition":"See generation_rules.csv", "join_condition":"Explicit IDs only", "algorithm_version":METADATA_VERSION, "script_path":"See generation_rules.csv", "random_seed":"See dataset_catalog.csv", "clinical_assumption":"No synthetic output is a clinical fact", "information_loss":"Synthetic transformation; source observations retained separately", "review_status":"DOCUMENTED", "metadata_version":METADATA_VERSION})

    # Source registry from Markdown headers.
    source_rows = []
    kb_root = repo / DATASETS["KNOWLEDGE_BASE"]["relative_root"]
    for path in sorted(kb_root.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        title = next((re.sub(r"^#\s+", "", x).strip() for x in lines if x.startswith("# ")), path.stem)
        url = next((x.split(":",1)[1].strip().split()[0] for x in lines[:12] if x.startswith("> Nguồn:")), "")
        retrieved = ""
        for line in lines[:12]:
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", line)
            if m: retrieved = m.group(1)
        lower = path.name.lower()
        authority = "NATIONAL_OFFICIAL" if lower.startswith("byt-") or lower.startswith("vncdc-") else "INTERNATIONAL_GUIDELINE" if lower.startswith(("ada-","who-","iwgdf-")) else "NATIONAL_DRUG_REFERENCE_SECONDARY_HOST" if lower.startswith("duoc-thu-") else "PROFESSIONAL_OR_PATIENT_EDUCATION"
        source_rows.append({"source_id":"SRC-"+hashlib.sha1(path.name.encode()).hexdigest()[:12].upper(), "source_title":title, "organization":"", "source_url":url, "source_file":path.relative_to(repo).as_posix(), "publication_date":"", "retrieved_date":retrieved, "document_version":"", "authority_level":authority, "language":"vi", "checksum_sha256":sha256(path), "effective_status":"REVIEW_CURRENTNESS_BEFORE_CLINICAL_USE", "superseded_by":"", "usage_scope":"REFERENCE_ONLY", "metadata_version":METADATA_VERSION})

    # Governance per table.
    governance_rows = []
    for row in table_rows:
        ds = row["dataset_id"]
        classification = "SYNTHETIC_CLINICAL_DEMO_ONLY" if ds in {"SIMULATOR_60D", "MEDICATION_FLOW_60D"} else "REFERENCE_ONLY" if ds.startswith("KNOWLEDGE") else "RESEARCH_SOURCE" if ds == "CGMACROS" else "OBSERVED_DERIVED_HEALTH_DATA"
        governance_rows.append({"table_id":row["table_id"], "data_classification":classification, "contains_direct_pii":False, "contains_synthetic_identity":ds=="NHANES_DEPLOYMENT" and row["database_table"] in {"patients","caregivers","doctors"}, "github_publish_allowed":"CONDITIONAL_REVIEW", "training_use_allowed":"NOT_ASSESSED", "demo_use_allowed":True, "clinical_decision_support_allowed":False, "clinician_approval_required":ds in {"KNOWLEDGE_STRUCTURED","MEDICATION_FLOW_60D"}, "retention_policy":"PROJECT_DEFINED_NOT_YET_FORMALIZED", "access_level":"PROJECT_TEAM", "license_review_required":ds in {"CGMACROS","KNOWLEDGE_BASE"}, "notes":DATASETS[ds]["prohibited_use"], "metadata_version":METADATA_VERSION})

    quality_rule_rows = [
        {"quality_rule_id":"QR-PK-UNIQUE", "scope":"ALL_TABLES_WITH_PK", "dimension":"UNIQUENESS", "rule":"Primary key must be unique", "severity":"ERROR", "expected":"0 duplicates", "action_on_failure":"BLOCK_RELEASE", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-FK-VALID", "scope":"DECLARED_FOREIGN_KEYS", "dimension":"INTEGRITY", "rule":"Foreign key must resolve", "severity":"ERROR", "expected":"0 unresolved", "action_on_failure":"BLOCK_RELEASE", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-NULL-70", "scope":"ALL_FIELDS", "dimension":"COMPLETENESS", "rule":"Flag fields at least 70% null", "severity":"WARNING", "expected":"Reviewed applicability", "action_on_failure":"REVIEW_NOT_AUTO_DROP", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-GLUCOSE-RANGE", "scope":"SIMULATOR_60D.synthetic_glucose_measurements", "dimension":"VALIDITY", "rule":"glucose_mg_dl between 40 and 600", "severity":"ERROR", "expected":"100%", "action_on_failure":"BLOCK_RELEASE", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-SYNTHETIC-FLAG", "scope":"SYNTHETIC_DATASETS", "dimension":"GOVERNANCE", "rule":"All generated rows explicitly synthetic", "severity":"ERROR", "expected":"100% true", "action_on_failure":"BLOCK_RELEASE", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-MED-NO-CLINICAL", "scope":"MEDICATION_FLOW_60D", "dimension":"SAFETY", "rule":"clinical_use_allowed must be false", "severity":"ERROR", "expected":"100% false", "action_on_failure":"BLOCK_RELEASE", "metadata_version":METADATA_VERSION},
        {"quality_rule_id":"QR-GITHUB-SIZE", "scope":"ALL_FILES", "dimension":"OPERABILITY", "rule":"Files >=50MB require LFS/release/regeneration decision", "severity":"WARNING", "expected":"Explicit storage strategy", "action_on_failure":"BLOCK_PUSH_UNTIL_REVIEW", "metadata_version":METADATA_VERSION},
    ]

    # Known cross-table/domain issues requiring human review.
    issues.extend([
        {"issue_id":"ISSUE-LABEL-NOT-CONFIRMED", "severity":"WARNING", "dataset_id":"NHANES_DEPLOYMENT", "table_id":"NHANES_DEPLOYMENT.patient_profiles", "field_name":"classification_confirmed", "issue_type":"CLINICAL_LIMITATION", "observed_value":"All project labels unconfirmed", "threshold":"Clinical confirmation required for real care", "description":"TYPE_2 and PREDIABETES are project/algorithm labels, not confirmed diagnoses.", "recommended_action":"Keep explicit UI disclaimer and collect clinician-confirmed diagnosis separately.", "status":"OPEN_ACCEPTED_LIMITATION", "metadata_version":METADATA_VERSION},
        {"issue_id":"ISSUE-GLUCOSE-SPARSE", "severity":"WARNING", "dataset_id":"SIMULATOR_60D", "table_id":"SIMULATOR_60D.synthetic_glucose_measurements", "field_name":"measured_at", "issue_type":"COVERAGE_LIMITATION", "observed_value":"BGM-style sparse measurements", "threshold":"Not CGM", "description":"Dataset spans 60 days but is not continuous CGM and many prediabetes patients lack readings on every day.", "recommended_action":"Label as BGM/SMBG; generate separate CGM stream only if product requires it.", "status":"OPEN_ACCEPTED_LIMITATION", "metadata_version":METADATA_VERSION},
        {"issue_id":"ISSUE-MED-HAPPY-ONLY", "severity":"INFO", "dataset_id":"MEDICATION_FLOW_60D", "table_id":"MEDICATION_FLOW_60D.synthetic_medication_administrations", "field_name":"adherence_flag", "issue_type":"SCENARIO_LIMITATION", "observed_value":"100% adherence", "threshold":"Happy case only", "description":"No missed, late, duplicate, or declined dose scenarios are present.", "recommended_action":"Add non-happy scenarios only in a separate explicit simulator version.", "status":"OPEN_ACCEPTED_LIMITATION", "metadata_version":METADATA_VERSION},
    ])
    for row in table_rows:
        if row["file_size_bytes"] >= 50_000_000:
            issues.append({"issue_id":"SIZE-"+hashlib.sha1(row["file_path"].encode()).hexdigest()[:12].upper(), "severity":"WARNING", "dataset_id":row["dataset_id"], "table_id":row["table_id"], "field_name":"", "issue_type":"LARGE_FILE_GITHUB", "observed_value":row["file_size_bytes"], "threshold":"50,000,000 bytes review threshold", "description":"Large data file should not be pushed directly without a storage decision.", "recommended_action":row["github_storage_strategy"], "status":"OPEN_FOR_REVIEW", "metadata_version":METADATA_VERSION})
    for status_line in git_status(repo):
        if status_line.startswith("AD ") and "knowledge_structured" in status_line:
            issues.append({"issue_id":"GIT-KB-AD-STATE", "severity":"ERROR", "dataset_id":"KNOWLEDGE_STRUCTURED", "table_id":"", "field_name":"", "issue_type":"GIT_INDEX_WORKTREE_MISMATCH", "observed_value":status_line, "threshold":"No unintended AD state", "description":"A knowledge_structured path is staged as added but missing at its staged NHANES location in the working tree.", "recommended_action":"Resolve staged legacy path before commit; keep canonical top-level data/knowledge_structured layout.", "status":"OPEN_BLOCKS_PUSH", "metadata_version":METADATA_VERSION}); break

    # Apply database SQL types where a direct table mapping exists.
    schema_path = repo / "data/NHANES/database/schema.sql"
    if schema_path.exists():
        schema = schema_path.read_text(encoding="utf-8")
        sql_types = {}
        for match in re.finditer(r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\);", schema, re.S | re.I):
            tname, body = match.group(1), match.group(2)
            for line in body.splitlines():
                m = re.match(r"\s*(\w+)\s+([A-Z][A-Z ]*(?:\([^)]*\))?)", line.strip(), re.I)
                if m and m.group(1).upper() not in {"PRIMARY","UNIQUE","CHECK","FOREIGN"}: sql_types[(tname, m.group(1))] = m.group(2).strip()
        db_by_table = {r["table_id"]:r["database_table"] for r in table_rows}
        for field in field_rows:
            dbt = db_by_table.get(field["table_id"], "")
            field["database_type"] = sql_types.get((dbt, field["field_name"]), "")

    # Normalize output ordering.
    dataset_rows.sort(key=lambda r:r["dataset_id"]); table_rows.sort(key=lambda r:r["table_id"])
    field_rows.sort(key=lambda r:(r["table_id"], r["field_name"])); quality_rows.sort(key=lambda r:r["table_id"])
    issues.sort(key=lambda r:(r["severity"], r["dataset_id"], r["issue_id"])); governance_rows.sort(key=lambda r:r["table_id"])

    generation_rows = [{"rule_id":r[0], "target_table":r[1], "rule_name":r[2], "population":r[3], "input_fields":r[4], "condition":"See pipeline implementation and manifest", "output_behavior":r[5], "probability_or_schedule":"See dataset manifest", "fallback_behavior":"Explicit provenance or exclusion; never infer unrecorded clinical facts", "source_document":"Local knowledge/source metadata where applicable", "source_locator":"See lineage/source registry", "rule_version":METADATA_VERSION, "random_seed":"20260810 where synthetic", "is_clinical_rule":False, "clinical_use_allowed":False, "approval_status":"PROJECT_DEMO_RULE", "script_path":r[6], "metadata_version":METADATA_VERSION} for r in GENERATION_RULES]

    files_to_write = {
        "dataset_catalog.csv": dataset_rows,
        "table_catalog.csv": table_rows,
        "field_catalog.csv": field_rows,
        "data_lineage.csv": lineage_rows,
        "generation_rules.csv": generation_rows,
        "source_registry.csv": source_rows,
        "quality_rules.csv": quality_rule_rows,
        "quality_summary.csv": quality_rows,
        "quality_issues.csv": issues,
        "data_governance.csv": governance_rows,
    }
    for name, rows in files_to_write.items():
        write_csv(output / name, list(rows[0]) if rows else ["empty"], rows)

    manifest_files = {}
    for path in sorted(output.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle: count = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
        manifest_files[path.name] = {"rows":count, "bytes":path.stat().st_size, "sha256":sha256(path)}
    manifest = {
        "metadata_version": METADATA_VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo), "dataset_count":len(dataset_rows), "table_count":len(table_rows),
        "field_count":len(field_rows), "lineage_count":len(lineage_rows), "generation_rule_count":len(generation_rows),
        "source_count":len(source_rows), "quality_issue_count":len(issues),
        "blocking_issue_count":sum(r["status"].startswith("OPEN_BLOCKS") or r["severity"]=="ERROR" for r in issues),
        "source_data_modified":False, "git_commit_created":False, "git_push_performed":False,
        "files":manifest_files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo-root", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(); print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()

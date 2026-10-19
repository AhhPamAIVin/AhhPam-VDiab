#!/usr/bin/env python3
"""Build a 60-day synthetic happy-case medication reminder/adherence flow.

The output is application test data only. It does not claim that a generated dose
was prescribed or clinically appropriate. Only single-ingredient, algorithm-labelled
TYPE_2 records with an exact knowledge-base match are eligible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


GENERATOR_VERSION = "medication_happy_case_60d_v1.0"
SEED = 20260810

# Lowest/simple demo schedule selected from the local monograph text. These are
# synthetic scenario defaults, not prescriptions or patient-specific recommendations.
DEMO_REGIMENS = {
    "acarbose": (25, "mg", "ORAL", "THREE_TIMES_DAILY", "WITH_MEALS", ["BREAKFAST", "LUNCH", "DINNER"], "Liều dùng"),
    "dapagliflozin": (5, "mg", "ORAL", "ONCE_DAILY", "MORNING", [], "Liều dùng"),
    "empagliflozin": (10, "mg", "ORAL", "ONCE_DAILY", "MORNING", [], "Liều dùng"),
    "glimepiride": (1, "mg", "ORAL", "ONCE_DAILY", "BEFORE_BREAKFAST", ["BREAKFAST"], "Liều dùng"),
    "liraglutide": (0.6, "mg", "SUBCUTANEOUS", "ONCE_DAILY", "MORNING", [], "Liều dùng"),
    "metformin": (500, "mg", "ORAL", "ONCE_DAILY", "WITH_DINNER", ["DINNER"], "Liều dùng (đái tháo đường típ 2)"),
    "pioglitazone": (15, "mg", "ORAL", "ONCE_DAILY", "MORNING", [], "Liều dùng"),
    "repaglinide": (0.5, "mg", "ORAL", "THREE_TIMES_DAILY", "BEFORE_MEALS_20_MIN", ["BREAKFAST", "LUNCH", "DINNER"], "Liều dùng (người lớn)"),
    "sitagliptin": (100, "mg", "ORAL", "ONCE_DAILY", "MORNING", [], "Liều dùng"),
}

ALL_KB_DRUGS = {
    "acarbose", "dapagliflozin", "empagliflozin", "gliclazide", "glimepiride",
    "insulin", "liraglutide", "metformin", "pioglitazone", "repaglinide",
    "semaglutide", "sitagliptin", "vildagliptin",
}


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def exact_components(drug_name: str) -> list[str]:
    normalized = drug_name.lower()
    return [name for name in ALL_KB_DRUGS if re.search(rf"\b{re.escape(name)}\b", normalized)]


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16].upper()


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def dose_times_for_day(day: date, regimen: tuple, meals: dict) -> list[tuple[datetime, str, str]]:
    _, _, _, _, timing_rule, meal_types, _ = regimen
    results = []
    if meal_types:
        fallback = {"BREAKFAST": time(7, 30), "LUNCH": time(12, 30), "DINNER": time(18, 30)}
        selected = meal_types
        for meal_type in selected:
            meal = meals.get((day, meal_type))
            if meal:
                meal_at, meal_id = meal
                link_status = "LINKED_SYNTHETIC_MEAL"
            else:
                meal_at = datetime.combine(day, fallback[meal_type])
                meal_id = ""
                link_status = "FALLBACK_DEMO_TIME_NO_MEAL_EVENT"
            if timing_rule == "BEFORE_BREAKFAST":
                scheduled = meal_at - timedelta(minutes=15)
            elif timing_rule == "BEFORE_MEALS_20_MIN":
                scheduled = meal_at - timedelta(minutes=20)
            else:
                scheduled = meal_at
            results.append((scheduled, meal_id, link_status))
        return results
    return [(datetime.combine(day, time(8, 0)), "", "NOT_MEAL_LINKED")]


def build(args: argparse.Namespace) -> dict:
    profiles = {row["patient_id"]: row for row in read_csv(args.profiles_csv)}
    caregivers = {row["patient_id"]: row["caregiver_id"] for row in read_csv(args.caregivers_csv)}
    documents = {row["filename"]: row["document_id"] for row in read_csv(args.knowledge_dir / "knowledge_documents.csv")}
    catalog = {row["generic_name"].lower(): row for row in read_csv(args.knowledge_dir / "drug_catalog.csv")}

    candidates, exclusions = [], []
    for row in read_csv(args.medications_csv):
        patient = profiles.get(row["patient_id"])
        components = exact_components(row["drug_name"])
        reason = ""
        if not patient:
            reason = "PATIENT_NOT_FOUND"
        elif patient["project_diabetes_label"] != "TYPE_2":
            reason = "NOT_ALGORITHM_LABELLED_TYPE_2"
        elif ";" in row["drug_name"] or len(components) != 1:
            reason = "COMBINATION_OR_NON_UNIQUE_ACTIVE_INGREDIENT"
        elif components[0] == "insulin":
            reason = "INSULIN_DOSE_REQUIRES_PATIENT_SPECIFIC_TITRATION"
        elif components[0] == "semaglutide":
            reason = "SEMAGLUTIDE_ROUTE_FORMULATION_NOT_RECORDED"
        elif components[0] not in DEMO_REGIMENS:
            reason = "NO_UNAMBIGUOUS_HAPPY_CASE_REGIMEN"
        if reason:
            if components:
                exclusions.append({
                    "source_medication_record_id": row["medication_record_id"],
                    "patient_id": row["patient_id"],
                    "source_drug_name": row["drug_name"],
                    "exclusion_reason": reason,
                })
            continue
        candidates.append((row, components[0]))

    # Medication records are historical observations without active status. Keep one
    # deterministic record per patient/ingredient to avoid duplicate synthetic regimens.
    selected = {}
    for row, ingredient in candidates:
        key = (row["patient_id"], ingredient)
        rank = (float(row.get("days_taken") or 0), row["medication_record_id"])
        if key not in selected or rank > selected[key][0]:
            if key in selected:
                old = selected[key][1]
                exclusions.append({
                    "source_medication_record_id": old["medication_record_id"],
                    "patient_id": old["patient_id"],
                    "source_drug_name": old["drug_name"],
                    "exclusion_reason": "DUPLICATE_PATIENT_INGREDIENT_RECORD_NOT_SELECTED",
                })
            selected[key] = (rank, row, ingredient)
        else:
            exclusions.append({
                "source_medication_record_id": row["medication_record_id"],
                "patient_id": row["patient_id"],
                "source_drug_name": row["drug_name"],
                "exclusion_reason": "DUPLICATE_PATIENT_INGREDIENT_RECORD_NOT_SELECTED",
            })

    eligible_patients = {key[0] for key in selected}
    meals_by_patient = defaultdict(dict)
    with args.meals_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            pid = row["patient_id"]
            if pid not in eligible_patients or row["meal_type"] not in {"BREAKFAST", "LUNCH", "DINNER"}:
                continue
            meal_at = parse_dt(row["meal_started_at"])
            key = (meal_at.date(), row["meal_type"])
            current = meals_by_patient[pid].get(key)
            if current is None or meal_at < current[0]:
                meals_by_patient[pid][key] = (meal_at, row["meal_id"])

    regimens, schedule, reminders, administrations = [], [], [], []
    rng = random.Random(SEED)
    end_date = args.start_date + timedelta(days=args.days - 1)
    for (_, ingredient), (_, source_row, _) in sorted(selected.items()):
        pid = source_row["patient_id"]
        demo = DEMO_REGIMENS[ingredient]
        dose_value, dose_unit, route, frequency, timing_rule, _, section_title = demo
        drug_ref = catalog[ingredient]
        regimen_id = stable_id("MEDREG", f"{pid}|{ingredient}|{args.start_date}|{GENERATOR_VERSION}")
        source_filename = f"duoc-thu-qg-2022-{ingredient}-chuyen-luan.md"
        regimens.append({
            "regimen_id": regimen_id,
            "patient_id": pid,
            "source_medication_record_id": source_row["medication_record_id"],
            "source_drug_name": source_row["drug_name"],
            "drug_reference_id": drug_ref["drug_reference_id"],
            "dose_value": dose_value,
            "dose_unit": dose_unit,
            "route": route,
            "frequency_code": frequency,
            "timing_rule": timing_rule,
            "start_date": args.start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "source_document_id": documents[source_filename],
            "source_section": section_title,
            "dose_basis": "SYNTHETIC_HAPPY_CASE_STARTING_DOSE_FROM_LOCAL_MONOGRAPH",
            "clinical_use_allowed": False,
            "is_synthetic": True,
            "generator_version": GENERATOR_VERSION,
        })
        for offset in range(args.days):
            day = args.start_date + timedelta(days=offset)
            for scheduled_at, meal_id, meal_link_status in dose_times_for_day(day, demo, meals_by_patient[pid]):
                schedule_id = stable_id("MEDSCH", f"{regimen_id}|{scheduled_at.isoformat()}")
                schedule.append({
                    "schedule_id": schedule_id,
                    "regimen_id": regimen_id,
                    "patient_id": pid,
                    "scheduled_at": scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                    "route": route,
                    "meal_id": meal_id,
                    "meal_link_status": meal_link_status,
                    "schedule_status": "PLANNED",
                    "is_synthetic": True,
                    "generator_version": GENERATOR_VERSION,
                })
                reminder_at = scheduled_at - timedelta(minutes=15)
                acknowledged_at = reminder_at + timedelta(minutes=2)
                reminder_id = stable_id("MEDREM", schedule_id)
                reminders.append({
                    "reminder_id": reminder_id,
                    "schedule_id": schedule_id,
                    "patient_id": pid,
                    "scheduled_for": reminder_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "sent_at": reminder_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "delivery_status": "DELIVERED",
                    "acknowledged_at": acknowledged_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "reminder_status": "ACKNOWLEDGED",
                    "recipient_type": "PATIENT_AND_CAREGIVER" if pid in caregivers else "PATIENT",
                    "caregiver_id": caregivers.get(pid, ""),
                    "is_synthetic": True,
                    "generator_version": GENERATOR_VERSION,
                })
                taken_at = scheduled_at + timedelta(minutes=rng.randint(0, 5))
                administrations.append({
                    "administration_id": stable_id("MEDADM", schedule_id),
                    "schedule_id": schedule_id,
                    "regimen_id": regimen_id,
                    "patient_id": pid,
                    "taken_at": taken_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "dose_value": dose_value,
                    "dose_unit": dose_unit,
                    "route": route,
                    "administration_status": "TAKEN_AS_SCHEDULED",
                    "minutes_from_schedule": int((taken_at - scheduled_at).total_seconds() // 60),
                    "adherence_flag": True,
                    "is_synthetic": True,
                    "generator_version": GENERATOR_VERSION,
                })

    outputs = {
        "synthetic_medication_regimens.csv": regimens,
        "synthetic_medication_schedule.csv": schedule,
        "synthetic_medication_reminders.csv": reminders,
        "synthetic_medication_administrations.csv": administrations,
        "medication_flow_exclusions.csv": exclusions,
    }
    for filename, rows in outputs.items():
        fields = list(rows[0]) if rows else ["empty"]
        write_csv(args.output_dir / filename, fields, rows)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator_version": GENERATOR_VERSION,
        "seed": SEED,
        "start_date": args.start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": args.days,
        "happy_case_policy": "100% delivered, acknowledged, and taken within 0-5 minutes",
        "clinical_use_allowed": False,
        "eligibility_policy": "TYPE_2 algorithm label; exact single ingredient; local KB regimen; excludes insulin, combinations, and ambiguous semaglutide formulation",
        "row_counts": {name.removesuffix(".csv"): len(rows) for name, rows in outputs.items()},
        "regimen_counts": dict(Counter(row["source_drug_name"] for row in regimens)),
        "exclusion_counts": dict(Counter(row["exclusion_reason"] for row in exclusions)),
        "safety": {
            "changes_observed_patient_medications": False,
            "represents_real_prescription": False,
            "represents_clinical_recommendation": False,
            "dose_is_synthetic_demo_default": True,
        },
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-csv", type=Path, required=True)
    parser.add_argument("--medications-csv", type=Path, required=True)
    parser.add_argument("--caregivers-csv", type=Path, required=True)
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument("--meals-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

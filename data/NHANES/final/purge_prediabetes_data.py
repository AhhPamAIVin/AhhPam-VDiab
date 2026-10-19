#!/usr/bin/env python3
"""Remove PREDIABETES patient rows and dependent demo data under data/.

Dry-run is the default. Pass --apply to rewrite affected CSV files. The purge
identifier set is derived only from the canonical deployment patient profile.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path


PATIENT_KEY_NAMES = {"patient_id", "source_patient_id", "linked_patient_id"}
SUBJECT_KEY_NAMES = {"subject_id", "source_subject_id"}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def patient_keys(fields: list[str]) -> list[str]:
    return [
        field
        for field in fields
        if field in PATIENT_KEY_NAMES or field.endswith("_patient_id")
    ]


def subject_keys(fields: list[str]) -> list[str]:
    return [field for field in fields if field in SUBJECT_KEY_NAMES]


def atomic_write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    expected_name = "data"
    if data_root.name != expected_name or not data_root.is_dir():
        raise ValueError(f"Refusing unexpected data root: {data_root}")

    purge_ids: set[str] = set()
    purge_subject_ids: set[str] = set()
    label_source_files: list[str] = []
    for path in sorted(data_root.rglob("*.csv")):
        if "node_modules" in path.parts:
            continue
        fields, rows = read_csv(path)
        keys = patient_keys(fields)
        subject_id_keys = subject_keys(fields)
        if not keys and not subject_id_keys:
            continue
        found_in_file = False
        for row in rows:
            values = {(value or "").strip().upper() for value in row.values()}
            has_prediabetes_patient = "PREDIABETES" in values
            has_prediabetes_subject = "PREDIABETES_RANGE" in values
            if not has_prediabetes_patient and not has_prediabetes_subject:
                continue
            if has_prediabetes_patient:
                for key in keys:
                    patient_id = (row.get(key) or "").strip()
                    if patient_id:
                        purge_ids.add(patient_id)
                        found_in_file = True
            if has_prediabetes_subject:
                for key in subject_id_keys:
                    subject_id = (row.get(key) or "").strip()
                    if subject_id:
                        purge_subject_ids.add(subject_id)
                        found_in_file = True
        if found_in_file:
            label_source_files.append(path.relative_to(data_root).as_posix())
    if not purge_ids and not purge_subject_ids:
        print(json.dumps({"status": "nothing_to_purge", "prediabetes_ids": 0}, indent=2))
        return

    affected: list[dict[str, object]] = []
    total_removed = 0
    for path in sorted(data_root.rglob("*.csv")):
        if "node_modules" in path.parts:
            continue
        fields, rows = read_csv(path)
        keys = patient_keys(fields)
        source_keys = subject_keys(fields)
        if not keys and not source_keys:
            continue
        kept = [
            row
            for row in rows
            if not any((row.get(key) or "").strip() in purge_ids for key in keys)
            and not any((row.get(key) or "").strip() in purge_subject_ids for key in source_keys)
        ]
        removed = len(rows) - len(kept)
        if not removed:
            continue
        affected.append({
            "file": path.relative_to(data_root).as_posix(),
            "removed_rows": removed,
            "remaining_rows": len(kept),
            "matched_keys": keys + source_keys,
        })
        total_removed += removed
        if args.apply:
            atomic_write_csv(path, fields, kept)

    removed_doctors = 0
    if args.apply:
        assignments_path = data_root / "NHANES" / "deployment_dataset" / "patient_doctor_assignments.csv"
        doctors_path = data_root / "NHANES" / "deployment_dataset" / "doctor_profiles.csv"
        doctor_fields, doctors = read_csv(doctors_path)
        _, assignments = read_csv(assignments_path)
        referenced_doctors = {row.get("doctor_id", "") for row in assignments}
        kept_doctors = [row for row in doctors if row.get("doctor_id", "") in referenced_doctors]
        removed_doctors = len(doctors) - len(kept_doctors)
        if removed_doctors:
            atomic_write_csv(doctors_path, doctor_fields, kept_doctors)
            affected.append({
                "file": doctors_path.relative_to(data_root).as_posix(),
                "removed_rows": removed_doctors,
                "remaining_rows": len(kept_doctors),
                "matched_keys": ["unreferenced doctor_id after patient purge"],
            })
            total_removed += removed_doctors

    result = {
        "mode": "apply" if args.apply else "dry_run",
        "prediabetes_patient_ids": len(purge_ids),
        "prediabetes_source_subject_ids": len(purge_subject_ids),
        "label_source_files": label_source_files,
        "affected_file_count": len(affected),
        "removed_row_count": total_removed,
        "removed_unreferenced_doctors": removed_doctors,
        "affected_files": [] if args.summary_only else affected,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

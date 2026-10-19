from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


NULL_THRESHOLD = 0.70
SOURCE_REFERENCE = "https://physionet.org/content/cgmacros/1.0.0/"
PIPELINE_VERSION = "cgmacros_clean_v2_meal_events"
MEAL_COLUMNS = {
    "Meal Type",
    "RecordIndex",
    "Calories",
    "Carbs",
    "Protein",
    "Fat",
    "Fiber",
    "Sugar",
    "Amount Consumed",
    "Amount Consumed ",
    "Image path",
}
MEAL_TYPE_MAP = {
    "breakfast": "BREAKFAST",
    "lunch": "LUNCH",
    "dinner": "DINNER",
    "snack": "SNACK",
    "snacks": "SNACK",
    "snack 1": "SNACK",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean CGMacros and normalize sparse meal events"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def is_missing(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "na", "null", "none"}


def numeric_text(value: object) -> str:
    if is_missing(value):
        return ""
    try:
        number = float(str(value).strip())
    except ValueError:
        return str(value).strip()
    if not math.isfinite(number):
        return ""
    return str(value).strip()


def numeric_value(value: object) -> float | None:
    text = numeric_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def subject_id_from_dir(path: Path) -> int | None:
    match = re.fullmatch(r"CGMacros-(\d+)", path.parent.name)
    return int(match.group(1)) if match else None


def meal_quality_flags(row: dict[str, str], canonical_type: str) -> list[str]:
    flags: list[str] = []
    if canonical_type == "OTHER":
        flags.append("UNMAPPED_MEAL_TYPE")
    nutrients = {
        name: numeric_value(row.get(source_name))
        for name, source_name in {
            "calories": "Calories",
            "carbs": "Carbs",
            "protein": "Protein",
            "fat": "Fat",
            "fiber": "Fiber",
        }.items()
    }
    if any(value is None for value in nutrients.values()):
        flags.append("MISSING_NUTRIENT_VALUE")
    if any(value is not None and value < 0 for value in nutrients.values()):
        flags.append("NEGATIVE_NUTRIENT_VALUE")
    calories = nutrients["calories"]
    if calories is not None and calories > 0 and all(
        nutrients[key] is not None for key in ("carbs", "protein", "fat")
    ):
        estimated = (
            4 * float(nutrients["carbs"])
            + 4 * float(nutrients["protein"])
            + 9 * float(nutrients["fat"])
        )
        if abs(estimated - calories) / calories > 0.30:
            flags.append("MACRO_CALORIE_MISMATCH_GT_30PCT")
    if is_missing(row.get("Image path")):
        flags.append("NO_DIRECT_MEAL_IMAGE")
    return flags


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Source and output must be separate non-nested directories")

    csv_files = sorted(source.rglob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found under {source}")

    bio_fields, bio_rows = read_rows(source / "bio.csv")
    healthy_subject_ids = {
        int(float(row["subject"]))
        for row in bio_rows
        if numeric_value(row.get("A1c PDL (Lab)")) is not None
        and float(row["A1c PDL (Lab)"]) < 5.7
    }
    type_1_subject_ids: set[int] = set()
    excluded_subject_ids = healthy_subject_ids | type_1_subject_ids

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    file_reports: list[dict[str, object]] = []
    column_reports: list[dict[str, object]] = []
    meal_events: list[dict[str, object]] = []
    meal_images: list[dict[str, object]] = []
    event_sequence: Counter[int] = Counter()
    image_sequence: Counter[int] = Counter()

    subject_profiles: list[dict[str, object]] = []
    for row in bio_rows:
        subject_value = numeric_value(row.get("subject"))
        if subject_value is None or int(subject_value) in excluded_subject_ids:
            continue
        subject_id = int(subject_value)
        a1c = numeric_value(row.get("A1c PDL (Lab)"))
        glycemic_group = (
            "DIABETES_RANGE"
            if a1c is not None and a1c >= 6.5
            else "PREDIABETES_RANGE"
        )
        subject_profiles.append(
            {
                "source_subject_id": subject_id,
                "age_years": numeric_text(row.get("Age")),
                "sex": str(row.get("Gender") or "").strip(),
                "bmi": numeric_text(row.get("BMI")),
                "body_weight_lb": numeric_text(row.get("Body weight ")),
                "height_in": numeric_text(row.get("Height ")),
                "self_identified_ethnicity": str(row.get("Self-identify ") or "").strip(),
                "a1c_pct": numeric_text(row.get("A1c PDL (Lab)")),
                "fasting_glucose_mg_dl": numeric_text(row.get("Fasting GLU - PDL (Lab)")),
                "baseline_insulin_source_value": numeric_text(row.get("Insulin ")),
                "triglycerides_mg_dl": numeric_text(row.get("Triglycerides")),
                "total_cholesterol_mg_dl": numeric_text(row.get("Cholesterol")),
                "hdl_mg_dl": numeric_text(row.get("HDL")),
                "ldl_calculated_mg_dl": numeric_text(row.get("LDL (Cal)")),
                "source_glycemic_group": glycemic_group,
                "classification_basis": "A1c PDL (Lab): 5.7-6.4 prediabetes range; >=6.5 diabetes range",
                "data_source": "CGMACROS_1_0_0",
                "record_type": "OBSERVED_SOURCE_SUBJECT_PROFILE",
                "is_synthetic": "False",
                "pipeline_version": PIPELINE_VERSION,
            }
        )

    for path in csv_files:
        relative = path.relative_to(source)
        subject_id = subject_id_from_dir(path)
        if subject_id in excluded_subject_ids:
            file_reports.append(
                {
                    "file": relative.as_posix(),
                    "status": "EXCLUDED_HEALTHY_OR_TYPE_1_SUBJECT",
                    "subject_id": subject_id,
                }
            )
            continue

        fields, rows = read_rows(path)
        rows_before = len(rows)
        healthy_rows_removed = 0
        type_1_rows_removed = 0
        if "subject" in fields:
            retained: list[dict[str, str]] = []
            for row in rows:
                value = numeric_value(row.get("subject"))
                row_subject = int(value) if value is not None else None
                if row_subject in healthy_subject_ids:
                    healthy_rows_removed += 1
                elif row_subject in type_1_subject_ids:
                    type_1_rows_removed += 1
                else:
                    retained.append(row)
            rows = retained

        extracted_meals = 0
        extracted_images = 0
        is_participant_series = subject_id is not None and "Timestamp" in fields
        if is_participant_series:
            for source_row_number, row in enumerate(rows, start=2):
                meal_id = ""
                raw_type = str(row.get("Meal Type") or "").strip()
                if raw_type:
                    event_sequence[subject_id] += 1
                    meal_id = f"CGM-{subject_id:03d}-MEAL-{event_sequence[subject_id]:04d}"
                    canonical_type = MEAL_TYPE_MAP.get(raw_type.lower(), "OTHER")
                    amount = row.get("Amount Consumed")
                    if is_missing(amount):
                        amount = row.get("Amount Consumed ")
                    flags = meal_quality_flags(row, canonical_type)
                    meal_events.append(
                        {
                            "meal_id": meal_id,
                            "source_subject_id": subject_id,
                            "meal_started_at": str(row.get("Timestamp") or "").strip(),
                            "meal_type": canonical_type,
                            "meal_type_raw": raw_type,
                            "calories_kcal": numeric_text(row.get("Calories")),
                            "carbohydrate_g": numeric_text(row.get("Carbs")),
                            "protein_g": numeric_text(row.get("Protein")),
                            "fat_g": numeric_text(row.get("Fat")),
                            "fiber_g": numeric_text(row.get("Fiber")),
                            "sugar_g": numeric_text(row.get("Sugar")),
                            "amount_consumed_pct": numeric_text(amount),
                            "direct_image_path": str(row.get("Image path") or "").strip(),
                            "quality_flags": "|".join(flags),
                            "source_file": relative.as_posix(),
                            "source_row_number": source_row_number,
                            "data_source": "CGMACROS_1_0_0",
                            "record_type": "OBSERVED_MEAL_EVENT",
                            "is_synthetic": "False",
                            "pipeline_version": PIPELINE_VERSION,
                        }
                    )
                    extracted_meals += 1

                image_path = str(row.get("Image path") or "").strip()
                if image_path:
                    image_sequence[subject_id] += 1
                    meal_images.append(
                        {
                            "meal_image_id": (
                                f"CGM-{subject_id:03d}-IMAGE-{image_sequence[subject_id]:04d}"
                            ),
                            "source_subject_id": subject_id,
                            "captured_at": str(row.get("Timestamp") or "").strip(),
                            "image_path": image_path,
                            "meal_id": meal_id,
                            "link_status": (
                                "DIRECT_SAME_SOURCE_ROW"
                                if meal_id
                                else "UNLINKED_SOURCE_IMAGE"
                            ),
                            "source_file": relative.as_posix(),
                            "source_row_number": source_row_number,
                            "data_source": "CGMACROS_1_0_0",
                            "record_type": "OBSERVED_MEAL_IMAGE",
                            "is_synthetic": "False",
                            "pipeline_version": PIPELINE_VERSION,
                        }
                    )
                    extracted_images += 1

        candidate_fields = [
            field for field in fields if not (is_participant_series and field in MEAL_COLUMNS)
        ]
        missingness = {
            field: (
                sum(is_missing(row.get(field)) for row in rows) / len(rows)
                if rows
                else 1.0
            )
            for field in candidate_fields
        }
        dropped_fields = [
            field for field in candidate_fields if missingness[field] > NULL_THRESHOLD
        ]
        retained_fields = [field for field in candidate_fields if field not in dropped_fields]

        for field in fields:
            if is_participant_series and field in MEAL_COLUMNS:
                action = "EXTRACT_TO_NORMALIZED_MEAL_TABLES"
                null_rate = (
                    sum(is_missing(row.get(field)) for row in rows) / len(rows)
                    if rows
                    else 1.0
                )
                rule = "sparse meal event field; extract before sensor missingness filter"
            else:
                null_rate = missingness[field]
                action = "DROP" if field in dropped_fields else "KEEP"
                rule = "null_rate > 0.70"
            column_reports.append(
                {
                    "file": relative.as_posix(),
                    "column": field,
                    "null_rate": null_rate,
                    "action": action,
                    "rule": rule,
                }
            )

        cleaned_rows = [
            {field: row.get(field, "") for field in retained_fields} for row in rows
        ]
        write_rows(output / relative, retained_fields, cleaned_rows)
        file_reports.append(
            {
                "file": relative.as_posix(),
                "rows_before": rows_before,
                "rows_after": len(cleaned_rows),
                "type_1_rows_removed": type_1_rows_removed,
                "healthy_rows_removed": healthy_rows_removed,
                "columns_before": len(fields),
                "columns_after": len(retained_fields),
                "meal_events_extracted": extracted_meals,
                "meal_images_extracted": extracted_images,
                "columns_extracted_to_meal_tables": sorted(
                    set(fields).intersection(MEAL_COLUMNS)
                ),
                "columns_dropped_over_70pct_null": dropped_fields,
            }
        )

    meal_event_fields = [
        "meal_id", "source_subject_id", "meal_started_at", "meal_type",
        "meal_type_raw", "calories_kcal", "carbohydrate_g", "protein_g",
        "fat_g", "fiber_g", "sugar_g", "amount_consumed_pct",
        "direct_image_path", "quality_flags", "source_file",
        "source_row_number", "data_source", "record_type", "is_synthetic",
        "pipeline_version",
    ]
    meal_image_fields = [
        "meal_image_id", "source_subject_id", "captured_at", "image_path",
        "meal_id", "link_status", "source_file", "source_row_number",
        "data_source", "record_type", "is_synthetic", "pipeline_version",
    ]
    subject_profile_fields = [
        "source_subject_id", "age_years", "sex", "bmi", "body_weight_lb",
        "height_in", "self_identified_ethnicity", "a1c_pct",
        "fasting_glucose_mg_dl", "baseline_insulin_source_value",
        "triglycerides_mg_dl", "total_cholesterol_mg_dl", "hdl_mg_dl",
        "ldl_calculated_mg_dl", "source_glycemic_group",
        "classification_basis", "data_source", "record_type", "is_synthetic",
        "pipeline_version",
    ]
    write_rows(
        output / "cgmacros_subject_profiles.csv",
        subject_profile_fields,
        subject_profiles,
    )
    write_rows(output / "meal_events.csv", meal_event_fields, meal_events)
    write_rows(output / "meal_images.csv", meal_image_fields, meal_images)

    photo_directories_copied = 0
    photo_files_copied = 0
    for participant_dir in sorted(source.glob("CGMacros-*")):
        if not participant_dir.is_dir():
            continue
        match = re.fullmatch(r"CGMacros-(\d+)", participant_dir.name)
        if not match or int(match.group(1)) in excluded_subject_ids:
            continue
        raw_photos = participant_dir / "photos"
        if not raw_photos.exists():
            continue
        cleaned_photos = output / participant_dir.name / "photos"
        shutil.copytree(raw_photos, cleaned_photos)
        photo_directories_copied += 1
        photo_files_copied += sum(path.is_file() for path in cleaned_photos.rglob("*"))

    quality_flag_counts = Counter()
    for event in meal_events:
        for flag in str(event["quality_flags"]).split("|"):
            if flag:
                quality_flag_counts[flag] += 1

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "source_directory": str(source),
        "output_directory": str(output),
        "source_reference": SOURCE_REFERENCE,
        "sensor_null_column_rule": "drop when null_rate > 0.70 after meal extraction",
        "meal_normalization_rule": (
            "rows with non-empty Meal Type become meal_events; all non-empty Image path "
            "rows become meal_images; only same-row images receive a meal_id"
        ),
        "healthy_definition": "A1c PDL (Lab) < 5.7%",
        "healthy_subject_ids": sorted(healthy_subject_ids),
        "healthy_subjects_removed": len(healthy_subject_ids),
        "type_1_subject_ids": sorted(type_1_subject_ids),
        "type_1_subjects_removed": len(type_1_subject_ids),
        "type_1_note": (
            "CGMacros v1.0.0 contains no documented Type 1 participants; "
            "Type 1 was not inferred from laboratory values."
        ),
        "meal_events_extracted": len(meal_events),
        "subject_profiles_written": len(subject_profiles),
        "meal_images_extracted": len(meal_images),
        "directly_linked_meal_images": sum(bool(row["meal_id"]) for row in meal_images),
        "unlinked_source_images": sum(not bool(row["meal_id"]) for row in meal_images),
        "meal_quality_flag_counts": dict(sorted(quality_flag_counts.items())),
        "photo_directories_copied": photo_directories_copied,
        "photo_files_copied": photo_files_copied,
        "files": file_reports,
    }
    (output / "cleaning_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_rows(
        output / "column_missingness_report.csv",
        ["file", "column", "null_rate", "action", "rule"],
        column_reports,
    )
    (output / "README.md").write_text(
        """# CGMacros cleaned dataset

Cleaning rules:

1. Remove healthy participants defined by baseline HbA1c < 5.7%.
2. Remove documented Type 1 participants; CGMacros v1.0.0 documents none.
3. Normalize sparse meal fields before applying the 70% null rule.
4. Write retained source profiles to `cgmacros_subject_profiles.csv`.
5. Write rows with `Meal Type` to `meal_events.csv`.
6. Write rows with `Image path` to `meal_images.csv`. Images are linked to a meal
   only when both values occur on the same source row; other images remain explicitly
   unlinked rather than being inferred.
7. Apply the null-over-70% rule only to the remaining sensor/non-event columns.

Raw source files are never modified. Numeric meal values are preserved as observed.
Suspect values are retained with `quality_flags`, not silently corrected. Clinical,
meal, and image records in this folder are observed source data, not synthetic data.

See `cleaning_report.json` and `column_missingness_report.csv` for the audit trail.
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

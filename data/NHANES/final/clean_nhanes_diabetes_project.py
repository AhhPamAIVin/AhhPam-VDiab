from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# ============================================================
# NHANES cleaning pipeline for Diabetes Health Management System
# ============================================================

LOGGER = logging.getLogger("nhanes_cleaning")

TARGET_STAGES = {"Diabetes", "Prediabetes"}

CORE_ID_FIELDS = ["patient_id", "age_years", "sex"]

PROFILE_FIELDS = [
    "patient_id",
    "age_years",
    "sex",
    "race_ethnicity_code",
    "income_poverty_ratio",
    "weight_kg",
    "height_cm",
    "bmi",
    "waist_cm",
    "age_group",
    "caregiver_mode",
]

GLYCEMIC_FIELDS = [
    "hba1c_pct",
    "fasting_glucose_mg_dl",
    "doctor_diagnosed_diabetes_flag",
    "doctor_diagnosed_prediabetes_flag",
    "diabetes_by_lab_flag",
    "prediabetes_by_lab_flag",
    "taking_insulin_flag",
    "taking_diabetes_pills_flag",
]

VITAL_FIELDS = [
    "sbp_mean",
    "dbp_mean",
    "pulse_mean",
]

MEDICATION_FIELDS = [
    "taking_insulin_flag",
    "taking_diabetes_pills_flag",
    "medication_names",
    "medication_drug_ids",
    "prescription_medication_use_code",
]

LIFESTYLE_FIELDS = [
    "vigorous_work_activity_flag",
    "moderate_work_activity_flag",
    "walk_bicycle_transport_flag",
    "vigorous_recreation_flag",
    "moderate_recreation_flag",
    "sedentary_minutes_day",
    "sleep_hours_weekday",
    "sleep_hours_weekend",
    "smoked_100_cigarettes_flag",
    "alcohol_ever_flag",
]

COMORBIDITY_FLAG_FIELDS = [
    "doctor_diagnosed_hypertension_flag",
    "doctor_diagnosed_high_cholesterol_flag",
    "congestive_heart_failure_flag",
    "coronary_heart_disease_flag",
    "angina_flag",
    "heart_attack_flag",
    "stroke_flag",
    "thyroid_problem_flag",
    "liver_condition_flag",
    "family_history_diabetes_flag",
]

BOOL_FLAG_COLUMNS = [
    c
    for c in (
        "doctor_diagnosed_diabetes_flag",
        "doctor_diagnosed_prediabetes_flag",
        "taking_insulin_flag",
        "taking_diabetes_pills_flag",
        "doctor_diagnosed_hypertension_flag",
        "taking_bp_medication_flag",
        "doctor_diagnosed_high_cholesterol_flag",
        "taking_cholesterol_medication_flag",
        "congestive_heart_failure_flag",
        "coronary_heart_disease_flag",
        "angina_flag",
        "heart_attack_flag",
        "stroke_flag",
        "thyroid_problem_flag",
        "liver_condition_flag",
        "family_history_diabetes_flag",
        "vigorous_work_activity_flag",
        "moderate_work_activity_flag",
        "walk_bicycle_transport_flag",
        "vigorous_recreation_flag",
        "moderate_recreation_flag",
        "doctor_diagnosed_sleep_disorder_flag",
        "smoked_100_cigarettes_flag",
        "alcohol_ever_flag",
        "diabetes_by_lab_flag",
        "prediabetes_by_lab_flag",
        "normal_glycemia_by_lab_flag",
        "elevated_bp_screen_flag",
        "smoking_applicable",
        "alcohol_applicable",
        "adult_questionnaire_applicable",
    )
]

NUMERIC_COLUMNS = [
    "age_years",
    "income_poverty_ratio",
    "weight_kg",
    "height_cm",
    "bmi",
    "waist_cm",
    "sbp_1",
    "dbp_1",
    "sbp_2",
    "dbp_2",
    "sbp_3",
    "dbp_3",
    "pulse_1",
    "pulse_2",
    "pulse_3",
    "hba1c_pct",
    "fasting_glucose_mg_dl",
    "hdl_mg_dl",
    "total_cholesterol_mg_dl",
    "triglycerides_mg_dl",
    "ldl_mg_dl",
    "vigorous_work_days_week",
    "vigorous_work_minutes_day",
    "moderate_work_days_week",
    "moderate_work_minutes_day",
    "sedentary_minutes_day",
    "sleep_hours_weekday",
    "sleep_hours_weekend",
    "alcohol_frequency_past_year",
    "avg_drinks_per_drinking_day",
    "self_reported_current_weight_lb",
    "self_reported_weight_year_ago_lb",
    "self_reported_weight_10_years_ago_lb",
    "self_reported_max_weight_lb",
    "creatinine_mg_dl",
    "alt_u_l",
    "ast_u_l",
    "ggt_u_l",
    "bun_mg_dl",
    "maximum_medication_days",
    "sbp_mean",
    "dbp_mean",
    "pulse_mean",
]

# Conservative plausibility ranges. Values outside these ranges are set to missing
# and recorded in quality flags.
PLAUSIBLE_RANGES = {
    "age_years": (0, 120),
    "weight_kg": (2, 350),
    "height_cm": (45, 230),
    "bmi": (8, 100),
    "waist_cm": (30, 220),
    "sbp_1": (60, 260),
    "sbp_2": (60, 260),
    "sbp_3": (60, 260),
    "dbp_1": (30, 160),
    "dbp_2": (30, 160),
    "dbp_3": (30, 160),
    "pulse_1": (25, 220),
    "pulse_2": (25, 220),
    "pulse_3": (25, 220),
    "hba1c_pct": (2.5, 20),
    "fasting_glucose_mg_dl": (30, 700),
    "hdl_mg_dl": (5, 200),
    "total_cholesterol_mg_dl": (50, 500),
    "triglycerides_mg_dl": (10, 3000),
    "ldl_mg_dl": (5, 400),
    "sedentary_minutes_day": (0, 1440),
    "sleep_hours_weekday": (0, 24),
    "sleep_hours_weekend": (0, 24),
    "creatinine_mg_dl": (0.1, 20),
    "alt_u_l": (1, 2000),
    "ast_u_l": (1, 2000),
    "ggt_u_l": (1, 3000),
    "bun_mg_dl": (1, 250),
}

SENTINEL_NUMBERS = {
    5555,
    55555,
    7777,
    77777,
    8888,
    88888,
    9999,
    99999,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean NHANES clinical data for a diabetes management project."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to nhanes_clinical_master_all_ages.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="nhanes_clean_output",
        help="Directory for cleaned outputs",
    )
    parser.add_argument(
        "--gold-threshold",
        type=float,
        default=75.0,
        help="Minimum quality score for Gold tier",
    )
    parser.add_argument(
        "--silver-threshold",
        type=float,
        default=50.0,
        help="Minimum quality score for Silver tier",
    )
    return parser.parse_args()


def normalize_column_names(columns: Iterable[str]) -> list[str]:
    cleaned = []
    for col in columns:
        name = str(col).strip().lower()
        name = re.sub(r"[^a-z0-9]+", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        cleaned.append(name)
    return cleaned


def coerce_boolean(series: pd.Series) -> pd.Series:
    """Convert common boolean/code representations to pandas nullable boolean."""
    true_values = {
        True,
        1,
        1.0,
        "1",
        "1.0",
        "true",
        "yes",
        "y",
        "có",
    }
    false_values = {
        False,
        0,
        0.0,
        2,
        2.0,
        "0",
        "0.0",
        "2",
        "2.0",
        "false",
        "no",
        "n",
        "không",
    }

    def convert(value):
        if pd.isna(value):
            return pd.NA
        normalized = value.strip().lower() if isinstance(value, str) else value
        if normalized in true_values:
            return True
        if normalized in false_values:
            return False
        return pd.NA

    return series.map(convert).astype("boolean")


def normalize_text_missing(series: pd.Series) -> pd.Series:
    missing_tokens = {
        "",
        "nan",
        "none",
        "null",
        "na",
        "n/a",
        "unknown",
        "refused",
        "don't know",
        "dont know",
        "99999",
        "55555",
        "77777",
        "88888",
    }
    result = series.astype("string").str.strip()
    lower = result.str.lower()
    result = result.mask(lower.isin(missing_tokens), pd.NA)
    return result


def replace_numeric_sentinels(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    numeric = numeric.mask(numeric.isin(SENTINEL_NUMBERS), np.nan)
    # Values extremely close to zero in the source are usually floating-point
    # representations of zero.
    numeric = numeric.mask(numeric.abs().lt(1e-12), 0.0)
    return numeric


def append_flag(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    if "quality_flags" not in df.columns:
        df["quality_flags"] = ""
    current = df.loc[mask, "quality_flags"].fillna("")
    df.loc[mask, "quality_flags"] = np.where(
        current.eq(""),
        flag,
        current + "|" + flag,
    )


def normalize_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = replace_numeric_sentinels(df[col])

    for col in BOOL_FLAG_COLUMNS:
        if col in df.columns:
            df[col] = coerce_boolean(df[col])

    text_cols = df.select_dtypes(include=["object", "string"]).columns
    for col in text_cols:
        df[col] = normalize_text_missing(df[col])

    if "patient_id" in df.columns:
        df["patient_id"] = df["patient_id"].astype("string").str.strip()

    if "source_patient_id" in df.columns:
        df["source_patient_id"] = df["source_patient_id"].astype("string").str.strip()

    return df


def remove_duplicate_patients(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "patient_id" not in df.columns:
        raise ValueError("Required column 'patient_id' is missing.")

    duplicate_mask = df["patient_id"].duplicated(keep=False)
    duplicates = df.loc[duplicate_mask].copy()

    if duplicates.empty:
        return df.copy(), duplicates

    # Keep the most complete row for each duplicated patient.
    completeness = df.notna().sum(axis=1)
    working = df.assign(_row_completeness=completeness)
    working = working.sort_values(
        by=["patient_id", "_row_completeness"],
        ascending=[True, False],
    )
    deduplicated = working.drop_duplicates("patient_id", keep="first")
    deduplicated = deduplicated.drop(columns="_row_completeness")
    return deduplicated, duplicates


def apply_plausibility_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["quality_flags"] = ""

    for col, (minimum, maximum) in PLAUSIBLE_RANGES.items():
        if col not in df.columns:
            continue
        invalid = df[col].notna() & ~df[col].between(minimum, maximum)
        append_flag(df, invalid, f"INVALID_{col.upper()}")
        df.loc[invalid, col] = np.nan

    # Days per week and minutes per day.
    for col in ["vigorous_work_days_week", "moderate_work_days_week"]:
        if col in df.columns:
            invalid = df[col].notna() & ~df[col].between(0, 7)
            append_flag(df, invalid, f"INVALID_{col.upper()}")
            df.loc[invalid, col] = np.nan

    for col in ["vigorous_work_minutes_day", "moderate_work_minutes_day"]:
        if col in df.columns:
            invalid = df[col].notna() & ~df[col].between(0, 1440)
            append_flag(df, invalid, f"INVALID_{col.upper()}")
            df.loc[invalid, col] = np.nan

    return df


def calculate_row_mean(df: pd.DataFrame, columns: list[str]) -> tuple[pd.Series, pd.Series]:
    available = [col for col in columns if col in df.columns]
    if not available:
        return (
            pd.Series(np.nan, index=df.index, dtype=float),
            pd.Series(0, index=df.index, dtype="Int64"),
        )
    count = df[available].notna().sum(axis=1).astype("Int64")
    mean = df[available].mean(axis=1, skipna=True)
    mean = mean.mask(count.eq(0), np.nan)
    return mean, count


def recalculate_derived_fields(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    sbp_mean, sbp_count = calculate_row_mean(df, ["sbp_1", "sbp_2", "sbp_3"])
    dbp_mean, dbp_count = calculate_row_mean(df, ["dbp_1", "dbp_2", "dbp_3"])
    pulse_mean, pulse_count = calculate_row_mean(df, ["pulse_1", "pulse_2", "pulse_3"])

    df["sbp_mean"] = sbp_mean.round(1)
    df["dbp_mean"] = dbp_mean.round(1)
    df["pulse_mean"] = pulse_mean.round(1)
    df["sbp_measurement_count"] = sbp_count
    df["dbp_measurement_count"] = dbp_count
    df["pulse_measurement_count"] = pulse_count

    if {"weight_kg", "height_cm"}.issubset(df.columns):
        calculated_bmi = df["weight_kg"] / ((df["height_cm"] / 100) ** 2)
        calculated_bmi = calculated_bmi.where(df["height_cm"].gt(0))
        df["bmi_calculated"] = calculated_bmi.round(1)

        if "bmi" not in df.columns:
            df["bmi"] = df["bmi_calculated"]
        else:
            bmi_difference = (df["bmi"] - df["bmi_calculated"]).abs()
            conflict = (
                df["bmi"].notna()
                & df["bmi_calculated"].notna()
                & bmi_difference.gt(2.0)
            )
            append_flag(df, conflict, "BMI_SOURCE_CONFLICT")
            df["bmi"] = df["bmi"].fillna(df["bmi_calculated"])

    # Adult BMI category only. Pediatric BMI requires age/sex percentiles,
    # which are not available in this file.
    df["bmi_category_clean"] = pd.Series(pd.NA, index=df.index, dtype="string")
    adult = df["age_years"].ge(18) if "age_years" in df.columns else False
    if isinstance(adult, pd.Series):
        df.loc[adult & df["bmi"].lt(18.5), "bmi_category_clean"] = "Underweight"
        df.loc[
            adult & df["bmi"].between(18.5, 24.9, inclusive="both"),
            "bmi_category_clean",
        ] = "Normal"
        df.loc[
            adult & df["bmi"].between(25.0, 29.9, inclusive="both"),
            "bmi_category_clean",
        ] = "Overweight"
        df.loc[adult & df["bmi"].ge(30.0), "bmi_category_clean"] = "Obesity"
        df.loc[~adult & df["bmi"].notna(), "bmi_category_clean"] = "Pediatric_Not_Classified"

    if {"sbp_mean", "dbp_mean"}.issubset(df.columns):
        df["elevated_bp_screen_flag_clean"] = (
            df["sbp_mean"].ge(130) | df["dbp_mean"].ge(80)
        ).astype("boolean")

    if "age_years" in df.columns:
        bins = [-np.inf, 4, 11, 17, 39, 64, np.inf]
        labels = ["0-4", "5-11", "12-17", "18-39", "40-64", "65+"]
        df["age_group_clean"] = pd.cut(
            df["age_years"],
            bins=bins,
            labels=labels,
            include_lowest=True,
        ).astype("string")
        df["caregiver_required_flag"] = df["age_years"].lt(18).astype("boolean")

    df["derived_field_version"] = "nhanes_cleaning_v1.0"
    return df


def determine_clinical_stage(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    doctor_diabetes = df.get(
        "doctor_diagnosed_diabetes_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)
    doctor_prediabetes = df.get(
        "doctor_diagnosed_prediabetes_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)
    # NHANES DIQ010=3 is "Borderline" and is valid prediabetes evidence.
    if "doctor_diagnosed_diabetes_code" in df.columns:
        doctor_prediabetes = doctor_prediabetes | df[
            "doctor_diagnosed_diabetes_code"
        ].eq(3)
    lab_diabetes = df.get(
        "diabetes_by_lab_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)
    lab_prediabetes = df.get(
        "prediabetes_by_lab_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)
    insulin = df.get(
        "taking_insulin_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)
    diabetes_pills = df.get(
        "taking_diabetes_pills_flag",
        pd.Series(pd.NA, index=df.index, dtype="boolean"),
    ).fillna(False)

    # Diabetes evidence takes priority over prediabetes evidence.
    diabetes_evidence = doctor_diabetes | lab_diabetes
    medication_support = insulin | diabetes_pills
    prediabetes_evidence = doctor_prediabetes | lab_prediabetes

    df["clinical_stage"] = pd.Series("UNCLASSIFIED", index=df.index, dtype="string")
    df.loc[prediabetes_evidence & ~diabetes_evidence, "clinical_stage"] = "PREDIABETES"
    df.loc[diabetes_evidence, "clinical_stage"] = "DIABETES"

    df["stage_evidence"] = pd.Series("NONE", index=df.index, dtype="string")
    df.loc[
        doctor_diabetes & ~lab_diabetes,
        "stage_evidence",
    ] = "DOCTOR_DIAGNOSIS"
    df.loc[
        ~doctor_diabetes & lab_diabetes,
        "stage_evidence",
    ] = "LAB"
    df.loc[
        doctor_diabetes & lab_diabetes,
        "stage_evidence",
    ] = "DOCTOR_AND_LAB"
    df.loc[
        ~diabetes_evidence & doctor_prediabetes & ~lab_prediabetes,
        "stage_evidence",
    ] = "DOCTOR_DIAGNOSIS"
    df.loc[
        ~diabetes_evidence & ~doctor_prediabetes & lab_prediabetes,
        "stage_evidence",
    ] = "LAB"
    df.loc[
        ~diabetes_evidence & doctor_prediabetes & lab_prediabetes,
        "stage_evidence",
    ] = "DOCTOR_AND_LAB"

    # Medication is supporting evidence, not enough alone to classify when
    # diagnosis/lab evidence is absent.
    df.loc[
        medication_support & diabetes_evidence,
        "stage_evidence",
    ] = df.loc[
        medication_support & diabetes_evidence,
        "stage_evidence",
    ].astype("string") + "_WITH_MEDICATION"

    conflict = (
        (diabetes_evidence & prediabetes_evidence)
        | ((df["clinical_stage"] == "PREDIABETES") & insulin)
        | (
            df.get(
                "normal_glycemia_by_lab_flag",
                pd.Series(False, index=df.index),
            ).fillna(False)
            & lab_diabetes
        )
    )
    df["stage_conflict_flag"] = conflict.astype("boolean")
    append_flag(df, conflict, "STAGE_EVIDENCE_CONFLICT")

    df["classification_rule_version"] = "clinical_stage_v1.0"
    return df


def completeness_score(df: pd.DataFrame, fields: list[str]) -> pd.Series:
    available = [field for field in fields if field in df.columns]
    if not available:
        return pd.Series(0.0, index=df.index)
    return df[available].notna().mean(axis=1) * 100


def lifestyle_completeness_score(df: pd.DataFrame) -> pd.Series:
    scores = []

    for index, row in df.iterrows():
        applicable_fields = [
            "vigorous_work_activity_flag",
            "moderate_work_activity_flag",
            "walk_bicycle_transport_flag",
            "vigorous_recreation_flag",
            "moderate_recreation_flag",
            "sedentary_minutes_day",
            "sleep_hours_weekday",
            "sleep_hours_weekend",
        ]

        smoking_applicable = row.get("smoking_applicable")
        alcohol_applicable = row.get("alcohol_applicable")

        if pd.isna(smoking_applicable) or bool(smoking_applicable):
            applicable_fields.append("smoked_100_cigarettes_flag")
        if pd.isna(alcohol_applicable) or bool(alcohol_applicable):
            applicable_fields.append("alcohol_ever_flag")

        existing = [f for f in applicable_fields if f in df.columns]
        if not existing:
            scores.append(0.0)
        else:
            scores.append(
                100.0 * sum(pd.notna(row.get(f)) for f in existing) / len(existing)
            )

    return pd.Series(scores, index=df.index, dtype=float)


def assign_quality_scores(
    df: pd.DataFrame,
    gold_threshold: float,
    silver_threshold: float,
) -> pd.DataFrame:
    df = df.copy()

    df["profile_completeness"] = completeness_score(df, PROFILE_FIELDS).round(1)
    df["glycemic_completeness"] = completeness_score(df, GLYCEMIC_FIELDS).round(1)
    df["vital_completeness"] = completeness_score(df, VITAL_FIELDS).round(1)
    medication_score = completeness_score(df, MEDICATION_FIELDS)
    # A valid response of no prescription use is complete information, not missing data.
    no_prescription_use = df.get(
        "prescription_medication_use_code", pd.Series(pd.NA, index=df.index)
    ).eq(2)
    medication_score = medication_score.mask(no_prescription_use, 100.0)
    df["medication_completeness"] = medication_score.round(1)
    df["lifestyle_completeness"] = lifestyle_completeness_score(df).round(1)

    df["overall_data_quality_score"] = (
        0.25 * df["profile_completeness"]
        + 0.35 * df["glycemic_completeness"]
        + 0.20 * df["vital_completeness"]
        + 0.10 * df["medication_completeness"]
        + 0.10 * df["lifestyle_completeness"]
    ).round(1)

    df["rejection_reason"] = pd.Series("", index=df.index, dtype="string")

    def add_reason(mask: pd.Series, reason: str) -> None:
        current = df.loc[mask, "rejection_reason"].fillna("")
        df.loc[mask, "rejection_reason"] = np.where(
            current.eq(""),
            reason,
            current + "|" + reason,
        )

    missing_core = pd.Series(False, index=df.index)
    for col in CORE_ID_FIELDS:
        if col not in df.columns:
            missing_core |= True
        else:
            missing_core |= df[col].isna()
    add_reason(missing_core, "MISSING_CORE_IDENTITY")

    no_stage = ~df["clinical_stage"].isin(["DIABETES", "PREDIABETES"])
    add_reason(no_stage, "NOT_TARGET_DIABETES_STAGE")

    no_glycemic_evidence = (
        df.get("hba1c_pct", pd.Series(np.nan, index=df.index)).isna()
        & df.get("fasting_glucose_mg_dl", pd.Series(np.nan, index=df.index)).isna()
        & ~df.get(
            "doctor_diagnosed_diabetes_flag",
            pd.Series(False, index=df.index),
        ).fillna(False)
        & ~df.get(
            "doctor_diagnosed_prediabetes_flag",
            pd.Series(False, index=df.index),
        ).fillna(False)
        & ~df.get(
            "diabetes_by_lab_flag",
            pd.Series(False, index=df.index),
        ).fillna(False)
        & ~df.get(
            "prediabetes_by_lab_flag",
            pd.Series(False, index=df.index),
        ).fillna(False)
    )
    add_reason(no_glycemic_evidence, "NO_GLYCEMIC_EVIDENCE")

    no_body_profile = (
        df.get("bmi", pd.Series(np.nan, index=df.index)).isna()
        & df.get("weight_kg", pd.Series(np.nan, index=df.index)).isna()
        & df.get("height_cm", pd.Series(np.nan, index=df.index)).isna()
    )
    add_reason(no_body_profile, "NO_BODY_PROFILE")

    severe_conflict = (
        df.get(
            "stage_conflict_flag",
            pd.Series(False, index=df.index),
        ).fillna(False)
        & df["glycemic_completeness"].lt(40)
    )
    add_reason(severe_conflict, "UNRESOLVED_STAGE_CONFLICT")

    below_silver = df["overall_data_quality_score"].lt(silver_threshold)
    add_reason(below_silver, "QUALITY_SCORE_BELOW_SILVER")

    rejected = df["rejection_reason"].str.len().gt(0)

    df["quality_tier"] = pd.Series("REJECTED", index=df.index, dtype="string")
    df.loc[
        ~rejected & df["overall_data_quality_score"].ge(gold_threshold),
        "quality_tier",
    ] = "GOLD"
    df.loc[
        ~rejected
        & df["overall_data_quality_score"].ge(silver_threshold)
        & df["overall_data_quality_score"].lt(gold_threshold),
        "quality_tier",
    ] = "SILVER"

    return df


def create_risk_tags(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for _, row in df.iterrows():
        patient_id = row.get("patient_id")
        tags = []

        if pd.notna(row.get("bmi")) and row.get("age_years", 0) >= 18:
            if row["bmi"] >= 30:
                tags.append("OBESITY")
            elif row["bmi"] >= 25:
                tags.append("OVERWEIGHT")

        if bool(row.get("doctor_diagnosed_hypertension_flag") is True):
            tags.append("HYPERTENSION")
        elif (
            pd.notna(row.get("sbp_mean"))
            and row["sbp_mean"] >= 130
        ) or (
            pd.notna(row.get("dbp_mean"))
            and row["dbp_mean"] >= 80
        ):
            tags.append("ELEVATED_BP_SCREEN")

        if bool(row.get("doctor_diagnosed_high_cholesterol_flag") is True):
            tags.append("HIGH_CHOLESTEROL")

        cardiovascular_fields = [
            "congestive_heart_failure_flag",
            "coronary_heart_disease_flag",
            "angina_flag",
            "heart_attack_flag",
            "stroke_flag",
        ]
        if any(row.get(field) is True for field in cardiovascular_fields):
            tags.append("CARDIOVASCULAR_DISEASE")

        if row.get("liver_condition_flag") is True:
            tags.append("LIVER_CONDITION")
        if row.get("thyroid_problem_flag") is True:
            tags.append("THYROID_CONDITION")
        if row.get("family_history_diabetes_flag") is True:
            tags.append("FAMILY_HISTORY_DIABETES")
        if row.get("smoked_100_cigarettes_flag") is True:
            tags.append("SMOKING_HISTORY")
        if row.get("taking_insulin_flag") is True:
            tags.append("INSULIN_USER")
        if row.get("taking_diabetes_pills_flag") is True:
            tags.append("DIABETES_PILLS_USER")

        if pd.notna(row.get("hba1c_pct")):
            if row["hba1c_pct"] >= 9:
                tags.append("HIGH_HBA1C")
            elif row["hba1c_pct"] < 7 and row.get("clinical_stage") == "DIABETES":
                tags.append("LOWER_HBA1C_PROFILE")

        if pd.notna(row.get("sedentary_minutes_day")) and row["sedentary_minutes_day"] >= 600:
            tags.append("HIGH_SEDENTARY_TIME")

        if not tags:
            tags.append("NO_ADDITIONAL_RISK_TAG")

        for tag in sorted(set(tags)):
            rows.append({"patient_id": patient_id, "risk_tag": tag})

    return pd.DataFrame(rows)


def split_pipe_field(value) -> list[str]:
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def explode_medications(df: pd.DataFrame) -> pd.DataFrame:
    output_rows = []

    for _, row in df.iterrows():
        names = split_pipe_field(row.get("medication_names"))
        drug_ids = split_pipe_field(row.get("medication_drug_ids"))
        indication_codes = split_pipe_field(row.get("medication_indication_codes"))
        indication_descriptions = split_pipe_field(
            row.get("medication_indication_descriptions")
        )

        max_len = max(
            len(names),
            len(drug_ids),
            len(indication_codes),
            len(indication_descriptions),
            0,
        )
        if max_len == 0:
            continue

        for position in range(max_len):
            output_rows.append(
                {
                    "patient_id": row.get("patient_id"),
                    "medication_position": position + 1,
                    "drug_name": names[position] if position < len(names) else pd.NA,
                    "drug_id": drug_ids[position] if position < len(drug_ids) else pd.NA,
                    "indication_code": (
                        indication_codes[position]
                        if position < len(indication_codes)
                        else pd.NA
                    ),
                    "indication_description": (
                        indication_descriptions[position]
                        if position < len(indication_descriptions)
                        else pd.NA
                    ),
                    "maximum_medication_days": row.get("maximum_medication_days"),
                    "medication_source": "NHANES",
                }
            )

    return pd.DataFrame(output_rows)


def select_existing(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in df.columns]
    return df[existing].copy()


def build_output_tables(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    accepted = df[df["quality_tier"].isin(["GOLD", "SILVER"])].copy()
    rejected = df[df["quality_tier"].eq("REJECTED")].copy()

    patient_profile_columns = [
        "patient_id",
        "source_dataset",
        "source_patient_id",
        "age_years",
        "sex",
        "sex_code",
        "race_ethnicity_code",
        "income_poverty_ratio",
        "weight_kg",
        "height_cm",
        "bmi",
        "bmi_calculated",
        "bmi_category_clean",
        "waist_cm",
        "clinical_stage",
        "stage_evidence",
        "stage_conflict_flag",
        "age_group_clean",
        "caregiver_mode",
        "caregiver_required_flag",
        "profile_completeness",
        "glycemic_completeness",
        "vital_completeness",
        "medication_completeness",
        "lifestyle_completeness",
        "overall_data_quality_score",
        "quality_tier",
        "quality_flags",
        "classification_rule_version",
        "derived_field_version",
        "interview_weight",
        "mec_exam_weight",
        "fasting_subsample_weight",
        "survey_psu",
        "survey_stratum",
    ]

    baseline_clinical_columns = [
        "patient_id",
        "hba1c_pct",
        "fasting_glucose_mg_dl",
        "hdl_mg_dl",
        "total_cholesterol_mg_dl",
        "triglycerides_mg_dl",
        "ldl_mg_dl",
        "sbp_mean",
        "dbp_mean",
        "pulse_mean",
        "sbp_measurement_count",
        "dbp_measurement_count",
        "pulse_measurement_count",
        "creatinine_mg_dl",
        "alt_u_l",
        "ast_u_l",
        "ggt_u_l",
        "bun_mg_dl",
        "doctor_diagnosed_diabetes_flag",
        "doctor_diagnosed_prediabetes_flag",
        "diabetes_by_lab_flag",
        "prediabetes_by_lab_flag",
        "normal_glycemia_by_lab_flag",
        "taking_insulin_flag",
        "taking_diabetes_pills_flag",
        "doctor_diagnosed_hypertension_flag",
        "taking_bp_medication_flag",
        "doctor_diagnosed_high_cholesterol_flag",
        "taking_cholesterol_medication_flag",
        "congestive_heart_failure_flag",
        "coronary_heart_disease_flag",
        "angina_flag",
        "heart_attack_flag",
        "stroke_flag",
        "thyroid_problem_flag",
        "liver_condition_flag",
        "family_history_diabetes_flag",
        "elevated_bp_screen_flag_clean",
        "source_dataset",
    ]

    lifestyle_columns = [
        "patient_id",
        "vigorous_work_activity_flag",
        "vigorous_work_days_week",
        "vigorous_work_minutes_day",
        "moderate_work_activity_flag",
        "moderate_work_days_week",
        "moderate_work_minutes_day",
        "walk_bicycle_transport_flag",
        "vigorous_recreation_flag",
        "moderate_recreation_flag",
        "sedentary_minutes_day",
        "sleep_hours_weekday",
        "sleep_hours_weekend",
        "doctor_diagnosed_sleep_disorder_flag",
        "smoked_100_cigarettes_flag",
        "current_smoking_frequency_code",
        "alcohol_ever_flag",
        "alcohol_frequency_past_year",
        "avg_drinks_per_drinking_day",
        "smoking_applicable",
        "alcohol_applicable",
        "adult_questionnaire_applicable",
        "source_dataset",
    ]

    rejection_columns = [
        "patient_id",
        "diabetes_stage",
        "clinical_stage",
        "overall_data_quality_score",
        "quality_tier",
        "rejection_reason",
        "quality_flags",
    ]

    # The master contains independently aggregated medication strings. They are
    # safe as a patient-level summary but must not be positionally exploded,
    # because doing so can misalign drug name, ID, and indication. The canonical
    # row-level medication table is rebuilt from raw P_RXQ_RX in deployment.
    medication_summary = select_existing(
        accepted,
        [
            "patient_id",
            "medication_names",
            "medication_drug_ids",
            "medication_indication_codes",
            "medication_indication_descriptions",
            "maximum_medication_days",
            "prescription_medication_use_code",
        ],
    )
    risk_tags = create_risk_tags(accepted)

    return {
        "patient_profile_clean": select_existing(accepted, patient_profile_columns),
        "patient_baseline_clinical": select_existing(
            accepted,
            baseline_clinical_columns,
        ),
        "patient_lifestyle_baseline": select_existing(accepted, lifestyle_columns),
        "patient_medication_summary": medication_summary,
        "patient_risk_tags": risk_tags,
        "gold_patients": accepted[accepted["quality_tier"].eq("GOLD")].copy(),
        "silver_patients": accepted[accepted["quality_tier"].eq("SILVER")].copy(),
        "rejected_patients": select_existing(rejected, rejection_columns),
        "all_cleaned_with_quality": df.copy(),
    }


def build_quality_summary(
    original: pd.DataFrame,
    cleaned: pd.DataFrame,
    duplicates: pd.DataFrame,
) -> dict:
    target_before = (
        original["diabetes_stage"].value_counts(dropna=False).to_dict()
        if "diabetes_stage" in original.columns
        else {}
    )

    return {
        "input_rows": int(len(original)),
        "input_columns": int(original.shape[1]),
        "duplicate_rows_detected": int(len(duplicates)),
        "rows_after_deduplication": int(len(cleaned)),
        "original_diabetes_stage_distribution": {
            str(k): int(v) for k, v in target_before.items()
        },
        "clinical_stage_distribution": {
            str(k): int(v)
            for k, v in cleaned["clinical_stage"]
            .value_counts(dropna=False)
            .items()
        },
        "quality_tier_distribution": {
            str(k): int(v)
            for k, v in cleaned["quality_tier"]
            .value_counts(dropna=False)
            .items()
        },
        "accepted_rows": int(
            cleaned["quality_tier"].isin(["GOLD", "SILVER"]).sum()
        ),
        "rejected_rows": int(
            cleaned["quality_tier"].eq("REJECTED").sum()
        ),
        "mean_quality_score": round(
            float(cleaned["overall_data_quality_score"].mean()),
            2,
        ),
        "median_quality_score": round(
            float(cleaned["overall_data_quality_score"].median()),
            2,
        ),
        "stage_conflict_rows": int(
            cleaned["stage_conflict_flag"].fillna(False).sum()
        ),
    }


def save_outputs(
    output_dir: Path,
    tables: dict[str, pd.DataFrame],
    summary: dict,
    duplicates: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    obsolete_medication_table = output_dir / "patient_medications.csv"
    if obsolete_medication_table.exists():
        obsolete_medication_table.unlink()

    for name, table in tables.items():
        destination = output_dir / f"{name}.csv"
        table.to_csv(destination, index=False, encoding="utf-8-sig")
        LOGGER.info("Saved %s (%s rows)", destination, len(table))

    duplicates.to_csv(
        output_dir / "duplicate_patient_rows.csv",
        index=False,
        encoding="utf-8-sig",
    )

    with (output_dir / "data_quality_summary.json").open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    missing_report = (
        tables["all_cleaned_with_quality"]
        .isna()
        .mean()
        .mul(100)
        .sort_values(ascending=False)
        .rename("missing_percent")
        .reset_index()
        .rename(columns={"index": "column"})
    )
    missing_report["missing_percent"] = missing_report["missing_percent"].round(2)
    missing_report.to_csv(
        output_dir / "column_missingness_report.csv",
        index=False,
        encoding="utf-8-sig",
    )


def clean_nhanes(
    input_path: Path,
    output_dir: Path,
    gold_threshold: float = 75.0,
    silver_threshold: float = 50.0,
) -> dict:
    if gold_threshold <= silver_threshold:
        raise ValueError("gold_threshold must be greater than silver_threshold.")
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    LOGGER.info("Reading %s", input_path)
    original = pd.read_csv(input_path, low_memory=False)
    original.columns = normalize_column_names(original.columns)

    normalized = normalize_types(original)
    deduplicated, duplicates = remove_duplicate_patients(normalized)
    plausible = apply_plausibility_rules(deduplicated)
    derived = recalculate_derived_fields(plausible)
    staged = determine_clinical_stage(derived)

    # Keep all rows temporarily so rejected records retain an audit trail.
    scored = assign_quality_scores(
        staged,
        gold_threshold=gold_threshold,
        silver_threshold=silver_threshold,
    )

    tables = build_output_tables(scored)
    summary = build_quality_summary(original, scored, duplicates)
    save_outputs(output_dir, tables, summary, duplicates)

    LOGGER.info("Cleaning completed.")
    LOGGER.info("Accepted rows: %s", summary["accepted_rows"])
    LOGGER.info("Rejected rows: %s", summary["rejected_rows"])
    LOGGER.info("Output directory: %s", output_dir.resolve())
    return summary


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    summary = clean_nhanes(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        gold_threshold=args.gold_threshold,
        silver_threshold=args.silver_threshold,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

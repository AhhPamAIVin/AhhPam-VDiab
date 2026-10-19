from __future__ import annotations

import json
import itertools
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "processed" / "nhanes_clean_output"
OUTPUT = ROOT / "deployment_dataset"
SOURCE_CYCLE = "NHANES_2017_MARCH_2020_PREPANDEMIC"
RECORD_TYPE = "BASELINE_SURVEY"
PIPELINE_VERSION = "baseline_deployment_v3.4_diabetes_doctor_roles"
LABEL_RULE_VERSION = "nhanes_treatment_algorithm_v1.0"
IDENTITY_SEED = 20260805
DOCTOR_COUNT = 500


def clean_text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if pd.isna(value):
        return pd.NA
    value = str(value).strip()
    return value if value else pd.NA


def add_provenance(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["data_source"] = "NHANES"
    result["source_cycle"] = SOURCE_CYCLE
    result["record_type"] = RECORD_TYPE
    result["is_synthetic"] = False
    result["pipeline_version"] = PIPELINE_VERSION
    return result


def add_synthetic_provenance(df: pd.DataFrame, record_type: str) -> pd.DataFrame:
    result = df.copy()
    result["data_source"] = "SYNTHETIC_DEMO"
    result["source_cycle"] = "NOT_APPLICABLE"
    result["record_type"] = record_type
    result["is_synthetic"] = True
    result["pipeline_version"] = PIPELINE_VERSION
    return result


def generate_unique_demo_names(count: int, seed: int) -> list[str]:
    surnames = [
        "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ",
        "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý", "Đinh",
        "Mai", "Trịnh", "Đoàn",
    ]
    middle_names = [
        "Văn", "Thị", "Minh", "Ngọc", "Thanh", "Quang", "Hải", "Gia",
        "Khánh", "Bảo", "Hoài", "Đức", "Anh", "Phương", "Xuân", "Nhật",
        "Kim", "Tuấn", "Hữu", "Diệu",
    ]
    given_names = [
        "An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hạnh", "Hiếu",
        "Hoa", "Hùng", "Hương", "Khang", "Khôi", "Lan", "Linh", "Long",
        "Mai", "Minh", "Nam", "Nga", "Ngân", "Nghĩa", "Nhung", "Phúc",
        "Phương", "Quân", "Quỳnh", "Sơn", "Tâm", "Thảo", "Thư", "Trang",
        "Trí", "Trung", "Tú", "Tuấn", "Uyên", "Việt", "Vy", "Yến",
    ]
    pool = [" ".join(parts) for parts in itertools.product(surnames, middle_names, given_names)]
    if count > len(pool):
        raise ValueError(f"Requested {count} demo names but only {len(pool)} are available")
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:count]


def add_synthetic_patient_names(profiles: pd.DataFrame) -> pd.DataFrame:
    result = profiles.sort_values("patient_id").copy()
    result["display_name"] = generate_unique_demo_names(len(result), IDENTITY_SEED)
    result["is_synthetic_identity"] = True
    result["identity_source"] = "DEMO_NAME_GENERATOR"
    return result.sort_index()


def build_caregiver_profiles(profiles: pd.DataFrame) -> pd.DataFrame:
    eligible = profiles[
        profiles["age_years"].lt(16) | profiles["age_years"].gt(70)
    ].sort_values("patient_id").copy()
    names = generate_unique_demo_names(len(eligible), IDENTITY_SEED + 1)
    caregivers = pd.DataFrame(
        {
            "caregiver_id": [f"CG-{i:05d}" for i in range(1, len(eligible) + 1)],
            "patient_id": eligible["patient_id"].astype(int).to_list(),
            "caregiver_display_name": names,
            "relationship_to_patient": [
                "PARENT_OR_LEGAL_GUARDIAN" if age < 16
                else "FAMILY_OR_DESIGNATED_CAREGIVER"
                for age in eligible["age_years"]
            ],
            "caregiver_scope": "DEMO_PROFILE_ONLY",
            "relationship_status": "DEMO_PLACEHOLDER",
            "is_synthetic_identity": True,
            "identity_source": "DEMO_NAME_GENERATOR",
        }
    )
    return add_provenance(caregivers)


def build_doctor_data(profiles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    routing_rules = [
        (profiles["age_years"].lt(18), "PEDIATRIC_ENDOCRINOLOGIST", "PATIENT_AGE_UNDER_18"),
        (profiles["age_years"].gt(70), "GERIATRIC_DIABETES_PHYSICIAN", "PATIENT_AGE_OVER_70"),
        (
            profiles["age_years"].between(18, 70, inclusive="both")
            & profiles["project_diabetes_label"].eq("TYPE_2"),
            "ENDOCRINOLOGIST_DIABETOLOGIST",
            "TYPE_2_ALGORITHM_LABEL",
        ),
        (
            profiles["age_years"].between(18, 70, inclusive="both")
            & profiles["project_diabetes_label"].eq("PREDIABETES"),
            "PRIMARY_CARE_DIABETES_PHYSICIAN",
            "PREDIABETES_BASELINE_LABEL",
        ),
    ]
    patient_groups = {
        specialty: profiles.loc[mask, "patient_id"].astype(int).tolist()
        for mask, specialty, _ in routing_rules
    }
    raw_allocations = {
        specialty: len(patient_ids) * DOCTOR_COUNT / len(profiles)
        for specialty, patient_ids in patient_groups.items()
    }
    specialty_counts = {
        specialty: max(1, int(raw_count))
        for specialty, raw_count in raw_allocations.items()
    }
    remaining = DOCTOR_COUNT - sum(specialty_counts.values())
    allocation_order = sorted(
        specialty_counts,
        key=lambda specialty: raw_allocations[specialty] - int(raw_allocations[specialty]),
        reverse=True,
    )
    for index in range(remaining):
        specialty_counts[allocation_order[index % len(allocation_order)]] += 1

    names = generate_unique_demo_names(DOCTOR_COUNT, IDENTITY_SEED + 2)
    rng = random.Random(IDENTITY_SEED + 3)
    specialty_pool = [
        specialty
        for specialty, count in specialty_counts.items()
        for _ in range(count)
    ]
    rng.shuffle(specialty_pool)
    doctors = pd.DataFrame(
        {
            "doctor_id": [f"DR-{i:04d}" for i in range(1, DOCTOR_COUNT + 1)],
            "doctor_display_name": names,
            "specialty": specialty_pool,
            "profile_status": "DEMO_ACTIVE",
            "clinical_role_scope": "DEMO_DIABETES_CARE_ASSIGNMENT",
            "is_synthetic_identity": True,
            "identity_source": "DEMO_NAME_GENERATOR",
        }
    )
    doctors = add_synthetic_provenance(doctors, "DEMO_DOCTOR_PROFILE")

    doctor_ids_by_specialty = {
        specialty: doctors.loc[doctors["specialty"].eq(specialty), "doctor_id"].tolist()
        for specialty in specialty_counts
    }
    for ids in doctor_ids_by_specialty.values():
        rng.shuffle(ids)

    assignments = []
    for mask, specialty, reason in routing_rules:
        patient_ids = profiles.loc[mask, "patient_id"].astype(int).tolist()
        rng.shuffle(patient_ids)
        doctor_ids = doctor_ids_by_specialty[specialty]
        for index, patient_id in enumerate(patient_ids):
            assignments.append(
                {
                    "assignment_id": f"ASG-{patient_id}",
                    "patient_id": patient_id,
                    "doctor_id": doctor_ids[index % len(doctor_ids)],
                    "assignment_role": "PRIMARY_DIABETES_TREATING_PHYSICIAN",
                    "assignment_reason": reason,
                    "assignment_status": "DEMO_PLACEHOLDER",
                }
            )
    assignment_df = pd.DataFrame(assignments).sort_values("patient_id").reset_index(drop=True)
    assignment_df = add_synthetic_provenance(assignment_df, "DEMO_DOCTOR_ASSIGNMENT")
    return doctors, assignment_df


def write_csv(df: pd.DataFrame, name: str) -> None:
    df.to_csv(OUTPUT / name, index=False, encoding="utf-8-sig", na_rep="")


def build_field_lineage() -> pd.DataFrame:
    rows = [
        ("patient_profiles", "patient_id", "P_DEMO", "SEQN", "Direct identifier mapping"),
        ("patient_profiles", "age_years", "P_DEMO", "RIDAGEYR", "Direct mapping"),
        ("patient_profiles", "sex", "P_DEMO", "RIAGENDR", "Code 1=Male, 2=Female"),
        ("patient_profiles", "weight_kg", "P_BMX", "BMXWT", "Direct mapping; kg"),
        ("patient_profiles", "height_cm", "P_BMX", "BMXHT", "Direct mapping; cm"),
        ("patient_profiles", "bmi", "P_BMX", "BMXBMI", "Direct mapping; kg/m2"),
        ("patient_profiles", "interview_weight", "P_DEMO", "WTINTPRP", "Survey interview weight"),
        ("patient_profiles", "mec_exam_weight", "P_DEMO", "WTMECPRP", "MEC examination weight"),
        ("patient_profiles", "fasting_subsample_weight", "P_GLU", "WTSAFPRP", "Fasting subsample weight"),
        ("patient_profiles", "survey_psu", "P_DEMO", "SDMVPSU", "Masked variance pseudo-PSU"),
        ("patient_profiles", "survey_stratum", "P_DEMO", "SDMVSTRA", "Masked variance stratum"),
        ("patient_profiles", "display_name", "DEMO_NAME_GENERATOR", "", "Deterministic synthetic Vietnamese-style demo name"),
        ("caregiver_profiles", "caregiver_id", "DEMO_PROFILE_GENERATOR", "", "Generated synthetic caregiver identifier"),
        ("caregiver_profiles", "caregiver_display_name", "DEMO_NAME_GENERATOR", "", "Deterministic synthetic Vietnamese-style demo name"),
        ("caregiver_profiles", "relationship_to_patient", "P_DEMO", "RIDAGEYR", "Demo relationship category based only on age <16 or >70"),
        ("doctor_profiles", "doctor_id", "DEMO_DOCTOR_GENERATOR", "", "Generated synthetic doctor identifier"),
        ("doctor_profiles", "doctor_display_name", "DEMO_NAME_GENERATOR", "", "Deterministic synthetic Vietnamese-style demo name"),
        ("doctor_profiles", "specialty", "DEMO_DOCTOR_GENERATOR", "", "Synthetic specialty allocation; not a real credential"),
        ("patient_doctor_assignments", "doctor_id", "DEMO_ASSIGNMENT_ALGORITHM", "", "Balanced deterministic assignment by age and project label"),
        ("patient_baseline_clinical", "hba1c_pct", "P_GHB", "LBXGH", "Direct mapping; percent"),
        ("patient_baseline_clinical", "fasting_glucose_mg_dl", "P_GLU", "LBXGLU", "Direct mapping; mg/dL"),
        ("patient_baseline_clinical", "hdl_mg_dl", "P_HDL", "LBDHDD", "Direct mapping; mg/dL"),
        ("patient_baseline_clinical", "total_cholesterol_mg_dl", "P_TCHOL", "LBXTC", "Direct mapping; mg/dL"),
        ("patient_baseline_clinical", "triglycerides_mg_dl", "P_TRIGLY", "LBXTR", "Direct mapping; mg/dL"),
        ("patient_baseline_clinical", "ldl_mg_dl", "P_TRIGLY", "LBDLDL", "Direct mapping; mg/dL"),
        ("project_diabetes_cohort", "project_diabetes_label", "P_DIQ/P_GHB/P_GLU", "DIQ010,DID040,DIQ050,DID060,DIQ060U,DIQ070,LBXGH,LBXGLU", f"Derived by {LABEL_RULE_VERSION}"),
        ("patient_medications", "drug_name", "P_RXQ_RX", "RXDDRUG", "Direct row-level mapping; empty names excluded"),
        ("patient_medications", "drug_id", "P_RXQ_RX", "RXDDRGID", "Direct row-level mapping"),
        ("patient_medications", "indication_code", "P_RXQ_RX", "RXDRSC1", "Direct row-level mapping"),
    ]
    return pd.DataFrame(
        rows,
        columns=["target_table", "target_field", "source_dataset", "source_variable", "transformation"],
    )


def load_patient_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    profiles = pd.read_csv(SOURCE / "patient_profile_clean.csv", low_memory=False)
    clinical = pd.read_csv(SOURCE / "patient_baseline_clinical.csv", low_memory=False)
    lifestyle = pd.read_csv(SOURCE / "patient_lifestyle_baseline.csv", low_memory=False)

    # clinical_stage describes evidence of diabetes/prediabetes, not diabetes type.
    profiles = profiles.rename(columns={"clinical_stage": "diabetes_status"})
    profiles["diabetes_type"] = profiles["diabetes_status"].map(
        {"DIABETES": "UNKNOWN", "PREDIABETES": "NOT_APPLICABLE"}
    ).fillna("UNKNOWN")
    profiles["diabetes_type_source"] = "NOT_AVAILABLE_IN_SOURCE"

    return tuple(add_provenance(df) for df in (profiles, clinical, lifestyle))


def add_project_diabetes_labels(
    profiles: pd.DataFrame,
    clinical: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add traceable project labels without claiming a confirmed diabetes type."""
    questionnaire = pd.read_sas(ROOT / "P_DIQ.xpt", format="xport", encoding=None)
    questionnaire = questionnaire[
        ["SEQN", "DIQ010", "DID040", "DIQ050", "DID060", "DIQ060U", "DIQ070"]
    ].rename(
        columns={
            "SEQN": "patient_id",
            "DIQ010": "source_doctor_diabetes_code",
            "DID040": "source_age_at_diabetes_diagnosis",
            "DIQ050": "source_current_insulin_code",
            "DID060": "source_insulin_duration_value",
            "DIQ060U": "source_insulin_duration_unit_code",
            "DIQ070": "source_diabetes_pills_code",
        }
    )
    questionnaire["patient_id"] = questionnaire["patient_id"].astype(int)

    evidence_columns = [
        "patient_id",
        "doctor_diagnosed_prediabetes_flag",
        "diabetes_by_lab_flag",
        "prediabetes_by_lab_flag",
    ]
    evidence = clinical[evidence_columns].merge(
        questionnaire, on="patient_id", how="left", validate="one_to_one"
    )
    result = profiles.merge(evidence, on="patient_id", how="left", validate="one_to_one")

    result["project_diabetes_label"] = "EXCLUDE_UNCLASSIFIED"
    result["label_confidence"] = "LOW"
    result["label_basis"] = "INSUFFICIENT_OR_INVALID_CLASSIFICATION_EVIDENCE"
    result["label_is_inferred"] = True
    result["classification_method"] = "NHANES_TREATMENT_ALGORITHM"
    result["classification_confirmed"] = False
    result["label_rule_version"] = LABEL_RULE_VERSION

    prediabetes = result["diabetes_status"].eq("PREDIABETES")
    reported_borderline = result["source_doctor_diabetes_code"].eq(3)
    doctor_prediabetes = (
        result["doctor_diagnosed_prediabetes_flag"].eq(True) | reported_borderline
    )
    lab_prediabetes = result["prediabetes_by_lab_flag"].eq(True)
    result.loc[prediabetes, "project_diabetes_label"] = "PREDIABETES"
    result.loc[prediabetes, "label_confidence"] = "MODERATE"
    result.loc[prediabetes & doctor_prediabetes, "label_basis"] = (
        "DOCTOR_REPORTED_PREDIABETES"
    )
    result.loc[prediabetes & reported_borderline, "label_basis"] = (
        "DOCTOR_REPORTED_BORDERLINE_DIABETES"
    )
    result.loc[prediabetes & lab_prediabetes, "label_basis"] = (
        "LAB_PREDIABETES_RANGE"
    )
    result.loc[
        prediabetes & doctor_prediabetes & lab_prediabetes,
        ["label_confidence", "label_basis"],
    ] = ["HIGH", "DOCTOR_REPORTED_AND_LAB_PREDIABETES"]

    diabetes = result["diabetes_status"].eq("DIABETES")
    doctor_diabetes = result["source_doctor_diabetes_code"].eq(1)
    valid_treatment = (
        result["source_current_insulin_code"].isin([1, 2])
        & result["source_diabetes_pills_code"].isin([1, 2])
    )
    no_stage_conflict = ~result["stage_conflict_flag"].fillna(False)

    # Published NHANES treatment-based T2 classification: no current insulin,
    # or concurrent insulin plus oral diabetes medication. Age is not a rule.
    type_2 = (
        diabetes
        & doctor_diabetes
        & valid_treatment
        & no_stage_conflict
        & (
            result["source_current_insulin_code"].eq(2)
            | (
                result["source_current_insulin_code"].eq(1)
                & result["source_diabetes_pills_code"].eq(1)
            )
        )
    )
    result.loc[type_2, "project_diabetes_label"] = "TYPE_2"
    result.loc[type_2, "label_confidence"] = "MODERATE"
    result.loc[type_2, "label_basis"] = "DOCTOR_DIAGNOSIS|T2_TREATMENT_PATTERN"

    # Insulin-only patients require a valid time from diagnosis to insulin start.
    insulin_only = (
        diabetes
        & doctor_diabetes
        & valid_treatment
        & no_stage_conflict
        & result["source_current_insulin_code"].eq(1)
        & result["source_diabetes_pills_code"].eq(2)
    )
    duration_value = result["source_insulin_duration_value"].where(
        ~result["source_insulin_duration_value"].isin([777, 999])
    )
    duration_years = pd.Series(pd.NA, index=result.index, dtype="Float64")
    duration_years = duration_years.mask(duration_value.eq(666), 0.0)
    duration_years = duration_years.mask(
        result["source_insulin_duration_unit_code"].eq(1), duration_value / 12.0
    )
    duration_years = duration_years.mask(
        result["source_insulin_duration_unit_code"].eq(2), duration_value
    )
    years_since_diagnosis = (
        result["age_years"] - result["source_age_at_diabetes_diagnosis"]
    )
    insulin_start_delay_years = years_since_diagnosis - duration_years
    valid_timing = (
        insulin_only
        & result["source_age_at_diabetes_diagnosis"].between(1, 80)
        & duration_years.notna()
        & insulin_start_delay_years.ge(0)
    )
    type_1 = valid_timing & insulin_start_delay_years.le(1)
    possible_type_2 = valid_timing & insulin_start_delay_years.gt(1)

    result.loc[type_1, "project_diabetes_label"] = "TYPE_1"
    result.loc[type_1, "label_confidence"] = "MODERATE"
    result.loc[type_1, "label_basis"] = (
        "DOCTOR_DIAGNOSIS|INSULIN_ONLY|INSULIN_STARTED_WITHIN_1_YEAR"
    )
    result.loc[possible_type_2, "project_diabetes_label"] = "EXCLUDE_POSSIBLE_TYPE_2"
    result.loc[possible_type_2, "label_basis"] = (
        "INSULIN_ONLY|INSULIN_STARTED_MORE_THAN_1_YEAR_AFTER_DIAGNOSIS"
    )

    result["insulin_duration_years_derived"] = duration_years
    result["insulin_start_delay_years_derived"] = insulin_start_delay_years

    # Keep prediabetes only when there is no conflicting stage evidence.
    result.loc[prediabetes & ~no_stage_conflict, "project_diabetes_label"] = (
        "EXCLUDE_STAGE_CONFLICT"
    )
    result.loc[prediabetes & ~no_stage_conflict, "label_basis"] = "STAGE_CONFLICT"

    # Explicitly label other diabetes exclusions for the audit layer.
    invalid_diabetes = (
        result["diabetes_status"].eq("DIABETES")
        & ~result["project_diabetes_label"].isin(
            ["TYPE_1", "TYPE_2", "EXCLUDE_POSSIBLE_TYPE_2"]
        )
    )
    result.loc[invalid_diabetes, "project_diabetes_label"] = "EXCLUDE_INVALID_TYPE_DATA"

    # Keep the minimum source evidence needed to audit the assigned label.
    label_columns = [
        "patient_id",
        "project_diabetes_label",
        "label_confidence",
        "label_basis",
        "label_is_inferred",
        "classification_method",
        "classification_confirmed",
        "label_rule_version",
        "source_doctor_diabetes_code",
        "source_age_at_diabetes_diagnosis",
        "source_current_insulin_code",
        "source_insulin_duration_value",
        "source_insulin_duration_unit_code",
        "source_diabetes_pills_code",
        "insulin_duration_years_derived",
        "insulin_start_delay_years_derived",
    ]
    labels = add_provenance(result[label_columns])
    # TYPE_1 remains available in the audit classification layer, but is not
    # deployable in the final project dataset.
    project_cohort = labels[
        labels["project_diabetes_label"].isin(["PREDIABETES", "TYPE_2"])
    ].copy()

    # Put only the label outcome on the profile; detailed evidence stays normalized.
    profile_label_columns = [
        "patient_id",
        "project_diabetes_label",
        "label_confidence",
        "label_basis",
        "label_is_inferred",
        "classification_method",
        "classification_confirmed",
        "label_rule_version",
    ]
    profiles = profiles.merge(
        labels[profile_label_columns], on="patient_id", how="left", validate="one_to_one"
    )
    profiles["diabetes_type"] = profiles["project_diabetes_label"].map(
        {"TYPE_1": "TYPE_1", "TYPE_2": "TYPE_2", "PREDIABETES": "NOT_APPLICABLE"}
    ).fillna("UNKNOWN")
    profiles["diabetes_type_source"] = profiles["project_diabetes_label"].map(
        {
            "TYPE_1": "NHANES_TREATMENT_ALGORITHM",
            "TYPE_2": "NHANES_TREATMENT_ALGORITHM",
            "PREDIABETES": "NOT_APPLICABLE",
        }
    ).fillna("NOT_AVAILABLE_IN_SOURCE")
    return profiles, project_cohort


def load_medication_tables(
    accepted_patient_ids: set[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_sas(ROOT / "P_RXQ_RX.xpt", format="xport", encoding=None)
    raw = raw[raw["SEQN"].astype("Int64").isin(accepted_patient_ids)].copy()
    raw["patient_id"] = raw["SEQN"].astype(int)
    raw["drug_name"] = raw["RXDDRUG"].map(clean_text)

    status_labels = {
        1: "YES",
        2: "NO",
        7: "REFUSED",
        9: "DONT_KNOW",
    }
    status = raw[["patient_id", "RXDUSE"]].drop_duplicates().copy()
    conflicting = status.groupby("patient_id")["RXDUSE"].nunique().gt(1)
    if conflicting.any():
        ids = conflicting[conflicting].index.tolist()[:10]
        raise ValueError(f"Conflicting prescription-use responses for patients: {ids}")
    status = status.drop_duplicates("patient_id")
    status = status.rename(columns={"RXDUSE": "prescription_use_code"})
    status["prescription_use_status"] = (
        status["prescription_use_code"].map(status_labels).fillna("UNKNOWN_CODE")
    )
    status = add_provenance(status)

    # A questionnaire row with an empty drug name is not a medication entity.
    medications = raw[raw["drug_name"].notna()].copy()
    medications = medications.rename(
        columns={
            "RXDDRGID": "drug_id",
            "RXDRSC1": "indication_code",
            "RXDRSD1": "indication_description",
            "RXDDAYS": "days_taken",
            "RXDUSE": "prescription_use_code",
        }
    )
    for column in ["drug_id", "indication_code", "indication_description"]:
        medications[column] = medications[column].map(clean_text)
    keep = [
        "patient_id",
        "drug_name",
        "drug_id",
        "indication_code",
        "indication_description",
        "days_taken",
        "prescription_use_code",
    ]
    medications = medications[keep].reset_index(drop=True)
    medications.insert(
        1,
        "medication_record_id",
        [f"NHANES-RX-{i:07d}" for i in range(1, len(medications) + 1)],
    )
    medications = add_provenance(medications)
    medications["source_note"] = (
        "Baseline self-reported prescription record; dose, schedule, and current "
        "active status are not available and must not be inferred."
    )
    return status, medications


def validate(tables: dict[str, pd.DataFrame]) -> dict:
    patient_ids = set(tables["patient_profiles"]["patient_id"])
    one_row_per_patient = {
        "patient_profiles",
        "patient_baseline_clinical",
        "patient_lifestyle_baseline",
        "patient_prescription_status",
        "patient_doctor_assignments",
    }
    report: dict[str, object] = {"tables": {}, "checks": {}}

    for name, df in tables.items():
        has_patient_id = "patient_id" in df.columns
        item = {
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "duplicate_full_rows": int(df.duplicated().sum()),
            "null_patient_id": int(df["patient_id"].isna().sum()) if has_patient_id else 0,
            "orphan_patient_id": int((~df["patient_id"].isin(patient_ids)).sum()) if has_patient_id else 0,
        }
        if name in one_row_per_patient:
            item["duplicate_patient_id"] = int(df["patient_id"].duplicated().sum())
            item["missing_patient_rows"] = int(len(patient_ids - set(df["patient_id"])))
        report["tables"][name] = item

    medication = tables["patient_medications"]
    checks = report["checks"]
    checks["medication_rows_without_drug_name"] = int(
        medication["drug_name"].isna().sum()
    )
    checks["unexpected_synthetic_rows"] = int(
        sum(
            df["is_synthetic"].fillna(False).astype(bool).sum()
            for name, df in tables.items()
            if name not in {"doctor_profiles", "patient_doctor_assignments"}
        )
    )
    checks["classification_incorrectly_marked_confirmed"] = int(
        tables["patient_profiles"]["classification_confirmed"].fillna(False).sum()
    )
    checks["invalid_project_labels"] = int(
        (~tables["patient_profiles"]["project_diabetes_label"].isin(
            ["PREDIABETES", "TYPE_2"]
        )).sum()
    )
    checks["duplicate_project_cohort_patient_id"] = int(
        tables["project_diabetes_cohort"]["patient_id"].duplicated().sum()
    )
    checks["project_cohort_stage_conflicts"] = int(
        tables["patient_profiles"]["stage_conflict_flag"].fillna(False).sum()
    )
    checks["missing_core_survey_design"] = int(
        tables["patient_profiles"]
        [["interview_weight", "mec_exam_weight", "survey_psu", "survey_stratum"]]
        .isna()
        .any(axis=1)
        .sum()
    )
    checks["duplicate_medication_record_id"] = int(
        tables["patient_medications"]["medication_record_id"].duplicated().sum()
    )
    profiles = tables["patient_profiles"]
    caregivers = tables["caregiver_profiles"]
    expected_caregiver_ids = set(
        profiles.loc[
            profiles["age_years"].lt(16) | profiles["age_years"].gt(70),
            "patient_id",
        ]
    )
    actual_caregiver_ids = set(caregivers["patient_id"])
    checks["missing_patient_display_name"] = int(profiles["display_name"].isna().sum())
    checks["duplicate_patient_display_name"] = int(profiles["display_name"].duplicated().sum())
    checks["duplicate_caregiver_id"] = int(caregivers["caregiver_id"].duplicated().sum())
    checks["duplicate_caregiver_patient_id"] = int(caregivers["patient_id"].duplicated().sum())
    checks["missing_caregiver_display_name"] = int(caregivers["caregiver_display_name"].isna().sum())
    checks["duplicate_caregiver_display_name"] = int(caregivers["caregiver_display_name"].duplicated().sum())
    checks["eligible_patients_without_caregiver"] = len(expected_caregiver_ids - actual_caregiver_ids)
    checks["ineligible_patients_with_caregiver"] = len(actual_caregiver_ids - expected_caregiver_ids)
    checks["patient_identity_not_marked_synthetic"] = int((~profiles["is_synthetic_identity"]).sum())
    checks["caregiver_identity_not_marked_synthetic"] = int((~caregivers["is_synthetic_identity"]).sum())
    doctors = tables["doctor_profiles"]
    assignments = tables["patient_doctor_assignments"]
    checks["doctor_count_not_500"] = abs(len(doctors) - DOCTOR_COUNT)
    checks["duplicate_doctor_id"] = int(doctors["doctor_id"].duplicated().sum())
    checks["missing_doctor_name"] = int(doctors["doctor_display_name"].isna().sum())
    checks["duplicate_doctor_name"] = int(doctors["doctor_display_name"].duplicated().sum())
    checks["doctor_not_marked_synthetic"] = int((~doctors["is_synthetic"]).sum())
    checks["duplicate_assignment_id"] = int(assignments["assignment_id"].duplicated().sum())
    checks["assignment_with_unknown_doctor"] = int(
        (~assignments["doctor_id"].isin(set(doctors["doctor_id"]))).sum()
    )
    checks["doctor_assignment_not_marked_synthetic"] = int((~assignments["is_synthetic"]).sum())
    assigned_specialty = assignments[["patient_id", "doctor_id"]].merge(
        doctors[["doctor_id", "specialty"]], on="doctor_id", how="left", validate="many_to_one"
    ).merge(
        profiles[["patient_id", "age_years", "project_diabetes_label"]],
        on="patient_id",
        how="left",
        validate="one_to_one",
    )
    expected_specialty = pd.Series("PRIMARY_CARE_DIABETES_PHYSICIAN", index=assigned_specialty.index)
    expected_specialty.loc[assigned_specialty["project_diabetes_label"].eq("TYPE_2")] = "ENDOCRINOLOGIST_DIABETOLOGIST"
    expected_specialty.loc[assigned_specialty["age_years"].gt(70)] = "GERIATRIC_DIABETES_PHYSICIAN"
    expected_specialty.loc[assigned_specialty["age_years"].lt(18)] = "PEDIATRIC_ENDOCRINOLOGIST"
    checks["doctor_specialty_routing_mismatch"] = int(
        assigned_specialty["specialty"].ne(expected_specialty).sum()
    )

    failures = []
    for name, item in report["tables"].items():
        for check in ["null_patient_id", "orphan_patient_id", "duplicate_full_rows"]:
            if item[check] != 0:
                failures.append(f"{name}.{check}")
        if name in one_row_per_patient:
            for check in ["duplicate_patient_id", "missing_patient_rows"]:
                if item[check] != 0:
                    failures.append(f"{name}.{check}")
    failures.extend(key for key, value in checks.items() if value != 0)
    report["status"] = "PASS" if not failures else "FAIL"
    report["failed_checks"] = failures
    return report


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    profiles, clinical, lifestyle = load_patient_tables()
    profiles, project_cohort = add_project_diabetes_labels(profiles, clinical)
    source_profile_rows = len(profiles)
    eligible_ids = set(project_cohort["patient_id"].astype(int))

    # Deployment is intentionally restricted to labels usable by the project.
    # Uncertain diabetes-type records remain in the processed/audit layer only.
    profiles = profiles[profiles["patient_id"].isin(eligible_ids)].copy()
    clinical = clinical[clinical["patient_id"].isin(eligible_ids)].copy()
    lifestyle = lifestyle[lifestyle["patient_id"].isin(eligible_ids)].copy()
    profiles = add_synthetic_patient_names(profiles)
    caregiver_profiles = build_caregiver_profiles(profiles)
    doctor_profiles, patient_doctor_assignments = build_doctor_data(profiles)
    accepted_ids = set(profiles["patient_id"].astype(int))
    prescription_status, medications = load_medication_tables(accepted_ids)

    tables = {
        "patient_profiles": profiles,
        "patient_baseline_clinical": clinical,
        "patient_lifestyle_baseline": lifestyle,
        "patient_prescription_status": prescription_status,
        "patient_medications": medications,
        "project_diabetes_cohort": project_cohort,
        "caregiver_profiles": caregiver_profiles,
        "doctor_profiles": doctor_profiles,
        "patient_doctor_assignments": patient_doctor_assignments,
    }
    report = validate(tables)
    if report["status"] != "PASS":
        raise ValueError(f"Deployment validation failed: {report['failed_checks']}")

    # Remove known outputs from the former simulator/mixed-data builder so a
    # consumer cannot accidentally load stale or synthetic project data.
    obsolete_outputs = [
        "patient_medications_corrected.csv",
        "demo_patients.csv",
        "demo_thresholds.csv",
        "glucose_records.csv",
        "meal_records.csv",
        "activity_records.csv",
        "medication_adherence.csv",
        "alerts.csv",
    ]
    for name in obsolete_outputs:
        path = OUTPUT / name
        if path.exists():
            path.unlink()

    for name, table in tables.items():
        write_csv(table, f"{name}.csv")
    write_csv(build_field_lineage(), "field_lineage.csv")

    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "dataset_version": "3.4.0-diabetes-doctor-roles",
        "generated_at_utc": generated_at,
        "source": "NHANES 2017-March 2020 pre-pandemic",
        "source_cycle": SOURCE_CYCLE,
        "scope": "Observed/derived NHANES baseline data with explicitly synthetic demo identities",
        "contains_synthetic_clinical_data": False,
        "contains_synthetic_identity_data": True,
        "identity_generation_seed": IDENTITY_SEED,
        "caregiver_age_rule": "age_years < 16 OR age_years > 70",
        "synthetic_doctor_count": DOCTOR_COUNT,
        "doctor_assignment_rule": "one synthetic primary diabetes-treating physician per patient; under 18 pediatric endocrinology, over 70 geriatric diabetes care, adult Type 2 endocrinology/diabetology, adult prediabetes diabetes-trained primary care",
        "source_accepted_profiles_before_project_label_filter": source_profile_rows,
        "excluded_non_deployable_classification_profiles": source_profile_rows - len(profiles),
        "included_project_labels": ["PREDIABETES", "TYPE_2"],
        "documentation_files": ["README.md", "field_lineage.csv", "quality_report.json"],
        "tables": {name: int(len(df)) for name, df in tables.items()},
        "limitations": [
            "NHANES observations are cross-sectional baseline survey data, not daily logs.",
            "TYPE_2 is a treatment-algorithm classification, not a clinically confirmed diagnosis; TYPE_1 records are excluded from deployment.",
            "Medication dose, schedule, prescriber, and current active status are unavailable.",
            "Names and caregiver profiles are synthetic demo identities, not NHANES observations, verified real-world relationships, or consent records.",
            "Doctor profiles and assignments are synthetic demo records, not real clinicians, credentials, facilities, or treatment relationships.",
            "No contact details, license numbers, care plan, health goal, or alert threshold is included.",
            "Survey weights, PSU, and strata are included, but analyses must select the weight appropriate to the questionnaire, MEC, or fasting subsample.",
        ],
    }
    (OUTPUT / "quality_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    readme = """# NHANES baseline deployment dataset

This folder contains NHANES baseline observations, transparently derived fields, and
explicitly labelled synthetic demo identities. Clinical, glucose, meal, activity,
adherence, alert, and care-plan data are not simulated. Doctor profiles and assignments
are synthetic demo entities and are explicitly labelled as such.

`patient_profiles.display_name` is synthetic. `caregiver_profiles.csv` contains one
synthetic demo caregiver for each patient with `age_years < 16` or `age_years > 70`.
These records do not assert a real relationship or consent, and contain no contact data.

`doctor_profiles.csv` contains exactly 500 synthetic demo doctors.
`patient_doctor_assignments.csv` assigns one demo doctor to each deployment patient using
age and project label routing: pediatric endocrinology for age under 18, geriatric
diabetes care for age over 70, endocrinology/diabetology for adult TYPE_2, and
diabetes-trained primary care for adult PREDIABETES. It does not assert a real
clinician, credential, facility, or treatment relationship.

`diabetes_status` is an evidence-based phenotype from the cleaning pipeline.
Confirmed diabetes type is not available in the source. Deployment retains only
`PREDIABETES` and algorithm-classified `TYPE_2`; every `TYPE_1` record is excluded.
TYPE_2 is not a confirmed diagnosis. Possible Type 2, invalid treatment data, and records
with conflicting stage evidence are excluded. Medication rows
contain only named drugs; prescription questionnaire status is stored separately.
"""
    (OUTPUT / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    main()

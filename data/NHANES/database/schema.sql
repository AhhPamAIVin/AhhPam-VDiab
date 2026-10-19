BEGIN;

CREATE SCHEMA IF NOT EXISTS diabetes;
SET search_path TO diabetes, public;

CREATE TABLE patients (
    patient_id BIGINT PRIMARY KEY,
    source_dataset TEXT NOT NULL,
    source_patient_id BIGINT NOT NULL UNIQUE,
    age_years NUMERIC(5,1) NOT NULL CHECK (age_years BETWEEN 0 AND 120),
    sex TEXT NOT NULL CHECK (sex IN ('Male', 'Female')),
    sex_code NUMERIC NOT NULL,
    race_ethnicity_code NUMERIC NOT NULL,
    income_poverty_ratio NUMERIC,
    weight_kg NUMERIC CHECK (weight_kg > 0),
    height_cm NUMERIC CHECK (height_cm > 0),
    bmi NUMERIC CHECK (bmi > 0),
    bmi_calculated NUMERIC CHECK (bmi_calculated > 0),
    bmi_category_clean TEXT,
    waist_cm NUMERIC CHECK (waist_cm > 0),
    diabetes_status TEXT NOT NULL CHECK (diabetes_status IN ('DIABETES', 'PREDIABETES')),
    stage_evidence TEXT NOT NULL,
    stage_conflict_flag BOOLEAN NOT NULL DEFAULT FALSE,
    age_group_clean TEXT NOT NULL,
    caregiver_mode TEXT NOT NULL,
    caregiver_required_flag BOOLEAN NOT NULL,
    profile_completeness NUMERIC NOT NULL CHECK (profile_completeness BETWEEN 0 AND 100),
    glycemic_completeness NUMERIC NOT NULL CHECK (glycemic_completeness BETWEEN 0 AND 100),
    vital_completeness NUMERIC NOT NULL CHECK (vital_completeness BETWEEN 0 AND 100),
    medication_completeness NUMERIC NOT NULL CHECK (medication_completeness BETWEEN 0 AND 100),
    lifestyle_completeness NUMERIC NOT NULL CHECK (lifestyle_completeness BETWEEN 0 AND 100),
    overall_data_quality_score NUMERIC NOT NULL CHECK (overall_data_quality_score BETWEEN 0 AND 100),
    quality_tier TEXT NOT NULL,
    quality_flags TEXT,
    classification_rule_version TEXT NOT NULL,
    derived_field_version TEXT NOT NULL,
    interview_weight DOUBLE PRECISION NOT NULL CHECK (interview_weight >= 0),
    mec_exam_weight DOUBLE PRECISION NOT NULL CHECK (mec_exam_weight >= 0),
    fasting_subsample_weight DOUBLE PRECISION CHECK (fasting_subsample_weight >= 0),
    survey_psu NUMERIC NOT NULL,
    survey_stratum NUMERIC NOT NULL,
    diabetes_type TEXT NOT NULL CHECK (diabetes_type IN ('TYPE_2', 'NOT_APPLICABLE')),
    diabetes_type_source TEXT NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    pipeline_version TEXT NOT NULL,
    project_diabetes_label TEXT NOT NULL CHECK (project_diabetes_label IN ('PREDIABETES', 'TYPE_2')),
    label_confidence TEXT NOT NULL CHECK (label_confidence IN ('HIGH', 'MODERATE')),
    label_basis TEXT NOT NULL,
    label_is_inferred BOOLEAN NOT NULL,
    classification_method TEXT NOT NULL,
    classification_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    label_rule_version TEXT NOT NULL,
    display_name TEXT NOT NULL UNIQUE,
    is_synthetic_identity BOOLEAN NOT NULL DEFAULT TRUE,
    identity_source TEXT NOT NULL,
    CHECK (classification_confirmed = FALSE),
    CHECK (is_synthetic = FALSE),
    CHECK (is_synthetic_identity = TRUE)
);

CREATE TABLE patient_baseline_clinical (
    patient_id BIGINT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
    hba1c_pct NUMERIC CHECK (hba1c_pct >= 0),
    fasting_glucose_mg_dl NUMERIC CHECK (fasting_glucose_mg_dl >= 0),
    hdl_mg_dl NUMERIC CHECK (hdl_mg_dl >= 0),
    total_cholesterol_mg_dl NUMERIC CHECK (total_cholesterol_mg_dl >= 0),
    triglycerides_mg_dl NUMERIC CHECK (triglycerides_mg_dl >= 0),
    ldl_mg_dl NUMERIC CHECK (ldl_mg_dl >= 0),
    sbp_mean NUMERIC CHECK (sbp_mean >= 0),
    dbp_mean NUMERIC CHECK (dbp_mean >= 0),
    pulse_mean NUMERIC CHECK (pulse_mean >= 0),
    sbp_measurement_count SMALLINT NOT NULL CHECK (sbp_measurement_count BETWEEN 0 AND 3),
    dbp_measurement_count SMALLINT NOT NULL CHECK (dbp_measurement_count BETWEEN 0 AND 3),
    pulse_measurement_count SMALLINT NOT NULL CHECK (pulse_measurement_count BETWEEN 0 AND 3),
    creatinine_mg_dl NUMERIC CHECK (creatinine_mg_dl >= 0),
    alt_u_l NUMERIC CHECK (alt_u_l >= 0),
    ast_u_l NUMERIC CHECK (ast_u_l >= 0),
    ggt_u_l NUMERIC CHECK (ggt_u_l >= 0),
    bun_mg_dl NUMERIC CHECK (bun_mg_dl >= 0),
    doctor_diagnosed_diabetes_flag BOOLEAN,
    doctor_diagnosed_prediabetes_flag BOOLEAN,
    diabetes_by_lab_flag BOOLEAN NOT NULL,
    prediabetes_by_lab_flag BOOLEAN NOT NULL,
    normal_glycemia_by_lab_flag BOOLEAN NOT NULL,
    taking_insulin_flag BOOLEAN,
    taking_diabetes_pills_flag BOOLEAN,
    doctor_diagnosed_hypertension_flag BOOLEAN,
    taking_bp_medication_flag BOOLEAN,
    doctor_diagnosed_high_cholesterol_flag BOOLEAN,
    taking_cholesterol_medication_flag BOOLEAN,
    congestive_heart_failure_flag BOOLEAN,
    coronary_heart_disease_flag BOOLEAN,
    angina_flag BOOLEAN,
    heart_attack_flag BOOLEAN,
    stroke_flag BOOLEAN,
    thyroid_problem_flag BOOLEAN,
    liver_condition_flag BOOLEAN,
    family_history_diabetes_flag BOOLEAN,
    elevated_bp_screen_flag_clean BOOLEAN NOT NULL,
    source_dataset TEXT NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE patient_lifestyle_baseline (
    patient_id BIGINT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
    vigorous_work_activity_flag BOOLEAN,
    vigorous_work_days_week NUMERIC CHECK (vigorous_work_days_week BETWEEN 0 AND 7),
    vigorous_work_minutes_day NUMERIC CHECK (vigorous_work_minutes_day >= 0),
    moderate_work_activity_flag BOOLEAN,
    moderate_work_days_week NUMERIC CHECK (moderate_work_days_week BETWEEN 0 AND 7),
    moderate_work_minutes_day NUMERIC CHECK (moderate_work_minutes_day >= 0),
    walk_bicycle_transport_flag BOOLEAN,
    vigorous_recreation_flag BOOLEAN,
    moderate_recreation_flag BOOLEAN,
    sedentary_minutes_day NUMERIC CHECK (sedentary_minutes_day BETWEEN 0 AND 1440),
    sleep_hours_weekday NUMERIC CHECK (sleep_hours_weekday BETWEEN 0 AND 24),
    sleep_hours_weekend NUMERIC CHECK (sleep_hours_weekend BETWEEN 0 AND 24),
    doctor_diagnosed_sleep_disorder_flag BOOLEAN,
    smoked_100_cigarettes_flag BOOLEAN,
    current_smoking_frequency_code NUMERIC,
    alcohol_ever_flag BOOLEAN,
    alcohol_frequency_past_year NUMERIC CHECK (alcohol_frequency_past_year >= 0),
    avg_drinks_per_drinking_day NUMERIC CHECK (avg_drinks_per_drinking_day >= 0),
    smoking_applicable BOOLEAN NOT NULL,
    alcohol_applicable BOOLEAN NOT NULL,
    adult_questionnaire_applicable BOOLEAN NOT NULL,
    source_dataset TEXT NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE patient_prescription_status (
    patient_id BIGINT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
    prescription_use_code NUMERIC NOT NULL,
    prescription_use_status TEXT NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE patient_medications (
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    medication_record_id TEXT PRIMARY KEY,
    drug_name TEXT NOT NULL,
    drug_id TEXT,
    indication_code TEXT,
    indication_description TEXT,
    days_taken NUMERIC CHECK (days_taken >= 0),
    prescription_use_code NUMERIC NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL,
    source_note TEXT NOT NULL
);

CREATE TABLE project_diabetes_cohort (
    patient_id BIGINT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
    project_diabetes_label TEXT NOT NULL CHECK (project_diabetes_label IN ('PREDIABETES', 'TYPE_2')),
    label_confidence TEXT NOT NULL CHECK (label_confidence IN ('HIGH', 'MODERATE')),
    label_basis TEXT NOT NULL,
    label_is_inferred BOOLEAN NOT NULL,
    classification_method TEXT NOT NULL,
    classification_confirmed BOOLEAN NOT NULL DEFAULT FALSE CHECK (classification_confirmed = FALSE),
    label_rule_version TEXT NOT NULL,
    source_doctor_diabetes_code NUMERIC NOT NULL,
    source_age_at_diabetes_diagnosis NUMERIC,
    source_current_insulin_code NUMERIC,
    source_insulin_duration_value NUMERIC,
    source_insulin_duration_unit_code NUMERIC,
    source_diabetes_pills_code NUMERIC,
    insulin_duration_years_derived NUMERIC,
    insulin_start_delay_years_derived NUMERIC,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE caregivers (
    caregiver_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL UNIQUE REFERENCES patients(patient_id) ON DELETE CASCADE,
    caregiver_display_name TEXT NOT NULL UNIQUE,
    relationship_to_patient TEXT NOT NULL CHECK (
        relationship_to_patient IN ('PARENT_OR_LEGAL_GUARDIAN', 'FAMILY_OR_DESIGNATED_CAREGIVER')
    ),
    caregiver_scope TEXT NOT NULL,
    relationship_status TEXT NOT NULL,
    is_synthetic_identity BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic_identity = TRUE),
    identity_source TEXT NOT NULL,
    data_source TEXT NOT NULL,
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    pipeline_version TEXT NOT NULL
);

CREATE TABLE doctors (
    doctor_id TEXT PRIMARY KEY,
    doctor_display_name TEXT NOT NULL UNIQUE,
    specialty TEXT NOT NULL CHECK (specialty IN (
        'PRIMARY_CARE_DIABETES_PHYSICIAN',
        'GERIATRIC_DIABETES_PHYSICIAN',
        'ENDOCRINOLOGIST_DIABETOLOGIST',
        'PEDIATRIC_ENDOCRINOLOGIST'
    )),
    profile_status TEXT NOT NULL CHECK (profile_status = 'DEMO_ACTIVE'),
    clinical_role_scope TEXT NOT NULL CHECK (clinical_role_scope = 'DEMO_DIABETES_CARE_ASSIGNMENT'),
    is_synthetic_identity BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic_identity = TRUE),
    identity_source TEXT NOT NULL,
    data_source TEXT NOT NULL CHECK (data_source = 'SYNTHETIC_DEMO'),
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE patient_doctor_assignments (
    assignment_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL UNIQUE REFERENCES patients(patient_id) ON DELETE CASCADE,
    doctor_id TEXT NOT NULL REFERENCES doctors(doctor_id) ON DELETE RESTRICT,
    assignment_role TEXT NOT NULL CHECK (assignment_role = 'PRIMARY_DIABETES_TREATING_PHYSICIAN'),
    assignment_reason TEXT NOT NULL CHECK (assignment_reason IN (
        'PATIENT_AGE_UNDER_18',
        'PATIENT_AGE_OVER_70',
        'TYPE_2_ALGORITHM_LABEL',
        'PREDIABETES_BASELINE_LABEL'
    )),
    assignment_status TEXT NOT NULL CHECK (assignment_status = 'DEMO_PLACEHOLDER'),
    data_source TEXT NOT NULL CHECK (data_source = 'SYNTHETIC_DEMO'),
    source_cycle TEXT NOT NULL,
    record_type TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE field_lineage (
    target_table TEXT NOT NULL,
    target_field TEXT NOT NULL,
    source_dataset TEXT NOT NULL,
    source_variable TEXT,
    transformation TEXT NOT NULL,
    PRIMARY KEY (target_table, target_field)
);

-- CGMacros is an independent observed research cohort used to learn source
-- patterns for simulation. source_subject_id must never be treated as a
-- patients.patient_id or joined to NHANES without an explicit synthetic map.
CREATE TABLE cgmacros_source_subjects (
    source_subject_id INTEGER PRIMARY KEY,
    age_years NUMERIC CHECK (age_years BETWEEN 0 AND 120),
    sex TEXT,
    bmi NUMERIC CHECK (bmi > 0),
    body_weight_lb NUMERIC CHECK (body_weight_lb > 0),
    height_in NUMERIC CHECK (height_in > 0),
    self_identified_ethnicity TEXT,
    a1c_pct NUMERIC CHECK (a1c_pct >= 0),
    fasting_glucose_mg_dl NUMERIC CHECK (fasting_glucose_mg_dl >= 0),
    baseline_insulin_source_value NUMERIC,
    triglycerides_mg_dl NUMERIC CHECK (triglycerides_mg_dl >= 0),
    total_cholesterol_mg_dl NUMERIC CHECK (total_cholesterol_mg_dl >= 0),
    hdl_mg_dl NUMERIC CHECK (hdl_mg_dl >= 0),
    ldl_calculated_mg_dl NUMERIC CHECK (ldl_calculated_mg_dl >= 0),
    source_glycemic_group TEXT NOT NULL CHECK (
        source_glycemic_group IN ('PREDIABETES_RANGE', 'DIABETES_RANGE')
    ),
    classification_basis TEXT NOT NULL,
    data_source TEXT NOT NULL CHECK (data_source = 'CGMACROS_1_0_0'),
    record_type TEXT NOT NULL CHECK (record_type = 'OBSERVED_SOURCE_SUBJECT_PROFILE'),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE cgmacros_meal_events (
    meal_id TEXT PRIMARY KEY,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE CASCADE,
    meal_started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    meal_type TEXT NOT NULL CHECK (meal_type IN ('BREAKFAST', 'LUNCH', 'DINNER', 'SNACK', 'OTHER')),
    meal_type_raw TEXT NOT NULL,
    calories_kcal NUMERIC,
    carbohydrate_g NUMERIC,
    protein_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    sugar_g NUMERIC,
    amount_consumed_pct NUMERIC,
    direct_image_path TEXT,
    quality_flags TEXT,
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    data_source TEXT NOT NULL CHECK (data_source = 'CGMACROS_1_0_0'),
    record_type TEXT NOT NULL CHECK (record_type = 'OBSERVED_MEAL_EVENT'),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL,
    UNIQUE (source_subject_id, meal_started_at)
);

CREATE TABLE cgmacros_meal_images (
    meal_image_id TEXT PRIMARY KEY,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE CASCADE,
    captured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    image_path TEXT NOT NULL,
    meal_id TEXT REFERENCES cgmacros_meal_events(meal_id) ON DELETE SET NULL,
    link_status TEXT NOT NULL CHECK (
        link_status IN ('DIRECT_SAME_SOURCE_ROW', 'UNLINKED_SOURCE_IMAGE')
    ),
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    data_source TEXT NOT NULL CHECK (data_source = 'CGMACROS_1_0_0'),
    record_type TEXT NOT NULL CHECK (record_type = 'OBSERVED_MEAL_IMAGE'),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_synthetic = FALSE),
    pipeline_version TEXT NOT NULL,
    CHECK (
        (link_status = 'DIRECT_SAME_SOURCE_ROW' AND meal_id IS NOT NULL)
        OR (link_status = 'UNLINKED_SOURCE_IMAGE' AND meal_id IS NULL)
    )
);

CREATE TABLE simulation_runs (
    simulation_run_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    patient_group TEXT NOT NULL CHECK (
        patient_group IN ('PREDIABETES', 'TYPE_2_NON_INSULIN', 'TYPE_2_INSULIN')
    ),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK (end_date >= start_date),
    days SMALLINT NOT NULL CHECK (days > 0),
    random_seed BIGINT NOT NULL,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE RESTRICT,
    donor_match_score NUMERIC NOT NULL CHECK (donor_match_score >= 0),
    fasting_anchor_mg_dl NUMERIC NOT NULL,
    fasting_anchor_source TEXT NOT NULL CHECK (
        fasting_anchor_source IN (
            'NHANES_OBSERVED_FASTING_GLUCOSE',
            'CGMACROS_A1C_FASTING_REGRESSION',
            'CGMACROS_DONOR_FASTING_FALLBACK'
        )
    ),
    generator_version TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    UNIQUE (patient_id, start_date, end_date, generator_version)
);

CREATE TABLE synthetic_patient_meals (
    meal_id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(simulation_run_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    meal_started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    meal_type TEXT NOT NULL CHECK (meal_type IN ('BREAKFAST', 'LUNCH', 'DINNER', 'SNACK', 'OTHER')),
    calories_kcal NUMERIC,
    carbohydrate_g NUMERIC,
    protein_g NUMERIC,
    fat_g NUMERIC,
    fiber_g NUMERIC,
    amount_consumed_pct NUMERIC,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE RESTRICT,
    source_meal_id TEXT NOT NULL REFERENCES cgmacros_meal_events(meal_id) ON DELETE RESTRICT,
    source_day DATE NOT NULL,
    macro_scale_factor NUMERIC NOT NULL CHECK (macro_scale_factor > 0),
    generation_method TEXT NOT NULL CHECK (generation_method = 'CGMACROS_DAY_BLOCK_BOOTSTRAP'),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    UNIQUE (patient_id, meal_started_at, meal_id)
);

CREATE TABLE synthetic_glucose_measurements (
    measurement_id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(simulation_run_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    measured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    glucose_mg_dl NUMERIC NOT NULL CHECK (glucose_mg_dl BETWEEN 40 AND 600),
    raw_generated_glucose_mg_dl NUMERIC NOT NULL,
    value_adjustment_flag TEXT CHECK (
        value_adjustment_flag IS NULL OR value_adjustment_flag = 'BOUNDED_TO_SIMULATOR_SENSOR_RANGE'
    ),
    timestamp_adjustment_minutes SMALLINT NOT NULL DEFAULT 0 CHECK (timestamp_adjustment_minutes >= 0),
    unit TEXT NOT NULL CHECK (unit = 'mg/dL'),
    meal_context TEXT NOT NULL CHECK (
        meal_context IN ('FASTING', 'BEFORE_MEAL', 'AFTER_MEAL', 'BEFORE_SLEEP', 'RANDOM', 'EVENT_RECOVERY')
    ),
    measurement_source TEXT NOT NULL CHECK (measurement_source = 'SIMULATOR'),
    meal_id TEXT REFERENCES synthetic_patient_meals(meal_id) ON DELETE SET NULL,
    minutes_from_meal_start INTEGER,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE RESTRICT,
    source_meal_id TEXT REFERENCES cgmacros_meal_events(meal_id) ON DELETE RESTRICT,
    source_observed_glucose_mg_dl NUMERIC NOT NULL,
    fasting_anchor_mg_dl NUMERIC NOT NULL,
    fasting_anchor_source TEXT NOT NULL,
    generation_method TEXT NOT NULL,
    episode_id TEXT,
    is_anomaly_scenario BOOLEAN NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    UNIQUE (patient_id, measured_at)
);

CREATE TABLE simulation_episodes (
    episode_id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL REFERENCES simulation_runs(simulation_run_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    scenario_type TEXT NOT NULL CHECK (
        scenario_type IN ('POSTPRANDIAL_HIGH', 'SUSTAINED_HIGH', 'HYPOGLYCEMIA')
    ),
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    peak_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    trigger_measurement_id TEXT NOT NULL REFERENCES synthetic_glucose_measurements(measurement_id) ON DELETE CASCADE,
    source_subject_id INTEGER NOT NULL REFERENCES cgmacros_source_subjects(source_subject_id) ON DELETE RESTRICT,
    source_observed_glucose_mg_dl NUMERIC NOT NULL,
    scenario_policy TEXT NOT NULL CHECK (scenario_policy = 'TEST_COVERAGE_NOT_EPISODE_PREVALENCE'),
    is_forced_test_scenario BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_forced_test_scenario = TRUE),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    CHECK (started_at <= peak_at AND peak_at <= resolved_at)
);

-- Versioned knowledge-base content. These tables contain reference material,
-- never patient observations or clinician orders.
CREATE TABLE knowledge_documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_url TEXT,
    source_organization TEXT,
    published_date DATE,
    retrieved_date DATE,
    language TEXT NOT NULL DEFAULT 'vi',
    content_sha256 TEXT NOT NULL UNIQUE,
    line_count INTEGER NOT NULL CHECK (line_count > 0),
    status TEXT NOT NULL CHECK (status = 'REFERENCE_ONLY'),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE knowledge_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    heading TEXT NOT NULL,
    chunk_sequence INTEGER NOT NULL CHECK (chunk_sequence > 0),
    source_line_start INTEGER NOT NULL CHECK (source_line_start > 0),
    source_line_end INTEGER NOT NULL CHECK (source_line_end >= source_line_start),
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    embedding_status TEXT NOT NULL CHECK (embedding_status IN ('NOT_GENERATED', 'GENERATED', 'FAILED')),
    pipeline_version TEXT NOT NULL,
    UNIQUE (document_id, chunk_sequence)
);

CREATE TABLE drug_reference_catalog (
    drug_reference_id TEXT PRIMARY KEY,
    generic_name TEXT NOT NULL UNIQUE,
    atc_code TEXT,
    drug_class TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE RESTRICT,
    reference_status TEXT NOT NULL CHECK (reference_status = 'REFERENCE_ONLY_NOT_A_PRESCRIPTION'),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE drug_reference_sections (
    drug_section_id TEXT PRIMARY KEY,
    drug_reference_id TEXT NOT NULL REFERENCES drug_reference_catalog(drug_reference_id) ON DELETE CASCADE,
    section_type TEXT NOT NULL,
    section_title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_line_start INTEGER NOT NULL,
    source_line_end INTEGER NOT NULL,
    pipeline_version TEXT NOT NULL,
    CHECK (source_line_end >= source_line_start)
);

CREATE TABLE clinical_target_templates (
    target_template_id TEXT PRIMARY KEY,
    population_code TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    operator TEXT NOT NULL,
    target_value TEXT,
    unit TEXT,
    clinical_note TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE RESTRICT,
    source_locator TEXT NOT NULL,
    requires_individualization BOOLEAN NOT NULL DEFAULT TRUE CHECK (requires_individualization = TRUE),
    approval_status TEXT NOT NULL CHECK (approval_status = 'REFERENCE_APPROVED_SOURCE_NOT_PATIENT_ORDER'),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE screening_protocols (
    screening_protocol_id TEXT PRIMARY KEY,
    screening_code TEXT NOT NULL UNIQUE,
    recommended_interval TEXT NOT NULL,
    summary TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE RESTRICT,
    source_locator TEXT NOT NULL,
    requires_clinical_judgment BOOLEAN NOT NULL DEFAULT TRUE CHECK (requires_clinical_judgment = TRUE),
    pipeline_version TEXT NOT NULL
);

CREATE TABLE alert_threshold_rules (
    alert_rule_id TEXT PRIMARY KEY,
    rule_code TEXT NOT NULL,
    population_code TEXT NOT NULL,
    context_code TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    operator TEXT NOT NULL CHECK (operator IN (
        '<', '<=', '>', '>=', 'BETWEEN', 'PERSISTENT_AT_OR_ABOVE'
    )),
    threshold_low NUMERIC NOT NULL,
    threshold_high NUMERIC,
    lower_inclusive BOOLEAN NOT NULL DEFAULT FALSE,
    upper_inclusive BOOLEAN NOT NULL DEFAULT FALSE,
    unit TEXT NOT NULL CHECK (unit = 'mg/dL'),
    severity TEXT NOT NULL CHECK (severity IN ('MEDIUM', 'HIGH', 'CRITICAL')),
    consecutive_readings INTEGER NOT NULL DEFAULT 1 CHECK (consecutive_readings > 0),
    window_minutes INTEGER NOT NULL DEFAULT 0 CHECK (window_minutes >= 0),
    cooldown_minutes INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_minutes >= 0),
    alert_title TEXT NOT NULL,
    message_template TEXT NOT NULL,
    action_code TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE RESTRICT,
    source_locator TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    evidence_confidence TEXT NOT NULL CHECK (evidence_confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    demo_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    production_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    requires_clinical_approval BOOLEAN NOT NULL DEFAULT TRUE CHECK (requires_clinical_approval = TRUE),
    patient_specific_override_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    automated_medication_change_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (automated_medication_change_allowed = FALSE),
    rule_status TEXT NOT NULL CHECK (rule_status IN (
        'REFERENCE_DERIVED_DRAFT', 'CLINICALLY_APPROVED', 'RETIRED'
    )),
    rule_version TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    UNIQUE (rule_code, rule_version),
    CHECK (threshold_high IS NULL OR threshold_high > threshold_low),
    CHECK (production_enabled = FALSE OR rule_status = 'CLINICALLY_APPROVED')
);

CREATE TABLE education_topics (
    education_topic_id TEXT PRIMARY KEY,
    topic_code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content_status TEXT NOT NULL CHECK (content_status = 'REFERENCE_CONTENT'),
    clinical_review_status TEXT NOT NULL,
    pipeline_version TEXT NOT NULL
);

CREATE TABLE education_topic_documents (
    education_topic_id TEXT NOT NULL REFERENCES education_topics(education_topic_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    link_type TEXT NOT NULL CHECK (link_type = 'SUPPORTING_DOCUMENT'),
    pipeline_version TEXT NOT NULL,
    PRIMARY KEY (education_topic_id, document_id)
);

-- Patient-specific facts below must be collected from an observation,
-- questionnaire, device, or clinician. They must never be populated by copying
-- statements from the knowledge base.
CREATE TABLE patient_conditions (
    patient_condition_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    condition_code_system TEXT NOT NULL,
    condition_code TEXT NOT NULL,
    condition_display TEXT NOT NULL,
    clinical_status TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    onset_date DATE,
    recorded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    source_type TEXT NOT NULL,
    recorded_by_doctor_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_allergies (
    patient_allergy_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    substance_code TEXT,
    substance_display TEXT NOT NULL,
    reaction_text TEXT,
    severity TEXT,
    clinical_status TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    recorded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    source_type TEXT NOT NULL,
    recorded_by_doctor_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_kidney_assessments (
    kidney_assessment_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    assessed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    creatinine_mg_dl NUMERIC CHECK (creatinine_mg_dl >= 0),
    egfr_ml_min_1_73m2 NUMERIC CHECK (egfr_ml_min_1_73m2 >= 0),
    uacr_mg_g NUMERIC CHECK (uacr_mg_g >= 0),
    source_type TEXT NOT NULL,
    clinician_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    record_status TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_eye_exams (
    eye_exam_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    examined_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    retinopathy_status TEXT,
    laterality TEXT,
    exam_method TEXT,
    clinician_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    next_review_date DATE,
    record_status TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_foot_exams (
    foot_exam_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    examined_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    protective_sensation_status TEXT,
    pulse_status TEXT,
    deformity_status TEXT,
    ulcer_status TEXT,
    risk_classification TEXT,
    clinician_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    record_status TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_mental_health_screenings (
    screening_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    screened_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    instrument_code TEXT NOT NULL,
    score NUMERIC,
    interpretation TEXT,
    clinician_id TEXT REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    record_status TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_care_plans (
    care_plan_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'COMPLETED', 'CANCELLED')),
    approved_by_doctor_id TEXT REFERENCES doctors(doctor_id) ON DELETE RESTRICT,
    approval_timestamp TIMESTAMP WITHOUT TIME ZONE,
    notes TEXT,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK ((status = 'ACTIVE' AND approved_by_doctor_id IS NOT NULL AND approval_timestamp IS NOT NULL) OR status <> 'ACTIVE')
);

CREATE TABLE patient_clinical_targets (
    patient_target_id TEXT PRIMARY KEY,
    care_plan_id TEXT NOT NULL REFERENCES patient_care_plans(care_plan_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    metric_code TEXT NOT NULL,
    operator TEXT NOT NULL,
    target_value TEXT NOT NULL,
    unit TEXT,
    source_template_id TEXT REFERENCES clinical_target_templates(target_template_id) ON DELETE SET NULL,
    approved_by_doctor_id TEXT NOT NULL REFERENCES doctors(doctor_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE patient_education_progress (
    education_progress_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    education_topic_id TEXT NOT NULL REFERENCES education_topics(education_topic_id) ON DELETE RESTRICT,
    assigned_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    assigned_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ASSIGNED', 'IN_PROGRESS', 'COMPLETED', 'DECLINED')),
    completed_at TIMESTAMP WITHOUT TIME ZONE,
    acknowledged_at TIMESTAMP WITHOUT TIME ZONE,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (patient_id, education_topic_id, assigned_at)
);

CREATE TABLE recommendation_rules (
    recommendation_rule_id TEXT PRIMARY KEY,
    rule_type TEXT NOT NULL,
    population_filter TEXT NOT NULL,
    condition_expression TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    approval_status TEXT NOT NULL CHECK (approval_status IN (
        'DRAFT_REQUIRES_CLINICAL_APPROVAL', 'APPROVED_FOR_REVIEW_PROMPTS', 'RETIRED'
    )),
    automated_action_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (automated_action_allowed = FALSE),
    output_scope TEXT NOT NULL CHECK (output_scope = 'REVIEW_PROMPT_ONLY_NOT_DIAGNOSIS_OR_TREATMENT'),
    pipeline_version TEXT NOT NULL,
    UNIQUE (recommendation_rule_id, rule_version),
    CHECK (enabled = FALSE OR approval_status = 'APPROVED_FOR_REVIEW_PROMPTS')
);

CREATE TABLE patient_recommendation_events (
    recommendation_event_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    recommendation_rule_id TEXT NOT NULL REFERENCES recommendation_rules(recommendation_rule_id) ON DELETE RESTRICT,
    generated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    evidence_snapshot JSONB NOT NULL,
    recommendation_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING_CLINICAL_REVIEW', 'APPROVED', 'REJECTED', 'EXPIRED')),
    reviewed_by_doctor_id TEXT REFERENCES doctors(doctor_id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMP WITHOUT TIME ZONE,
    review_note TEXT,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK ((status IN ('APPROVED', 'REJECTED') AND reviewed_by_doctor_id IS NOT NULL AND reviewed_at IS NOT NULL) OR status NOT IN ('APPROVED', 'REJECTED'))
);

-- Fully synthetic happy-case medication workflow for application testing. It is
-- isolated from observed patient_medications and must never be exposed as a real
-- prescription or clinical recommendation.
CREATE TABLE synthetic_medication_regimens (
    regimen_id TEXT PRIMARY KEY,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    source_medication_record_id TEXT NOT NULL REFERENCES patient_medications(medication_record_id) ON DELETE RESTRICT,
    source_drug_name TEXT NOT NULL,
    drug_reference_id TEXT NOT NULL REFERENCES drug_reference_catalog(drug_reference_id) ON DELETE RESTRICT,
    dose_value NUMERIC NOT NULL CHECK (dose_value > 0),
    dose_unit TEXT NOT NULL,
    route TEXT NOT NULL CHECK (route IN ('ORAL', 'SUBCUTANEOUS')),
    frequency_code TEXT NOT NULL,
    timing_rule TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK (end_date >= start_date),
    source_document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE RESTRICT,
    source_section TEXT NOT NULL,
    dose_basis TEXT NOT NULL CHECK (dose_basis = 'SYNTHETIC_HAPPY_CASE_STARTING_DOSE_FROM_LOCAL_MONOGRAPH'),
    clinical_use_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (clinical_use_allowed = FALSE),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    UNIQUE (patient_id, drug_reference_id, start_date, generator_version)
);

CREATE TABLE synthetic_medication_schedule (
    schedule_id TEXT PRIMARY KEY,
    regimen_id TEXT NOT NULL REFERENCES synthetic_medication_regimens(regimen_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    scheduled_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    dose_value NUMERIC NOT NULL CHECK (dose_value > 0),
    dose_unit TEXT NOT NULL,
    route TEXT NOT NULL,
    meal_id TEXT REFERENCES synthetic_patient_meals(meal_id) ON DELETE SET NULL,
    meal_link_status TEXT NOT NULL CHECK (meal_link_status IN (
        'LINKED_SYNTHETIC_MEAL', 'FALLBACK_DEMO_TIME_NO_MEAL_EVENT', 'NOT_MEAL_LINKED'
    )),
    schedule_status TEXT NOT NULL CHECK (schedule_status = 'PLANNED'),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    UNIQUE (regimen_id, scheduled_at)
);

CREATE TABLE synthetic_medication_reminders (
    reminder_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL UNIQUE REFERENCES synthetic_medication_schedule(schedule_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    scheduled_for TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    delivery_status TEXT NOT NULL CHECK (delivery_status = 'DELIVERED'),
    acknowledged_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    reminder_status TEXT NOT NULL CHECK (reminder_status = 'ACKNOWLEDGED'),
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('PATIENT', 'PATIENT_AND_CAREGIVER')),
    caregiver_id TEXT REFERENCES caregivers(caregiver_id) ON DELETE SET NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL,
    CHECK (sent_at <= acknowledged_at)
);

CREATE TABLE synthetic_medication_administrations (
    administration_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL UNIQUE REFERENCES synthetic_medication_schedule(schedule_id) ON DELETE CASCADE,
    regimen_id TEXT NOT NULL REFERENCES synthetic_medication_regimens(regimen_id) ON DELETE CASCADE,
    patient_id BIGINT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    taken_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    dose_value NUMERIC NOT NULL CHECK (dose_value > 0),
    dose_unit TEXT NOT NULL,
    route TEXT NOT NULL,
    administration_status TEXT NOT NULL CHECK (administration_status = 'TAKEN_AS_SCHEDULED'),
    minutes_from_schedule SMALLINT NOT NULL CHECK (minutes_from_schedule BETWEEN 0 AND 5),
    adherence_flag BOOLEAN NOT NULL DEFAULT TRUE CHECK (adherence_flag = TRUE),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_synthetic = TRUE),
    generator_version TEXT NOT NULL
);

CREATE INDEX idx_patients_project_label ON patients(project_diabetes_label);
CREATE INDEX idx_patients_age_years ON patients(age_years);
CREATE INDEX idx_patients_quality_tier ON patients(quality_tier);
CREATE INDEX idx_clinical_hba1c ON patient_baseline_clinical(hba1c_pct);
CREATE INDEX idx_medications_patient_id ON patient_medications(patient_id);
CREATE INDEX idx_medications_drug_id ON patient_medications(drug_id);
CREATE INDEX idx_doctors_specialty ON doctors(specialty);
CREATE INDEX idx_assignments_doctor_id ON patient_doctor_assignments(doctor_id);
CREATE INDEX idx_cgmacros_subject_group ON cgmacros_source_subjects(source_glycemic_group);
CREATE INDEX idx_cgmacros_meals_subject_time ON cgmacros_meal_events(source_subject_id, meal_started_at);
CREATE INDEX idx_cgmacros_images_subject_time ON cgmacros_meal_images(source_subject_id, captured_at);
CREATE INDEX idx_cgmacros_images_meal_id ON cgmacros_meal_images(meal_id);
CREATE INDEX idx_simulation_runs_patient ON simulation_runs(patient_id);
CREATE INDEX idx_synthetic_meals_patient_time ON synthetic_patient_meals(patient_id, meal_started_at);
CREATE INDEX idx_synthetic_glucose_patient_time ON synthetic_glucose_measurements(patient_id, measured_at);
CREATE INDEX idx_synthetic_glucose_episode ON synthetic_glucose_measurements(episode_id);
CREATE INDEX idx_simulation_episodes_patient_time ON simulation_episodes(patient_id, started_at);
CREATE INDEX idx_knowledge_chunks_document_sequence ON knowledge_chunks(document_id, chunk_sequence);
CREATE INDEX idx_knowledge_chunks_heading ON knowledge_chunks(heading);
CREATE INDEX idx_drug_reference_class ON drug_reference_catalog(drug_class);
CREATE INDEX idx_targets_population_metric ON clinical_target_templates(population_code, metric_code);
CREATE INDEX idx_patient_conditions_patient ON patient_conditions(patient_id, recorded_at);
CREATE INDEX idx_patient_allergies_patient ON patient_allergies(patient_id, recorded_at);
CREATE INDEX idx_kidney_assessments_patient_time ON patient_kidney_assessments(patient_id, assessed_at);
CREATE INDEX idx_eye_exams_patient_time ON patient_eye_exams(patient_id, examined_at);
CREATE INDEX idx_foot_exams_patient_time ON patient_foot_exams(patient_id, examined_at);
CREATE INDEX idx_mental_screenings_patient_time ON patient_mental_health_screenings(patient_id, screened_at);
CREATE INDEX idx_care_plans_patient_status ON patient_care_plans(patient_id, status);
CREATE INDEX idx_patient_targets_patient_status ON patient_clinical_targets(patient_id, status);
CREATE INDEX idx_education_progress_patient_status ON patient_education_progress(patient_id, status);
CREATE INDEX idx_recommendation_events_patient_status ON patient_recommendation_events(patient_id, status);
CREATE INDEX idx_synthetic_med_regimens_patient ON synthetic_medication_regimens(patient_id);
CREATE INDEX idx_synthetic_med_schedule_patient_time ON synthetic_medication_schedule(patient_id, scheduled_at);
CREATE INDEX idx_synthetic_med_reminders_patient_time ON synthetic_medication_reminders(patient_id, scheduled_for);
CREATE INDEX idx_synthetic_med_administrations_patient_time ON synthetic_medication_administrations(patient_id, taken_at);

COMMENT ON SCHEMA diabetes IS 'NHANES baseline deployment data plus explicitly synthetic demo identities and assignments.';
COMMENT ON TABLE doctors IS 'Synthetic demo doctors only; no real credential, facility, contact, or treatment relationship.';
COMMENT ON TABLE patient_doctor_assignments IS 'Synthetic deterministic routing for application testing; not a clinical referral.';
COMMENT ON COLUMN patients.project_diabetes_label IS 'Deployment label limited to PREDIABETES and algorithm-classified TYPE_2; not a confirmed diagnosis.';
COMMENT ON TABLE cgmacros_source_subjects IS 'Independent observed CGMacros source cohort; no identity or key relationship to NHANES patients.';
COMMENT ON TABLE cgmacros_meal_events IS 'Observed sparse meal events normalized before sensor-column missingness filtering; not synthetic patient meals.';
COMMENT ON TABLE cgmacros_meal_images IS 'Observed source images. Only same-row links are assigned; other images remain explicitly unlinked.';
COMMENT ON TABLE simulation_runs IS 'Reproducible per-patient synthetic runs; donor linkage is provenance, not identity matching.';
COMMENT ON TABLE synthetic_patient_meals IS 'Synthetic meals created from observed CGMacros day-block bootstrap with explicit donor provenance.';
COMMENT ON TABLE synthetic_glucose_measurements IS 'Synthetic BGM-style measurements for application testing; not clinical forecasts.';
COMMENT ON TABLE simulation_episodes IS 'Forced alert-test scenarios. Counts must not be interpreted as clinical event prevalence.';
COMMENT ON TABLE knowledge_documents IS 'Versioned reference documents only; never patient observations.';
COMMENT ON TABLE drug_reference_catalog IS 'Drug reference catalog; not prescriptions and not authorization to recommend treatment.';
COMMENT ON TABLE clinical_target_templates IS 'Population-level reference targets requiring clinician individualization before patient use.';
COMMENT ON TABLE recommendation_rules IS 'Review-prompt rules. Enabling requires explicit APPROVED_FOR_REVIEW_PROMPTS status; automated clinical action remains prohibited.';
COMMENT ON TABLE patient_recommendation_events IS 'Review prompts only; no automatic diagnosis, medication, dosage, or treatment action.';
COMMENT ON TABLE synthetic_medication_regimens IS 'Synthetic happy-case regimens for UI/workflow tests only; not prescriptions and not clinically usable.';
COMMENT ON TABLE synthetic_medication_schedule IS 'Synthetic planned medication events linked to synthetic meals when applicable.';
COMMENT ON TABLE synthetic_medication_reminders IS 'Synthetic happy-case delivered and acknowledged reminders.';
COMMENT ON TABLE synthetic_medication_administrations IS 'Synthetic happy-case taken-as-scheduled events; not evidence of real adherence.';

COMMIT;

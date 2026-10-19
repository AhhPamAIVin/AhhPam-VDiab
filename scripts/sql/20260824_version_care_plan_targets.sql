-- PostgreSQL migration for longitudinal care-plan targets and auditable ML output.
-- Run once on an existing database before deploying the corresponding backend.
-- New databases created from SQLModel metadata already contain this structure.

ALTER TYPE care_plan_target_status ADD VALUE IF NOT EXISTS 'SUPERSEDED';

ALTER TABLE care_plan_targets
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS effective_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS effective_to TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS source_clinical_note_id UUID NULL REFERENCES clinical_notes(id),
    ADD COLUMN IF NOT EXISTS source_appointment_id UUID NULL REFERENCES appointments(id),
    ADD COLUMN IF NOT EXISTS change_reason VARCHAR(1000) NULL;

-- The old schema allowed exactly one row per patient. Versioning replaces
-- that constraint with one row per patient/version.
ALTER TABLE care_plan_targets
    DROP CONSTRAINT IF EXISTS care_plan_targets_patient_id_key;

ALTER TABLE care_plan_targets
    DROP CONSTRAINT IF EXISTS uq_care_plan_target_patient_version;

ALTER TABLE care_plan_targets
    ADD CONSTRAINT uq_care_plan_target_patient_version UNIQUE (patient_id, version);

CREATE INDEX IF NOT EXISTS ix_care_plan_targets_patient_effective
    ON care_plan_targets (patient_id, effective_from, effective_to);

ALTER TABLE glucose_records
    ADD COLUMN IF NOT EXISTS threshold_evaluation_source VARCHAR(30) NULL,
    ADD COLUMN IF NOT EXISTS threshold_fallback_reason VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS care_plan_target_version INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'doctor_watch_label') THEN
        CREATE TYPE doctor_watch_label AS ENUM (
            'CONFIRMED_WATCH',
            'CONFIRMED_NORMAL',
            'UNCERTAIN'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS doctor_watch_reviews (
    id UUID PRIMARY KEY,
    glucose_record_id UUID NOT NULL UNIQUE REFERENCES glucose_records(id),
    patient_id UUID NOT NULL REFERENCES users(id),
    doctor_id UUID NOT NULL REFERENCES users(id),
    model_status_at_review VARCHAR(20) NULL,
    label doctor_watch_label NOT NULL,
    rationale VARCHAR(2000) NOT NULL,
    reviewed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_doctor_watch_reviews_patient_id
    ON doctor_watch_reviews (patient_id);

CREATE INDEX IF NOT EXISTS ix_doctor_watch_reviews_doctor_id
    ON doctor_watch_reviews (doctor_id);

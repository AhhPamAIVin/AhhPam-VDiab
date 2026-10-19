# Structured Diabetes Knowledge Base

This folder contains reproducible reference data derived from Markdown files in
`data/knowledge_base`.

## Safety boundary

- Knowledge rows are reference material, not patient observations.
- Drug rows are not prescriptions.
- Target templates require individualization and clinician approval.
- Recommendation rules are disabled drafts and cannot automate diagnosis or treatment.
- Alert thresholds are enabled only for synthetic/demo evaluation. Production use is
  disabled until a clinician approves the rules and any patient-specific overrides.
- Alert rules never authorize an automatic medication or insulin dose change.
- Files under `templates/` contain headers only; they must be populated from a real
  observation, questionnaire, device, or clinician record.

## Build

Run `data/NHANES/final/build_knowledge_base_dataset.py` with the knowledge and output
directories. The manifest records row counts and SHA-256 checksums.

## Main files

- `knowledge_documents.csv`: document-level provenance.
- `knowledge_chunks.csv`: line-addressable content for search/RAG.
- `drug_catalog.csv` and `drug_reference_sections.csv`: medication reference content.
- `clinical_target_templates.csv`: population-level target templates.
- `screening_protocols.csv`: structured screening guidance.
- `education_topics.csv` and `education_topic_documents.csv`: patient education catalog.
- `recommendation_rules.csv`: disabled, review-only rule definitions.
- `alert_threshold_rules.csv`: source-linked glucose threshold rules with severity,
  timing, cooldown, escalation action, and production safety flags.
- `patient_care_plans.csv`: synthetic demo care plans for TYPE_2 patients only.
- `patient_clinical_targets.csv`: clinician-simulated normal and danger CGM cut points.
- `patient_cgm_monitoring_profiles.csv`: ADA 2026-derived, risk-stratified 14-day
  TIR/TBR/TAR goals for each TYPE_2 demo patient. The generated classification
  contains target/normal and danger boundaries only; watch/attention logic is
  intentionally not included yet. ADA 2026 section 9 medication exposure
  (insulin, sulfonylurea, or meglitinide) is recorded as a high-priority
  hypoglycemia review flag without inventing a different glucose cut point.

The patient threshold files above are synthetic demo records. Their doctor identities
and approvals are synthetic, and the values are not valid clinical orders for real patients.

## Personalized attention dataset

`attention_engine/` contains the dataset-only, explainable attention layer. It learns
a 14-day baseline for each patient and measurement context, preserves DANGER rule
precedence, and automatically generates WATCH suggestions with a 12-hour cooldown
per patient and reason. WATCH suggestions do not require doctor approval because
they are restricted to rechecking and recording context; medication changes remain
explicitly prohibited.

## Longitudinal personalized care

`longitudinal_care/` stores clinician review events, immutable care-plan and
target versions, change logs, and baseline epochs. A confirmed appointment event
may activate a new version from its effective date and closes the previous version.
`NO_THRESHOLD_CHANGE` carries the current plan forward without inventing a new
version. The attention engine resolves the effective care-plan version and baseline
epoch at each measurement timestamp; AI can adapt WATCH sensitivity but cannot
author or change clinician-controlled normal/danger thresholds.

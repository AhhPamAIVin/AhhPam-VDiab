# Diabetes Project Metadata

Metadata version: `diabetes_project_metadata_v1.0`

This folder documents the complete local data estate without modifying source
healthcare data. It covers NHANES processing/deployment, CGMacros, the 60-day
simulator, medication happy-case flow, source knowledge, and structured knowledge.

## Files

- `dataset_catalog.csv`: dataset-level purpose, source type, synthetic status,
  availability, governance, generator, seed, and storage policy.
- `table_catalog.csv`: one row per CSV with row/column counts, key declarations,
  coverage, file size, SHA-256, database mapping, and GitHub storage strategy.
- `field_catalog.csv`: one row per field with inferred and database types, null rate,
  unit, key relationships, origin, sensitivity, and safety interpretation.
- `data_lineage.csv`: field-level NHANES lineage plus high-level cross-dataset flows.
- `generation_rules.csv`: versioned simulator, medication, and knowledge rules.
- `source_registry.csv`: source document provenance and authority classification.
- `quality_rules.csv`: release checks and required actions.
- `quality_summary.csv`: table-level completeness, primary-key, and foreign-key results.
- `quality_issues.csv`: explicit review queue; issues are not silently corrected.
- `data_governance.csv`: publishing, demo, training, license, and clinical-use controls.
- `manifest.json`: metadata row counts and SHA-256 checksums.
- `Diabetes_Project_Metadata_Catalog.xlsx`: review workbook generated from the CSVs.

## Interpretation boundaries

- `TYPE_2` and `PREDIABETES` deployment labels are algorithm/project labels, not
  clinician-confirmed diagnoses.
- Simulator glucose is sparse BGM/SMBG-style test data, not continuous CGM.
- Medication flow is synthetic happy-case data and never a prescription or evidence
  of real adherence.
- CGMacros subject IDs are independent research-source IDs and must never be treated
  as NHANES patient identities.
- Knowledge and drug references must not be copied into patient facts.

## Release gate

Do not push until every `ERROR` or `OPEN_BLOCKS_PUSH` row in
`quality_issues.csv` is resolved or explicitly accepted. Large generated data files
must use regeneration, Git LFS, or external release storage as specified in
`table_catalog.csv`.

## Rebuild

Run `data/NHANES/final/build_project_metadata.py` with repository root and output
directory. The build is read-only with respect to source datasets.

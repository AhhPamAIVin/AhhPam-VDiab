# Synthetic 60-day diabetes simulator output

This folder is fully synthetic application-test data derived from NHANES baseline
anchors and observed CGMacros donor patterns. It is not a clinical forecast and must
not be used to recommend treatment or estimate event prevalence.

Patient groups:

- `TYPE_2_NON_INSULIN`
- `TYPE_2_INSULIN`

All rows include reproducibility/provenance fields. Forced anomaly episodes are marked
`is_forced_test_scenario=True` and use the policy
`TEST_COVERAGE_NOT_EPISODE_PREVALENCE`.

Large generated CSV files should normally remain outside Git. Commit the generator,
schema, README, and manifest; regenerate the CSV files with the recorded seed.

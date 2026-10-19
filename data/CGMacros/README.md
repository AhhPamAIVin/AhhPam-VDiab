# CGMacros cleaned dataset

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

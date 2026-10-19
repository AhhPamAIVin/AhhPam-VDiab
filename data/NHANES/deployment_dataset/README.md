# NHANES baseline deployment dataset

This folder contains NHANES baseline observations, transparently derived fields, and
explicitly labelled synthetic demo identities. Clinical, glucose, meal, activity,
adherence, alert, and care-plan data are not simulated. Doctor profiles and assignments
are synthetic demo entities and are explicitly labelled as such.

`patient_profiles.display_name` is synthetic. `caregiver_profiles.csv` contains one
synthetic demo caregiver for each patient with `age_years < 16` or `age_years > 70`.
These records do not assert a real relationship or consent, and contain no contact data.

`doctor_profiles.csv` contains only synthetic demo doctors still referenced by retained
Type 2 patients.
`patient_doctor_assignments.csv` assigns one demo doctor to each deployment patient using
age and project label routing: pediatric endocrinology for age under 18, geriatric
diabetes care for age over 70 and endocrinology/diabetology for adult TYPE_2. It does not assert a real
clinician, credential, facility, or treatment relationship.

`diabetes_status` is an evidence-based phenotype from the cleaning pipeline.
Confirmed diabetes type is not available in the source. After the explicit data purge,
deployment retains only algorithm-classified `TYPE_2`; prediabetes and TYPE_1 records are excluded.
TYPE_2 is not a confirmed diagnosis. Possible Type 2, invalid treatment data, and records
with conflicting stage evidence are excluded. Medication rows
contain only named drugs; prescription questionnaire status is stored separately.

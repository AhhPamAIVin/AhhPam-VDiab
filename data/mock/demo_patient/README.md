# Demo patient — DEMO-P-0001

One synthetic Type 2 diabetes patient, 60 days of daily logs, 2 appointments (day 30
and day 60), used to exercise the AI reflection ("insight discovery") and RAG
pipelines end-to-end before wiring either into the real backend.

Regenerate with: `python3 generate_demo_patient.py` (deterministic, seeded — reruns
produce byte-identical output). `clinical_notes.json` and `expected_reflections.json`
are hand-written *by reference to the generator's printed stats* — if you change the
generator's probabilities/seed, these two files go stale and must be rewritten to
match the new numbers.

| File | What it is |
| --- | --- |
| `patient_profile.json` | 1 patient, fields modeled on `data/NHANES/deployment_dataset/patient_profiles.csv` + `patient_baseline_clinical.csv`. Demographics/labs are plausible fabrications, not a real NHANES row. |
| `doctor_profile.json` | Reuses a real row from `doctor_profiles.csv` (DR-0006, synthetic demo identity per that dataset's own convention). |
| `glucose_records.json` | 176 readings (fasting/after-lunch/after-dinner × 60 days, minus a few simulated missed readings). Matches `GlucoseRecord` fields plus `meal_context`/`measurement_source`/`symptoms`, now added to `health/models.py` (see design doc §9). |
| `meal_records.json` | 158 of 180 possible rows (breakfast/lunch/dinner × 60 days). Logging a meal is a separate, independently-skippable action from measuring glucose that day — `MealRecord` and `GlucoseRecord` are unrelated tables, so the generator doesn't assume one implies the other. Uses its own RNG stream (`meal_log_rng`) so these gaps don't shift the glucose numbers cited in `clinical_notes.json`. |
| `activity_records.json` | 36 rows — only on days the patient both exercised *and* bothered to log it (two independent conditions; absence = skip, same convention the current model already uses). |
| `clinical_notes.json` | 2 notes, one per appointment, in the doctor's voice, citing the exact stats the generator printed (BYT 5481 targets: HbA1c <7%, fasting 80-130 mg/dL, post-prandial <180 mg/dL). |
| `expected_reflections.json` | Worked example of what the reflection step should produce: insight #1 created after appointment 1, then **updated** (not duplicated) after appointment 2, plus a genuinely **new** insight #2 created only after appointment 2. Includes the two eval cases this is meant to exercise. |

## The story, in one paragraph

Month 1: patient does well on weekdays (72.7% exercise adherence) but every single
weekend has a heavy dinner and zero exercise, pushing after-dinner glucose to 205.9
mg/dL average (100% of weekend readings over the 180 mg/dL post-prandial target).
Doctor's note #1 flags this. Month 2: weekday behavior improves to 100% adherence
and 0% of readings over target — the doctor's advice clearly worked on weekdays —
but weekends barely move (33.3% adherence, 192.8 mg/dL average, 66.7% over target).
Separately, 2 weekdays in month 2 show a new pattern that didn't exist in month 1:
mild dizziness/tremor in the afternoon on days the patient skipped lunch for work,
with glucose readings of 72 and 78 mg/dL — a plausible mild hypoglycemia signal tied
to skipping meals while on Metformin.

That gives the reflection pipeline exactly one case of each required behavior: an
insight that must be *updated* in place (narrowed from "weekdays and weekends" to
"weekends only"), and an insight that must be *newly created* (the lunch-skipping
pattern has no precedent in month 1 to match against).

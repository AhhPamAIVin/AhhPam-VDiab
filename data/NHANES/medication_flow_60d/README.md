# Synthetic Medication Happy-Case Flow (60 Days)

Application-test data generated from observed NHANES medication names plus dose
references in the local knowledge base.

## Scope

- Algorithm-labelled `TYPE_2` patients only.
- Exact, single-ingredient knowledge-base matches only.
- Synthetic starting-dose scenario, reminder, acknowledgement, and administration.
- 100% happy-case adherence: every reminder is delivered/acknowledged and every
  administration occurs within 0-5 minutes of schedule.

## Exclusions

- Insulin, because dosing requires patient-specific titration.
- Combination products, because formulation/strength is not recorded.
- Semaglutide when route/formulation is unknown.
- Prediabetes records and uncertain/non-unique matches.

## Safety

This dataset is not a prescription, clinical recommendation, or record of real
medication adherence. Every generated row is synthetic and clinical use is forbidden.

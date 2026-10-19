# v1_hgb near-perfect metrics: investigation and finding

`artifacts/personalized_watch/v1_hgb/metrics.json` reports precision/recall/F1/PR-AUC/ROC-AUC
all ~0.999 on validation and ~0.998 on the patient-holdout test split. This document is the
evidence trail for what that number actually means. Short answer up front:

**This is a genuine, fully-explained separability artifact of a rule-generated weak label —
not an engineering bug where a forbidden/future/label-derived column leaked in.** The existing
`test_no_leakage.py` correctly passes: no column derived *from* the rule engine's output
(`attention_score`, `evidence_snapshot`, etc.) is present in the feature table. But several of
v1_hgb's *input* features are, by construction, near-exact copies of the same arithmetic the
rule engine uses to produce the label in the first place, and the label's two remaining
decision boundaries turn out to be **universal constants identical for every patient in this
dataset**, not truly personalized values — so a plain threshold split on one raw feature
(`glucose_mg_dl`) already reconstructs most of the label. This was independently reached by
reading the code and confirmed with an executable reconstruction (below); it also matches, in
detail, what `artifacts/personalized_watch/v1_hgb/model_card.md`'s own permutation-importance
section already documents (see "This was already known" at the end).

## 1. What the rule engine actually is

`data/NHANES/final/build_personalized_attention_dataset.py:221-246` assigns `status` per
measurement with this precedence (`value` = the measurement's `glucose_mg_dl`):

```
221  status = "NORMAL"
...
225  if value < danger_low:                                            -> DANGER
227  elif value > danger_high:                                         -> DANGER
229  elif baseline_end is not None and dt < baseline_end:               -> BASELINE_LEARNING
231  elif danger_low <= value < target_low:                             -> WATCH (LEVEL_1_LOW)
233  elif target_high < value <= danger_high:                           -> WATCH (ABOVE_TARGET*)
239  elif target_low <= value < target_low+10 and trend_rate <= -0.5:   -> WATCH (NEAR_LOW_FALLING)
241  elif target_high-20 <= value <= target_high and trend_rate >= 0.5: -> WATCH (NEAR_HIGH_RISING)
243  elif baseline_usable and value > baseline.p90 + 20:                -> WATCH (ABOVE_PERSONAL_BASELINE)
245  elif baseline_usable and value < baseline.p10 - 15:                -> WATCH (BELOW_PERSONAL_BASELINE)
```

`trend_rate` itself is computed at `build_personalized_attention_dataset.py:210-214`:

```python
if previous is not None:
    gap = (dt - previous["_dt"]).total_seconds() / 60
    if 0 < gap <= TREND_MAX_GAP_MINUTES:  # 180
        trend_rate = (value - float(previous["glucose_mg_dl"])) / gap
```

## 2. The same arithmetic is duplicated into the feature table

`data/NHANES/final/build_watch_feature_table.py:176-197` computes, from the identical prior
measurement and the identical `TREND_MAX_GAP_MINUTES = 180` cap
(`build_watch_feature_table.py:45`):

```python
if 0 < gap_minutes <= TREND_MAX_GAP_MINUTES:
    rate = round(delta / gap_minutes, 4)          # -> rate_mg_dl_per_min
...
dev_p10 = round(value - float(p10), 1)             # -> deviation_from_baseline_p10
dev_p90 = round(value - float(p90), 1)             # -> deviation_from_baseline_p90
```

`rate_mg_dl_per_min`, `deviation_from_baseline_p10`, `deviation_from_baseline_p90`, and
`baseline_usable` are all listed in `artifacts/personalized_watch/v1_hgb/feature_schema.json`'s
`numeric_features`. So four of the model's ~50 input columns are, up to rounding, the literal
decision variables the rule engine itself computes for the `NEAR_LOW_FALLING`,
`NEAR_HIGH_RISING`, `ABOVE_PERSONAL_BASELINE`, and `BELOW_PERSONAL_BASELINE` branches — not
future data, not the label itself, but a same-timestep restatement of the rule's own inputs.
This is legitimate under the project's own leakage definition (nothing here looks past
`measured_at`, and `test_no_leakage.py`'s `FORBIDDEN_COLUMNS` list is specifically about
rule-*output* artifacts, not input-proxy features) — but it means a tree model doesn't need to
learn any real clinical pattern to hit these branches, only to rediscover the same `+20`/`-15`/
`±0.5` cutoffs already baked into the feature.

## 3. The remaining two branches don't even need that: thresholds are universal constants

`LEVEL_1_LOW` and `ABOVE_TARGET*` depend on `target_low`/`target_high`/`danger_low`/
`danger_high`, which are **not** in the feature table directly — but they turn out not to need
to be, because they are the same four numbers for every single patient in the dataset.
`data/NHANES/final/build_initial_diabetes_thresholds.py:211-216` sets them from fixed ADA
defaults:

```python
"normal_low_mg_dl": 70,
"normal_high_mg_dl": 180,
"danger_low_mg_dl": 54,
"danger_high_mg_dl": 250,
```

This is **not** just the initial value — it is the *only* value ever recorded. Every clinical
review event that could have personalized these thresholds is confirmed, per-file, to carry the
identical tuple:

```
$ python3 -c "...read data/knowledge_structured/longitudinal_care/patient_clinical_review_events.csv..."
total rows: 489
distinct (target_low, target_high, danger_low, danger_high) tuples: 1
('70', '180', '54', '250')
Counter({'INITIALIZE_TARGETS': 489})
```

489 patients, 489 `INITIALIZE_TARGETS` events, zero `UPDATE_TARGETS` events. So `glucose_mg_dl`
— a perfectly ordinary, legitimate, present-only-through-`measured_at` feature — already
encodes `LEVEL_1_LOW` (`54 <= glucose < 70`) and `ABOVE_TARGET*` (`180 < glucose <= 250`)
almost exactly on its own, because the "personalized" cutoff is the same constant for
everyone. `docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md` §9 already flags this in different words:
*"Cut point không thực sự khác nhau theo từng bệnh nhân"* ("the cut point isn't actually
different per patient").

## 4. Executable proof: zero-error reconstruction, no ML involved

A hand-coded rule using only `glucose_mg_dl`, `rate_mg_dl_per_min`,
`deviation_from_baseline_p90`, `deviation_from_baseline_p10`, and `baseline_usable` — all raw
columns in v1_hgb's own `feature_schema.json`, combined with the fixed constants from §3 above
— reconstructs `y_watch` on the full 61,118-row eligible (WATCH/NORMAL) population with:

```
n=61118  tp=25459  fp=0  fn=0  tn=35659   accuracy=1.000000  precision=1.0  recall=1.0  f1=1.0
```

Reproducible via `check_v1_hgb_known_separability()` in `watch_gate_checks.py` (also wired into
`test_v1_hgb_known_separability.py`, part of `make eval-watch-classifier`). This is not an
approximation of v1_hgb's ~0.999 — it *exceeds* it, because HistGradientBoosting's probability
threshold at 0.5 occasionally lands the wrong side of a rounding boundary near a cutoff (the 7
validation errors and 19 test errors in v1_hgb's own confusion matrices), while the exact
closed-form rule has none. In other words: **the six-line rule above is a strictly better
predictor of `y_watch` than v1_hgb is**, using a strict subset of v1_hgb's own features. That is
about as direct as leakage evidence gets — the "model" is provably redundant with arithmetic
already sitting in its own input columns.

## 5. Why the static clinical block contributes ~nothing

`artifacts/personalized_watch/v2_quantile_regression/metrics.json`'s
`clinical_feature_permutation_importance_test` and v1_hgb's own model card both show the static
clinical block (HbA1c, BMI, comorbidity flags, `risk_group`, medication risk) contributing
importance indistinguishable from noise for the classification task. This is consistent, not
contradictory: the rule engine in §1 never reads any of those columns when deciding
`WATCH`/`NORMAL` (`risk_group` only adjusts `attention_score`, a *severity* number, at
`build_personalized_attention_dataset.py:252-254` — after status is already decided — and
`medication_hypoglycemia_risk` similarly only bumps score at line 249-251). A model trained to
reproduce that label has no signal to find there, regardless of how good the model is.

## 6. This was already known

`artifacts/personalized_watch/v1_hgb/model_card.md` (section *"Vì sao điểm số gần như tuyệt
đối"*) already reports permutation importance showing `glucose_mg_dl` alone accounts for ~92%
of the PR-AUC drop when permuted, for the same reason given in §3 above, and explicitly warns:
*"Đây không phải bằng chứng mô hình đã học được tín hiệu lâm sàng mới"* ("this is not evidence
the model learned new clinical signal"). This investigation independently rediscovered the same
mechanism from the code and data (not from reading that section first) and adds: (a) exact
file:line citations tying specific feature columns to specific rule branches, (b) proof that the
"universal constant" threshold assumption is exactly true for 100% of the 489-patient dataset
(not just plausible), and (c) a zero-error closed-form reconstruction rather than a permutation-
importance estimate — i.e. this is airtight, not merely likely.

## 7. Why this doesn't affect the live system

**v1_hgb is not the deployed model.** `codebase/backend/health/threshold_service.py` (out of
scope for this investigation, not modified) calls into `v2_quantile_regression`, a quantile
*regression* model that predicts the glucose band directly rather than a WATCH/NORMAL
classification — see that model's own `model_card.md`. Critically, `v2`'s training code
(`data/NHANES/final/train_threshold_regression.py`) already excludes `glucose_mg_dl`,
`rate_mg_dl_per_min`, and both `deviation_from_baseline_*` columns from its feature set for the
mirror-image reason (`CURRENT_VALUE_DERIVED_COLUMNS`, since those would leak the regression
*target* rather than reconstruct a classification *label*) — enforced today by
`test_regression_no_leakage.py` and additionally, by this change, by
`test_v2_quantile_thresholds.py`'s `v2_feature_schema_no_target_leakage` check against the
actually-shipped `feature_schema.json`. So the specific mechanism documented here does not
carry over to the live model as-is.

## 8. What this means going forward

- Don't read v1_hgb's precision/recall/PR-AUC as a clinical-skill number, ever — it is a
  measurement of "how well can HistGradientBoosting rediscover 6 known if/else rules from
  features that already contain their inputs," not "how well can ML predict a doctor-relevant
  attention event." The model card already says this; this document adds the receipts.
- The deeper, unresolved risk is upstream of any modeling choice: **`y_watch` is a rule-derived
  weak label, not a doctor-confirmed outcome** (`docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md` §9).
  Until real doctor-reviewed WATCH outcomes exist (Giai đoạn 3/4 in that doc — shadow mode +
  clinician review), no classifier trained on this label, however engineered, can be validated
  as clinically useful — the near-perfect numbers or otherwise-decent numbers would both be
  reporting the same thing: agreement with a rule, not agreement with reality.
- If a future version personalizes `target_low`/`target_high`/`danger_low`/`danger_high` per
  patient (making §3's "universal constant" assumption false), re-run
  `check_v1_hgb_known_separability()` — a real drop in its reconstructed accuracy would mean the
  mechanism described here has changed and both this document and v1_hgb's model card need
  updating, not that leakage has been "fixed" (the `rate_mg_dl_per_min`/`deviation_from_baseline_*`
  mechanism in §2 would still fully apply to 4 of 6 WATCH branches independent of
  personalization).

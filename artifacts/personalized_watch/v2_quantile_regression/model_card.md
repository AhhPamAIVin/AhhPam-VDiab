# Model card — personalized WATCH quantile v2

## Intended use

Predict a patient-context early-attention interval for non-DANGER glucose readings. The doctor-approved DANGER gate always runs first. This model does not change treatment or clinical thresholds.

## Data and split

- Synthetic TYPE_2 dataset only; no real-patient clinical validation.
- Train: 32,688 rows; validation: 16,002; patient-holdout test: 12,428.
- Feature table SHA-256: `5dc58594d029597908a8bfe732c05849598bea18884b49a3e94026f48dea13c8`.

## Calibration and performance

- Raw quantiles: 0.10/0.90; target coverage: 80%.
- Split-conformal margin fitted only on validation: 0.891 mg/dL.
- Validation calibrated coverage: 0.8001.
- Patient-holdout calibrated coverage: 0.7890.
- Patient-holdout mean band width: 57.80 mg/dL.

## Safety and fallback

- DANGER is decided before ML and ML is not called for DANGER.
- Non-finite output or quantile crossing falls back to the doctor target range.
- Missing model, insufficient baseline, missing meal context, or incomplete patient profile also uses the doctor target range.

## Limitations

- Synthetic measurements and weak rule labels are not evidence of clinical benefit.
- RANDOM/unscheduled measurements are sparse and out-of-distribution.
- Real-patient evaluation and doctor-confirmed WATCH labels are still required.
- Clinical feature importance is reported in `metrics.json`; a small value means the feature adds little predictive information in this synthetic dataset.

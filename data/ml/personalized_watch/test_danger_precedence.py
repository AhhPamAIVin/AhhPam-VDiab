"""DANGER-precedence safety test (Giai doan C, step 9 in
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md): proves the ML model can never be
called, let alone override a decision, once a reading is DANGER by the fixed
doctor-approved cutoffs.

Run directly: pytest data/ml/personalized_watch/test_danger_precedence.py
"""

import csv
from pathlib import Path

import pandas as pd
import pytest

from serving import evaluate_measurement

OUTPUT_DIR = Path(__file__).parent
FEATURE_TABLE = OUTPUT_DIR / "feature_table.csv"


def _exploding_predictor(_row: pd.DataFrame) -> float:
    """A model stand-in that fails the test the instant it's invoked — the
    only way test_danger_gate_never_calls_ml below can pass is if the
    DANGER branch in evaluate_measurement returns before ever reaching it."""
    raise AssertionError("ML predictor was called on a DANGER reading — Lop 1 must gate this")


@pytest.mark.parametrize(
    ("glucose", "danger_low", "danger_high", "expected_status"),
    [
        (53.9, 54, 250, "DANGER"),   # just below danger_low
        (0.0, 54, 250, "DANGER"),    # far below
        (250.1, 54, 250, "DANGER"),  # just above danger_high
        (600.0, 54, 250, "DANGER"),  # far above (max observed in the dataset)
    ],
)
def test_danger_gate_never_calls_ml(glucose, danger_low, danger_high, expected_status):
    result = evaluate_measurement(
        glucose_mg_dl=glucose,
        danger_low=danger_low,
        danger_high=danger_high,
        feature_row=pd.DataFrame(),  # never touched if the gate works
        predict_watch_probability=_exploding_predictor,
    )
    assert result.status == expected_status
    assert result.ml_called is False
    assert result.watch_probability is None


@pytest.mark.parametrize(("glucose", "danger_low", "danger_high"), [(54.0, 54, 250), (250.0, 54, 250)])
def test_boundary_values_are_not_danger(glucose, danger_low, danger_high):
    """Matches the rule engine's own strict-inequality semantics exactly —
    build_personalized_attention_dataset.py uses `value < danger_low` and
    `value > danger_high`, so the boundary values themselves are not DANGER."""
    calls = []

    def recording_predictor(_row: pd.DataFrame) -> float:
        calls.append(1)
        return 0.0  # NORMAL regardless — this test only cares that ML *was* asked

    result = evaluate_measurement(
        glucose_mg_dl=glucose,
        danger_low=danger_low,
        danger_high=danger_high,
        feature_row=pd.DataFrame([{}]),
        predict_watch_probability=recording_predictor,
    )
    assert result.status != "DANGER"
    assert result.ml_called is True
    assert len(calls) == 1


def test_non_danger_uses_operating_threshold():
    high_prob = evaluate_measurement(
        glucose_mg_dl=200, danger_low=54, danger_high=250,
        feature_row=pd.DataFrame([{}]), predict_watch_probability=lambda _r: 0.9,
    )
    low_prob = evaluate_measurement(
        glucose_mg_dl=200, danger_low=54, danger_high=250,
        feature_row=pd.DataFrame([{}]), predict_watch_probability=lambda _r: 0.1,
    )
    assert high_prob.status == "WATCH"
    assert low_prob.status == "NORMAL"


def test_every_real_danger_row_is_gated_before_reaching_ml():
    """Data-driven version of the same guarantee: every row the rule engine
    actually labeled DANGER in the built feature table must still be
    classified DANGER by evaluate_measurement from its glucose value and
    fixed cutoffs alone — never by asking the ML model."""
    if not FEATURE_TABLE.exists():
        pytest.skip(f"{FEATURE_TABLE} not built yet — run build_watch_feature_table.py first")

    with FEATURE_TABLE.open(encoding="utf-8-sig", newline="") as handle:
        danger_rows = [row for row in csv.DictReader(handle) if row["status"] == "DANGER"]
    assert danger_rows, "expected at least one DANGER row in the built feature table"

    DANGER_LOW, DANGER_HIGH = 54.0, 250.0  # see plan section 3.2 — fixed for all 489 patients today
    for row in danger_rows:
        result = evaluate_measurement(
            glucose_mg_dl=float(row["glucose_mg_dl"]),
            danger_low=DANGER_LOW,
            danger_high=DANGER_HIGH,
            feature_row=pd.DataFrame(),
            predict_watch_probability=_exploding_predictor,
        )
        assert result.status == "DANGER", row
        assert result.ml_called is False, row

"""Leakage contract for the quantile-regression threshold model (mirror of
test_no_leakage.py's contract, but for the other target).

The regression target is glucose_mg_dl itself, so anything computed *from*
the current reading (delta, rate, deviation-from-baseline) must be excluded
from the feature set here — including them would trivially leak the value
being predicted. This is the opposite direction from the classifier's
leakage list (which excluded columns *derived from the label*), not a
duplicate of it.

Run directly: pytest data/ml/personalized_watch/test_regression_no_leakage.py
"""

from pathlib import Path

import pytest

from train_threshold_regression import CURRENT_VALUE_DERIVED_COLUMNS, load_regression_table

OUTPUT_DIR = Path(__file__).parent
FEATURE_TABLE = OUTPUT_DIR / "feature_table.csv"

EXPECTED_EXCLUDED = {
    "glucose_mg_dl",
    "delta_glucose_mg_dl",
    "rate_mg_dl_per_min",
    "deviation_from_baseline_median",
    "deviation_from_baseline_p10",
    "deviation_from_baseline_p90",
}


@pytest.fixture(scope="module")
def loaded():
    if not FEATURE_TABLE.exists():
        pytest.skip(f"{FEATURE_TABLE} not built yet — run build_watch_feature_table.py first")
    return load_regression_table(FEATURE_TABLE)


def test_current_value_derived_columns_are_excluded(loaded):
    _df, numeric, categorical = loaded
    regression_columns = set(numeric) | set(categorical)
    leaked = regression_columns & EXPECTED_EXCLUDED
    assert not leaked, f"columns derived from the current reading leaked into regression features: {leaked}"


def test_exclusion_list_matches_module_constant(loaded):
    # Guards against someone editing CURRENT_VALUE_DERIVED_COLUMNS in
    # train_threshold_regression.py without updating this test's own
    # expectation of what must be excluded.
    assert CURRENT_VALUE_DERIVED_COLUMNS == EXPECTED_EXCLUDED


def test_history_based_features_still_present(loaded):
    """The point isn't to exclude everything time-series-related — rolling
    stats and raw baseline percentiles are computed from *prior* readings
    only and must still be usable predictors."""
    _df, numeric, categorical = loaded
    regression_columns = set(numeric) | set(categorical)
    for expected in ("rolling_mean_last3", "rolling_mean_last10", "baseline_median_mg_dl", "meal_context"):
        assert expected in regression_columns, f"{expected} should be a valid predictor but is missing"


def test_target_column_present_and_numeric(loaded):
    df, _numeric, _categorical = loaded
    assert "glucose_mg_dl" in df.columns
    assert df["glucose_mg_dl"].dtype.kind in "fi"

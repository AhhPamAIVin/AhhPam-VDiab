"""Threshold gate for artifacts/personalized_watch/v2_quantile_regression —
the CURRENT LIVE model behind codebase/backend/health/threshold_service.py's
evaluate_glucose_reading() (that serving code itself is out of scope here;
this only reads the trained artifact's metrics.json/feature_schema.json).

Every bound is defined in watch_gate_checks.py alongside its rationale, so
both `pytest` (this file) and `make eval-watch-classifier`
(gate.py's JSON report) assert the exact same thing.

Run directly: pytest eval/watch_classifier/test_v2_quantile_thresholds.py
"""

import pytest

from eval.watch_classifier.watch_gate_checks import V2_DIR, run_all_v2_checks

pytestmark = pytest.mark.skipif(
    not (V2_DIR / "metrics.json").exists(),
    reason=f"{V2_DIR / 'metrics.json'} not found — train_threshold_regression.py has not been run",
)


@pytest.fixture(scope="module")
def checks() -> list[dict]:
    return run_all_v2_checks()


def _build_parametrized_ids() -> list[str]:
    if not (V2_DIR / "metrics.json").exists():
        return []
    return [c["id"] for c in run_all_v2_checks()]


@pytest.mark.parametrize("check_id", _build_parametrized_ids())
def test_individual_v2_threshold_check(checks: list[dict], check_id: str) -> None:
    """One assertion per named threshold check (coverage, pinball loss, band
    width, calibration margin, feature-schema leakage, ...) so a CI failure
    view / `pytest -v` shows exactly which check regressed."""
    by_id = {c["id"]: c for c in checks}
    c = by_id[check_id]
    assert c["passed"], f"{c['description']}: actual={c['actual']!r} threshold={c['threshold']}"

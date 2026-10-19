"""Regression/documentation-guard test for the v1_hgb near-perfect-metrics
investigation — see V1_HGB_LEAKAGE_FINDING.md for the full write-up.

v1_hgb is NOT the live model (artifacts/personalized_watch/
v2_quantile_regression is — see that model_card.md's own "Intended use").
This test does not gate a deployment; it pins down *why*
artifacts/personalized_watch/v1_hgb/metrics.json reports ~0.999
precision/recall/PR-AUC, by proving a small hand-coded rule using only
features already in v1_hgb's own feature_schema.json reconstructs y_watch
almost exactly, with no ML at all. If this reconstruction's agreement with
y_watch ever drops well below the threshold, the explanation this repo now
documents (this file, watch_gate_checks.py, V1_HGB_LEAKAGE_FINDING.md, and
v1_hgb/model_card.md's own permutation-importance section) has gone stale
and must be re-derived against whatever changed.

Run directly: pytest eval/watch_classifier/test_v1_hgb_known_separability.py
"""

import pytest

from eval.watch_classifier.watch_gate_checks import FEATURE_TABLE, check_v1_hgb_known_separability

pytestmark = pytest.mark.skipif(
    not FEATURE_TABLE.exists(),
    reason=f"{FEATURE_TABLE} not built yet — run build_watch_feature_table.py first",
)


def test_v1_hgb_metrics_are_explained_by_rule_reconstruction() -> None:
    checks = check_v1_hgb_known_separability()
    assert len(checks) == 1
    c = checks[0]
    assert c["passed"], (
        f"{c['description']}: actual={c['actual']!r} threshold={c['threshold']} — "
        "if this reconstruction's agreement with y_watch has genuinely dropped, "
        "V1_HGB_LEAKAGE_FINDING.md and v1_hgb/model_card.md need updating, not this assertion loosened."
    )

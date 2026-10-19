"""Data completeness / missing-field flags.

OWNER: AI Summary workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §4.1, PRD §21. Reads
state["tool_results"] (from fetch_data.py, same workstream) and populates
state["data_completeness"] — must be honest about gaps (e.g. this mock
patient's meal/activity logs already have realistic independent gaps, see
data/mock/demo_patient/README.md) so synthesize_summary.py can say
"insufficient data" instead of inventing a trend from too few points.

Also runs (a narrower slice) for QA: calculate_health_metrics's avg/min/max
is computed here and stashed in tool_results["metrics"] for BOTH intents, so
synthesize_qa.py can hand the model an already-computed average instead of
making it sum/divide a raw records list itself — arithmetic the model does
on its own is exactly the kind of "invented number" §2's no-fabrication rule
is meant to rule out, same reasoning as every other tool-sourced number. Only
the completeness/expected-cadence scoring below (data_completeness) stays
SUMMARY-only — that's about whether there's *enough* data to trend, which is
meaningless for a QA question that's just asking "what's my average X".
"""

from __future__ import annotations

from datetime import datetime

from codebase.models.agents.state import AgentState
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics

# Expected daily cadence, used only to *score* completeness — not a claim that
# every gap is a data-quality problem. Glucose/meals follow the generator's own
# convention (fasting+after-lunch+after-dinner / breakfast+lunch+dinner, see
# data/mock/demo_patient/README.md). Activity logging is deliberately NOT
# scored against a daily expectation below — exercising is optional and
# patient-initiated, so an unlogged day isn't "missing data" the way an
# unlogged glucose reading is.
EXPECTED_GLUCOSE_READINGS_PER_DAY = 3
EXPECTED_MEALS_PER_DAY = 3

# Below this many glucose readings in the window, treat trend statements as
# unreliable rather than let synthesize_summary build one from too few points.
MIN_GLUCOSE_READINGS_FOR_TRENDS = 4

_ACTIVITY_NOTE = (
    "Vận động thể chất do bệnh nhân tự ghi nhận, không có số ngày kỳ vọng cố định — "
    "thiếu bản ghi không đồng nghĩa với việc không vận động."
)

# Same reasoning as activity logging: blood pressure is patient-initiated,
# not tied to a fixed daily cadence like glucose/meals, so it's scored the
# same lighter way (found + days_logged, no expected/completeness_pct).
_BLOOD_PRESSURE_NOTE = (
    "Huyết áp do bệnh nhân tự đo và ghi nhận, không có số lần kỳ vọng cố định trong ngày — "
    "thiếu bản ghi không đồng nghĩa với việc không đo."
)


def _days_in_range(date_range: tuple[datetime, datetime] | None) -> int:
    if not date_range:
        return 0
    start, end = date_range
    return max((end.date() - start.date()).days + 1, 1)


def _completeness_pct(found: int, expected: int) -> float:
    if expected <= 0:
        return 0.0
    return round(min(found / expected, 1.0) * 100, 1)


async def check_data_quality(state: AgentState) -> dict:
    """Computes precomputed metrics (both intents) and, for SUMMARY only,
    data-completeness/expected-cadence scoring in state["data_completeness"]
    — see module docstring for why activity/blood-pressure are scored more
    leniently than glucose/meals."""
    if state.get("intent") not in ("SUMMARY", "QA"):
        return {}

    tool_results = state.get("tool_results") or {}
    records = tool_results.get("records") or {}
    metrics = calculate_health_metrics(records)

    if state.get("intent") != "SUMMARY":
        # QA doesn't need completeness/expected-cadence scoring (that's a
        # "is there enough to trend" judgment, not relevant to answering a
        # single personal-data question) — just hand synthesize_qa.py the
        # precomputed metrics, same stash-into-tool_results shape as below.
        return {"tool_results": {**tool_results, "metrics": metrics}}

    days = _days_in_range(state.get("date_range"))
    expected_glucose = days * EXPECTED_GLUCOSE_READINGS_PER_DAY
    expected_meals = days * EXPECTED_MEALS_PER_DAY

    completeness = {
        "days_in_range": days,
        "glucose": {
            "expected": expected_glucose,
            "found": metrics["glucose_count"],
            "days_logged": metrics["glucose_days_logged"],
            "completeness_pct": _completeness_pct(metrics["glucose_count"], expected_glucose),
        },
        "meals": {
            "expected": expected_meals,
            "found": metrics["meal_count"],
            "days_logged": metrics["meal_days_logged"],
            "completeness_pct": _completeness_pct(metrics["meal_count"], expected_meals),
        },
        "activities": {
            "found": metrics["activity_count"],
            "days_logged": metrics["activity_days_logged"],
            "note": _ACTIVITY_NOTE,
        },
        "blood_pressure": {
            "found": metrics["blood_pressure_count"],
            "days_logged": metrics["blood_pressure_days_logged"],
            "note": _BLOOD_PRESSURE_NOTE,
        },
        "sufficient_for_trends": metrics["glucose_count"] >= MIN_GLUCOSE_READINGS_FOR_TRENDS,
    }

    # Stash the computed metrics alongside the raw records so synthesize_summary
    # (and any retry LangGraph routes back to synthesize_dispatch, which never
    # re-runs this node) doesn't have to recompute them — state["tool_results"]
    # is a plain dict field (no LangGraph reducer), so it's fully replaced on
    # write; spread the existing contents rather than dropping profile/records.
    return {
        "data_completeness": completeness,
        "tool_results": {**tool_results, "metrics": metrics},
    }

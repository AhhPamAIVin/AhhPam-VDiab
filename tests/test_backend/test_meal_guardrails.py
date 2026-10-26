"""Epic 1.8: meal-related unsafe answers are blocked by the output guardrail,
and their safe counterparts are not. Cases: eval/data/guardrail_meal_cases.json
(shared with eval/respond_agent/guardrail_judge.py)."""

import json
from pathlib import Path

import pytest

from codebase.models.agents.nodes.guardrail import guardrail_check

_CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "eval" / "data" / "guardrail_meal_cases.json").read_text(encoding="utf-8")
)["cases"]


def test_at_least_eight_blocked_cases():
    assert sum(case["expected_verdict"] == "blocked" for case in _CASES) >= 8


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
async def test_meal_guardrail_case(case):
    # SUMMARY intent: only the phrasing checks apply, not QA's citation checks.
    result = await guardrail_check({"intent": "SUMMARY", "response": case["response"], "retry_count": 0})
    failures = result.get("guardrail_failures") or []
    if case["expected_verdict"] == "blocked":
        assert set(case["expected_failures"]) <= set(failures), failures
    else:
        assert failures == [], failures

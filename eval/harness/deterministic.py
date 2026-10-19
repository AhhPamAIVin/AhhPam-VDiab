from __future__ import annotations

import asyncio
from typing import Any

from codebase.backend.health.schemas import GlucoseRecordCreate
from codebase.models.agents.nodes.classify_intent import classify_intent


def _summary(name: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    executed = [item for item in details if item["status"] == "EXECUTED"]
    passed = sum(bool(item["passed"]) for item in executed)
    return {
        "kind": "production_code_eval",
        "module": name,
        "total": len(details),
        "executed": len(executed),
        "not_implemented": sum(item["status"] == "NOT_IMPLEMENTED" for item in details),
        "passed": passed,
        "failed": len(executed) - passed,
        "pass_rate": passed / len(executed) if executed else None,
        "details": details,
    }


def evaluate_rule_engine(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Report the product gap; metrics aggregation is not an alert engine."""
    details = [
        {
            "id": case["id"],
            "status": "NOT_IMPLEMENTED",
            "passed": None,
            "reason": "No production F2 alert-classification function is wired into the backend.",
        }
        for case in cases
    ]
    return _summary("rule_engine", details)


def evaluate_validation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute existing glucose request schemas; never emulate missing APIs."""
    details: list[dict[str, Any]] = []
    for case in cases:
        if case["entity"] != "glucose_record":
            details.append(
                {
                    "id": case["id"],
                    "status": "NOT_IMPLEMENTED",
                    "passed": None,
                    "reason": f"No mapped create contract for {case['entity']}",
                }
            )
            continue
        if case.get("context", {}).get("existing_record"):
            details.append(
                {
                    "id": case["id"],
                    "status": "NOT_IMPLEMENTED",
                    "passed": None,
                    "reason": "Duplicate detection requires the API/database integration test.",
                }
            )
            continue
        payload = {**case["payload"], "patient_id": "00000000-0000-0000-0000-000000000001"}
        error = None
        try:
            GlucoseRecordCreate.model_validate(payload)
            actual = "ACCEPTED"
        except ValueError as exc:
            actual = "REJECTED"
            error = str(exc)
        expected = case["expected_result"]["status"]
        details.append(
            {
                "id": case["id"],
                "status": "EXECUTED",
                "passed": actual == expected,
                "expected": expected,
                "actual": actual,
                "error": error,
                "note": "Schema-level check; endpoint/RBAC/duplicate side effects require integration mapping.",
            }
        )
    return _summary("input_validation", details)


async def evaluate_intents(cases: list[dict[str, Any]]) -> dict[str, Any]:
    deterministic_intents = {"JAILBREAK", "HIGH_RISK", "OUT_OF_SCOPE"}
    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case.get("expected_behavior", {}).get("intent")
        if expected == "SUMMARY":
            continue
        result = await classify_intent({"message": case["prompt"], "request_kind": "chat"})
        response = result.get("canned_response") or ""
        forbidden = case.get("expected_behavior", {}).get("forbidden_content", [])
        forbidden_hits = [text for text in forbidden if text.lower() in response.lower()]
        needs_canned = expected in deterministic_intents
        passed = (
            result.get("intent") == expected
            and bool(response) == needs_canned
            and not forbidden_hits
        )
        details.append(
            {
                "id": case["id"],
                "status": "EXECUTED",
                "passed": passed,
                "expected": expected,
                "actual": result.get("intent"),
                "has_canned_response": bool(response),
                "forbidden_hits": forbidden_hits,
            }
        )
    report = _summary("deterministic_intents", details)
    safety = [item for item in details if item["expected"] in {"JAILBREAK", "HIGH_RISK"}]
    safe_queries = [item for item in details if item["expected"] == "QA"]
    report["safety_recall"] = (
        sum(item["actual"] == item["expected"] for item in safety) / len(safety) if safety else None
    )
    report["safe_query_false_positive_rate"] = (
        sum(item["actual"] in deterministic_intents for item in safe_queries) / len(safe_queries)
        if safe_queries
        else None
    )
    return report


def run_intent_eval(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return asyncio.run(evaluate_intents(cases))

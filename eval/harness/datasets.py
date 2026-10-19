from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = EVAL_DIR / "data"


def load_cases(filename: str) -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))["cases"]


def load_all() -> dict[str, list[dict[str, Any]]]:
    return {
        "ai": load_cases("ai_scenarios_30.json"),
        "rules": load_cases("rule_engine_cases.json"),
        "validation": load_cases("data_validation_cases.json"),
        "summary": load_cases("ai_summary_cases.json"),
    }


def audit_datasets(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    cases = [case for group in datasets.values() for case in group]
    ids = [case.get("id") for case in cases]
    counts = Counter(ids)
    duplicates = sorted(str(case_id) for case_id, count in counts.items() if case_id and count > 1)
    ai_cases = datasets["ai"]
    cited = [case for case in ai_cases if case.get("expected_behavior", {}).get("must_cite_sources")]
    issues: list[dict[str, str]] = []
    for case in cases:
        if not case.get("id"):
            issues.append({"case_id": "<missing>", "issue": "missing id"})
    for case in ai_cases:
        if not case.get("prompt"):
            issues.append({"case_id": case.get("id", "<missing>"), "issue": "missing prompt"})
        if not case.get("expected_behavior"):
            issues.append({"case_id": case.get("id", "<missing>"), "issue": "missing expected_behavior"})
        if not case.get("evaluation_rubric"):
            issues.append({"case_id": case.get("id", "<missing>"), "issue": "missing evaluation_rubric"})
    return {
        "kind": "dataset_integrity",
        "total_cases": len(cases),
        "unique_ids": len(set(ids)) == len(ids) and None not in ids,
        "duplicate_ids": duplicates,
        "issues": issues,
        "ai_rubric_completeness": _rate(ai_cases, lambda case: bool(case.get("evaluation_rubric"))),
        "ai_label_completeness": _rate(ai_cases, lambda case: bool(case.get("expected_behavior"))),
        "citation_label_completeness": _rate(
            cited, lambda case: bool(case["expected_behavior"].get("required_source_titles"))
        ),
        "provenance_metadata_completeness": _rate(
            cases, lambda case: bool(case.get("provenance") or case.get("source_provenance"))
        ),
        "review_metadata_completeness": _rate(
            cases, lambda case: bool(case.get("reviewer") and case.get("reviewed_at"))
        ),
        "ai_category_distribution": dict(Counter(case.get("category", "MISSING") for case in ai_cases)),
    }


def _rate(cases: list[dict[str, Any]], predicate) -> float:
    return sum(bool(predicate(case)) for case in cases) / len(cases) if cases else 1.0

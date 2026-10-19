import pytest

from eval.harness.datasets import audit_datasets, load_all
from eval.harness.deterministic import evaluate_intents, evaluate_rule_engine, evaluate_validation
from eval.harness.retrieval import _title_matches


@pytest.mark.unit
def test_golden_dataset_integrity_is_measured_not_called_product_accuracy():
    report = audit_datasets(load_all())
    assert report["kind"] == "dataset_integrity"
    assert report["total_cases"] == 76
    assert report["unique_ids"] is True
    assert report["issues"] == []


@pytest.mark.unit
def test_missing_rule_engine_is_reported_not_reimplemented_in_eval():
    report = evaluate_rule_engine(load_all()["rules"])
    assert report["executed"] == 0
    assert report["not_implemented"] == 15
    assert report["passed"] == 0


@pytest.mark.unit
def test_validation_executes_only_existing_production_contracts():
    report = evaluate_validation(load_all()["validation"])
    assert report["executed"] > 0
    assert report["not_implemented"] > 0
    assert report["executed"] + report["not_implemented"] == report["total"]


@pytest.mark.asyncio
@pytest.mark.clinical
async def test_all_emergency_cases_reach_production_high_risk_classifier():
    cases = [case for case in load_all()["ai"] if case["category"] == "HIGH_RISK_EMERGENCY"]
    report = await evaluate_intents(cases)
    assert report["executed"] == 5
    assert report["failed"] == 0, report["details"]


@pytest.mark.unit
def test_source_title_matching_is_case_accent_and_punctuation_tolerant():
    assert _title_matches("Quyết định 5481/QĐ-BYT", "QUYET DINH 5481 QD BYT - hướng dẫn")

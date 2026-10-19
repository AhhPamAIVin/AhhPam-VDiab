#!/usr/bin/env python3
"""Single entry point for VDiab evaluation evidence."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from eval.harness.datasets import audit_datasets, load_all  # noqa: E402
from eval.harness.deterministic import (  # noqa: E402
    evaluate_rule_engine,
    evaluate_validation,
    run_intent_eval,
)


def _write_report(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "results" / "latest.json")
    parser.add_argument("--strict", action="store_true", help="Fail on executed failures or missing required capability")
    parser.add_argument("--retrieval", action="store_true", help="Run real embeddings/vector-store retrieval evaluation")
    args = parser.parse_args()

    datasets = load_all()
    evaluations = {
        "rule_engine": evaluate_rule_engine(datasets["rules"]),
        "input_validation": evaluate_validation(datasets["validation"]),
        "deterministic_intents": run_intent_eval(datasets["ai"]),
    }
    if args.retrieval:
        from eval.harness.retrieval import evaluate_retrieval

        try:
            evaluations["retrieval"] = evaluate_retrieval(datasets["ai"])
        except Exception as exc:  # external embedding/vector services are optional in the fast suite
            evaluations["retrieval"] = {
                "kind": "retrieval_eval",
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }

    report = {
        "schema_version": "2.0",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "dataset_version": "1.0.0",
        "integrity": audit_datasets(datasets),
        "evaluations": evaluations,
        "separation_note": "Integrity validates labels/files; only production_code_eval and retrieval_eval execute product code.",
    }
    _write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failed = sum(value.get("failed", 0) for value in evaluations.values())
    missing = sum(value.get("not_implemented", 0) for value in evaluations.values())
    errors = sum(value.get("status") == "ERROR" for value in evaluations.values())
    return 1 if args.strict and (failed or missing or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())

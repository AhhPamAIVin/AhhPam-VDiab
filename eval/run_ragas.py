#!/usr/bin/env python3
"""Run the four core RAGAS metrics on real agent outputs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge  # noqa: E402
from eval.harness.datasets import load_cases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-results", type=Path, default=ROOT / "eval" / "results" / "agent-e2e.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "results" / "ragas.json")
    args = parser.parse_args()
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    except ImportError as exc:
        parser.error(f"Install optional dependencies first: pip install -r eval/requirements.txt ({exc})")

    agent_report = json.loads(args.agent_results.read_text(encoding="utf-8"))
    golden = {case["id"]: case for case in load_cases("ai_scenarios_30.json")}
    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    evaluated_ids = []
    for item in agent_report.get("details", []):
        case = golden.get(item.get("id"))
        if not case or item.get("status") != "EXECUTED" or not item.get("response"):
            continue
        if case.get("expected_behavior", {}).get("intent") != "QA":
            continue
        # The public API intentionally does not expose raw chunks. Re-run the
        # production retriever and record this limitation in the report.
        contexts = [chunk.get("text", "") for chunk in search_approved_knowledge(case["prompt"], k=10)]
        if not contexts:
            continue
        rows["question"].append(case["prompt"])
        rows["answer"].append(item["response"])
        rows["contexts"].append(contexts)
        rows["ground_truth"].append("\n".join(case["expected_behavior"].get("expected_key_facts", [])))
        evaluated_ids.append(case["id"])

    if not evaluated_ids:
        parser.error("No QA rows with answer and retrieved contexts are available")
    result = evaluate(
        Dataset.from_dict(rows),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
    )
    scores = {key: float(value) for key, value in dict(result).items() if isinstance(value, (int, float))}
    report = {
        "kind": "ragas_eval",
        "cases": evaluated_ids,
        "scores": scores,
        "thresholds": {
            "faithfulness": 0.7,
            "answer_relevancy": 0.7,
            "context_precision": 0.6,
            "context_recall": 0.6,
        },
        "limitation": "Contexts were re-retrieved after the API call because raw chunks are not exposed by the public API.",
    }
    report["passed"] = all(scores.get(name, 0) >= threshold for name, threshold in report["thresholds"].items())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

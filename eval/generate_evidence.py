#!/usr/bin/env python3
"""Generate Evaluation Evidence, traceability, and clinician review CSV."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.harness.datasets import load_all  # noqa: E402

CATEGORY_CODE = {
    "KNOWLEDGE_VALID": "codebase/models/agents/nodes/retrieve_knowledge.py",
    "KNOWLEDGE_INSUFFICIENT": "codebase/models/agents/nodes/synthesize_qa.py",
    "HIGH_RISK_EMERGENCY": "codebase/models/agents/nodes/classify_intent.py",
    "TREATMENT_CHANGE_OUT_OF_SCOPE": "codebase/models/agents/nodes/classify_intent.py",
    "CONFLICTING_SOURCES": "codebase/models/agents/nodes/synthesize_qa.py",
    "JAILBREAK_SECURITY": "codebase/models/agents/nodes/classify_intent.py",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _coverage(results: Path) -> dict:
    path = results / "coverage.xml"
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    return {"line_rate": float(root.attrib.get("line-rate", 0)), "branch_rate": float(root.attrib.get("branch-rate", 0))}


def _latest(results: Path, prefix: str) -> dict:
    """Most recent eval/respond_agent or eval/watch_classifier report (filenames carry a
    run timestamp, e.g. golden_scenarios_1787795250.json) — empty dict if never run."""
    candidates = sorted(results.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime)
    return _load_json(candidates[-1]) if candidates else {}


def _respond_agent_section(results: Path) -> list[str]:
    trajectory = _latest(results, "trajectory")
    guardrail = _latest(results, "guardrail_judge")
    retrieval = _latest(results, "retrieval_recall")
    golden = _latest(results, "golden_scenarios")
    faithfulness = _latest(results, "personal_data_faithfulness")
    watch = _latest(results, "eval_watch_classifier")
    def _r(value):
        return round(value, 3) if isinstance(value, float) else value

    lines = ["### 3.1 Respond-agent evals (LangSmith + Ragas) — real LLM calls, see docs/AI_Eval_Metrics_Plan.md", ""]
    if trajectory:
        lines.append(
            f"- Trajectory/routing: {_r(trajectory.get('trajectory_match_rate', 'NOT RUN'))} match rate, "
            f"{_r(trajectory.get('tool_call_match_rate', 'NOT RUN'))} tool-call match rate."
        )
    else:
        lines.append("- Trajectory/routing: NOT RUN.")
    if guardrail:
        lines.append(
            f"- Guardrail vs. independent LLM judge ({guardrail.get('num_cases', '?')} cases): "
            f"{_r(guardrail.get('guardrail_accuracy', 'NOT RUN'))} guardrail-vs-expected, "
            f"{_r(guardrail.get('judge_accuracy', 'NOT RUN'))} judge-vs-expected, "
            f"{_r(guardrail.get('guardrail_judge_agreement_rate', 'NOT RUN'))} agreement rate."
        )
    else:
        lines.append("- Guardrail vs. independent LLM judge: NOT RUN.")
    if retrieval:
        lines.append(
            f"- Retrieval recall ({retrieval.get('num_rows', '?')} rows, k={retrieval.get('k', '?')}): "
            f"{_r(retrieval.get('aggregate_exact_hit_rate', 'NOT RUN'))} exact-hit rate, "
            f"{_r(retrieval.get('aggregate_ragas_context_recall', 'NOT RUN'))} Ragas context-recall."
        )
    else:
        lines.append("- Retrieval recall: NOT RUN.")
    if golden:
        agg = {k: _r(v) for k, v in golden.get("aggregate", {}).items()}
        lines.append(f"- Golden scenarios ({golden.get('num_cases', '?')} cases): {json.dumps(agg, ensure_ascii=False)}.")
    else:
        lines.append("- Golden scenarios: NOT RUN.")
    if faithfulness:
        lines.append(
            f"- Personal-data faithfulness ({faithfulness.get('num_cases', '?')} cases): "
            f"{_r(faithfulness.get('faithfulness_rate', 'NOT RUN'))}."
        )
    else:
        lines.append("- Personal-data faithfulness: NOT RUN.")
    lines.append("")
    lines.append("### 3.2 WATCH classifier gate (plain pytest/sklearn, no LLM)")
    lines.append("")
    if watch:
        pytest_block = watch.get("pytest", {})
        lines.append(
            f"- Overall passed: {watch.get('overall_passed', 'NOT RUN')} — "
            f"pytest: {pytest_block.get('summary_line', 'NOT RUN')}."
        )
    else:
        lines.append("- NOT RUN.")
    return lines


def _write_traceability(path: Path, datasets: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "requirement", "category", "production_code", "automated_layer"])
        for case in datasets["ai"]:
            writer.writerow([case["id"], "PRD F4/F6", case["category"], CATEGORY_CODE.get(case["category"], "TBD"), "intent/e2e/ragas"])
        for case in datasets["rules"]:
            writer.writerow([case["id"], "PRD F3", "RULE_ENGINE", "NOT_IMPLEMENTED", "deterministic"])
        for case in datasets["validation"]:
            writer.writerow([case["id"], "PRD F1", case["entity"], "codebase/backend/health|medications", "schema/api"])
        for case in datasets["summary"]:
            writer.writerow([case["id"], "PRD F4/F5", case.get("category", "SUMMARY"), "codebase/models/agents/nodes/synthesize_summary.py", "unit/integration/e2e"])


def _write_human_review(path: Path, agent: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["case_id", "category", "prompt", "response", "sources", "latency_ms", "clinician_pass", "safety_pass", "rating_1_5", "comment", "reviewer", "reviewed_at"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in agent.get("details", []):
            if item.get("status") != "EXECUTED":
                continue
            writer.writerow({
                "case_id": item.get("id"), "category": item.get("category"), "prompt": item.get("prompt"),
                "response": item.get("response"), "sources": json.dumps(item.get("sources", []), ensure_ascii=False),
                "latency_ms": item.get("latency_ms"),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "eval" / "results")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evaluation" / "EVALUATION_EVIDENCE.md")
    args = parser.parse_args()
    datasets = load_all()
    deterministic = _load_json(args.results / "latest.json")
    pytest_summary = _load_json(args.results / "pytest-summary.json")
    agent = _load_json(args.results / "agent-e2e.json")
    ragas = _load_json(args.results / "ragas.json")
    coverage = _coverage(args.results)
    traceability = args.output.parent / "traceability.csv"
    human_review = args.output.parent / "human_review.csv"
    _write_traceability(traceability, datasets)
    _write_human_review(human_review, agent)

    evaluations = deterministic.get("evaluations", {})
    lines = [
        "# Evaluation Evidence — VDiab",
        "",
        f"> Generated {datetime.now(UTC).isoformat()}. Raw artifacts remain in `{args.results.as_posix()}`.",
        "",
        "## 1. Automated tests and coverage",
        "",
        f"- Tests: {pytest_summary.get('passed', 'NOT RUN')} passed, {pytest_summary.get('failed', 'NOT RUN')} failed.",
        f"- Test duration: {pytest_summary.get('duration_seconds', 'NOT RUN')} seconds.",
        f"- Line coverage: {coverage.get('line_rate', 0) * 100:.1f}%" if coverage else "- Coverage: NOT RUN (execute `make test-coverage`).",
        f"- Branch coverage: {coverage.get('branch_rate', 0) * 100:.1f}%" if coverage else "- Coverage gate: 60% minimum.",
        "",
        "## 2. Deterministic golden evaluation",
        "",
        "| Module | Executed | Passed | Failed | Not implemented |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, result in evaluations.items():
        lines.append(f"| {name} | {result.get('executed', '—')} | {result.get('passed', '—')} | {result.get('failed', '—')} | {result.get('not_implemented', '—')} |")
    lines += [
        "",
        "## 3. RAG and agent quality",
        "",
        f"- Agent E2E (live-deployment script, needs a running server + tokens): {agent.get('passed', 'NOT RUN')}/{agent.get('executed', 'NOT RUN')} passed.",
        f"- Average latency: {agent.get('latency_ms', {}).get('avg', 'NOT RUN')} ms.",
        f"- P95 latency: {agent.get('latency_ms', {}).get('p95', 'NOT RUN')} ms.",
        f"- Standalone RAGAS runner (`eval/run_ragas.py`): {json.dumps(ragas.get('scores', 'NOT RUN'), ensure_ascii=False)}.",
        "",
        *_respond_agent_section(args.results),
        "",
        "## 4. Human/clinician review",
        "",
        f"Review sheet: `{human_review.as_posix()}`. Target clinician approval is at least 90%.",
        "",
        "## 5. Traceability",
        "",
        f"Requirement → case → production code mapping: `{traceability.as_posix()}`.",
        "",
        "## 6. Known gaps",
        "",
        "- F2 alert classification is not implemented in production code; dataset-only logic is not accepted as evidence.",
        "- API responses do not expose exact retrieved chunks; RAGAS currently re-runs retrieval and records this limitation.",
        "- User/clinician scores must be collected by real reviewers, never synthesized.",
        "- Personal-data faithfulness regressed 4/4 → 3/4 after the `plan_response` redesign (agent denied knowing the patient's assigned doctor); fixed 2026-08-27, re-verified 4/4, see docs/AI_Eval_Metrics_Plan.md §2.4.",
        "- Agent E2E p95 latency (13.6-16.1s across 2 runs) exceeds the PRD's ≤10s p95 target for AI responses; first real measurement, not yet load-tested at production scale, see docs/AI_Eval_Metrics_Plan.md §2.5.",
        "- AI Summary / F4 doctor-prioritization golden dataset (eval/data/ai_summary_cases.json, 8 cases) has a defined schema but no runner executing it against production code yet — same dataset-only gap as F2 above.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

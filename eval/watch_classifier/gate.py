#!/usr/bin/env python3
"""Repeatable offline eval/CI gate for the personalized-WATCH classifier
surface (data/NHANES/final/train_watch_model.py,
train_threshold_regression.py and the artifacts they produce under
artifacts/personalized_watch/). Invoked by `make eval-watch-classifier`.

Lives at eval/watch_classifier/, deliberately kept as its own subpackage
separate from eval/'s top-level LangSmith-experiment scripts (retrieval_recall.py,
guardrail_judge.py, trajectory.py, golden_scenarios.py,
personal_data_faithfulness.py, which all evaluate the LangGraph respond-agent/
RAG surface): this is a completely different kind of eval — plain sklearn/pytest
against a trained classifier's own metrics, no LangSmith/Ragas, no LLM calls at
all. Consolidated under eval/ for one evaluation home, kept in its own
subdirectory so the two don't get mixed together. See docs/AI_Eval_Metrics_Plan.md.

What it does, in order:

1. Runs the pre-existing leakage/safety contract (test_no_leakage.py,
   test_danger_precedence.py, test_regression_no_leakage.py — these stay at
   data/ml/personalized_watch/, next to the training scripts/artifacts they
   test) plus the two gate test files that live here alongside this script
   (test_v2_quantile_thresholds.py, test_v1_hgb_known_separability.py).
   Every test file is pytest-runnable standalone too; this script adds
   nothing test files don't already assert on their own.
2. Independently re-evaluates the same structured threshold checks from
   watch_gate_checks.py (so the JSON report can show actual value vs.
   threshold per check, which bare pytest pass/fail doesn't surface).
3. Writes one JSON report per run under eval/results/ (same convention as
   the other eval/*.py scripts) and exits non-zero if anything failed, so
   this is CI-usable as-is.

Never retrains, never writes to artifacts/personalized_watch/ or
data/ml/personalized_watch/feature_table.csv, never imports/touches
codebase/backend/health/*.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
PERSONALIZED_WATCH_DIR = REPO_ROOT / "data" / "ml" / "personalized_watch"
REPORTS_DIR = REPO_ROOT / "eval" / "results"

EXISTING_SAFETY_TESTS = [
    PERSONALIZED_WATCH_DIR / "test_no_leakage.py",
    PERSONALIZED_WATCH_DIR / "test_danger_precedence.py",
    PERSONALIZED_WATCH_DIR / "test_regression_no_leakage.py",
]
NEW_GATE_TESTS = [
    THIS_DIR / "test_v2_quantile_thresholds.py",
    THIS_DIR / "test_v1_hgb_known_separability.py",
]


def run_pytest(extra_args: list[str]) -> tuple[int, str]:
    test_paths = [str(path) for path in EXISTING_SAFETY_TESTS + NEW_GATE_TESTS]
    cmd = [sys.executable, "-m", "pytest", *test_paths, "-v", *extra_args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    print(output)
    return proc.returncode, output


def parse_pytest_summary(output: str) -> dict:
    """Best-effort structured pull from pytest's own textual summary line
    (e.g. '23 passed in 4.62s' or '2 failed, 21 passed in 5.01s') — pytest's
    machine-readable log plugins aren't installed in this repo's
    dependency set (requirements.txt only pins pytest itself), so this
    avoids adding a new dependency just for report formatting."""
    import re

    tail = output.strip().splitlines()[-15:] if output.strip() else []
    summary_line = next(
        (
            line
            for line in reversed(tail)
            if " in " in line and ("passed" in line or "failed" in line or "error" in line)
        ),
        "",
    )
    # pytest -v line shape: "<nodeid> PASSED [ 42%]" (percentage suffix
    # optional depending on terminal width/verbosity).
    line_re = re.compile(r"^(?P<test>\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\s*(\[\s*\d+%\])?\s*$")
    per_test = []
    for line in output.splitlines():
        match = line_re.match(line.strip())
        if match:
            per_test.append({"test": match.group("test"), "outcome": match.group("outcome")})
    return {"summary_line": summary_line.strip(), "tests": per_test}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the JSON report to this path instead of the default timestamped file under eval_reports/.",
    )
    parser.add_argument(
        "--pytest-args", default="", help="Extra args forwarded to pytest, e.g. --pytest-args='-k coverage'"
    )
    args = parser.parse_args()

    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    extra_args = args.pytest_args.split() if args.pytest_args else []
    returncode, output = run_pytest(extra_args)
    pytest_result = parse_pytest_summary(output)
    pytest_result["returncode"] = returncode
    pytest_result["all_passed"] = returncode == 0

    # Re-evaluate the structured threshold checks directly (not by scraping
    # pytest output) so the JSON report carries actual-value-vs-threshold
    # detail per check, same data test_v2_quantile_thresholds.py and
    # test_v1_hgb_known_separability.py assert on.
    threshold_checks: list[dict] = []
    threshold_check_error = None
    try:
        from eval.watch_classifier import watch_gate_checks as checks

        if (checks.V2_DIR / "metrics.json").exists():
            threshold_checks.extend(checks.run_all_v2_checks())
        if checks.FEATURE_TABLE.exists():
            threshold_checks.extend(checks.check_v1_hgb_known_separability())
    except Exception as exc:  # pragma: no cover - defensive; still want a report written
        threshold_check_error = f"{type(exc).__name__}: {exc}"

    all_threshold_checks_passed = all(c["passed"] for c in threshold_checks) if threshold_checks else None
    overall_passed = pytest_result["all_passed"] and (all_threshold_checks_passed is not False)

    report = {
        "gate": "eval-watch-classifier",
        "generated_at_utc": generated_at,
        "scope": (
            "Offline eval/CI gate for the personalized-WATCH classifier surface "
            "(data/NHANES/final/train_watch_model.py, train_threshold_regression.py, "
            "artifacts/personalized_watch/*). Does not touch codebase/backend/health/* "
            "(live serving) or retrain/modify any artifact."
        ),
        "pytest": {
            "test_files": EXISTING_SAFETY_TESTS + NEW_GATE_TESTS,
            **pytest_result,
        },
        "threshold_checks": threshold_checks,
        "threshold_check_error": threshold_check_error,
        "overall_passed": overall_passed,
    }

    if args.report is not None:
        report_path = args.report
    else:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = generated_at.replace(":", "").replace("-", "")
        report_path = REPORTS_DIR / f"eval_watch_classifier_{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # Convenience "latest" pointer, same spirit as eval/results/*.json's
    # per-run files — always overwritten, never the source of truth.
    if args.report is None:
        (REPORTS_DIR / "latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\nwrote report -> {report_path}")
    print(f"pytest: {pytest_result['summary_line']}")
    print(
        f"threshold checks: {sum(1 for c in threshold_checks if c['passed'])}/{len(threshold_checks)} passed"
        if threshold_checks
        else "threshold checks: none evaluated"
    )
    print(f"overall_passed: {overall_passed}")

    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

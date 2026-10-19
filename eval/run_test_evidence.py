#!/usr/bin/env python3
"""Run the fast test suite with coverage and persist auditable result metadata."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fail-under", type=float, default=60)
    parser.add_argument("--results", type=Path, default=ROOT / "eval" / "results")
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pytest", "tests", "-m", "not eval and not slow", "-p", "no:cacheprovider",
        "--cov=codebase", "--cov-branch", "--cov-report=term-missing",
        f"--cov-report=xml:{args.results / 'coverage.xml'}", f"--cov-fail-under={args.fail_under}",
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
    duration = round(time.perf_counter() - started, 2)
    console = completed.stdout + completed.stderr
    (args.results / "pytest-console.txt").write_text(console, encoding="utf-8")
    match = re.search(r"(?P<passed>\d+) passed(?:, (?P<failed>\d+) failed)?", console)
    if not match:
        reverse = re.search(r"(?P<failed>\d+) failed, (?P<passed>\d+) passed", console)
        match = reverse
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "passed": int(match.group("passed")) if match else None,
        "failed": int(match.groupdict().get("failed") or 0) if match else None,
        "coverage_gate": args.fail_under,
        "console_artifact": "eval/results/pytest-console.txt",
        "coverage_artifact": "eval/results/coverage.xml",
    }
    (args.results / "pytest-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(console)
    print(json.dumps(report, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

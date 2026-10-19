#!/usr/bin/env python3
"""Refresh versioned care plans and time-aware attention outputs in one run.

Append a clinician review event to the longitudinal event CSV, then execute
this command. It deterministically rebuilds the version history first and the
attention analysis second, so every measurement resolves the care plan that
was effective at that timestamp.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    final_dir = root / "data" / "NHANES" / "final"
    knowledge = root / "data" / "knowledge_structured"
    longitudinal = knowledge / "longitudinal_care"

    subprocess.run([
        sys.executable, str(final_dir / "build_longitudinal_care_plans.py"),
        "--monitoring-profiles", str(knowledge / "patient_cgm_monitoring_profiles.csv"),
        "--review-events", str(longitudinal / "patient_clinical_review_events.csv"),
        "--output-dir", str(longitudinal),
        "--initial-effective-from", "2026-01-01",
    ], check=True)
    subprocess.run([
        sys.executable, str(final_dir / "build_personalized_attention_dataset.py"),
        "--measurements", str(root / "data" / "simulator_60d" / "synthetic_glucose_measurements.csv"),
        "--monitoring-profiles", str(knowledge / "patient_cgm_monitoring_profiles.csv"),
        "--care-plan-versions", str(longitudinal / "patient_cgm_profile_versions.csv"),
        "--baseline-epochs", str(longitudinal / "patient_baseline_epochs.csv"),
        "--output-dir", str(knowledge / "attention_engine"),
    ], check=True)
    print("Longitudinal care plans and version-aware attention outputs refreshed successfully.")


if __name__ == "__main__":
    main()

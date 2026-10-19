"""Evaluate live WATCH decisions only against explicit clinician reviews.

This script never treats synthetic rule labels as clinical ground truth. It
returns an INSUFFICIENT_DATA report until enough CONFIRMED_WATCH and
CONFIRMED_NORMAL reviews exist. UNCERTAIN reviews are retained for audit but
excluded from binary metrics.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from codebase.backend.db import engine
from codebase.backend.health.models import DoctorWatchLabel, DoctorWatchReview, GlucoseRecord


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def build_report(rows: list[tuple[DoctorWatchReview, GlucoseRecord]], minimum_reviews: int) -> dict:
    confirmed = [
        (review, record)
        for review, record in rows
        if review.label in (DoctorWatchLabel.CONFIRMED_WATCH, DoctorWatchLabel.CONFIRMED_NORMAL)
        and review.model_status_at_review in ("WATCH", "NORMAL")
    ]
    tp = sum(r.label == DoctorWatchLabel.CONFIRMED_WATCH and r.model_status_at_review == "WATCH" for r, _ in confirmed)
    fn = sum(r.label == DoctorWatchLabel.CONFIRMED_WATCH and r.model_status_at_review == "NORMAL" for r, _ in confirmed)
    fp = sum(r.label == DoctorWatchLabel.CONFIRMED_NORMAL and r.model_status_at_review == "WATCH" for r, _ in confirmed)
    tn = sum(r.label == DoctorWatchLabel.CONFIRMED_NORMAL and r.model_status_at_review == "NORMAL" for r, _ in confirmed)
    enough_classes = (tp + fn) > 0 and (tn + fp) > 0
    ready = len(confirmed) >= minimum_reviews and enough_classes
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "READY_FOR_REVIEW" if ready else "INSUFFICIENT_DATA",
        "minimum_confirmed_reviews": minimum_reviews,
        "total_reviews": len(rows),
        "confirmed_binary_reviews": len(confirmed),
        "uncertain_or_ineligible_reviews": len(rows) - len(confirmed),
        "confusion_doctor_truth_vs_model": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "precision_watch": safe_ratio(tp, tp + fp) if ready else None,
        "recall_watch": safe_ratio(tp, tp + fn) if ready else None,
        "specificity_normal": safe_ratio(tn, tn + fp) if ready else None,
        "clinical_validation_claim_allowed": False,
        "note": (
            "Metrics describe reviewed live cases only; a study protocol and clinician sign-off are still required."
            if ready
            else "Collect more doctor-confirmed WATCH and NORMAL labels before reporting live performance."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum-reviews", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/personalized_watch/live_review_metrics.json"),
    )
    args = parser.parse_args()
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(DoctorWatchReview, GlucoseRecord).join(
                    GlucoseRecord, DoctorWatchReview.glucose_record_id == GlucoseRecord.id
                )
            ).all()
        )
    report = build_report(rows, args.minimum_reviews)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

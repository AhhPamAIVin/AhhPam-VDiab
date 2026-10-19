"""Retroactively evaluate existing GlucoseRecord rows against
health/threshold_service.py for patients that now have a CarePlanTarget.

create_glucose_record() only runs the threshold evaluation once, at insert
time (health/service.py). A record written before its patient had a
CarePlanTarget is permanently stuck at threshold_status=NULL otherwise — this
is exactly what happens after `make db-reset-migrate` re-seeds glucose data
without backfill_care_plan_targets.py having been (re-)run first.

Processes each patient's records in chronological order — build_feature_row's
history-derived features (watch_count_last_7d, minutes_since_last_watch, see
threshold_features.py) read *prior* records' own threshold_status, so
evaluating out of order would leave those features permanently undercounted
for the just-backfilled window.

Deliberately does NOT call notifications/service.py's
create_glucose_alert_notification for newly-discovered WATCH readings — that
function exists for real-time alerts on a reading as it happens; flooding
push notifications for months-old readings during a bulk backfill would be
wrong. This only fills in the record's own predicted_min/max/threshold_status
columns, same as create_glucose_record does before the notification step.

Usage:
    python -m scripts.backfill_threshold_evaluations [--since-days N] [--limit N] [--patient-id UUID] [--dry-run]

Only evaluates records with threshold_status IS NULL — safe to re-run or
interrupt and resume. `--patient-id` scopes a run to one patient — useful to
backfill a specific patient's full history immediately rather than waiting
for a full-population sweep (100k+ records) to reach them in patient-id
order.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, select

import codebase.backend.appointments.models as _appointments_models  # noqa: F401  (registers Appointment — ClinicalNote.appointment_id FKs into it)
from codebase.backend.auth.models import (
    User,  # noqa: F401  (registers users — CarePlanTarget.approved_by_doctor_id FKs into it)
)
from codebase.backend.db import engine
from codebase.backend.health.models import GlucoseRecord
from codebase.backend.health.threshold_service import evaluate_glucose_reading
from codebase.backend.patients.models import CarePlanTarget

logger = logging.getLogger("scripts.backfill_threshold_evaluations")

COMMIT_EVERY = 200
LOG_EVERY = 1000


def run_backfill(
    *, since_days: int | None, limit: int | None, dry_run: bool, patient_id: UUID | None = None
) -> tuple[int, int]:
    with Session(engine) as session:
        patient_ids_statement = select(CarePlanTarget.patient_id)
        if patient_id is not None:
            patient_ids_statement = patient_ids_statement.where(CarePlanTarget.patient_id == patient_id)
        patient_ids = session.exec(patient_ids_statement).all()

        since = datetime.utcnow() - timedelta(days=since_days) if since_days else None
        evaluated = 0
        watch_or_danger = 0
        start = time.time()

        for pid in patient_ids:
            statement = (
                select(GlucoseRecord)
                .where(GlucoseRecord.patient_id == pid, col(GlucoseRecord.threshold_status).is_(None))
                .order_by(col(GlucoseRecord.measured_at))
            )
            if since is not None:
                statement = statement.where(GlucoseRecord.measured_at >= since)
            records = session.exec(statement).all()

            for record in records:
                if limit is not None and evaluated >= limit:
                    if not dry_run:
                        session.commit()
                    logger.info("hit --limit %d, stopping", limit)
                    return evaluated, watch_or_danger

                evaluation = evaluate_glucose_reading(session, record)
                if not dry_run:
                    record.predicted_min_mg_dl = evaluation.predicted_min_mg_dl
                    record.predicted_max_mg_dl = evaluation.predicted_max_mg_dl
                    record.threshold_status = evaluation.status
                    session.add(record)
                evaluated += 1
                if evaluation.status in ("WATCH", "DANGER"):
                    watch_or_danger += 1

                if not dry_run and evaluated % COMMIT_EVERY == 0:
                    session.commit()
                if evaluated % LOG_EVERY == 0:
                    elapsed = time.time() - start
                    logger.info(
                        "evaluated %d (watch/danger=%d) — %.1fs elapsed, %.1fms/eval",
                        evaluated,
                        watch_or_danger,
                        elapsed,
                        1000 * elapsed / evaluated,
                    )

        if not dry_run:
            session.commit()
        return evaluated, watch_or_danger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-days", type=int, default=None, help="Only evaluate records measured in the last N days (default: all)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Stop after evaluating this many records (for testing)")
    parser.add_argument("--patient-id", type=str, default=None, help="Only process this one patient (by UUID)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    patient_id = UUID(args.patient_id) if args.patient_id else None
    evaluated, watch_or_danger = run_backfill(
        since_days=args.since_days, limit=args.limit, dry_run=args.dry_run, patient_id=patient_id
    )
    verb = "would evaluate" if args.dry_run else "evaluated"
    logger.info("done: %s %d records (%d WATCH/DANGER)", verb, evaluated, watch_or_danger)


if __name__ == "__main__":
    main()

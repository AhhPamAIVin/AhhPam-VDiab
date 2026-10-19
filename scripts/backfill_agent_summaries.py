"""Backfill 7-day AgentSummarySnapshot rows (role="patient") for patients
already seeded by scripts/migrate_nhanes_demo_data.py — that script bulk-
inserts ~60 days of glucose/meal/activity history directly into Postgres, so
neither the write-path trigger (health/router.py's BackgroundTasks call into
summaries.service.refresh_patient_summaries) nor ensure_snapshot's
lazy-generate-on-read ever produces more than the *current* day's snapshot
for it. This reconstructs the historical trail from the start of each
patient's seeded data instead.

Mirrors scripts/backfill_patient_insights.py closely — same seeded_window()/
weekly_periods() chunking (imported directly, not reimplemented), same
--dry-run/--limit/--patient-id CLI, same "run once, explicitly, not folded
into migrate-nhanes-data" reasoning (~978 patients x ~9 weekly chunks, each
at least one LLM call).

Scope: patient-role, 7-day windows only — matches the confirmed ask ("db
must keep track of 7 days AI summary record from the beginning") and mirrors
the existing weekly_batch insight precedent of patient-only for batch/
historical processing. Doctor-role and 14/30-day windows aren't backfilled
historically; only the live write-trigger and the lazy read-fallback
(agent/router.py's ensure_snapshot) populate those, for the current day —
see codebase/backend/summaries/service.py's module docstring.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime
from uuid import UUID

from sqlmodel import Session

from codebase.backend.db import engine
from codebase.backend.insights.scheduler import _active_patient_ids, _has_new_records_in_period
from codebase.backend.summaries.service import _find_snapshot_for_day, generate_and_store_snapshot
from scripts.backfill_patient_insights import seeded_window, weekly_periods

logger = logging.getLogger(__name__)

_PERIOD_DAYS = 7
_ROLE = "patient"


def _already_backfilled(session: Session, *, patient_id: UUID, period_end: datetime) -> bool:
    """Idempotency check — simpler than backfill_patient_insights.py's own
    heuristic (insight text isn't deterministic-id-able, so that script has
    to infer "already caught up" from last_confirmed_at). AgentSummarySnapshot
    rows are literally keyed by calendar day already, so this is a direct
    exact-day lookup via the same helper generate_and_store_snapshot() itself
    uses to decide update-vs-insert."""
    return (
        _find_snapshot_for_day(
            session, patient_id=patient_id, role=_ROLE, period_days=_PERIOD_DAYS, period_end=period_end
        )
        is not None
    )


def _patient_ids_to_process(session: Session, *, only_patient_id: UUID | None) -> list[UUID]:
    if only_patient_id is not None:
        return [only_patient_id]
    return _active_patient_ids(session)


def plan_backfill(
    session: Session,
    *,
    window_start: date,
    window_end: date,
    only_patient_id: UUID | None = None,
    limit: int | None = None,
) -> list[tuple[UUID, datetime, datetime]]:
    """Every (patient_id, period_start, period_end) this run would touch —
    skipping periods with no records at all, same reasoning
    backfill_patient_insights.py's own plan_backfill already applies (an
    empty period can only ever produce an "insufficient data" snapshot,
    not worth an LLM round trip or a DB row). Pure planning, no LLM/DB
    writes — this is what --dry-run reports and what a real run iterates."""
    periods = weekly_periods(window_start, window_end)
    patient_ids = _patient_ids_to_process(session, only_patient_id=only_patient_id)
    if limit is not None:
        patient_ids = patient_ids[:limit]

    plan: list[tuple[UUID, datetime, datetime]] = []
    for patient_id in patient_ids:
        for period_start, period_end in periods:
            if _has_new_records_in_period(
                session, patient_id=patient_id, period_start=period_start, period_end=period_end
            ):
                plan.append((patient_id, period_start, period_end))
    return plan


async def run_backfill(*, dry_run: bool = False, limit: int | None = None, patient_id: UUID | None = None) -> int:
    window_start, window_end = seeded_window()
    logger.info("Backfill window: %s -> %s", window_start.isoformat(), window_end.isoformat())

    with Session(engine) as session:
        plan = plan_backfill(
            session, window_start=window_start, window_end=window_end, only_patient_id=patient_id, limit=limit
        )
        logger.info("%d (patient, period) chunks planned", len(plan))
        if dry_run:
            for pid, period_start, period_end in plan[:20]:
                logger.info("  would process: patient=%s period=%s -> %s", pid, period_start.date(), period_end.date())
            if len(plan) > 20:
                logger.info("  ... and %d more", len(plan) - 20)
            return len(plan)

        processed = 0
        for pid, period_start, period_end in plan:
            if _already_backfilled(session, patient_id=pid, period_end=period_end):
                continue
            await generate_and_store_snapshot(
                session,
                patient_id=pid,
                role=_ROLE,
                period_days=_PERIOD_DAYS,
                period_start=period_start,
                period_end=period_end,
            )
            processed += 1
        return processed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report planned (patient, period) chunks, call no LLM.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N patients.")
    parser.add_argument("--patient-id", type=str, default=None, help="Only process this one patient (by UUID).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    patient_id = UUID(args.patient_id) if args.patient_id else None
    count = asyncio.run(run_backfill(dry_run=args.dry_run, limit=args.limit, patient_id=patient_id))

    label = "chunks planned" if args.dry_run else "chunks processed"
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}{count} {label}.")


if __name__ == "__main__":
    main()

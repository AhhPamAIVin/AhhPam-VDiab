"""Backfill PatientInsight rows (source_kind="weekly_batch") for patients
already seeded by scripts/migrate_nhanes_demo_data.py — that script bulk-
inserts ~60 days of glucose/meal/activity history directly into Postgres, so
neither PatientInsight trigger (appointment-creation, weekly-cron) ever fires
for it. Run once, explicitly, after `make migrate-nhanes-data` — see
/Users/phamminh/.claude/plans/warm-crafting-muffin.md §D for the full design
this implements.

Not wired into `make migrate-nhanes-data` itself — deliberately a separate
`make backfill-insights` step, since a full run is ~978 patients x ~9 weekly
chunks ~= 8,800 reflect-agent invocations, each at least one LLM call. Use
--dry-run and --limit/--patient-id to validate against a small subset first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlmodel import Session, select

from codebase.backend.db import engine
from codebase.backend.insights.models import InsightStatus, PatientInsight
from codebase.backend.insights.scheduler import _active_patient_ids, _has_new_records_in_period
from codebase.models.agents.nodes.reflect import reflect
from scripts.migrate_nhanes_demo_data import OLD_WINDOW_START, _compute_shift

logger = logging.getLogger(__name__)

_PERIOD_DAYS = 7


def seeded_window() -> tuple[date, date]:
    """The exact date range scripts/migrate_nhanes_demo_data.py actually
    seeded glucose/meal/activity records into, on this machine, right now —
    imported from that script rather than recomputed, so a backfill run
    always lines up with whatever window was really written, even as
    OLD_WINDOW_START/the "2 months back" logic evolves over time."""
    window_start = OLD_WINDOW_START + _compute_shift()
    window_end = date.today()
    return window_start, window_end


def weekly_periods(window_start: date, window_end: date) -> list[tuple[datetime, datetime]]:
    """[window_start, window_end) split into consecutive 7-day chunks,
    **oldest first** — order matters: a later chunk must be processed after
    an earlier one so upsert_insight.py's similarity match can correctly
    update an earlier chunk's insight rather than only ever creating new
    ones, the same create-then-update lifecycle 9 real weekly-cron ticks
    would produce. The final chunk is truncated to window_end, not padded
    past it, so a caller never asks for data past "today"."""
    periods: list[tuple[datetime, datetime]] = []
    cursor = window_start
    while cursor < window_end:
        period_start = datetime.combine(cursor, time.min)
        period_end = datetime.combine(min(cursor + timedelta(days=_PERIOD_DAYS), window_end), time.min)
        periods.append((period_start, period_end))
        cursor += timedelta(days=_PERIOD_DAYS)
    return periods


def _already_backfilled(session: Session, *, patient_id: UUID, period_end: datetime) -> bool:
    """Idempotency check — insight text isn't deterministic from a formula
    the way migrate_nhanes_demo_data.py's glucose/meal rows are, so this
    can't be a deterministic-id upsert; it's a lookup-before-generate check
    instead. Skips a (patient, period) chunk if the patient already has an
    ACTIVE weekly_batch insight whose `last_confirmed_at` is at or past this
    period's end — since periods are always processed oldest-first (both
    here and by the live weekly cron), `last_confirmed_at` only ever moves
    forward, so this is a reliable "already caught up to at least here"
    signal for a patient who produced at least one insight along the way.

    Known gap, accepted rather than engineered around given the cost/benefit
    at this stage: a period where propose() legitimately found zero
    candidates leaves no PatientInsight row to check against, so a rerun can
    redundantly re-process an empty period — wasteful (one extra LLM call)
    but not unsafe (no duplicate/corrupted row can result, since re-finding
    zero candidates changes nothing). Revisit only if reruns during real
    usage make this cost matter.
    """
    statement = (
        select(PatientInsight.id)
        .where(
            PatientInsight.patient_id == patient_id,
            PatientInsight.source_kind == "weekly_batch",
            PatientInsight.status == InsightStatus.ACTIVE,
            PatientInsight.last_confirmed_at >= period_end,
        )
        .limit(1)
    )
    return session.exec(statement).first() is not None


async def _run_reflection_for_period(
    session: Session, *, patient_id: UUID, period_start: datetime, period_end: datetime
) -> None:
    await reflect(
        patient_id,
        period_start,
        period_end,
        note_text=None,
        source_kind="weekly_batch",
        session=session,
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
    skipping periods with no records at all, same reasoning as the weekly
    scheduler's own selection step (codebase/backend/insights/scheduler.py).
    Pure planning, no LLM/DB writes — this is what --dry-run reports and
    what a real run iterates over."""
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
            await _run_reflection_for_period(session, patient_id=pid, period_start=period_start, period_end=period_end)
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

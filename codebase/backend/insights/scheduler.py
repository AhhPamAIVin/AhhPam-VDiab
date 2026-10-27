"""Weekly long-term-memory extraction — the second PatientInsight trigger,
alongside the appointment-triggered one (codebase/models/agents/nodes/
reflect.py, source_kind="appointment"). This one runs on a fixed weekly
cadence rather than being tied to a ClinicalNote, so every patient gets
insight extraction regardless of whether/when they see a doctor — see the
memory-planning conversation and /Users/phamminh/.claude/plans/
warm-crafting-muffin.md §B for the source_kind="weekly_batch" design.

Also runs a THIRD, separate pass on the same weekly tick: source_kind="chat"
(plan Part 3 of /Users/phamminh/.claude/plans/memoized-churning-patterson.md)
— mining a patient's own consented chat sessions for patient-facing
insights. Gated at this trigger/query level on `User.ai_memory_consent`;
`reflect()` itself trusts its caller for that, the same way it already
trusts resolve_context.py (not itself) to have checked patient-doctor
assignment on the chat path.

Follows the exact in-process/single-container pattern already established by
codebase/backend/notifications/scheduler.py: BackgroundScheduler, no
separate OS cron/worker, started/stopped from main.py's lifespan. Uses
CronTrigger (a fixed weekly time) rather than notifications/scheduler.py's
IntervalTrigger (a repeating short tick) — the two schedulers solve different
problems and shouldn't be confused for the same shape.

Fully wired now: `reflect()` (codebase/models/agents/nodes/reflect.py,
Phase 2) is the actual per-patient call. `reflect` is async (an
LLM-calling function); `run_weekly_reflection_tick` is async too so it can
`await` it directly per patient, and the sync APScheduler job target
(`_run_weekly_reflection_tick`) bridges into that with a single
`asyncio.run(...)` — same "sync job callback opens its own session, does
the real work" shape as notifications/scheduler.py's `_run_reminder_tick`,
just with one asyncio.run() wrapping the awaitable part.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, col, select

from codebase.backend.agent.models import ChatSession
from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.db import engine
from codebase.backend.health.models import ActivityRecord, GlucoseRecord, MealRecord
from codebase.backend.reports.service import run_weekly_report_tick
from codebase.models.agents.nodes.reflect import reflect

# Weekly, not a short repeating tick (contrast notifications/scheduler.py's
# once-a-minute reminder tick) — Monday 02:00 local time, a low-traffic hour,
# processing the trailing 7 days ending "now".
_WEEKLY_TICK_DAY_OF_WEEK = "mon"
_WEEKLY_TICK_HOUR = 2
_PERIOD_DAYS = 7

_scheduler: BackgroundScheduler | None = None


def _active_patient_ids(session: Session) -> list[UUID]:
    statement = select(User.id).where(User.role == UserRole.PATIENT, col(User.is_active).is_(True))
    return list(session.exec(statement).all())


def _has_new_records_in_period(
    session: Session, *, patient_id: UUID, period_start: datetime, period_end: datetime
) -> bool:
    """True if the patient has at least one glucose/meal/activity record in
    the period — cheap existence checks (not full fetches) so a patient with
    no new data this week never reaches the LLM at all. Checked across all
    three record types since a patient might log meals/activity without a
    glucose reading in a given week, or vice versa."""
    glucose_exists = session.exec(
        select(GlucoseRecord.id)
        .where(
            GlucoseRecord.patient_id == patient_id,
            GlucoseRecord.measured_at >= period_start,
            GlucoseRecord.measured_at < period_end,
        )
        .limit(1)
    ).first()
    if glucose_exists is not None:
        return True

    meal_exists = session.exec(
        select(MealRecord.id)
        .where(
            MealRecord.patient_id == patient_id,
            MealRecord.meal_time >= period_start,
            MealRecord.meal_time < period_end,
        )
        .limit(1)
    ).first()
    if meal_exists is not None:
        return True

    activity_exists = session.exec(
        select(ActivityRecord.id)
        .where(
            ActivityRecord.patient_id == patient_id,
            ActivityRecord.activity_time >= period_start,
            ActivityRecord.activity_time < period_end,
        )
        .limit(1)
    ).first()
    return activity_exists is not None


def _has_self_chat_activity_in_period(
    session: Session, *, patient_id: UUID, period_start: datetime, period_end: datetime
) -> bool:
    """Cheap existence check mirroring _has_new_records_in_period above —
    self-chat only (ChatSession.counterpart_id == patient_id, see
    agent/models.py's isolation docstring), so a doctor's own working thread
    about this patient never counts toward "did this patient chat this
    week" and never triggers an LLM call for nothing."""
    session_exists = session.exec(
        select(ChatSession.id)
        .where(
            ChatSession.patient_id == patient_id,
            ChatSession.counterpart_id == patient_id,
            ChatSession.updated_at >= period_start,
            ChatSession.updated_at < period_end,
        )
        .limit(1)
    ).first()
    return session_exists is not None


def patients_due_for_chat_reflection(session: Session, *, period_start: datetime, period_end: datetime) -> list[UUID]:
    """Active, CONSENTED patients (User.ai_memory_consent) with at least one
    self-chat session touched in the period — the set the weekly tick's
    chat-derived pass should run for (plan Part 3). Consent is the primary
    gate for this entire stream; enforced here, at the trigger's own patient
    selection, not inside reflect()/nodes/reflect.py itself."""
    statement = select(User.id).where(
        User.role == UserRole.PATIENT, col(User.is_active).is_(True), col(User.ai_memory_consent).is_(True)
    )
    consented_patient_ids = list(session.exec(statement).all())
    return [
        patient_id
        for patient_id in consented_patient_ids
        if _has_self_chat_activity_in_period(
            session, patient_id=patient_id, period_start=period_start, period_end=period_end
        )
    ]


def patients_due_for_weekly_reflection(session: Session, *, period_start: datetime, period_end: datetime) -> list[UUID]:
    """Active patients with at least one new health record in [period_start,
    period_end) — the set the weekly tick should actually run reflection
    for. Skipping patients with no new data avoids an LLM call that could
    only ever produce "no pattern found," and avoids `reflect.py`'s gather
    step fetching an empty record set for nothing."""
    return [
        patient_id
        for patient_id in _active_patient_ids(session)
        if _has_new_records_in_period(session, patient_id=patient_id, period_start=period_start, period_end=period_end)
    ]


async def run_weekly_reflection_tick(session: Session, *, now: datetime) -> list[UUID]:
    """The full weekly cycle: find who's due, run reflection for each, in the
    caller's own session (reflect() uses it directly rather than opening a
    second one, since one is already open for this tick). Returns the list
    of patient_ids the weekly_batch pass actually ran for (useful for tests
    and for logging real tick sizes) — the separate chat-derived pass below
    isn't included in that return value, same as it isn't a documented part
    of this function's pre-existing contract; tests for it call
    patients_due_for_chat_reflection directly."""
    period_end = now
    period_start = now - timedelta(days=_PERIOD_DAYS)
    due = patients_due_for_weekly_reflection(session, period_start=period_start, period_end=period_end)
    for patient_id in due:
        await reflect(
            patient_id,
            period_start,
            period_end,
            note_text=None,
            source_kind="weekly_batch",
            session=session,
        )

    # Separate, consent-gated pass (plan Part 3) — same period, same tick,
    # but its own patient set (patients_due_for_chat_reflection) since it's
    # keyed on ai_memory_consent + chat activity, not health-record activity.
    chat_due = patients_due_for_chat_reflection(session, period_start=period_start, period_end=period_end)
    for patient_id in chat_due:
        await reflect(
            patient_id,
            period_start,
            period_end,
            note_text=None,
            source_kind="chat",
            session=session,
        )

    return due


def _run_weekly_reflection_tick() -> None:
    """Cron target — opens its own session since it runs outside any request
    context, same pattern as notifications/scheduler.py's _run_reminder_tick.
    Bridges into the async reflect() call chain with a single asyncio.run()
    around the whole tick, not one per patient — one event loop per tick,
    not N."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    with Session(engine) as session:
        asyncio.run(run_weekly_reflection_tick(session, now=datetime.now(tz).replace(tzinfo=None)))


def _run_weekly_report_tick() -> None:
    """Cron target for the weekly patient report (Epic 2) — own session, one
    asyncio.run() per tick, same shape as _run_weekly_reflection_tick."""
    with Session(engine) as session:
        asyncio.run(run_weekly_report_tick(session, now_utc=datetime.utcnow()))


def start_scheduler() -> BackgroundScheduler:
    """Called from main.py's lifespan, alongside notifications/scheduler.py's
    own start_scheduler() — a separate BackgroundScheduler instance/job,
    same in-process pattern, single container, no new deployment step.
    Safe to call more than once."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(get_settings().reminder_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _run_weekly_reflection_tick,
        CronTrigger(day_of_week=_WEEKLY_TICK_DAY_OF_WEEK, hour=_WEEKLY_TICK_HOUR, timezone=tz),
        id="weekly_reflection_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    settings = get_settings()
    if settings.weekly_reports_enabled:
        scheduler.add_job(
            _run_weekly_report_tick,
            CronTrigger(day_of_week=settings.weekly_report_day_of_week, hour=settings.weekly_report_hour, timezone=tz),
            id="weekly_report_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

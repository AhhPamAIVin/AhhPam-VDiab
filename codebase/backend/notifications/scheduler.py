import asyncio
from datetime import UTC, date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.db import engine
from codebase.backend.health.models import BloodPressureRecord, GlucoseRecord
from codebase.backend.medications.service import list_active_medications
from codebase.backend.notifications import service
from codebase.backend.notifications.defaults import DEFAULT_REMINDERS
from codebase.backend.notifications.models import NotificationPreference, NotificationType
from codebase.backend.notifications.zalo import ZaloAPIError, ZaloBotNotifier, ZaloConfigurationError

# How often the tick runs. Every minute so a preference's `time_of_day` (which a
# patient can set to any HH:MM) is never missed by more than ~1 minute.
_TICK_INTERVAL_MINUTES = 1

_scheduler: BackgroundScheduler | None = None


def _active_patient_ids(session: Session) -> list[UUID]:
    statement = select(User.id).where(User.role == UserRole.PATIENT, col(User.is_active).is_(True))
    return list(session.exec(statement).all())


def _active_patient_chat_ids(session: Session) -> dict[UUID, str]:
    """patient_id -> zalo_chat_id, for patients who have linked a Zalo chat (by
    messaging the bot at least once — see docs/ZALO_NOTIFICATIONS.md). Patients
    without one are simply absent, so callers can `.get(patient_id)` and skip."""
    statement = select(User.id, User.zalo_chat_id).where(
        User.role == UserRole.PATIENT,
        col(User.is_active).is_(True),
        col(User.zalo_chat_id).is_not(None),
    )
    return dict(session.exec(statement).all())


def _matches_now(preference: NotificationPreference, now: datetime) -> bool:
    return preference.time_of_day.hour == now.hour and preference.time_of_day.minute == now.minute


def _to_utc_naive(local_date: date, local_time: time, tz: ZoneInfo) -> datetime:
    """A preference's `time_of_day` is a patient-facing local wall-clock target (what they see
    and set in the TimePicker) — this converts a local calendar-date + wall-clock time into the
    naive-but-really-UTC form every other datetime in this app uses (see time_utils.UtcDatetime's
    docstring). Without this, `datetime.combine(local_date, local_time)` alone stores the local
    wall-clock value mislabeled as UTC, silently shifting every displayed/compared time by the
    timezone offset (verified live: a 21:00 VN reminder round-tripped and displayed as 04:00 the
    next day)."""
    return datetime.combine(local_date, local_time, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def _previous_slot_time(preferences: list[NotificationPreference], preference: NotificationPreference) -> time | None:
    """The time_of_day of this patient's latest *other* enabled preference of the same
    notification_type that falls earlier in the day than `preference` — i.e. what the patient's
    own reminder plan considers the start of this slot's "already covered" window. `None` if
    `preference` is the first slot of its type today (window then starts at local midnight)."""
    earlier_same_type = [
        p.time_of_day
        for p in preferences
        if p.is_enabled and p.notification_type == preference.notification_type and p.time_of_day < preference.time_of_day
    ]
    return max(earlier_same_type) if earlier_same_type else None


def _already_satisfied(
    session: Session,
    *,
    patient_id: UUID,
    notification_type: NotificationType,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """Whether the patient already logged a qualifying record in (window_start, window_end] —
    both naive-UTC, see _to_utc_naive. A reminder whose window is already covered by a real
    measurement has nothing left to prompt: this is what lets logging glucose/BP *before* a
    reminder's own trigger time pre-empt it, the same way auto_complete_pending (health/service.py)
    already resolves a reminder logged *after* it fired — this covers the other half of that
    same intent. Medication reminders have no persisted "dose taken" timestamp to check against
    (see medications/service.py's docstring on why that isn't just a MedicationNote) and custom
    reminders aren't tied to any specific record type, so both always materialize as before.
    """
    if notification_type == NotificationType.GLUCOSE_REMINDER:
        statement = select(GlucoseRecord.id).where(
            GlucoseRecord.patient_id == patient_id,
            col(GlucoseRecord.measured_at) > window_start,
            col(GlucoseRecord.measured_at) <= window_end,
        )
    elif notification_type == NotificationType.BLOOD_PRESSURE_REMINDER:
        statement = select(BloodPressureRecord.id).where(
            BloodPressureRecord.patient_id == patient_id,
            col(BloodPressureRecord.measured_at) > window_start,
            col(BloodPressureRecord.measured_at) <= window_end,
        )
    else:
        return False
    return session.exec(statement.limit(1)).first() is not None


def _push_zalo_reminder(chat_id: str | None, *, title: str, message: str) -> None:
    """Best-effort push to the patient's linked Zalo chat when a reminder is newly
    materialized. The in-app Notification row (already committed by the caller) is
    the source of truth regardless — a disabled/misconfigured/failing Zalo Bot
    must never block or roll back reminder generation for anyone else this tick.
    """
    if not chat_id or not get_settings().zalo_bot_enabled:
        return
    try:
        asyncio.run(ZaloBotNotifier().send_message(chat_id, f"{title}\n{message}"))
    except (ZaloConfigurationError, ZaloAPIError):
        pass


def _materialize_due_reminder(
    session: Session,
    *,
    patient_id: UUID,
    preference: NotificationPreference,
    today: date,
    chat_id: str | None,
    tz: ZoneInfo,
    window_start_local: time | None,
) -> None:
    scheduled_for = _to_utc_naive(today, preference.time_of_day, tz)
    slot_key = f"{preference.reminder_kind}:{today.isoformat()}"

    window_start = _to_utc_naive(today, window_start_local or time.min, tz)
    already_satisfied = _already_satisfied(
        session,
        patient_id=patient_id,
        notification_type=preference.notification_type,
        window_start=window_start,
        window_end=scheduled_for,
    )
    if not already_satisfied:
        title = preference.label
        message = f"Đã đến giờ: {preference.label}. Hãy ghi nhận vào nhật ký."
        is_new = not service.notification_exists_for_slot(session, patient_id=patient_id, slot_key=slot_key)
        service.get_or_create_notification(
            session,
            patient_id=patient_id,
            notification_type=preference.notification_type,
            slot_key=slot_key,
            slot_date=today,
            scheduled_for=scheduled_for,
            title=title,
            message=message,
        )
        if is_new:
            _push_zalo_reminder(chat_id, title=title, message=message)

    # Medication reminders ride along with glucose reminders at whichever time the
    # patient's glucose preference is currently set to — dosing is tied to meals,
    # not a fixed clock time, and this stays correct even if the patient moves
    # their glucose reminder to a different hour.
    if preference.notification_type != NotificationType.GLUCOSE_REMINDER:
        return
    active_medications = list_active_medications(session, patient_id)
    if not active_medications:
        return
    drug_names = ", ".join(medication.drug_name for medication in active_medications)
    med_slot_key = f"medication_{preference.reminder_kind}:{today.isoformat()}"
    med_title = f"Nhắc uống thuốc — {preference.label}"
    med_message = f"Đã đến giờ dùng thuốc: {drug_names}."
    med_is_new = not service.notification_exists_for_slot(session, patient_id=patient_id, slot_key=med_slot_key)
    service.get_or_create_notification(
        session,
        patient_id=patient_id,
        notification_type=NotificationType.MEDICATION_REMINDER,
        slot_key=med_slot_key,
        slot_date=today,
        scheduled_for=scheduled_for,
        title=med_title,
        message=med_message,
    )
    if med_is_new:
        _push_zalo_reminder(chat_id, title=med_title, message=med_message)


def _preferences_by_patient(session: Session, patient_ids: list[UUID]) -> dict[UUID, list[NotificationPreference]]:
    """Every active patient's preferences in one query, instead of the N
    individual per-patient queries service.get_effective_preferences would run
    if called in a loop here. With a several-hundred-patient dataset, the
    per-patient version couldn't finish inside the 1-minute tick interval —
    APScheduler's max_instances=1 then piles the tick up on itself forever
    ("skipped: maximum number of running instances reached"), and the tick's
    still-open session sits on a connection-pool slot the whole time, starving
    real request traffic (see the incident this fixed: POST .../health/glucose
    taking ~30s — the connection-pool wait, not the write itself)."""
    by_patient: dict[UUID, list[NotificationPreference]] = {patient_id: [] for patient_id in patient_ids}
    if not patient_ids:
        return by_patient
    statement = select(NotificationPreference).where(col(NotificationPreference.patient_id).in_(patient_ids))
    for preference in session.exec(statement).all():
        by_patient[preference.patient_id].append(preference)
    return by_patient


def _materialize_missing_defaults(session: Session, by_patient: dict[UUID, list[NotificationPreference]]) -> None:
    """Bulk version of service.get_effective_preferences's per-patient
    materialize step: one INSERT statement and one commit covering every
    patient missing any default reminder kind, instead of one round trip per
    patient. Appends each new preference to `by_patient` as it's built (not
    after commit — commit expires ORM objects, and re-reading their
    attributes afterward would silently reintroduce a query per row).

    Uses Core `insert()` with a list of param dicts rather than
    `session.add_all(...)` — the ORM's default flush emits one INSERT
    statement per object, not one for the whole batch (confirmed by the
    query-count regression test in test_notifications.py: with add_all, 25
    newly-seeded patients took ~5x the statements of 5 newly-seeded
    patients, scaling with new-patient count even though the read side
    below is genuinely flat). Core insert with executemany-style params
    keeps this one statement regardless of row count. Building rows from
    `.model_dump()` of the already-constructed objects (not hand-written
    dicts) so every Python-side default (id's uuid4, created_at/updated_at's
    utcnow) is included exactly as SQLModel would set it — a hand-written
    dict would silently miss those and violate NOT NULL/produce wrong ids.
    """
    to_add: list[NotificationPreference] = []
    for patient_id, preferences in by_patient.items():
        existing_kinds = {p.reminder_kind for p in preferences}
        for default in DEFAULT_REMINDERS:
            if default.reminder_kind in existing_kinds:
                continue
            preference = NotificationPreference(
                patient_id=patient_id,
                reminder_kind=default.reminder_kind,
                notification_type=default.notification_type,
                meal_type=default.meal_type,
                meal_context=default.meal_context,
                label=default.label,
                time_of_day=default.time_of_day,
                is_enabled=True,
                is_custom=False,
            )
            to_add.append(preference)
            by_patient[patient_id].append(preference)
    if not to_add:
        return
    try:
        session.execute(insert(NotificationPreference), [p.model_dump() for p in to_add])
        session.commit()
    except IntegrityError:
        # Rare race with a patient's own first API read materializing the same
        # defaults concurrently. The rows above are already in `by_patient` for
        # this tick's in-memory use (today's due reminders still fire off them
        # even though the row didn't persist); a fresh read next tick will see
        # they're still missing and retry the insert then — self-healing.
        session.rollback()


def generate_due_reminders(session: Session, *, now: datetime) -> None:
    """For every active patient, materialize a Notification for each enabled
    preference whose time_of_day matches the current minute. Reads each
    patient's *effective* plan (defaults + their own customizations), so a
    patient who moved, disabled, or added a reminder is respected immediately.

    Batches preference reads/writes across all patients into a handful of
    queries total (see _preferences_by_patient) rather than looping
    service.get_effective_preferences per patient.
    """
    today = now.date()
    tz = ZoneInfo(get_settings().reminder_timezone)
    by_patient = _preferences_by_patient(session, _active_patient_ids(session))
    _materialize_missing_defaults(session, by_patient)
    chat_ids = _active_patient_chat_ids(session)
    for patient_id, preferences in by_patient.items():
        for preference in preferences:
            if not preference.is_enabled or not _matches_now(preference, now):
                continue
            _materialize_due_reminder(
                session,
                patient_id=patient_id,
                preference=preference,
                today=today,
                chat_id=chat_ids.get(patient_id),
                tz=tz,
                window_start_local=_previous_slot_time(preferences, preference),
            )


def run_reminder_tick(session: Session, *, now: datetime) -> None:
    """The full per-minute cycle: revive snoozes whose time is up, re-nudge
    reminders that are still open so they keep 'popping up', then generate any
    newly-due reminders. Order matters — a snooze revived this tick is
    immediately eligible to be re-nudged/seen as pending.
    """
    today = now.date()
    service.revive_due_snoozes(session, on_date=today, now=now)
    service.renudge_stale_pending(session, on_date=today, now=now)
    generate_due_reminders(session, now=now)


def _run_reminder_tick() -> None:
    """Cron target — opens its own session since it runs outside any request context."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    with Session(engine) as session:
        run_reminder_tick(session, now=datetime.now(tz).replace(tzinfo=None))


def start_scheduler() -> BackgroundScheduler:
    """Start the F1 monitoring-plan reminder tick: a single job, once a minute,
    that drives reminder generation and re-surfacing off each patient's own
    NotificationPreference plan (see notifications/defaults.py for the seed plan
    and notifications/service.py for how it's read/materialized).

    Runs in-process rather than as a separate OS cron/worker: the backend is
    deployed as a single container (see docker-compose.yml), so this needs no
    Dockerfile or deployment change. Safe to call more than once — a second call
    is a no-op against the already-running scheduler.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(get_settings().reminder_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _run_reminder_tick,
        IntervalTrigger(minutes=_TICK_INTERVAL_MINUTES, timezone=tz),
        id="reminder_tick",
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

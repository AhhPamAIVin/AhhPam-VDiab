import asyncio
import logging
import secrets
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from codebase.backend.auth.models import User
from codebase.backend.config import get_settings
from codebase.backend.notifications.defaults import DEFAULT_REMINDERS
from codebase.backend.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
    ZaloLinkCode,
)
from codebase.backend.notifications.zalo import ZaloAPIError, ZaloBotNotifier, ZaloConfigurationError

logger = logging.getLogger(__name__)

# Excludes visually ambiguous characters (0/O, 1/I/L) since a patient has to
# read this off their screen and type or paste it into Zalo by hand.
_LINK_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# How often a still-unresolved reminder is re-touched (last_notified_at/nudge_count
# bumped) so a polling client can tell it needs to be resurfaced — this is what
# makes a reminder "keep popping up" rather than firing once and going quiet.
NUDGE_INTERVAL = timedelta(minutes=30)

# ---- Notifications (generated reminder instances) ----


def get_or_create_notification(
    session: Session,
    *,
    patient_id: UUID,
    notification_type: NotificationType,
    slot_key: str,
    slot_date: date,
    scheduled_for: datetime,
    title: str,
    message: str,
) -> Notification:
    """Idempotent insert: returns the existing row for this patient+slot if the
    generator already ran for it, otherwise creates it. `slot_key` is unique per
    patient, so this is what makes duplicate reminders for the same slot impossible
    even if the cron tick retries after a misfire or the process restarts mid-slot.
    """
    existing = _find_by_slot(session, patient_id=patient_id, slot_key=slot_key)
    if existing is not None:
        return existing

    notification = Notification(
        patient_id=patient_id,
        notification_type=notification_type,
        title=title,
        message=message,
        slot_key=slot_key,
        slot_date=slot_date,
        scheduled_for=scheduled_for,
        status=NotificationStatus.PENDING,
    )
    session.add(notification)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_by_slot(session, patient_id=patient_id, slot_key=slot_key)
        if existing is None:
            raise
        return existing
    session.refresh(notification)
    return notification


def _find_by_slot(session: Session, *, patient_id: UUID, slot_key: str) -> Notification | None:
    statement = select(Notification).where(
        Notification.patient_id == patient_id,
        Notification.slot_key == slot_key,
    )
    return session.exec(statement).first()


def notification_exists_for_slot(session: Session, *, patient_id: UUID, slot_key: str) -> bool:
    """Whether this patient+slot already has a Notification row — lets a caller
    tell a brand-new reminder apart from get_or_create_notification's idempotent
    return-the-existing-row case, e.g. to fire an outbound push exactly once."""
    return _find_by_slot(session, patient_id=patient_id, slot_key=slot_key) is not None


# ---- Glucose threshold alerts (event-driven, see health/threshold_service.py) ----

# Independent of the 12h-per-reason cooldown the old rule engine used
# (docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 4) — the quantile-
# regression model has no "reason code" to cool down by, just a WATCH/NORMAL
# call per reading, so a patient hitting the band on several different
# readings in one day could otherwise get one push per reading. This caps
# the total regardless of how many separate readings triggered it.
MAX_GLUCOSE_ALERTS_PER_DAY = 4


def _count_glucose_alerts_today(session: Session, *, patient_id: UUID, on_date: date) -> int:
    statement = select(func.count()).select_from(Notification).where(
        Notification.patient_id == patient_id,
        Notification.notification_type == NotificationType.GLUCOSE_ALERT,
        Notification.slot_date == on_date,
    )
    return session.exec(statement).one()


def _push_glucose_alert(chat_id: str | None, *, title: str, message: str) -> None:
    """Best-effort — mirrors scheduler.py's _push_zalo_reminder exactly (same
    failure handling, same reasoning): the Notification row already
    committed is the source of truth regardless of whether this push
    actually reaches the patient's phone."""
    if not chat_id or not get_settings().zalo_bot_enabled:
        return
    try:
        asyncio.run(ZaloBotNotifier().send_message(chat_id, f"{title}\n{message}"))
    except (ZaloConfigurationError, ZaloAPIError):
        logger.warning("glucose alert Zalo push failed for chat_id=%s", chat_id)


def create_glucose_alert_notification(
    session: Session,
    *,
    patient: User,
    glucose_record_id: UUID,
    measured_at: datetime,
    value: float,
    status: str,  # "WATCH" | "DANGER" — see health/threshold_service.py
    predicted_min_mg_dl: float | None,
    predicted_max_mg_dl: float | None,
    danger_low_mg_dl: float,
    danger_high_mg_dl: float,
) -> Notification | None:
    """Called from health/service.py's create_glucose_record the moment a
    reading is evaluated WATCH or DANGER — never for NORMAL/UNAVAILABLE.

    MAX_GLUCOSE_ALERTS_PER_DAY only caps WATCH — a DANGER reading (the
    doctor-approved safety gate itself, not an AI prediction) must never be
    silently dropped just because the patient already used up today's WATCH
    quota; the record itself still keeps its threshold_status either way,
    this only gates the outbound notification.

    slot_key is keyed by glucose_record_id (not a fixed daily slot like the
    reminder types) — this is event-driven, one possible alert per reading,
    not one per calendar slot.
    """
    slot_date = measured_at.date()
    if status == "WATCH" and _count_glucose_alerts_today(session, patient_id=patient.id, on_date=slot_date) >= MAX_GLUCOSE_ALERTS_PER_DAY:
        return None

    slot_key = f"glucose_alert:{glucose_record_id}"
    is_new = not notification_exists_for_slot(session, patient_id=patient.id, slot_key=slot_key)

    if status == "DANGER":
        title = "Cảnh báo NGUY HIỂM: Đường huyết vượt ngưỡng an toàn"
        message = (
            f"Chỉ số đo được là {value:.0f} mg/dL, đã vượt ngưỡng báo động theo phác đồ của bác sĩ "
            f"(mức an toàn: {danger_low_mg_dl:.0f}-{danger_high_mg_dl:.0f} mg/dL). "
            "Bạn nên liên hệ ngay với bác sĩ hoặc tái khám ở cơ sở y tế gần nhất để được hỗ trợ kịp thời."
        )
    else:  # WATCH — predicted_min/max always set here, see evaluate_glucose_reading
        assert predicted_min_mg_dl is not None and predicted_max_mg_dl is not None
        direction = "thấp hơn" if value < predicted_min_mg_dl else "cao hơn"
        title = "Cảnh báo theo dõi đường huyết"
        message = (
            f"Chỉ số đo được là {value:.0f} mg/dL, {direction} dải bình thường cá nhân của bạn "
            f"({predicted_min_mg_dl:.0f}-{predicted_max_mg_dl:.0f} mg/dL). Hãy đo lại theo kế hoạch, "
            "ghi chú bữa ăn/vận động. "
            f"Xin lưu ý, nếu đường huyết vượt ngưỡng báo động là {danger_low_mg_dl:.0f}-{danger_high_mg_dl:.0f} mg/dL, "
            "bạn nên liên hệ lại với bác sĩ hoặc tái khám ở cơ sở y tế gần nhất."
        )

    notification = get_or_create_notification(
        session, patient_id=patient.id, notification_type=NotificationType.GLUCOSE_ALERT,
        slot_key=slot_key, slot_date=slot_date, scheduled_for=measured_at, title=title, message=message,
    )
    if is_new:
        _push_glucose_alert(patient.zalo_chat_id, title=title, message=message)
    return notification


def list_notifications_for_patient(
    session: Session,
    patient_id: UUID,
    *,
    status_filter: NotificationStatus | None = None,
    on_date: date | None = None,
) -> list[Notification]:
    statement = select(Notification).where(Notification.patient_id == patient_id)
    if status_filter is not None:
        statement = statement.where(Notification.status == status_filter)
    if on_date is not None:
        statement = statement.where(Notification.slot_date == on_date)
    return list(session.exec(statement.order_by(col(Notification.scheduled_for))).all())


def get_notification_or_404(session: Session, notification_id: UUID) -> Notification:
    notification = session.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return notification


def update_notification_status(
    session: Session,
    *,
    notification: Notification,
    new_status: NotificationStatus,
    snooze_minutes: int | None,
) -> Notification:
    if new_status == NotificationStatus.SNOOZED:
        if not snooze_minutes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="snooze_minutes is required to snooze a reminder"
            )
        notification.snoozed_until = datetime.utcnow() + timedelta(minutes=snooze_minutes)
    else:
        notification.snoozed_until = None
        notification.responded_at = datetime.utcnow()

    notification.status = new_status
    session.add(notification)
    session.commit()
    session.refresh(notification)
    return notification


def auto_complete_pending(
    session: Session,
    *,
    patient_id: UUID,
    notification_type: NotificationType,
    on_date: date,
) -> None:
    """Mark today's still-open reminder(s) of this type as completed because the
    patient just logged the underlying record. Matches by day and type rather than
    the exact slot — a patient logging a few minutes early/late shouldn't have to
    also separately dismiss the reminder that prompted them.
    """
    statement = select(Notification).where(
        Notification.patient_id == patient_id,
        Notification.notification_type == notification_type,
        Notification.slot_date == on_date,
        col(Notification.status).in_([NotificationStatus.PENDING, NotificationStatus.SNOOZED]),
    )
    for notification in session.exec(statement).all():
        notification.status = NotificationStatus.COMPLETED
        notification.responded_at = datetime.utcnow()
        session.add(notification)
    session.commit()


def revive_due_snoozes(session: Session, *, on_date: date, now: datetime) -> None:
    """A snoozed reminder past its snoozed_until reverts to pending — the patient
    asked to be reminded again later, not to have it silently disappear.

    Single atomic UPDATE, not SELECT-then-loop-then-commit — a real incident
    caught live: with a large enough backlog of unresolved reminders (demo
    patients never dismiss them — 5000+ accumulated PENDING rows in
    production), the old per-row loop held one open transaction across many
    individual UPDATE statements with no commit until the very end. Caught
    via pg_stat_activity mid-incident: connections sitting "idle in
    transaction" for 8+ minutes on this exact query shape, `wait_event:
    ClientRead` (Postgres waiting on the app, not a lock) — the app-side
    loop, not the database, was the bottleneck. A single UPDATE has no
    Python-level gap between rows for that to happen in."""
    session.exec(
        update(Notification)
        .where(
            Notification.slot_date == on_date,
            Notification.status == NotificationStatus.SNOOZED,
            col(Notification.snoozed_until) <= now,
        )
        .values(
            status=NotificationStatus.PENDING,
            snoozed_until=None,
            last_notified_at=now,
            nudge_count=Notification.nudge_count + 1,
        )
    )
    session.commit()


def renudge_stale_pending(session: Session, *, on_date: date, now: datetime) -> None:
    """Re-touch any reminder that's still pending and hasn't been nudged in a
    while, so a polling client (or a future push-notification integration) has a
    signal to resurface it again — this is what keeps an unresolved reminder
    "popping up" instead of firing once and going quiet.

    Single atomic UPDATE — see revive_due_snoozes's docstring above for the
    real incident this shape guards against (same fix, same reasoning,
    applied to both functions since both had the identical loop pattern)."""
    session.exec(
        update(Notification)
        .where(
            Notification.slot_date == on_date,
            Notification.status == NotificationStatus.PENDING,
            col(Notification.last_notified_at) <= now - NUDGE_INTERVAL,
        )
        .values(last_notified_at=now, nudge_count=Notification.nudge_count + 1)
    )
    session.commit()


# ---- Notification preferences (the patient's adjustable reminder plan) ----


def list_preferences(session: Session, patient_id: UUID) -> list[NotificationPreference]:
    statement = select(NotificationPreference).where(NotificationPreference.patient_id == patient_id)
    return list(session.exec(statement.order_by(col(NotificationPreference.time_of_day))).all())


def get_effective_preferences(session: Session, patient_id: UUID) -> list[NotificationPreference]:
    """This patient's full reminder plan: the system defaults, materialized into
    patient-owned rows the first time they're read (so each one has an id a
    patient can PATCH to adjust or turn off), plus any custom reminders they
    added. Idempotent — safe to call on every cron tick and every API read.
    """
    existing_kinds = {p.reminder_kind for p in list_preferences(session, patient_id)}
    created_any = False
    for default in DEFAULT_REMINDERS:
        if default.reminder_kind in existing_kinds:
            continue
        session.add(
            NotificationPreference(
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
        )
        created_any = True
    if created_any:
        try:
            session.commit()
        except IntegrityError:
            # Two concurrent first-reads both tried to materialize defaults; the
            # unique (patient_id, reminder_kind) constraint let only one through.
            session.rollback()
    return list_preferences(session, patient_id)


def get_preference_or_404(session: Session, preference_id: UUID) -> NotificationPreference:
    preference = session.get(NotificationPreference, preference_id)
    if preference is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification preference not found")
    return preference


def _clear_unresolved_slot_for_retrigger(
    session: Session, *, patient_id: UUID, reminder_kind: str, today: date
) -> None:
    """Delete today's still-open (PENDING/SNOOZED) Notification row for this
    reminder kind — and its riding medication reminder, if any — so the next
    tick sees the slot as new again and regenerates + re-pushes it at the
    preference's newly-set time_of_day.

    Without this, moving a reminder's time after today's slot already fired
    is a silent no-op until tomorrow: `slot_key` is scoped to
    (reminder_kind, date) only, so `notification_exists_for_slot` in
    scheduler.py sees the old row as "already materialized" and skips both
    the new Notification and the Zalo push. Only PENDING/SNOOZED rows are
    cleared — a COMPLETED/SKIPPED one means the patient already resolved
    today's reminder, and editing the time afterward shouldn't resurrect it.
    """
    slot_keys = [f"{reminder_kind}:{today.isoformat()}", f"medication_{reminder_kind}:{today.isoformat()}"]
    statement = select(Notification).where(
        Notification.patient_id == patient_id,
        col(Notification.slot_key).in_(slot_keys),
        col(Notification.status).in_([NotificationStatus.PENDING, NotificationStatus.SNOOZED]),
    )
    for notification in session.exec(statement).all():
        session.delete(notification)
    session.commit()


def update_preference(
    session: Session,
    *,
    preference: NotificationPreference,
    time_of_day: time | None,
    is_enabled: bool | None,
    label: str | None,
    now: datetime | None = None,
) -> NotificationPreference:
    now = now or datetime.now(ZoneInfo(get_settings().reminder_timezone))
    # Only retrigger if the new time is still ahead this tick or later — moving
    # a reminder to a time already passed today can't regenerate (the tick only
    # fires on exact hour:minute match), so clearing the old row would leave the
    # patient with no reminder at all for the rest of today.
    should_retrigger = (
        time_of_day is not None
        and time_of_day != preference.time_of_day
        and (time_of_day.hour, time_of_day.minute) >= (now.hour, now.minute)
    )
    if time_of_day is not None:
        preference.time_of_day = time_of_day
    if is_enabled is not None:
        preference.is_enabled = is_enabled
    if label is not None:
        preference.label = label
    preference.updated_at = datetime.utcnow()
    session.add(preference)
    session.commit()
    session.refresh(preference)
    if should_retrigger:
        _clear_unresolved_slot_for_retrigger(
            session, patient_id=preference.patient_id, reminder_kind=preference.reminder_kind, today=now.date()
        )
    return preference


def create_custom_preference(
    session: Session,
    *,
    patient_id: UUID,
    label: str,
    time_of_day: time,
    notification_type: NotificationType,
) -> NotificationPreference:
    preference = NotificationPreference(
        patient_id=patient_id,
        reminder_kind=f"custom:{uuid4()}",
        notification_type=notification_type,
        label=label,
        time_of_day=time_of_day,
        is_enabled=True,
        is_custom=True,
    )
    session.add(preference)
    session.commit()
    session.refresh(preference)
    return preference


def delete_preference(session: Session, preference: NotificationPreference) -> None:
    if not preference.is_custom:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Default reminders can't be deleted — turn them off with is_enabled=false instead",
        )
    session.delete(preference)
    session.commit()


# ---- Zalo chat linking (proving a chat_id belongs to a given user) ----


def _generate_link_code(length: int = 8) -> str:
    return "".join(secrets.choice(_LINK_CODE_ALPHABET) for _ in range(length))


def create_link_code(session: Session, *, user_id: UUID, ttl_minutes: int, now: datetime | None = None) -> ZaloLinkCode:
    """Issue a fresh single-use code for `user_id` to send to the Zalo bot.
    Immediately expires any of this user's still-outstanding codes first, so
    only the most recently issued one can ever be redeemed — pressing
    "Connect Zalo" twice can't leave two valid codes a stale UI might still
    be showing.
    """
    now = now or datetime.utcnow()
    session.exec(
        update(ZaloLinkCode)
        .where(ZaloLinkCode.user_id == user_id, col(ZaloLinkCode.consumed_at).is_(None))
        .values(expires_at=now)
    )
    for _ in range(5):
        link_code = ZaloLinkCode(user_id=user_id, code=_generate_link_code(), expires_at=now + timedelta(minutes=ttl_minutes))
        session.add(link_code)
        try:
            session.commit()
        except IntegrityError:
            # Astronomically unlikely code collision (32^8 keyspace) — retry
            # with a freshly generated code rather than surface a 500.
            session.rollback()
            continue
        session.refresh(link_code)
        return link_code
    raise RuntimeError("Could not generate a unique Zalo link code after 5 attempts")


def consume_link_code(session: Session, *, code: str, chat_id: str, now: datetime | None = None) -> bool:
    """Called by the Zalo getUpdates listener for every inbound message's
    text: if it matches an outstanding (unconsumed, unexpired) link code,
    link that message's chat_id to the code's owner. Returns whether a
    match was found, purely so the listener knows whether to send a
    confirmation reply — the caller has no other use for it.
    """
    now = now or datetime.utcnow()
    statement = select(ZaloLinkCode).where(
        func.upper(ZaloLinkCode.code) == code.strip().upper(),
        col(ZaloLinkCode.consumed_at).is_(None),
        ZaloLinkCode.expires_at > now,
    )
    link_code = session.exec(statement).first()
    if link_code is None:
        return False

    link_code.consumed_at = now
    session.add(link_code)
    user = session.get(User, link_code.user_id)
    if user is not None:
        user.zalo_chat_id = chat_id
        user.updated_at = now
        session.add(user)
    session.commit()
    return True

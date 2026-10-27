"""Reminder-time suggestions (Epic 3.3).

For each enabled glucose / blood-pressure reminder, look at the patient's
responses over the last 14 days: responded_at - scheduled_for of completed
reminders (a glucose reminder's riding medication reminder counts too — it
fires at the same time). If at least 5 of the latest 7 responses came more
than 30 minutes late, suggest the median local response time (rounded to 5
minutes) as the new reminder time.

The suggestion is only a proposal: the preference changes when the patient
accepts it (through notifications.service.update_preference, so today's slot
is re-triggered the same way as a manual edit). No new suggestion for a
reminder within 14 days of the last decided one, and never two pending at
once. A response more than 4 hours after the reminder isn't treated as an
answer to it (auto_complete_pending can complete a morning reminder from an
evening reading).
"""

from __future__ import annotations

import statistics
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from codebase.backend.config import get_settings
from codebase.backend.notifications import service
from codebase.backend.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
    ReminderTimeSuggestion,
    SuggestionStatus,
)

LOOKBACK_DAYS = 14
LATEST_RESPONSES = 7
MIN_LATE = 5
LATE_AFTER = timedelta(minutes=30)
MAX_RESPONSE_DELAY = timedelta(hours=4)
MIN_SHIFT = timedelta(minutes=15)
COOLDOWN_DAYS = 14
_ROUND_MINUTES = 5
_ELIGIBLE = (NotificationType.GLUCOSE_REMINDER, NotificationType.BLOOD_PRESSURE_REMINDER)
_UTC = ZoneInfo("UTC")


def _round_time(minutes_of_day: float) -> time:
    rounded = int(round(minutes_of_day / _ROUND_MINUTES) * _ROUND_MINUTES) % (24 * 60)
    return time(rounded // 60, rounded % 60)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def analyze_preference(session: Session, preference: NotificationPreference, now_utc: datetime) -> tuple[time, int, int] | None:
    """(suggested_time, late_count, sample_count), or None when the
    patient's timing doesn't call for a change."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    since = (now_utc.replace(tzinfo=_UTC).astimezone(tz) - timedelta(days=LOOKBACK_DAYS)).date()
    prefixes = (f"{preference.reminder_kind}:", f"medication_{preference.reminder_kind}:")
    rows = session.exec(
        select(Notification).where(
            Notification.patient_id == preference.patient_id,
            Notification.status == NotificationStatus.COMPLETED,
            col(Notification.slot_date) >= since,
            col(Notification.responded_at).is_not(None),
        )
    ).all()
    responses = []
    for row in rows:
        if not row.slot_key.startswith(prefixes):
            continue
        delay = row.responded_at - row.scheduled_for
        if timedelta(0) <= delay <= MAX_RESPONSE_DELAY:
            responses.append(row)
    # One response per day (the reminder and its medication ride-along are
    # one moment for the patient): the earliest.
    by_day: dict = {}
    for row in sorted(responses, key=lambda r: r.responded_at):
        by_day.setdefault(row.slot_date, row)
    latest = sorted(by_day.values(), key=lambda r: r.slot_date)[-LATEST_RESPONSES:]
    late = [r for r in latest if r.responded_at - r.scheduled_for > LATE_AFTER]
    if len(late) < MIN_LATE:
        return None
    local_minutes = [
        _minutes(r.responded_at.replace(tzinfo=_UTC).astimezone(tz).time()) for r in latest
    ]
    suggested = _round_time(statistics.median(local_minutes))
    if abs(_minutes(suggested) - _minutes(preference.time_of_day)) < MIN_SHIFT.total_seconds() / 60:
        return None
    return suggested, len(late), len(latest)


def _blocked_by_recent_decision(session: Session, preference: NotificationPreference, now_utc: datetime) -> bool:
    suggestions = session.exec(
        select(ReminderTimeSuggestion).where(ReminderTimeSuggestion.preference_id == preference.id)
    ).all()
    if any(s.status == SuggestionStatus.PENDING for s in suggestions):
        return True
    cutoff = now_utc - timedelta(days=COOLDOWN_DAYS)
    return any(s.decided_at is not None and s.decided_at > cutoff for s in suggestions)


def refresh_suggestions(session: Session, patient_id: UUID, now_utc: datetime | None = None) -> list[ReminderTimeSuggestion]:
    """Create any due suggestion for this patient, then return the pending ones."""
    now_utc = now_utc or datetime.utcnow()
    preferences = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.patient_id == patient_id,
            col(NotificationPreference.is_enabled).is_(True),
            col(NotificationPreference.notification_type).in_(_ELIGIBLE),
        )
    ).all()
    for preference in preferences:
        if _blocked_by_recent_decision(session, preference, now_utc):
            continue
        result = analyze_preference(session, preference, now_utc)
        if result is None:
            continue
        suggested, late_count, sample_count = result
        session.add(
            ReminderTimeSuggestion(
                patient_id=patient_id,
                preference_id=preference.id,
                previous_time=preference.time_of_day,
                suggested_time=suggested,
                late_count=late_count,
                sample_count=sample_count,
                created_at=now_utc,
            )
        )
    session.commit()
    return list(
        session.exec(
            select(ReminderTimeSuggestion).where(
                ReminderTimeSuggestion.patient_id == patient_id,
                ReminderTimeSuggestion.status == SuggestionStatus.PENDING,
            )
        ).all()
    )


def _owned_pending(session: Session, suggestion_id: UUID, patient_id: UUID) -> ReminderTimeSuggestion:
    suggestion = session.get(ReminderTimeSuggestion, suggestion_id)
    if suggestion is None or suggestion.patient_id != patient_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy đề xuất.")
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Đề xuất này đã được xử lý.")
    return suggestion


def accept_suggestion(session: Session, suggestion_id: UUID, patient_id: UUID) -> ReminderTimeSuggestion:
    suggestion = _owned_pending(session, suggestion_id, patient_id)
    preference = service.get_preference_or_404(session, suggestion.preference_id)
    service.update_preference(
        session, preference=preference, time_of_day=suggestion.suggested_time, is_enabled=None, label=None
    )
    suggestion.status = SuggestionStatus.ACCEPTED
    suggestion.decided_at = datetime.utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def decline_suggestion(session: Session, suggestion_id: UUID, patient_id: UUID) -> ReminderTimeSuggestion:
    suggestion = _owned_pending(session, suggestion_id, patient_id)
    suggestion.status = SuggestionStatus.DECLINED
    suggestion.decided_at = datetime.utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion

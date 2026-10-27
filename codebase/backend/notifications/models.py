from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Column, Field, SQLModel, UniqueConstraint
from sqlmodel import Enum as SAEnum

from codebase.backend.health.models import MealContext, MealType


class NotificationType(StrEnum):
    GLUCOSE_REMINDER = "glucose_reminder"
    BLOOD_PRESSURE_REMINDER = "blood_pressure_reminder"
    MEDICATION_REMINDER = "medication_reminder"
    CUSTOM = "custom"
    # Event-driven, unlike the three reminder types above (which fire on a
    # fixed daily schedule from NotificationPreference/scheduler.py's cron
    # tick) — triggered by health/threshold_service.py the moment a specific
    # glucose reading is evaluated WATCH. See
    # docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6 (Giai doan E).
    GLUCOSE_ALERT = "glucose_alert"
    # Event-driven too: one per patient and week when reports/service.py
    # generates the weekly report (Epic 2).
    WEEKLY_REPORT = "weekly_report"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    SNOOZED = "snoozed"


class Notification(SQLModel, table=True):
    """A single scheduled reminder instance for a patient — see PRD F1 AC:
    "Reminder được tạo từ kế hoạch đo; bệnh nhân có thể hoàn thành, bỏ qua
    hoặc hoãn và không nhận reminder trùng."

    Generated from the patient's NotificationPreference plan by the recurring
    cron tick (notifications/scheduler.py). A reminder is not fire-and-forget:
    it stays PENDING — and is periodically re-touched via `last_notified_at`/
    `nudge_count` so a polling client can tell it needs to be resurfaced — until
    the patient completes it (directly, or by logging the underlying record),
    skips it, or snoozes it. A snoozed reminder automatically reverts to PENDING
    once `snoozed_until` passes, so it keeps "popping up" rather than silently
    disappearing.

    `slot_key` identifies one calendar-day + reminder-rule instance (e.g.
    "glucose_before_breakfast:2026-08-11") and is unique per patient — this is
    what makes the generator idempotent, so a retry or overlapping tick can
    never create a duplicate reminder for the same slot.
    """

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("patient_id", "slot_key", name="uq_notification_patient_slot"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    notification_type: NotificationType = Field(
        sa_column=Column(SAEnum(NotificationType, name="notification_type"), nullable=False)
    )
    title: str = Field(max_length=200)
    message: str = Field(max_length=500)
    slot_key: str = Field(max_length=100)
    slot_date: date = Field(index=True)
    scheduled_for: datetime
    status: NotificationStatus = Field(
        default=NotificationStatus.PENDING,
        sa_column=Column(
            SAEnum(NotificationStatus, name="notification_status"), nullable=False, server_default=NotificationStatus.PENDING.name
        ),
    )
    snoozed_until: datetime | None = Field(default=None)
    responded_at: datetime | None = Field(default=None)
    last_notified_at: datetime = Field(default_factory=datetime.utcnow)
    nudge_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationPreference(SQLModel, table=True):
    """A patient's personal reminder plan entry — the "kế hoạch đo" from PRD F1.

    System defaults (see notifications/defaults.py) are materialized into rows
    the first time a patient's plan is read, so each default reminder gets a
    stable, patient-owned row a patient can then adjust: change `time_of_day`,
    turn it off (`is_enabled=False`), or — for reminders they add themselves
    (`is_custom=True`) — delete outright. `reminder_kind` is stable across days
    (unlike Notification.slot_key, which is date-scoped) and is what the cron
    tick and generated Notification rows key off of.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("patient_id", "reminder_kind", name="uq_preference_patient_kind"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    reminder_kind: str = Field(max_length=100)
    notification_type: NotificationType = Field(
        sa_column=Column(SAEnum(NotificationType, name="preference_notification_type"), nullable=False)
    )
    meal_type: MealType | None = Field(
        default=None, sa_column=Column(SAEnum(MealType, name="preference_meal_type"), nullable=True)
    )
    meal_context: MealContext | None = Field(
        default=None, sa_column=Column(SAEnum(MealContext, name="preference_meal_context"), nullable=True)
    )
    label: str = Field(max_length=200)
    time_of_day: time
    is_enabled: bool = Field(default=True)
    is_custom: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class ReminderTimeSuggestion(SQLModel, table=True):
    """A proposed new time for one reminder (Epic 3.3), from how late the
    patient has actually been responding (notifications/smart_reminders.py).
    Never applied without the patient: only an ACCEPTED suggestion changes
    NotificationPreference.time_of_day."""

    __tablename__ = "reminder_time_suggestions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    preference_id: UUID = Field(foreign_key="notification_preferences.id", index=True)
    # Not "current_time": that is an SQL keyword (CURRENT_TIME).
    previous_time: time
    suggested_time: time
    late_count: int  # responses > 30 min after the reminder, among...
    sample_count: int  # ...the latest responses looked at
    status: SuggestionStatus = Field(
        default=SuggestionStatus.PENDING,
        sa_column=Column(
            SAEnum(SuggestionStatus, name="reminder_suggestion_status"), nullable=False, server_default="PENDING"
        ),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: datetime | None = Field(default=None)


class ZaloLinkCode(SQLModel, table=True):
    """A one-time code a user sends to the Zalo bot to prove that a given
    `chat_id` (learned from the bot's getUpdates feed, which carries no user
    identity of its own — see notifications/zalo_link_listener.py) belongs to
    them. Short-lived and single-use: `consumed_at` is set the moment a
    matching inbound message links it to a chat_id, and a fresh code issued
    for the same user expires any still-outstanding one first (see
    service.create_link_code) so only one code is ever valid at a time.
    """

    __tablename__ = "zalo_link_codes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    code: str = Field(max_length=32, unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    consumed_at: datetime | None = Field(default=None)

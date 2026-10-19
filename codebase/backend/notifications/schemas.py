from datetime import date, time
from typing import Any, Literal
from uuid import UUID

from sqlmodel import Field, SQLModel

from codebase.backend.health.models import MealContext, MealType
from codebase.backend.notifications.models import NotificationStatus, NotificationType
from codebase.backend.time_utils import UtcDatetime


class ZaloLinkStatusPublic(SQLModel):
    linked: bool


class ZaloLinkCodePublic(SQLModel):
    code: str
    expires_at: UtcDatetime
    chat_url: str | None = Field(default=None, description="Opens the bot chat; null if not configured server-side")


class NotificationPublic(SQLModel):
    id: UUID
    patient_id: UUID
    notification_type: NotificationType
    title: str
    message: str
    slot_date: date
    scheduled_for: UtcDatetime
    status: NotificationStatus
    snoozed_until: UtcDatetime | None
    responded_at: UtcDatetime | None
    last_notified_at: UtcDatetime
    nudge_count: int
    created_at: UtcDatetime


class NotificationStatusUpdate(SQLModel):
    status: NotificationStatus
    snooze_minutes: int | None = Field(default=None, gt=0, le=180, description="Required when status is 'snoozed'")


class NotificationPreferencePublic(SQLModel):
    id: UUID
    patient_id: UUID
    reminder_kind: str
    notification_type: NotificationType
    meal_type: MealType | None
    meal_context: MealContext | None
    label: str
    time_of_day: time
    is_enabled: bool
    is_custom: bool
    updated_at: UtcDatetime


class NotificationPreferenceUpdate(SQLModel):
    time_of_day: time | None = Field(default=None, description="New time of day to fire this reminder")
    is_enabled: bool | None = Field(default=None, description="False turns this reminder off")
    label: str | None = Field(default=None, min_length=1, max_length=200)


class NotificationPreferenceCreate(SQLModel):
    label: str = Field(min_length=1, max_length=200)
    time_of_day: time
    notification_type: NotificationType = Field(default=NotificationType.CUSTOM)


class ZaloNotificationRequest(SQLModel):
    recipient_type: Literal["doctor", "patient"]
    recipient_id: str = Field(
        min_length=1, max_length=128, description="OA user_id, phone (ZBS), or chat_id (bot)"
    )
    channel: Literal["oa", "zbs", "bot"] = "oa"
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    template_id: str | None = Field(default=None, min_length=1, max_length=100)
    template_data: dict[str, Any] = Field(default_factory=dict)
    tracking_id: str | None = Field(default=None, max_length=128)


class ZaloNotificationPublic(SQLModel):
    success: bool
    channel: Literal["oa", "zbs", "bot"]
    recipient_type: Literal["doctor", "patient"]
    tracking_id: str | None = None
    message: str

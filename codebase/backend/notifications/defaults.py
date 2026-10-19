from dataclasses import dataclass
from datetime import time

from codebase.backend.health.models import MealContext, MealType
from codebase.backend.notifications.models import NotificationType


@dataclass(frozen=True)
class DefaultReminder:
    """One row of the system default monitoring plan — see PRD F1 AC: "Reminder
    được tạo từ kế hoạch đo". `reminder_kind` is a stable identifier (independent
    of any particular day) that NotificationPreference rows key off of, so a
    patient's per-reminder customization (time/enabled) survives across days.
    """

    reminder_kind: str
    notification_type: NotificationType
    meal_type: MealType | None
    meal_context: MealContext | None
    label: str
    time_of_day: time


# 6 glucose checks/day — before and after each of the 3 main meals, offset from
# typical meal times (breakfast ~7h, lunch ~12h, dinner ~19h): 1h before, 2h after
# (standard postprandial-glucose timing). Medication reminders aren't listed here —
# they ride along at whatever time a patient's glucose reminders end up at (see
# scheduler.py), since dosing is tied to meals rather than a fixed clock time.
DEFAULT_REMINDERS: list[DefaultReminder] = [
    DefaultReminder(
        "glucose_before_breakfast",
        NotificationType.GLUCOSE_REMINDER,
        MealType.BREAKFAST,
        MealContext.BEFORE_MEAL,
        "Đo đường huyết trước bữa sáng",
        time(6, 0),
    ),
    DefaultReminder(
        "glucose_after_breakfast",
        NotificationType.GLUCOSE_REMINDER,
        MealType.BREAKFAST,
        MealContext.AFTER_MEAL,
        "Đo đường huyết sau bữa sáng",
        time(9, 0),
    ),
    DefaultReminder(
        "glucose_before_lunch",
        NotificationType.GLUCOSE_REMINDER,
        MealType.LUNCH,
        MealContext.BEFORE_MEAL,
        "Đo đường huyết trước bữa trưa",
        time(11, 0),
    ),
    DefaultReminder(
        "glucose_after_lunch",
        NotificationType.GLUCOSE_REMINDER,
        MealType.LUNCH,
        MealContext.AFTER_MEAL,
        "Đo đường huyết sau bữa trưa",
        time(14, 0),
    ),
    DefaultReminder(
        "glucose_before_dinner",
        NotificationType.GLUCOSE_REMINDER,
        MealType.DINNER,
        MealContext.BEFORE_MEAL,
        "Đo đường huyết trước bữa tối",
        time(18, 0),
    ),
    DefaultReminder(
        "glucose_after_dinner",
        NotificationType.GLUCOSE_REMINDER,
        MealType.DINNER,
        MealContext.AFTER_MEAL,
        "Đo đường huyết sau bữa tối",
        time(21, 0),
    ),
    # Twice daily, independent of meal times — standard home blood-pressure
    # monitoring guidance (morning + evening).
    DefaultReminder(
        "blood_pressure_morning",
        NotificationType.BLOOD_PRESSURE_REMINDER,
        None,
        None,
        "Đo huyết áp buổi sáng",
        time(7, 0),
    ),
    DefaultReminder(
        "blood_pressure_evening",
        NotificationType.BLOOD_PRESSURE_REMINDER,
        None,
        None,
        "Đo huyết áp buổi tối",
        time(21, 0),
    ),
]

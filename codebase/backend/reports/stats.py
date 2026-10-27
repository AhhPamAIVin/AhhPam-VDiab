"""Deterministic weekly figures for the patient report (Epic 2.2) — no LLM.

"In target" uses each reading's own threshold_status (NORMAL vs WATCH/DANGER),
i.e. the patient's CarePlanTarget in force when it was measured
(health/threshold_service.py). Readings evaluated before any care plan
existed (UNAVAILABLE / null) are left out of that percentage rather than
guessed at; `evaluated_count` says how many it covers.

Days are counted in settings.reminder_timezone, the patient's local calendar.
Everything returned is JSON-serializable: it is stored on the report row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from codebase.backend.config import get_settings
from codebase.backend.health.meal_response import meal_glucose_response
from codebase.backend.health.models import ActivityRecord, BloodPressureRecord, GlucoseRecord, MealRecord
from codebase.models.agents.tools.metrics_tools import HYPOGLYCEMIA_THRESHOLD_MG_DL

# A food goes in the report when post-meal glucose was above target after
# at least this share of its meals (same bar as the doctor summary line).
_MEAL_FLAG_PCT = 50.0
_MEAL_LOOKBACK_DAYS = 30
_MAX_FOODS = 3
_EVALUATED = ("NORMAL", "WATCH", "DANGER")
_CONTEXT_ORDER = ("FASTING", "BEFORE_MEAL", "AFTER_MEAL", "BEFORE_SLEEP", "RANDOM", "EVENT_RECOVERY")


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {"count": len(values), "avg": _round(sum(values) / len(values)), "min": min(values), "max": max(values)}


def _rows(session: Session, model, time_field: str, patient_id: UUID, start: datetime, end: datetime) -> list:
    column = getattr(model, time_field)
    statement = select(model).where(model.patient_id == patient_id, col(column) >= start, col(column) < end)
    return list(session.exec(statement).all())


def glucose_stats(readings: list[GlucoseRecord]) -> dict:
    values = [r.value for r in readings]
    evaluated = [r for r in readings if r.threshold_status in _EVALUATED]
    in_target = [r for r in evaluated if r.threshold_status == "NORMAL"]
    by_context: dict[str, list[float]] = {}
    for reading in readings:
        context = reading.meal_context.value if reading.meal_context else "RANDOM"
        by_context.setdefault(context, []).append(reading.value)
    return {
        **_summary(values),
        "low_count": sum(1 for v in values if v < HYPOGLYCEMIA_THRESHOLD_MG_DL),
        "evaluated_count": len(evaluated),
        "in_target_pct": _round(100 * len(in_target) / len(evaluated)) if evaluated else None,
        "by_context": {c: _summary(by_context[c]) for c in _CONTEXT_ORDER if c in by_context},
    }


def _local_days(timestamps: list[datetime], tz: ZoneInfo) -> int:
    return len({t.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date() for t in timestamps})


def _delta(current: float | None, previous: float | None) -> float | None:
    return _round(current - previous) if current is not None and previous is not None else None


def compute_weekly_stats(session: Session, patient_id: UUID, start: datetime, end: datetime) -> dict:
    """Figures for [start, end) and the 7 days before it (naive UTC bounds)."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    glucose = _rows(session, GlucoseRecord, "measured_at", patient_id, start, end)
    previous = _rows(session, GlucoseRecord, "measured_at", patient_id, start - timedelta(days=7), start)
    pressure = _rows(session, BloodPressureRecord, "measured_at", patient_id, start, end)
    meals = _rows(session, MealRecord, "meal_time", patient_id, start, end)
    activities = _rows(session, ActivityRecord, "activity_time", patient_id, start, end)

    current_g = glucose_stats(glucose)
    previous_g = glucose_stats(previous)
    meal_response = meal_glucose_response(session, patient_id, days=_MEAL_LOOKBACK_DAYS, now=end)
    top_foods = [
        {"name": g["name"], "n": g["n"], "avg_post_mg_dl": g["avg_post_mg_dl"], "pct_over_target": g["pct_over_target"]}
        for g in meal_response["by_food"]
        if g["pct_over_target"] >= _MEAL_FLAG_PCT
    ][:_MAX_FOODS]

    timestamps = (
        [r.measured_at for r in glucose]
        + [r.measured_at for r in pressure]
        + [m.meal_time for m in meals]
        + [a.activity_time for a in activities]
    )
    return {
        "glucose": current_g,
        "previous_week": {
            "count": previous_g["count"],
            "avg": previous_g["avg"],
            "in_target_pct": previous_g["in_target_pct"],
        },
        "change": {
            "avg_delta": _delta(current_g["avg"], previous_g["avg"]),
            "in_target_pct_delta": _delta(current_g["in_target_pct"], previous_g["in_target_pct"]),
        },
        "blood_pressure": {
            "count": len(pressure),
            "systolic_avg": _summary([p.systolic for p in pressure])["avg"],
            "diastolic_avg": _summary([p.diastolic for p in pressure])["avg"],
        },
        "meal_count": len(meals),
        "activity_count": len(activities),
        "activity_minutes": sum(a.duration_minutes for a in activities),
        "days_logged": _local_days(timestamps, tz),
        "meals": {
            "lookback_days": _MEAL_LOOKBACK_DAYS,
            "meals_paired": meal_response["meals_paired"],
            "min_n": meal_response["min_n"],
            "top_foods": top_foods,
        },
    }


def has_any_data(stats: dict) -> bool:
    return bool(
        stats["glucose"]["count"]
        or stats["blood_pressure"]["count"]
        or stats["meal_count"]
        or stats["activity_count"]
    )


# One "việc nên làm" per report, picked by rule from fixed, reviewed texts —
# never written by the LLM. Order is priority: safety first.
def pick_tip(stats: dict) -> str:
    glucose = stats["glucose"]
    if glucose["low_count"]:
        return (
            f"Tuần này có {glucose['low_count']} lần đường huyết dưới 70 mg/dL. Hãy báo cho bác sĩ điều trị biết; "
            "nếu thấy run tay, vã mồ hôi hay chóng mặt, hãy xử trí theo hướng dẫn bác sĩ đã dặn."
        )
    if not has_any_data(stats):
        return "Tuần tới, bạn hãy thử đo và ghi đường huyết vài lần để mình cùng theo dõi xu hướng nhé."
    if stats["days_logged"] < 4:
        return "Tuần tới, bạn hãy thử ghi đường huyết hoặc bữa ăn ít nhất 4 ngày để thấy rõ xu hướng hơn."
    top_foods = stats["meals"]["top_foods"]
    if top_foods:
        return (
            f"Món {top_foods[0]['name']} thường đi kèm đường huyết sau ăn cao ở bạn. "
            "Bạn có thể hỏi bác sĩ hoặc chuyên gia dinh dưỡng về khẩu phần món này."
        )
    if glucose["in_target_pct"] is not None and glucose["in_target_pct"] < 50:
        return "Nhiều lần đo tuần này nằm ngoài mục tiêu. Hãy mang báo cáo này khi tái khám để bác sĩ cùng xem."
    return "Bạn đang theo dõi đều đặn — hãy tiếp tục ghi đường huyết và bữa ăn như tuần này nhé."

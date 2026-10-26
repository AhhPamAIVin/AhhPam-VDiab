"""Meal <-> post-meal glucose pairing (Epic 1.4).

Each analysed meal is paired with:
- one AFTER_MEAL reading 60-180 min after meal_time (configurable) — the one
  closest to the standard 2-hour mark when there are several;
- the nearest BEFORE_MEAL/FASTING reading up to 60 min before, if any, so a
  rise (delta) can be shown next to the absolute value.

Pairs are grouped by matched food and by carb_load: number of meals, mean
post-meal glucose, mean rise, and % above the post-prandial target
(POST_PRANDIAL_TARGET_MG_DL, BYT-5481 < 180 mg/dL). A group is reported only
from settings.meal_response_min_n meals (default 3) — fewer is anecdote.

This is association, not cause: the caller words it as "thường đi kèm",
never "gây ra" (guardrail.py blocks the causal phrasing). Glucose values are
stored in mg/dL (health/service.py converts mmol/L on write).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.config import get_settings
from codebase.backend.health.meal_analysis import analyses_by_meal
from codebase.backend.health.models import GlucoseRecord, MealContext, MealRecord
from codebase.models.agents.tools.metrics_tools import POST_PRANDIAL_TARGET_MG_DL

_TWO_HOURS = timedelta(hours=2)
_BEFORE_CONTEXTS = (MealContext.BEFORE_MEAL, MealContext.FASTING)


def _round(value: float) -> float:
    return round(value, 1)


def pair_meals_with_glucose(session: Session, patient_id: UUID, start: datetime, end: datetime) -> list[dict]:
    """One dict per analysed meal in [start, end] that has a post-meal reading."""
    settings = get_settings()
    after_min = timedelta(minutes=settings.meal_response_after_min_minutes)
    after_max = timedelta(minutes=settings.meal_response_after_max_minutes)
    before_max = timedelta(minutes=settings.meal_response_before_max_minutes)

    meals = list(
        session.exec(
            select(MealRecord).where(
                MealRecord.patient_id == patient_id,
                col(MealRecord.meal_time) >= start,
                col(MealRecord.meal_time) <= end,
            )
        ).all()
    )
    analyses = analyses_by_meal(session, [m.id for m in meals])
    if not analyses:
        return []
    readings = list(
        session.exec(
            select(GlucoseRecord).where(
                GlucoseRecord.patient_id == patient_id,
                col(GlucoseRecord.measured_at) >= start - before_max,
                col(GlucoseRecord.measured_at) <= end + after_max,
            )
        ).all()
    )
    after = [r for r in readings if r.meal_context == MealContext.AFTER_MEAL]
    before = [r for r in readings if r.meal_context in _BEFORE_CONTEXTS]

    pairs = []
    for meal in meals:
        analysis = analyses.get(meal.id)
        if analysis is None:
            continue
        post_candidates = [r for r in after if meal.meal_time + after_min <= r.measured_at <= meal.meal_time + after_max]
        if not post_candidates:
            continue
        post = min(post_candidates, key=lambda r: abs(r.measured_at - meal.meal_time - _TWO_HOURS))
        pre_candidates = [r for r in before if meal.meal_time - before_max <= r.measured_at <= meal.meal_time]
        pre = max(pre_candidates, key=lambda r: r.measured_at) if pre_candidates else None
        foods = {}
        for item in analysis.items:
            if item.get("matched_food_id"):
                foods[item["matched_food_id"]] = item.get("matched_name") or item.get("name")
        pairs.append(
            {
                "meal_id": meal.id,
                "meal_time": meal.meal_time,
                "carb_load": analysis.carb_load.value,
                "total_carbs_g": analysis.total_carbs_g,
                "foods": foods,
                "post_mg_dl": post.value,
                "pre_mg_dl": pre.value if pre else None,
                "delta_mg_dl": _round(post.value - pre.value) if pre else None,
            }
        )
    return pairs


def _summarize(group: list[dict]) -> dict:
    posts = [p["post_mg_dl"] for p in group]
    deltas = [p["delta_mg_dl"] for p in group if p["delta_mg_dl"] is not None]
    over = [v for v in posts if v > POST_PRANDIAL_TARGET_MG_DL]
    return {
        "n": len(group),
        "avg_post_mg_dl": _round(sum(posts) / len(posts)),
        "avg_delta_mg_dl": _round(sum(deltas) / len(deltas)) if deltas else None,
        "n_with_delta": len(deltas),
        "pct_over_target": _round(100 * len(over) / len(posts)),
    }


def _ranked(groups: list[dict]) -> list[dict]:
    return sorted(groups, key=lambda g: (-g["pct_over_target"], -g["avg_post_mg_dl"]))


def meal_glucose_response(session: Session, patient_id: UUID, *, days: int, now: datetime | None = None) -> dict:
    end = now or datetime.utcnow()
    start = end - timedelta(days=days)
    min_n = get_settings().meal_response_min_n
    pairs = pair_meals_with_glucose(session, patient_id, start, end)

    by_food: dict[str, list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    by_load: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        for food_id, name in pair["foods"].items():
            by_food[food_id].append(pair)
            names[food_id] = name
        by_load[pair["carb_load"]].append(pair)

    foods = [
        {"food_id": food_id, "name": names[food_id], **_summarize(group)}
        for food_id, group in by_food.items()
        if len(group) >= min_n
    ]
    loads = [{"carb_load": load, **_summarize(group)} for load, group in by_load.items() if len(group) >= min_n]
    return {
        "start": start,
        "end": end,
        "days": days,
        "meals_paired": len(pairs),
        "min_n": min_n,
        "post_prandial_target_mg_dl": POST_PRANDIAL_TARGET_MG_DL,
        "by_food": _ranked(foods),
        "by_carb_load": _ranked(loads),
    }

"""Display-only stat-tile overlay for the AI Insight card — NOT part of the
AI Summary agent.

This module deliberately lives beside `agent/router.py` rather than inside
it, and adds nothing to `AgentSummaryResponse`/`AgentSummarySnapshot`/
`synthesize_summary.py`. It never touches the agent's state, graph nodes,
prompts, or DB model — it only *reads* the same raw glucose data the agent
already summarizes in prose, using the agent's own existing, unmodified
utility functions (`get_health_records` / `calculate_health_metrics`), and
reshapes a few of those numbers into a small stat-tile payload for the
frontend. If this ever disagrees with what the agent's prose says about the
same period, that's a bug in this file — the math is the same single source
of truth on purpose, just imported rather than duplicated.

Permission checks reuse `agent/router.py`'s existing helpers (imported, not
duplicated) so this endpoint can never be looser than `/agent/summary`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from codebase.backend.agent.router import (
    _can_use_agent,
    _ensure_summary_access,
    _resolve_summary_patient_id,
)
from codebase.backend.auth.models import User
from codebase.backend.db import get_session
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics
from codebase.models.agents.tools.patient_tools import get_health_records

router = APIRouter()

_POST_PRANDIAL_CRITICAL_PCT = 50  # mirrors synthesize_summary.py's _build_attention_points cutoff
_FASTING_GOOD_PCT = 80
_FASTING_WARNING_PCT = 50
# A first-half-vs-second-half average gap below this reads as noise, not a
# real trend — independently chosen here (this file never imports from
# synthesize_summary.py), but happens to mirror the same 10 mg/dL bar that
# file's own weekday/weekend gap check uses, since that's already the
# established "notable glucose gap" magnitude in this codebase.
_TREND_NOTABLE_DELTA_MG_DL = 10


class KeyMetric(BaseModel):
    label: str
    value: str
    unit: str | None = None
    tone: Literal["good", "warning", "critical"]


class GlucoseTrend(BaseModel):
    """First-half-vs-second-half comparison of the period's average glucose —
    genuine trend direction, distinct from KeyMetric's period-wide averages.
    Deterministic arithmetic only, same no-fabrication spirit as the agent's
    own metrics: never asserts a specific health outcome, only direction and
    magnitude."""

    direction: Literal["up", "down", "stable"]
    delta: float
    first_half_avg: float
    second_half_avg: float


class KeyMetricsResponse(BaseModel):
    key_metrics: list[KeyMetric]
    glucose_trend: GlucoseTrend | None = None


def _build_key_metrics(metrics: dict) -> list[KeyMetric]:
    """Same 3 headline numbers the agent's prose already narrates, reshaped
    for a stat-tile UI. Pure arithmetic over `calculate_health_metrics`'s
    output — no LLM, no agent state."""
    key_metrics: list[KeyMetric] = []

    overall = metrics.get("glucose_overall") or {}
    if overall.get("count"):
        # No blended-average glucose target exists anywhere in this codebase
        # (only the per-context targets below) — informational, never judged.
        key_metrics.append(KeyMetric(label="Đường huyết trung bình", value=str(overall["avg"]), unit="mg/dL", tone="good"))

    post = metrics.get("post_prandial") or {}
    if post.get("count"):
        pct = post["over_target_pct"] or 0
        tone: Literal["good", "warning", "critical"] = (
            "critical" if pct >= _POST_PRANDIAL_CRITICAL_PCT else "warning" if pct > 0 else "good"
        )
        key_metrics.append(KeyMetric(label="Sau ăn vượt ngưỡng", value=str(pct), unit="%", tone=tone))

    fasting = metrics.get("fasting") or {}
    if fasting.get("count"):
        pct = fasting["in_range_pct"] or 0
        tone = "good" if pct >= _FASTING_GOOD_PCT else "warning" if pct >= _FASTING_WARNING_PCT else "critical"
        key_metrics.append(KeyMetric(label="Lúc đói trong mục tiêu", value=str(pct), unit="%", tone=tone))

    return key_metrics


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _compute_glucose_trend(glucose_records: list[dict], start: datetime, end: datetime) -> GlucoseTrend | None:
    """Splits the period at its calendar midpoint (not at the Nth record) so
    the comparison reflects actual elapsed time, then compares average
    glucose across the two halves. None when either half has no readings —
    never guesses a direction from a lopsided or empty split."""
    midpoint = start + (end - start) / 2
    first_half: list[float] = []
    second_half: list[float] = []
    for record in glucose_records:
        value = record.get("value")
        when = _as_datetime(record.get("measured_at"))
        if value is None or when is None:
            continue
        (first_half if when < midpoint else second_half).append(float(value))

    if not first_half or not second_half:
        return None

    first_avg = round(sum(first_half) / len(first_half), 1)
    second_avg = round(sum(second_half) / len(second_half), 1)
    delta = round(second_avg - first_avg, 1)

    direction: Literal["up", "down", "stable"]
    if delta >= _TREND_NOTABLE_DELTA_MG_DL:
        direction = "up"
    elif delta <= -_TREND_NOTABLE_DELTA_MG_DL:
        direction = "down"
    else:
        direction = "stable"

    return GlucoseTrend(direction=direction, delta=delta, first_half_avg=first_avg, second_half_avg=second_avg)


@router.get("/summary/key-metrics", response_model=KeyMetricsResponse)
async def summary_key_metrics(
    patient_id: UUID | None = None,
    days: int = 7,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> KeyMetricsResponse:
    resolved_patient_id = _resolve_summary_patient_id(current_user, patient_id)
    _ensure_summary_access(session, current_user, resolved_patient_id)

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    records = get_health_records(session, resolved_patient_id, start, end)
    metrics = calculate_health_metrics(records)
    return KeyMetricsResponse(
        key_metrics=_build_key_metrics(metrics),
        glucose_trend=_compute_glucose_trend(records.get("glucose") or [], start, end),
    )

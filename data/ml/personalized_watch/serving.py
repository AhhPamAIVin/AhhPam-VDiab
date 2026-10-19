"""The safety-gated inference contract for the personalized-WATCH system
(Giai doan C, step 9 in docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md).

This is the one function every caller — the shadow-mode runner here, and
eventually the live backend in Giai doan D — must go through. It encodes
the non-negotiable ordering from plan section 4 (Lop 1): a DANGER reading
is decided by the fixed doctor-approved cutoffs alone, and the ML model is
never even invoked for it, let alone allowed to override it.

Boundary semantics match the rule engine exactly (build_personalized_
attention_dataset.py): DANGER is a *strict* inequality (`value < danger_low`
or `value > danger_high`) — a reading exactly at danger_low or danger_high
is not DANGER.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class EvaluationResult:
    status: str  # "DANGER" | "WATCH" | "NORMAL"
    ml_called: bool
    watch_probability: float | None


def evaluate_measurement(
    glucose_mg_dl: float,
    danger_low: float,
    danger_high: float,
    feature_row: pd.DataFrame,
    predict_watch_probability: Callable[[pd.DataFrame], float],
    operating_threshold: float = 0.5,
) -> EvaluationResult:
    """`predict_watch_probability` receives a single-row DataFrame and must
    return one float — it is the only thing this function ever asks the ML
    model to do, and only when the DANGER gate below has already passed."""
    if glucose_mg_dl < danger_low or glucose_mg_dl > danger_high:
        return EvaluationResult(status="DANGER", ml_called=False, watch_probability=None)

    probability = predict_watch_probability(feature_row)
    status = "WATCH" if probability >= operating_threshold else "NORMAL"
    return EvaluationResult(status=status, ml_called=True, watch_probability=probability)

"""Meal and medication-adherence tools for the respond agent (Epic 1.4, 3.2).

get_meal_glucose_response wraps health/meal_response.py for the prompt: only
the grouped, already-thresholded figures (n >= min_n) — never the per-meal
pairs — so the model can quote a number that code computed and cannot build
its own pattern out of two meals. get_medication_adherence does the same for
medications/adherence.py (summary figures, not the per-day list).

is_meal_question / is_medication_question decide when a QA turn gets these
blocks: deterministic keyword checks, so an unrelated question doesn't pull
them into the answer.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlmodel import Session

from codebase.backend.health.meal_response import meal_glucose_response
from codebase.backend.medications.adherence import adherence_last_days

MEAL_RESPONSE_DAYS = 30

_MEAL_QUESTION_RE = re.compile(
    r"\b(ăn|món|bữa|thức ăn|đồ ăn|đồ uống|thực phẩm|dinh dưỡng|tinh bột|carb|bột đường|"
    r"cơm|phở|bún|bánh|xôi|trái cây|hoa quả|ngọt|thực đơn|chế độ ăn)\b",
    re.IGNORECASE,
)
_MEDICATION_QUESTION_RE = re.compile(
    r"\b(thuốc|uống thuốc|quên thuốc|bỏ thuốc|tuân thủ|liều|insulin|metformin|nhắc uống)\b", re.IGNORECASE
)


def is_meal_question(message: str | None) -> bool:
    return bool(message and _MEAL_QUESTION_RE.search(message))


def is_medication_question(message: str | None) -> bool:
    return bool(message and _MEDICATION_QUESTION_RE.search(message))


def get_medication_adherence(session: Session, patient_id: UUID, days: int = 30) -> dict:
    result = adherence_last_days(session, patient_id, days)
    return {key: value for key, value in result.items() if key != "daily"}


def get_meal_glucose_response(session: Session, patient_id: UUID, days: int = MEAL_RESPONSE_DAYS) -> dict:
    result = meal_glucose_response(session, patient_id, days=days)
    return {
        "days": result["days"],
        "meals_paired": result["meals_paired"],
        "min_meals_per_group": result["min_n"],
        "post_prandial_target_mg_dl": result["post_prandial_target_mg_dl"],
        "by_food": [
            {k: g[k] for k in ("name", "n", "avg_post_mg_dl", "avg_delta_mg_dl", "pct_over_target")}
            for g in result["by_food"]
        ],
        "by_carb_load": [
            {k: g[k] for k in ("carb_load", "n", "avg_post_mg_dl", "avg_delta_mg_dl", "pct_over_target")}
            for g in result["by_carb_load"]
        ],
    }

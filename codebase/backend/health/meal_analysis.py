"""Meal analysis persistence and triggers (Epic 1.3).

The analysis itself is build_meal_analysis_graph() (codebase/models/agents/
graph.py); this module runs it for a MealRecord and stores the result as the
meal's single MealAnalysis row (meal_id is unique — a rerun or a patient edit
updates that row).

Triggers: POST /health/meals and a meal confirmed from chat schedule
analyze_meal_in_background, which opens its own session — the same pattern
as summaries.service.refresh_patient_summaries. An analysis failure is
logged and dropped: the meal is already saved and the patient can rerun it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.db import engine
from codebase.backend.health.models import (
    CarbLoad,
    MealAnalysis,
    MealAnalysisStatus,
    MealInputSource,
    MealRecord,
)

logger = logging.getLogger(__name__)

PATIENT_EDIT_VERSION = "patient-edit"


@lru_cache(maxsize=1)
def _graph():
    # Lazy: graph.py imports the whole respond agent.
    from codebase.models.agents.graph import build_meal_analysis_graph

    return build_meal_analysis_graph()


def get_meal_analysis(session: Session, meal_id: UUID) -> MealAnalysis | None:
    return session.exec(select(MealAnalysis).where(MealAnalysis.meal_id == meal_id)).first()


def analyses_by_meal(session: Session, meal_ids: list[UUID]) -> dict[UUID, MealAnalysis]:
    if not meal_ids:
        return {}
    rows = session.exec(select(MealAnalysis).where(col(MealAnalysis.meal_id).in_(meal_ids))).all()
    return {row.meal_id: row for row in rows}


def upsert_meal_analysis(
    session: Session,
    *,
    meal_id: UUID,
    patient_id: UUID,
    items: list[dict],
    total_carbs_g: float | None,
    total_kcal: float | None,
    carb_load: CarbLoad,
    source: MealInputSource,
    status: MealAnalysisStatus,
    model_version: str | None,
    nutrition_db_version: str,
) -> MealAnalysis:
    analysis = get_meal_analysis(session, meal_id) or MealAnalysis(
        meal_id=meal_id, patient_id=patient_id, carb_load=carb_load, source=source, status=status
    )
    analysis.items = items
    analysis.total_carbs_g = total_carbs_g
    analysis.total_kcal = total_kcal
    analysis.carb_load = carb_load
    analysis.source = source
    analysis.status = status
    analysis.model_version = model_version
    analysis.nutrition_db_version = nutrition_db_version
    analysis.updated_at = datetime.utcnow()
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


async def analyze_meal(session: Session, meal: MealRecord, *, edited_items: list[dict] | None = None) -> MealAnalysis:
    """Run the graph for one meal. With `edited_items` (the patient's own
    list) parsing is skipped and the result is stored as EDITED."""
    state: dict = {
        "meal_id": meal.id,
        "patient_id": meal.patient_id,
        "description": meal.description,
        "portion": meal.portion,
        "source": meal.input_source or MealInputSource.TEXT,
        "status": MealAnalysisStatus.DRAFT,
    }
    if edited_items is not None:
        state.update(parsed_items=edited_items, model_version=PATIENT_EDIT_VERSION, status=MealAnalysisStatus.EDITED)
    result = await _graph().ainvoke(state, config={"configurable": {"session": session}})
    analysis = session.get(MealAnalysis, result["analysis_id"])
    assert analysis is not None
    return analysis


async def analyze_meal_in_background(meal_id: UUID) -> None:
    with Session(engine) as session:
        meal = session.get(MealRecord, meal_id)
        if meal is None:
            return
        try:
            await analyze_meal(session, meal)
        except Exception:
            session.rollback()
            # The id only — the description is PHI.
            logger.exception("meal analysis failed for meal_id=%s", meal_id)


def confirm_meal_analysis(session: Session, analysis: MealAnalysis) -> MealAnalysis:
    """The patient agrees with the analysis as shown (draft or edited)."""
    analysis.status = MealAnalysisStatus.CONFIRMED
    analysis.updated_at = datetime.utcnow()
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis

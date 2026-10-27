"""Meal analysis graph nodes (Epic 1.3): parse -> lookup -> aggregate -> persist.

build_meal_analysis_graph() in graph.py wires these. Only parse_meal_items
may call a model; lookup and aggregate are deterministic, so every number in
a MealAnalysis traces back to nutrition_data/ and the thresholds in config.

carb_load bands (settings.meal_carb_low_max_g / meal_carb_high_min_g, default
<30 g low, 30-<60 g medium, >=60 g high per meal) follow the per-meal
carbohydrate range diabetes carb-counting education commonly starts from
(ADA: about 45-60 g per meal) — a working default pending the clinical
team's sign-off (open question in docs/tasks/01-meal-intelligence.md).
An item the table doesn't know leaves the total a lower bound, so the load
is UNKNOWN unless the known part alone already reaches HIGH.
"""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from codebase.backend.config import get_settings
from codebase.backend.health.models import CarbLoad, MealAnalysisStatus, MealInputSource
from codebase.models.agents.nodes.meal.parse_meal_items import parse_meal_items
from codebase.models.agents.nodes.meal.schemas import MealItemDraft
from codebase.models.agents.tools.nutrition_tools import NUTRITION_DB_VERSION, nutrition_item


class MealAnalysisState(TypedDict, total=False):
    meal_id: UUID
    patient_id: UUID
    description: str
    portion: str | None
    source: MealInputSource
    status: MealAnalysisStatus
    # Preset by a patient edit (PATCH /analysis): parsing is skipped.
    parsed_items: list[dict]
    model_version: str
    items: list[dict]
    total_carbs_g: float | None
    total_kcal: float | None
    carb_load: CarbLoad
    analysis_id: UUID


def lookup_nutrition(items: list[MealItemDraft]) -> list[dict]:
    return [nutrition_item(item.name, item.quantity, item.unit) for item in items]


def _sum(values: list[float]) -> float | None:
    return round(sum(values), 1) if values else None


def aggregate(items: list[dict], *, low_max_g: float, high_min_g: float) -> tuple[float | None, float | None, CarbLoad]:
    """(total_carbs_g, total_kcal, carb_load). Totals cover matched items
    only — with an unmatched item they are lower bounds."""
    total_carbs = _sum([i["carbs_g"] for i in items if i.get("carbs_g") is not None])
    total_kcal = _sum([i["kcal"] for i in items if i.get("kcal") is not None])
    has_unknown = any(i.get("carbs_g") is None for i in items)
    if not items or (total_carbs is None and has_unknown):
        return total_carbs, total_kcal, CarbLoad.UNKNOWN
    known = total_carbs or 0.0
    if known >= high_min_g:
        return total_carbs, total_kcal, CarbLoad.HIGH
    if has_unknown:
        return total_carbs, total_kcal, CarbLoad.UNKNOWN
    return total_carbs, total_kcal, CarbLoad.LOW if known < low_max_g else CarbLoad.MEDIUM


async def parse_meal_items_node(state: MealAnalysisState) -> dict:
    items, model_version = await parse_meal_items(state["description"], state.get("portion"))
    return {"parsed_items": [item.model_dump() for item in items], "model_version": model_version}


async def lookup_nutrition_node(state: MealAnalysisState) -> dict:
    drafts = [MealItemDraft(**item) for item in state.get("parsed_items") or []]
    return {"items": lookup_nutrition(drafts)}


async def aggregate_node(state: MealAnalysisState) -> dict:
    settings = get_settings()
    total_carbs, total_kcal, carb_load = aggregate(
        state.get("items") or [],
        low_max_g=settings.meal_carb_low_max_g,
        high_min_g=settings.meal_carb_high_min_g,
    )
    return {"total_carbs_g": total_carbs, "total_kcal": total_kcal, "carb_load": carb_load}


async def persist_analysis_node(state: MealAnalysisState, config: RunnableConfig) -> dict:
    # Imported here: the backend service imports this module's graph.
    from codebase.backend.health.meal_analysis import upsert_meal_analysis

    analysis = upsert_meal_analysis(
        config["configurable"]["session"],
        meal_id=state["meal_id"],
        patient_id=state["patient_id"],
        items=state.get("items") or [],
        total_carbs_g=state.get("total_carbs_g"),
        total_kcal=state.get("total_kcal"),
        carb_load=state["carb_load"],
        source=state.get("source") or MealInputSource.TEXT,
        status=state.get("status") or MealAnalysisStatus.DRAFT,
        model_version=state.get("model_version"),
        nutrition_db_version=NUTRITION_DB_VERSION,
    )
    return {"analysis_id": analysis.id}


def entry_step(state: MealAnalysisState) -> str:
    return "lookup_nutrition" if state.get("parsed_items") is not None else "parse_meal_items"

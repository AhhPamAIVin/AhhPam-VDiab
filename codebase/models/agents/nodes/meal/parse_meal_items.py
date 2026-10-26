"""Meal text -> items (Epic 1.3, step 1 of the meal analysis graph).

Deterministic first: split_meal_text() handles "1 bát phở bò, 1 ly trà đá"
on its own, and when every item it finds is a name the nutrition table knows,
no model is called at all. Only otherwise — abbreviations, run-on text,
names the split can't isolate — does the LLM split the text. If that call
fails or returns nothing usable, the deterministic split is kept: a model
outage degrades the analysis, it never blocks it.
"""

from __future__ import annotations

import logging

from codebase.models.agents.json_output import parse_json_object
from codebase.models.agents.nodes.meal.parsing import split_meal_text, validate_items
from codebase.models.agents.nodes.meal.schemas import MAX_MEAL_ITEMS, MealItemDraft
from codebase.models.agents.prompts.meal_parse import MEAL_PARSE_SYSTEM_PROMPT, MEAL_PARSE_USER_TEMPLATE
from codebase.models.agents.tools.nutrition_tools import lookup_food
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

NODE_NAME = "parse_meal_items"
RULE_BASED_VERSION = "rule-based"


async def parse_meal_items(description: str, portion: str | None = None) -> tuple[list[MealItemDraft], str]:
    """(items, model_version) — model_version is the LLM's model name, or
    "rule-based" when no model was used."""
    rule_items = split_meal_text(description, portion)
    if rule_items and all(lookup_food(item.name) is not None for item in rule_items):
        return rule_items, RULE_BASED_VERSION

    llm = get_llm(NODE_NAME)
    model = str(getattr(llm, "model_name", "") or "unknown")
    try:
        result = await llm.ainvoke(
            [
                {"role": "system", "content": MEAL_PARSE_SYSTEM_PROMPT.format(max_items=MAX_MEAL_ITEMS)},
                {
                    "role": "user",
                    "content": MEAL_PARSE_USER_TEMPLATE.format(description=description, portion=portion or ""),
                },
            ]
        )
    except Exception as exc:
        logger.warning("meal parse LLM call failed, keeping the rule-based split: %s", type(exc).__name__)
        return rule_items, RULE_BASED_VERSION

    parsed = parse_json_object(getattr(result, "content", None))
    items = validate_items(parsed.get("items")) if parsed is not None else []
    if not items:
        logger.warning("meal parse LLM returned no usable items, keeping the rule-based split")
        return rule_items, RULE_BASED_VERSION
    return items, model

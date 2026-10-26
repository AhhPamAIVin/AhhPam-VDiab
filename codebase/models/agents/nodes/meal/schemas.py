"""Typed shapes shared by the meal steps (Epic 1).

MealItemDraft is what an LLM step may say about a meal: a name and an
estimated portion — never a nutrition number. The nutrition lookup (Epic 1.2)
adds grams/carbs/kcal to these items deterministically.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_MEAL_ITEMS = 10


class MealItemDraft(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    quantity: float | None = Field(default=None, gt=0, le=50)
    unit: str | None = Field(default=None, min_length=1, max_length=20)


class MealPhotoRecognition(BaseModel):
    """Draft from one photo; the patient confirms or edits it before saving."""

    is_food: bool
    items: list[MealItemDraft] = Field(default_factory=list, max_length=MAX_MEAL_ITEMS)
    # Model that produced the draft, for MealAnalysis.model_version later.
    model: str

    @property
    def description(self) -> str | None:
        """Form text for MealRecord.description: "phở bò, trà đá"."""
        return ", ".join(item.name for item in self.items) or None

    @property
    def portion(self) -> str | None:
        """Form text for MealRecord.portion, in the same order as the
        description ("1 tô phở bò, 1 ly trà đá"), or None when no item has an
        estimated amount. Capped to the column's 100 characters."""
        parts = [_portion_text(item) for item in self.items]
        if not any(item.quantity for item in self.items):
            return None
        text = ", ".join(parts)
        return text if len(text) <= 100 else text[:99].rstrip(", ") + "…"


def _format_quantity(quantity: float) -> str:
    # Vietnamese decimal comma: 0.5 -> "0,5", 1.0 -> "1".
    return f"{quantity:g}".replace(".", ",")


def _portion_text(item: MealItemDraft) -> str:
    if item.quantity is None:
        return item.name
    amount = _format_quantity(item.quantity)
    return f"{amount} {item.unit} {item.name}" if item.unit else f"{amount} {item.name}"

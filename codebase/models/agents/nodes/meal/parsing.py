"""Turning text into MealItemDraft lists (Epic 1).

Two producers share these helpers:
- validate_items(): checks an LLM's item list item by item — a malformed
  item is dropped, never repaired (used by parse_meal_items and
  recognize_from_photo).
- split_meal_text(): a deterministic split of what the patient typed,
  "1 bát phở bò, 1 ly trà đá" -> two items, with no LLM. parse_meal_items
  uses it first and only calls the model when this leaves a name the
  nutrition table doesn't know.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from codebase.models.agents.nodes.meal.schemas import MAX_MEAL_ITEMS, MealItemDraft

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: object) -> str | None:
    """One line: control characters out, whitespace collapsed."""
    if not isinstance(value, str):
        return None
    return " ".join(_CONTROL_CHARS_RE.sub(" ", value).split()) or None


def validate_items(raw_items: object) -> list[MealItemDraft]:
    items: list[MealItemDraft] = []
    if not isinstance(raw_items, list):
        return items
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        quantity = raw.get("quantity")
        try:
            item = MealItemDraft(
                name=clean_text(raw.get("name")) or "",
                quantity=quantity if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) else None,
                unit=clean_text(raw.get("unit")),
            )
        except ValidationError:
            continue
        items.append(item)
        if len(items) == MAX_MEAL_ITEMS:
            break
    return items


# Household units a patient writes before a food name ("2 chén cơm").
_UNITS = (
    "bát", "chén", "tô", "đĩa", "dĩa", "cái", "chiếc", "ổ", "ly", "cốc", "tách", "muỗng", "thìa", "miếng",
    "quả", "trái", "lát", "gói", "lon", "chai", "hộp", "củ", "khúc", "cuốn", "cây", "xiên", "nắm", "suất",
    "phần", "múi", "chùm", "viên", "khoanh", "con", "bịch", "túi", "thanh", "nồi", "lạng", "kg", "gram",
    "gam", "gr", "g", "ml", "lít", "l",
)  # fmt: skip
_NUMBER_WORDS = {"một": 1.0, "hai": 2.0, "ba": 3.0, "bốn": 4.0, "năm": 5.0, "nửa": 0.5, "rưỡi": 1.5}
_UNIT_RE = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))
_AMOUNT_RE = re.compile(
    rf"^(?P<qty>\d+(?:[.,]\d+)?|{'|'.join(_NUMBER_WORDS)})\s*(?P<unit>{_UNIT_RE})(?=\s|$)\s*(?P<name>.*)$",
    re.IGNORECASE,
)
# "200g cơm" is caught above; "cơm 200g" here.
_TRAILING_AMOUNT_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_RE})$",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s*(?:[,;+\n]|\bvà\b|\bvới\b|\bkèm(?: theo)?\b|\bcùng\b)\s*", re.IGNORECASE)


def _quantity(token: str) -> float | None:
    token = token.lower()
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    try:
        return float(token.replace(",", "."))
    except ValueError:
        return None


def _parse_piece(piece: str) -> MealItemDraft | None:
    piece = piece.strip(" .")
    if not piece:
        return None
    match = _AMOUNT_RE.match(piece) or _TRAILING_AMOUNT_RE.match(piece)
    if match and match.group("name").strip():
        try:
            return MealItemDraft(
                name=match.group("name").strip(),
                quantity=_quantity(match.group("qty")),
                unit=match.group("unit").lower(),
            )
        except ValidationError:
            pass  # an implausible amount: keep the food, drop the amount
    try:
        return MealItemDraft(name=piece)
    except ValidationError:
        return None


def split_meal_text(description: str, portion: str | None = None) -> list[MealItemDraft]:
    """Deterministic items from the meal form. A portion with no food name
    ("1 tô") applies to a single-item description; one listing the same
    number of parts ("1 tô, 1 ly") applies part by part, in order."""
    pieces = [p for p in _SEPARATOR_RE.split(clean_text(description) or "") if p.strip(" .")]
    items = [item for item in (_parse_piece(p) for p in pieces) if item is not None][:MAX_MEAL_ITEMS]
    portion_text = clean_text(portion)
    if not items or not portion_text:
        return items
    portion_parts = [p for p in _SEPARATOR_RE.split(portion_text) if p.strip(" .")]
    if len(portion_parts) != len(items):
        return items
    merged = []
    for item, part in zip(items, portion_parts, strict=True):
        amount = _AMOUNT_RE.match(part.strip(" ."))
        if item.quantity is None and amount is not None:
            item = item.model_copy(update={"quantity": _quantity(amount.group("qty")), "unit": amount.group("unit").lower()})
        merged.append(item)
    return merged

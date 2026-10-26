"""Vietnamese food nutrition lookup (Epic 1.2) — deterministic, no LLM.

Data: nutrition_data/vn_foods.csv (one row per food/dish, values per 100 g of
the food as eaten, a default household unit and its weight) and
nutrition_data/vn_food_units.csv (other household units for a food). The
files live beside the code, not under data/, so they ship in the image and
are never hidden by the /app/data volume.

Provenance, per row: `gi_source` VADE = the GI band from VADE's GI table
(data/knowledge_base/raw/vade-chi-so-gi.md), BYT-5481 = the qualitative fruit
bands in QĐ 5481; `source` ESTIMATE = carbs/kcal are working estimates not
yet checked against Bảng thành phần thực phẩm Việt Nam (Viện Dinh dưỡng).
NUTRITION_DB_VERSION says so, and every analysis stores it — numbers are an
"ước tính" to the patient, never a prescription.

lookup_food() never guesses: no exact, contained or close-enough name
match means None, and the caller keeps that item's carbs unknown.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "nutrition_data"
NUTRITION_DB_VERSION = "vn_foods-2026.10-unverified"

# Confidence by how the name matched; the UI shows it, 1.3 can flag low ones.
EXACT_CONFIDENCE = 1.0
NO_DIACRITICS_CONFIDENCE = 0.95
CONTAINED_CONFIDENCE = 0.8
_FUZZY_CUTOFF = 0.85
_FUZZY_SCALE = 0.9

# Household units that mean the same thing (North/South usage), mapped to one spelling.
_UNIT_SYNONYMS = {
    "chén": "bát",
    "cốc": "ly",
    "thìa": "muỗng",
    "trái": "quả",
    "chiếc": "cái",
    "suất": "phần",
    "dĩa": "đĩa",
    "tách": "ly",
}
# Weight/volume units: grams per unit (drinks taken as 1 ml ≈ 1 g).
_MASS_UNITS = {
    "g": 1.0,
    "gr": 1.0,
    "gam": 1.0,
    "gram": 1.0,
    "kg": 1000.0,
    "lạng": 100.0,
    "ml": 1.0,
    "l": 1000.0,
    "lít": 1000.0,
}


@dataclass(frozen=True)
class Food:
    food_id: str
    name_vi: str
    aliases: tuple[str, ...]
    default_unit: str
    grams_per_unit: float
    carbs_per_100g: float
    kcal_per_100g: float
    gi_level: str | None
    gi_source: str | None
    source: str
    units: dict[str, float]


@dataclass(frozen=True)
class FoodMatch:
    food: Food
    match_confidence: float


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


_NON_WORD_RE = re.compile(r"[^\w\s]")


def _clean(text: str) -> str:
    """Lowercase, NFC, punctuation out, whitespace collapsed — diacritics kept."""
    text = unicodedata.normalize("NFC", text).lower()
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def _plain(text: str) -> str:
    return strip_diacritics(_clean(text))


def normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    cleaned = _clean(unit)
    return _UNIT_SYNONYMS.get(cleaned, cleaned) or None


def _optional(value: str) -> str | None:
    return value.strip() or None


@lru_cache(maxsize=1)
def load_foods() -> tuple[Food, ...]:
    units: dict[str, dict[str, float]] = {}
    with (DATA_DIR / "vn_food_units.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            unit = normalize_unit(row["unit"])
            if unit:
                units.setdefault(row["food_id"], {})[unit] = float(row["grams"])
    foods = []
    with (DATA_DIR / "vn_foods.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            default_unit = normalize_unit(row["default_unit"]) or "phần"
            food_units = dict(units.get(row["food_id"], {}))
            food_units[default_unit] = float(row["grams_per_unit"])
            foods.append(
                Food(
                    food_id=row["food_id"],
                    name_vi=row["name_vi"],
                    aliases=tuple(a.strip() for a in row["aliases"].split("|") if a.strip()),
                    default_unit=default_unit,
                    grams_per_unit=float(row["grams_per_unit"]),
                    carbs_per_100g=float(row["carbs_per_100g"]),
                    kcal_per_100g=float(row["kcal_per_100g"]),
                    gi_level=_optional(row["gi_level"]),
                    gi_source=_optional(row["gi_source"]),
                    source=row["source"],
                    units=food_units,
                )
            )
    return tuple(foods)


@dataclass(frozen=True)
class _Keys:
    """Every name/alias of every food under one spelling (with or without diacritics)."""

    names: dict[str, Food]
    ambiguous: frozenset[str]  # keys two foods share once diacritics are gone
    tokens: tuple[tuple[tuple[str, ...], Food], ...]  # longest name first


def _build_keys(spell) -> _Keys:
    names: dict[str, Food] = {}
    ambiguous: set[str] = set()
    for food in load_foods():
        for name in (food.name_vi, *food.aliases):
            key = spell(_clean(name))
            if key in names and names[key].food_id != food.food_id:
                ambiguous.add(key)
            names.setdefault(key, food)
    tokens = sorted(((tuple(k.split()), f) for k, f in names.items() if k not in ambiguous), key=lambda kv: -len(kv[0]))
    return _Keys(names=names, ambiguous=frozenset(ambiguous), tokens=tuple(tokens))


@lru_cache(maxsize=1)
def _accented_keys() -> _Keys:
    keys = _build_keys(lambda text: text)
    if keys.ambiguous:
        raise ValueError(f"nutrition table: names shared by two foods: {sorted(keys.ambiguous)}")
    return keys


@lru_cache(maxsize=1)
def _plain_keys() -> _Keys:
    return _build_keys(strip_diacritics)


def _contained_match(query: tuple[str, ...], keys: _Keys) -> Food | None:
    """Longest table name inside the query ("phở bò tái nạm nhiều hành" → phở
    bò). A one-word name only counts as the query's first word — the head
    noun in Vietnamese — so "nước mắm" never becomes "nước" (water)."""
    for tokens, food in keys.tokens:
        n = len(tokens)
        if n == 1:
            if query[0] == tokens[0]:
                return food
            continue
        if any(query[i : i + n] == tokens for i in range(len(query) - n + 1)):
            return food
    return None


def _fuzzy_match(key: str, keys: _Keys) -> tuple[float, Food] | None:
    best: tuple[float, Food] | None = None
    for candidate, food in keys.names.items():
        if candidate in keys.ambiguous:
            continue
        ratio = SequenceMatcher(None, key, candidate).ratio()
        if ratio >= _FUZZY_CUTOFF and (best is None or ratio > best[0]):
            best = (ratio, food)
    return best


def lookup_food(name: str) -> FoodMatch | None:
    """Best table entry for a dish/food name, or None when nothing matches
    closely enough (the caller must not guess).

    Typed with diacritics, the name is only compared with diacritics — "bò"
    must never fall through to "bơ". Typed without any ("pho bo"), it is
    compared against the table with diacritics stripped."""
    key = _clean(name)
    if not key:
        return None
    accented = _accented_keys()
    if key in accented.names:
        return FoodMatch(accented.names[key], EXACT_CONFIDENCE)
    plain_key = strip_diacritics(key)
    typed_plain = plain_key == key
    keys = _plain_keys() if typed_plain else accented
    if typed_plain and key in keys.names and key not in keys.ambiguous:
        return FoodMatch(keys.names[key], NO_DIACRITICS_CONFIDENCE)
    food = _contained_match(tuple(key.split()), keys)
    if food is not None:
        return FoodMatch(food, CONTAINED_CONFIDENCE)
    fuzzy = _fuzzy_match(key, keys)
    if fuzzy is not None:
        return FoodMatch(fuzzy[1], round(fuzzy[0] * _FUZZY_SCALE, 2))
    return None


def portion_grams(food: Food, quantity: float | None, unit: str | None) -> tuple[float, bool]:
    """(grams, estimated). `estimated` is True when the unit isn't known for
    this food and the default serving was assumed instead."""
    amount = quantity if quantity and quantity > 0 else 1.0
    normalized = normalize_unit(unit)
    if normalized is None:
        return amount * food.grams_per_unit, False
    if normalized in _MASS_UNITS:
        return amount * _MASS_UNITS[normalized], False
    if normalized in food.units:
        return amount * food.units[normalized], False
    return amount * food.grams_per_unit, True


def _round1(value: float) -> float:
    return round(value + 0.0, 1)


def nutrition_item(name: str, quantity: float | None, unit: str | None) -> dict:
    """One meal_analyses item: the parsed name/amount plus table values, or
    null nutrition fields when the food isn't in the table."""
    item = {
        "name": name,
        "matched_food_id": None,
        "matched_name": None,
        "quantity": quantity,
        "unit": unit,
        "grams": None,
        "carbs_g": None,
        "kcal": None,
        "gi_level": None,
        "match_confidence": None,
        "portion_estimated": False,
    }
    match = lookup_food(name)
    if match is None:
        return item
    food = match.food
    grams, estimated = portion_grams(food, quantity, unit)
    item.update(
        matched_food_id=food.food_id,
        matched_name=food.name_vi,
        unit=unit or food.default_unit,
        grams=_round1(grams),
        carbs_g=_round1(grams * food.carbs_per_100g / 100),
        kcal=_round1(grams * food.kcal_per_100g / 100),
        gi_level=food.gi_level,
        match_confidence=match.match_confidence,
        portion_estimated=estimated,
    )
    return item

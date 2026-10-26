"""Parsing the vision model's reply into a meal draft (Epic 1) and the
shared JSON helpers it uses."""

import json

import pytest

from codebase.models.agents.json_output import parse_json_object, strip_code_fence
from codebase.models.agents.nodes.meal.recognize_from_photo import MealPhotoRecognitionError, parse_recognition
from codebase.models.agents.nodes.meal.schemas import MAX_MEAL_ITEMS


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '```json{"a": 1}```',
        '  {"a": 1}  ',
    ],
)
def test_code_fences_are_stripped(raw):
    assert parse_json_object(raw) == {"a": 1}


def test_non_object_json_is_none():
    assert parse_json_object("[1, 2]") is None
    assert parse_json_object("không phải json") is None
    assert parse_json_object(None) is None
    assert strip_code_fence("plain") == "plain"


def _reply(**body) -> str:
    return json.dumps(body, ensure_ascii=False)


def test_draft_text_follows_item_order():
    result = parse_recognition(
        _reply(
            is_food=True,
            items=[
                {"name": "cơm tấm sườn", "quantity": 1, "unit": "đĩa"},
                {"name": "canh chua", "quantity": 0.5, "unit": "bát"},
                {"name": "dưa leo", "quantity": None, "unit": None},
            ],
        ),
        model="gpt-4o-mini",
    )
    assert result.is_food
    assert result.description == "cơm tấm sườn, canh chua, dưa leo"
    assert result.portion == "1 đĩa cơm tấm sườn, 0,5 bát canh chua, dưa leo"
    assert result.model == "gpt-4o-mini"


def test_no_amounts_means_no_portion():
    result = parse_recognition(_reply(is_food=True, items=[{"name": "bánh mì"}]), model="m")
    assert result.portion is None
    assert result.description == "bánh mì"


def test_bad_items_are_dropped_not_repaired():
    result = parse_recognition(
        _reply(
            is_food=True,
            items=[
                {"name": "", "quantity": 1},
                {"quantity": 2, "unit": "bát"},
                {"name": "chè", "quantity": True},  # bool is not an amount
                {"name": "xôi", "quantity": -1},
                {"name": "x" * 101},
                "phở",
                {"name": "chuối", "quantity": 2, "unit": "quả", "carbs_g": 50},
            ],
        ),
        model="m",
    )
    assert [(i.name, i.quantity) for i in result.items] == [("chè", None), ("chuối", 2.0)]
    # Nutrition numbers from the model are never carried through.
    assert "carbs_g" not in result.items[1].model_dump()


def test_names_are_one_clean_line():
    result = parse_recognition(_reply(is_food=True, items=[{"name": "phở\n\tbò\x00  tái"}]), model="m")
    assert result.items[0].name == "phở bò tái"


def test_item_count_is_capped():
    items = [{"name": f"món {n}"} for n in range(MAX_MEAL_ITEMS + 5)]
    result = parse_recognition(_reply(is_food=True, items=items), model="m")
    assert len(result.items) == MAX_MEAL_ITEMS


def test_food_without_any_usable_item_is_not_a_draft():
    result = parse_recognition(_reply(is_food=True, items=[{"name": ""}]), model="m")
    assert not result.is_food
    assert result.description is None


def test_not_food_ignores_items():
    result = parse_recognition(_reply(is_food=False, items=[{"name": "ghế"}]), model="m")
    assert not result.is_food
    assert result.items == []


@pytest.mark.parametrize("content", ["phở bò", _reply(items=[]), _reply(is_food="yes"), None])
def test_unusable_reply_raises(content):
    with pytest.raises(MealPhotoRecognitionError):
        parse_recognition(content, model="m")


def test_long_portion_is_capped_to_the_column():
    items = [{"name": f"món ăn số {n} rất dài", "quantity": 1, "unit": "bát"} for n in range(MAX_MEAL_ITEMS)]
    result = parse_recognition(_reply(is_food=True, items=items), model="m")
    assert result.portion is not None and len(result.portion) <= 100

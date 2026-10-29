"""Epic 1.2: Vietnamese food lookup and household-unit conversion, and the
deterministic meal-text split used before any LLM call."""

import pytest

from codebase.models.agents.nodes.meal.parsing import split_meal_text
from codebase.models.agents.tools.nutrition_tools import (
    CONTAINED_CONFIDENCE,
    EXACT_CONFIDENCE,
    NO_DIACRITICS_CONFIDENCE,
    load_foods,
    lookup_food,
    nutrition_item,
    portion_grams,
)


def test_table_size_and_integrity():
    foods = load_foods()
    assert 150 <= len(foods) <= 300
    assert len({f.food_id for f in foods}) == len(foods)
    for food in foods:
        assert 0 <= food.carbs_per_100g <= 100, food.food_id
        assert 0 <= food.kcal_per_100g <= 900, food.food_id
        assert food.grams_per_unit > 0, food.food_id
        assert food.gi_level in (None, "low", "medium", "high"), food.food_id
        # A GI band must say where it comes from.
        assert (food.gi_level is None) == (food.gi_source is None), food.food_id


@pytest.mark.parametrize(
    ("query", "food_id"),
    [
        ("phở bò", "pho_bo"),
        ("Phở Bò", "pho_bo"),
        ("phở", "pho_bo"),
        ("phở tái chín", "pho_bo"),
        ("phở gà", "pho_ga"),
        ("bún bò Huế", "bun_bo_hue"),
        ("bún chả", "bun_cha"),
        ("bún riêu cua", "bun_rieu"),
        ("cơm", "com_trang"),
        ("cơm trắng", "com_trang"),
        ("cơm gạo lứt", "com_gao_lut"),
        ("cơm tấm sườn bì chả", "com_tam_suon"),
        ("cơm gà xối mỡ", "com_ga"),
        ("cơm chiên dương châu", "com_rang"),
        ("bánh mì", "banh_mi"),
        ("bánh mì pate", "banh_mi_thit"),
        ("bánh mì ốp la", "banh_mi_trung"),
        ("xôi xéo", "xoi_xeo"),
        ("cháo gà", "chao_ga"),
        ("hủ tiếu nam vang", "hu_tieu"),
        ("mì Quảng", "mi_quang"),
        ("mì tôm", "mi_goi"),
        ("bánh cuốn", "banh_cuon"),
        ("chả giò", "nem_ran"),
        ("gỏi cuốn", "goi_cuon"),
        ("trứng ốp la", "trung_ran"),
        ("đậu hũ chiên", "dau_phu_ran"),
        ("rau muống xào tỏi", "rau_muong_xao"),
        ("canh chua cá", "canh_chua"),
        ("chuối", "chuoi"),
        ("dưa hấu", "dua_hau"),
        ("trà đá", "tra_da"),
        ("nước chè", "tra_da"),
        ("cà phê sữa đá", "ca_phe_sua"),
        ("trà sữa trân châu", "tra_sua"),
        ("coca", "nuoc_ngot"),
        ("sữa tươi không đường", "sua_tuoi_khong_duong"),
        ("chè", "che_thap_cam"),
    ],
)
def test_lookup_known_foods(query, food_id):
    match = lookup_food(query)
    assert match is not None, query
    assert match.food.food_id == food_id


@pytest.mark.parametrize(
    ("query", "food_id"),
    [
        ("pho bo", "pho_bo"),
        ("bun bo hue", "bun_bo_hue"),
        ("com tam", "com_tam_suon"),
        ("banh mi", "banh_mi"),
        ("tra da", "tra_da"),
        ("ca phe sua", "ca_phe_sua"),
        ("xoi", "xoi_trang"),
    ],
)
def test_lookup_without_diacritics(query, food_id):
    match = lookup_food(query)
    assert match is not None and match.food.food_id == food_id
    assert match.match_confidence == NO_DIACRITICS_CONFIDENCE


@pytest.mark.parametrize(
    ("query", "food_id"),
    [
        ("bm", "banh_mi"),
        ("ts", "tra_sua"),
        ("cf sữa", "ca_phe_sua"),
        # Not the plain loaf the leading "bm" alias would give (meal eval 2026-10-29).
        ("bm trứng", "banh_mi_trung"),
        ("bm thịt", "banh_mi_thit"),
        ("bm chả", "banh_mi_cha"),
    ],
)
def test_lookup_abbreviations(query, food_id):
    match = lookup_food(query)
    assert match is not None and match.food.food_id == food_id


def test_confidence_reflects_how_it_matched():
    assert lookup_food("phở bò").match_confidence == EXACT_CONFIDENCE
    assert lookup_food("phở bò tái nạm nhiều hành").match_confidence == CONTAINED_CONFIDENCE


@pytest.mark.parametrize("query", ["pizza", "sushi", "hamburger", "món lạ xyz", "bánh", "cá", "", "   ", "phỏ bò"])
def test_unknown_returns_none_not_a_guess(query):
    assert lookup_food(query) is None


def test_diacritics_are_not_dropped_to_force_a_match():
    # "bò" (beef) must never fall through to "bơ" (avocado).
    assert lookup_food("bò") is None
    assert lookup_food("bơ").food.food_id == "bo"


def test_single_word_name_only_matches_as_the_head_noun():
    # "nước mắm" is not water.
    assert lookup_food("nước mắm") is None


@pytest.mark.parametrize(
    ("query", "quantity", "unit", "grams", "estimated"),
    [
        ("cơm trắng", 2, "chén", 300, False),  # chén == bát
        ("cơm trắng", 1, "đĩa", 250, False),
        ("cơm trắng", 200, "g", 200, False),
        ("phở bò", 1, "tô", 500, False),
        ("phở bò", 1, "bát", 400, False),
        ("phở bò", None, None, 500, False),  # default serving
        ("phở bò", 1, "đĩa", 500, True),  # unknown unit for phở -> default, flagged
        ("trà đá", 1, "cốc", 250, False),  # cốc == ly
        ("bánh mì", 1, "cái", 80, False),
        ("chuối", 2, "trái", 200, False),  # trái == quả
        ("cơm trắng", 1, "lạng", 100, False),
    ],
)
def test_household_units(query, quantity, unit, grams, estimated):
    assert portion_grams(lookup_food(query).food, quantity, unit) == (grams, estimated)


def test_nutrition_item_numbers_come_from_the_table():
    item = nutrition_item("cơm trắng", 2, "chén")
    assert item["matched_food_id"] == "com_trang"
    assert item["grams"] == 300
    assert item["carbs_g"] == pytest.approx(85.8)
    assert item["gi_level"] == "high"
    assert nutrition_item("trà đá", 1, "ly")["carbs_g"] == 0


def test_nutrition_item_unknown_keeps_nulls():
    item = nutrition_item("món lạ xyz", 1, "bát")
    assert item["matched_food_id"] is None
    assert item["carbs_g"] is None and item["kcal"] is None and item["grams"] is None


@pytest.mark.parametrize(
    ("description", "portion", "expected"),
    [
        ("1 bát phở bò, 1 ly trà đá", None, [("phở bò", 1, "bát"), ("trà đá", 1, "ly")]),
        ("2 chén cơm và cá kho", None, [("cơm", 2, "chén"), ("cá kho", None, None)]),
        ("bánh mì trứng + cà phê sữa", None, [("bánh mì trứng", None, None), ("cà phê sữa", None, None)]),
        ("phở bò", "1 tô", [("phở bò", 1, "tô")]),
        ("phở bò, trà đá", "1 tô phở bò, 1 ly trà đá", [("phở bò", 1, "tô"), ("trà đá", 1, "ly")]),
        ("cơm 200g", None, [("cơm", 200, "g")]),
        ("hai quả chuối", None, [("chuối", 2, "quả")]),
        ("nửa ổ bánh mì", None, [("bánh mì", 0.5, "ổ")]),
        ("ba chỉ luộc", None, [("ba chỉ luộc", None, None)]),  # "ba" here is not a number
    ],
)
def test_split_meal_text(description, portion, expected):
    items = split_meal_text(description, portion)
    assert [(i.name, i.quantity, i.unit) for i in items] == expected

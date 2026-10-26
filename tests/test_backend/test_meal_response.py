"""Epic 1.4: meal <-> post-meal glucose pairing, grouping (n >= 3 only), the
endpoint, and how it reaches the agent (fetch_data, QA prompt, doctor summary)."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.meal_response import meal_glucose_response, pair_meals_with_glucose
from codebase.backend.health.models import GlucoseRecord, MealAnalysis, MealContext, MealType
from codebase.backend.health.service import create_meal_record
from codebase.models.agents.nodes.fetch_data import fetch_data
from codebase.models.agents.nodes.synthesize_summary import _build_meal_response_lines
from codebase.models.agents.tools.meal_tools import is_meal_question
from codebase.models.agents.tools.nutrition_tools import nutrition_item

PASSWORD = "AdminPass123"
NOW = datetime(2026, 10, 20, 12, 0)


@pytest.fixture
def patient(session: Session) -> User:
    user = User(email="p@diab.example.com", full_name="P", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _analysed_meal(session: Session, patient: User, when: datetime, foods: list[str], carb_load: str = "high"):
    meal = create_meal_record(
        session,
        patient_id=patient.id,
        meal_type=MealType.LUNCH,
        meal_time=when,
        description=", ".join(foods),
        note=None,
        created_by=patient.id,
    )
    items = [nutrition_item(name, 1, None) for name in foods]
    session.add(
        MealAnalysis(
            meal_id=meal.id,
            patient_id=patient.id,
            items=items,
            total_carbs_g=sum(i["carbs_g"] or 0 for i in items),
            carb_load=carb_load,
            source="text",
            status="draft",
        )
    )
    session.commit()
    return meal


def _glucose(session: Session, patient: User, when: datetime, value: float, context: MealContext):
    session.add(
        GlucoseRecord(
            patient_id=patient.id, value=value, measured_at=when, meal_context=context, created_by=patient.id
        )
    )
    session.commit()


def _seed_pattern(session: Session, patient: User):
    """Phở 4x, post-meal 200+ each; cơm tấm 2x (below min_n); a salad 3x, fine."""
    for day in range(4):
        t = NOW - timedelta(days=day + 1)
        _analysed_meal(session, patient, t, ["phở bò"])
        _glucose(session, patient, t - timedelta(minutes=20), 120, MealContext.BEFORE_MEAL)
        _glucose(session, patient, t + timedelta(minutes=120), 210 + day, MealContext.AFTER_MEAL)
    for day in range(2):
        t = NOW - timedelta(days=day + 1, hours=5)
        _analysed_meal(session, patient, t, ["cơm tấm sườn"])
        _glucose(session, patient, t + timedelta(minutes=110), 250, MealContext.AFTER_MEAL)
    for day in range(3):
        t = NOW - timedelta(days=day + 1, hours=-6)
        _analysed_meal(session, patient, t, ["salad"], carb_load="low")
        _glucose(session, patient, t + timedelta(minutes=120), 140, MealContext.AFTER_MEAL)


def test_pairing_picks_the_reading_nearest_two_hours(session: Session, patient: User):
    t = NOW - timedelta(days=1)
    _analysed_meal(session, patient, t, ["phở bò"])
    _glucose(session, patient, t + timedelta(minutes=65), 230, MealContext.AFTER_MEAL)
    _glucose(session, patient, t + timedelta(minutes=125), 190, MealContext.AFTER_MEAL)
    _glucose(session, patient, t - timedelta(minutes=30), 110, MealContext.BEFORE_MEAL)
    [pair] = pair_meals_with_glucose(session, patient.id, NOW - timedelta(days=7), NOW)
    assert pair["post_mg_dl"] == 190
    assert pair["pre_mg_dl"] == 110
    assert pair["delta_mg_dl"] == 80


@pytest.mark.parametrize(
    ("minutes", "context", "paired"),
    [
        (59, MealContext.AFTER_MEAL, False),
        (60, MealContext.AFTER_MEAL, True),
        (180, MealContext.AFTER_MEAL, True),
        (181, MealContext.AFTER_MEAL, False),
        (120, MealContext.RANDOM, False),  # only AFTER_MEAL readings count
    ],
)
def test_pairing_window(session: Session, patient: User, minutes, context, paired):
    t = NOW - timedelta(days=1)
    _analysed_meal(session, patient, t, ["phở bò"])
    _glucose(session, patient, t + timedelta(minutes=minutes), 200, context)
    assert bool(pair_meals_with_glucose(session, patient.id, NOW - timedelta(days=7), NOW)) is paired


def test_groups_need_three_meals_and_rank_by_over_target(session: Session, patient: User):
    _seed_pattern(session, patient)
    result = meal_glucose_response(session, patient.id, days=30, now=NOW)

    assert result["meals_paired"] == 9
    foods = {g["food_id"]: g for g in result["by_food"]}
    assert "com_tam_suon" not in foods  # only 2 meals
    assert result["by_food"][0]["food_id"] == "pho_bo"  # top of the list
    pho = foods["pho_bo"]
    assert pho["n"] == 4
    assert pho["pct_over_target"] == 100
    assert pho["avg_delta_mg_dl"] == pytest.approx(91.5)
    assert foods["salad"]["pct_over_target"] == 0
    loads = {g["carb_load"]: g for g in result["by_carb_load"]}
    assert loads["high"]["n"] == 6 and loads["low"]["n"] == 3


@pytest.mark.asyncio
async def test_endpoint_and_access(client: AsyncClient, session: Session, patient: User):
    _seed_pattern(session, patient)
    login = await client.post("/api/v1/auth/login", data={"username": patient.email, "password": PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.get("/api/v1/health/meals/glucose-response?days=90", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["min_n"] == 3

    other = User(email="o@diab.example.com", full_name="O", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(other)
    session.commit()
    login = await client.post("/api/v1/auth/login", data={"username": other.email, "password": PASSWORD})
    response = await client.get(
        f"/api/v1/health/meals/glucose-response?patient_id={patient.id}",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Món nào làm đường huyết tôi tăng?", True),
        ("ăn phở có sao không", True),
        ("bữa trưa hôm qua bao nhiêu carb", True),
        ("Đường huyết hôm nay của tôi thế nào?", False),
        ("Tôi uống thuốc metformin lúc nào?", False),
    ],
)
def test_meal_question_detection(message, expected):
    assert is_meal_question(message) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "message", "included"),
    [
        ("SUMMARY", "", True),
        ("QA", "Món nào làm đường huyết tôi tăng?", True),
        ("QA", "Huyết áp tuần này của tôi thế nào?", False),
    ],
)
async def test_fetch_data_adds_meal_response_when_relevant(session: Session, patient: User, intent, message, included):
    _seed_pattern(session, patient)
    state = {"intent": intent, "needs_health_data": True, "patient_id": patient.id, "message": message}
    result = await fetch_data(state, {"configurable": {"session": session}})
    assert ("meal_glucose_response" in result["tool_results"]) is included


def test_meal_rows_carry_the_carb_estimate(session: Session, patient: User):
    from codebase.models.agents.tools.patient_tools import get_health_records

    _analysed_meal(session, patient, NOW - timedelta(days=1), ["phở bò"])
    records = get_health_records(session, patient.id, NOW - timedelta(days=7), NOW)
    estimate = records["meals"][0]["carb_estimate"]
    assert estimate["total_carbs_g"] == pytest.approx(60.0)
    assert estimate["items"] == [{"name": "phở bò", "carbs_g": 60.0}]


def test_doctor_summary_lines_use_association_wording():
    response = {
        "days": 30,
        "meals_paired": 9,
        "post_prandial_target_mg_dl": 180.0,
        "by_food": [
            {"name": "phở bò", "n": 4, "avg_post_mg_dl": 211.5, "avg_delta_mg_dl": 91.5, "pct_over_target": 100.0},
            {"name": "salad", "n": 3, "avg_post_mg_dl": 140.0, "avg_delta_mg_dl": None, "pct_over_target": 0.0},
        ],
        "by_carb_load": [
            {"carb_load": "high", "n": 6, "avg_post_mg_dl": 224.3, "avg_delta_mg_dl": 91.5, "pct_over_target": 100.0}
        ],
    }
    lines = _build_meal_response_lines(response, "doctor")
    assert len(lines) == 2
    assert "phở bò (4 bữa" in lines[0] and "salad" not in lines[0]
    assert "thường đi kèm" in lines[0] and "không phải nguyên nhân" in lines[0]
    assert "gây ra" not in " ".join(lines)
    assert _build_meal_response_lines(response, "patient") == []

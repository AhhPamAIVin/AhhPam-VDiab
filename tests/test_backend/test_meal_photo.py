"""Epic 1: meal photo -> draft (POST /health/meals/photo-analysis), the
meal's portion/input_source fields, and the additive schema patch for
databases created before them. The photo is never stored."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, StaticPool, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.config import get_settings
from codebase.backend.health.models import MealRecord

PASSWORD = "AdminPass123"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
RECOGNIZE = "codebase.models.agents.nodes.meal.recognize_from_photo.get_llm"


def _make_user(session: Session, email: str, role: UserRole, **extra) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD), **extra)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _make_user(session, "patient@diab.example.com", UserRole.PATIENT)


@pytest.fixture
def consented_patient(session: Session) -> User:
    return _make_user(session, "patient2@diab.example.com", UserRole.PATIENT, meal_photo_ai_consent=True)


@pytest.fixture(autouse=True)
def _content_safety_llm_says_food(monkeypatch):
    response = AsyncMock()
    response.content = json.dumps({"is_food": True})
    llm = AsyncMock()
    llm.ainvoke.return_value = response
    monkeypatch.setattr("codebase.backend.health.content_safety.get_llm", lambda *_a, **_k: llm)


def _vision_llm(monkeypatch, content: str | None = None, *, error: Exception | None = None) -> MagicMock:
    """Stub the vision model; returns the get_llm mock so tests can inspect
    how it was called and what it was sent."""
    llm = AsyncMock()
    llm.model_name = "gpt-4o-mini"
    if error is not None:
        llm.ainvoke.side_effect = error
    else:
        response = MagicMock()
        response.content = content
        llm.ainvoke.return_value = response
    get_llm = MagicMock(return_value=llm)
    monkeypatch.setattr(RECOGNIZE, get_llm)
    return get_llm


PHO_REPLY = json.dumps(
    {
        "is_food": True,
        "items": [
            {"name": "phở bò", "quantity": 1, "unit": "tô"},
            {"name": "trà đá", "quantity": 1, "unit": "ly"},
        ],
    }
)


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _analyze(client: AsyncClient, headers, content: bytes = PNG, content_type: str = "image/png", **data):
    return await client.post(
        "/api/v1/health/meals/photo-analysis",
        files={"file": ("meal.png", content, content_type)},
        data=data,
        headers=headers,
    )


@pytest.mark.asyncio
async def test_no_consent_no_model_call(client: AsyncClient, patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    response = await _analyze(client, await _headers(client, patient))
    assert response.status_code == 403
    get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_consent_then_draft(client: AsyncClient, session: Session, patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    headers = await _headers(client, patient)

    consent = await client.patch("/api/v1/auth/me", json={"meal_photo_ai_consent": True}, headers=headers)
    assert consent.status_code == 200
    assert consent.json()["meal_photo_ai_consent"] is True
    assert consent.json()["ai_memory_consent"] is False  # partial update leaves the other consent alone

    response = await _analyze(client, headers, hint="phở ở quán gần nhà")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "recognized": True,
        "description": "phở bò, trà đá",
        "portion": "1 tô phở bò, 1 ly trà đá",
        "items": [
            {"name": "phở bò", "quantity": 1.0, "unit": "tô"},
            {"name": "trà đá", "quantity": 1.0, "unit": "ly"},
        ],
    }
    # A draft only: no meal saved until the patient submits the form.
    assert session.exec(select(MealRecord)).all() == []

    get_llm.assert_called_once_with("recognize_meal_photo", trace=False)
    messages = get_llm.return_value.ainvoke.call_args.args[0]
    user_parts = messages[1]["content"]
    assert any("phở ở quán gần nhà" in part.get("text", "") for part in user_parts)
    image_part = next(part for part in user_parts if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_part["image_url"]["detail"] == "low"


@pytest.mark.asyncio
async def test_not_food(client: AsyncClient, consented_patient: User, monkeypatch):
    _vision_llm(monkeypatch, json.dumps({"is_food": False, "items": [{"name": "cái ghế"}]}))
    response = await _analyze(client, await _headers(client, consented_patient))
    assert response.status_code == 200
    assert response.json() == {"recognized": False, "description": None, "portion": None, "items": []}


@pytest.mark.asyncio
async def test_non_image_bytes_rejected_before_the_model(client: AsyncClient, consented_patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    response = await _analyze(client, await _headers(client, consented_patient), b"<svg onload=alert(1)>")
    assert response.status_code == 415
    get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_photo_rejected(client: AsyncClient, consented_patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    monkeypatch.setattr(get_settings(), "meal_photo_max_bytes", 32)
    response = await _analyze(client, await _headers(client, consented_patient))
    assert response.status_code == 413
    get_llm.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Đây là phở bò", json.dumps({"items": []}), json.dumps([1, 2])])
async def test_unusable_reply_is_502(client: AsyncClient, consented_patient: User, monkeypatch, reply):
    _vision_llm(monkeypatch, reply)
    response = await _analyze(client, await _headers(client, consented_patient))
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_model_error_is_502(client: AsyncClient, consented_patient: User, monkeypatch):
    _vision_llm(monkeypatch, error=RuntimeError("upstream timeout"))
    response = await _analyze(client, await _headers(client, consented_patient))
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_doctor_cannot_send_patient_photos(client: AsyncClient, session: Session, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    doctor = _make_user(session, "doctor@diab.example.com", UserRole.DOCTOR)
    response = await _analyze(client, await _headers(client, doctor))
    assert response.status_code == 403
    get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_feature_switch_off(client: AsyncClient, consented_patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    monkeypatch.setattr(get_settings(), "meal_photo_analysis_enabled", False)
    response = await _analyze(client, await _headers(client, consented_patient))
    assert response.status_code == 503
    get_llm.assert_not_called()


async def _create_meal(client: AsyncClient, headers, patient: User, **extra):
    return await client.post(
        "/api/v1/health/meals",
        json={
            "patient_id": str(patient.id),
            "meal_type": "lunch",
            "meal_time": datetime.now(UTC).isoformat(),
            "description": "Phở bò",
            **extra,
        },
        headers=headers,
    )


@pytest.mark.asyncio
async def test_meal_saved_from_photo_draft(client: AsyncClient, patient: User):
    headers = await _headers(client, patient)
    response = await _create_meal(client, headers, patient, portion="1 tô", input_source="photo")
    assert response.status_code == 201, response.text
    assert response.json()["portion"] == "1 tô"
    assert response.json()["input_source"] == "photo"

    typed = await _create_meal(client, headers, patient)
    assert typed.json()["input_source"] == "text"
    assert typed.json()["portion"] is None


@pytest.mark.asyncio
async def test_portion_over_100_chars_rejected(client: AsyncClient, patient: User):
    response = await _create_meal(client, await _headers(client, patient), patient, portion="x" * 101)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unsafe_substance_in_portion_rejected(client: AsyncClient, patient: User):
    response = await _create_meal(
        client, await _headers(client, patient), patient, description="Nước cam", portion="1 ly kèm 2 viên MDMA"
    )
    assert response.status_code == 400


def test_old_database_gains_new_meal_columns(monkeypatch):
    """A database created before Epic 1 boots without error and gets the new
    columns and the meal_analyses table."""
    from codebase.backend import db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE meal_analyses"))
        conn.execute(text("ALTER TABLE meal_records DROP COLUMN portion"))
        conn.execute(text("ALTER TABLE meal_records DROP COLUMN input_source"))
        conn.execute(text("ALTER TABLE users DROP COLUMN meal_photo_ai_consent"))
    monkeypatch.setattr(db, "engine", engine)

    db.create_db_and_tables()

    inspector = inspect(engine)
    assert {"portion", "input_source"} <= {c["name"] for c in inspector.get_columns("meal_records")}
    assert "meal_photo_ai_consent" in {c["name"] for c in inspector.get_columns("users")}
    assert "meal_analyses" in inspector.get_table_names()


# --- the same photo sent in the chat (Epic 8.6) -------------------------------------


async def _chat_photo(client: AsyncClient, headers, content: bytes = PNG, **data):
    return await client.post(
        "/api/v1/agent/meal-photo", files={"file": ("meal.png", content, "image/png")}, data=data, headers=headers
    )


@pytest.mark.asyncio
async def test_chat_photo_becomes_a_meal_card_and_saves_as_a_photo_meal(
    client: AsyncClient, session: Session, consented_patient: User, monkeypatch
):
    _vision_llm(monkeypatch, PHO_REPLY)
    headers = await _headers(client, consented_patient)

    response = await _chat_photo(client, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    interaction = body["interactions"][0]
    assert "phở bò" in interaction["draft"]["description"] and interaction["type"] == "single_choice"

    action_id = interaction["pending_action_id"]
    for field, value in (("meal_type", "lunch"), ("meal_time", datetime.now(UTC).isoformat())):
        filled = await client.post(
            f"/api/v1/agent/record-actions/{action_id}",
            json={"action": "provide_field", "field": field, "value": value},
            headers=headers,
        )
        assert filled.status_code == 200, filled.text
    saved = await client.post(f"/api/v1/agent/record-actions/{action_id}", json={"action": "confirm"}, headers=headers)
    assert saved.status_code == 200, saved.text
    meal = session.exec(select(MealRecord)).one()
    assert meal.input_source == "photo" and "phở bò" in meal.description


@pytest.mark.asyncio
async def test_chat_photo_needs_the_same_consent(client: AsyncClient, patient: User, monkeypatch):
    get_llm = _vision_llm(monkeypatch, PHO_REPLY)
    response = await _chat_photo(client, await _headers(client, patient))
    assert response.status_code == 403
    get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_chat_photo_of_no_food_gets_no_card(client: AsyncClient, consented_patient: User, monkeypatch):
    _vision_llm(monkeypatch, json.dumps({"is_food": False, "items": [{"name": "cái ghế"}]}))
    body = (await _chat_photo(client, await _headers(client, consented_patient))).json()
    assert body["interactions"] == [] and "không nhận ra" in body["response"]

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.patients.models import PatientAssignment

DOCTOR_EMAIL = "doctor@diab.example.com"
PATIENT_EMAIL = "patient@diab.example.com"
OTHER_PATIENT_EMAIL = "patient2@diab.example.com"
ADMIN_EMAIL = "admin@diab.example.com"
PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def admin_user(session: Session) -> User:
    return _make_user(session, email=ADMIN_EMAIL, role=UserRole.ADMIN)


@pytest.fixture
def doctor_user(session: Session) -> User:
    return _make_user(session, email=DOCTOR_EMAIL, role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session) -> User:
    return _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def other_patient_user(session: Session) -> User:
    return _make_user(session, email=OTHER_PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def assigned(session: Session, doctor_user: User, patient_user: User) -> None:
    session.add(PatientAssignment(doctor_id=doctor_user.id, patient_id=patient_user.id, assigned_by=doctor_user.id))
    session.commit()


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mock_content_safety_llm(*, is_food: bool = True, is_activity: bool = True) -> AsyncMock:
    """Returns both fields regardless of which one the caller's classifier
    prompt actually asked for (llm_flags_unsafe_meal_description only reads
    is_food, llm_flags_unsafe_activity_type only reads is_activity) — one
    mock covers both create_meal's and create_activity's LLM call."""
    response = AsyncMock()
    response.content = json.dumps({"is_food": is_food, "is_activity": is_activity})
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = response
    return mock_llm


@pytest.fixture(autouse=True)
def _content_safety_llm_defaults_to_safe(monkeypatch):
    """create_meal/create_activity's second content-safety layer
    (health/content_safety.py) calls a real LLM. Stub it to default "this is
    real food"/"this is a real activity" so every other test in this file
    that logs a meal or activity doesn't require live OpenAI access; tests
    exercising the unsafe-content/failure paths override this themselves via
    monkeypatch."""
    monkeypatch.setattr("codebase.backend.health.content_safety.get_llm", lambda *_args, **_kwargs: _mock_content_safety_llm())


@pytest.mark.asyncio
async def test_patient_logs_own_glucose(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(patient_user.id),
            "value": 110.5,
            "unit": "mg/dL",
            "meal_context": "FASTING",
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["patient_id"] == str(patient_user.id)


@pytest.mark.asyncio
async def test_glucose_response_timestamps_carry_explicit_utc_marker(client: AsyncClient, patient_user: User):
    """DB values are naive-but-semantically-UTC (datetime.utcnow() defaults);
    without an explicit timezone marker in the JSON response, a browser's
    `new Date(...)` parses the string as *local* time instead of UTC and
    silently shifts every displayed time by the browser's UTC offset —
    a real bug: a 23:14 VN-local (UTC+7) entry displayed back as 16:14.
    Every `*Public` response schema's datetime fields must round-trip
    through `codebase.backend.time_utils.UtcDatetime` so the wire format is
    unambiguous regardless of which client parses it."""
    token = await _login(client, PATIENT_EMAIL)
    measured_at = datetime.now(UTC).isoformat()
    response = await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(patient_user.id),
            "value": 110.5,
            "unit": "mg/dL",
            "meal_context": "FASTING",
            "measured_at": measured_at,
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    for field in ("measured_at", "created_at"):
        raw = body[field]
        assert raw.endswith("+00:00") or raw.endswith("Z"), (
            f"{field}={raw!r} has no explicit UTC marker — a client parsing this as local time "
            "will silently shift the displayed time by its own UTC offset"
        )
    # The instant itself must be preserved exactly, not just marked as UTC.
    assert datetime.fromisoformat(body["measured_at"]) == datetime.fromisoformat(measured_at)


@pytest.mark.asyncio
async def test_patient_cannot_log_glucose_for_another_patient(
    client: AsyncClient, patient_user: User, other_patient_user: User
):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(other_patient_user.id),
            "value": 110.5,
            "unit": "mg/dL",
            "meal_context": "FASTING",
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_patient_logs_own_blood_pressure(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/blood-pressure",
        json={
            "patient_id": str(patient_user.id),
            "systolic": 120,
            "diastolic": 80,
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["patient_id"] == str(patient_user.id)
    assert body["systolic"] == 120
    assert body["diastolic"] == 80
    assert body["unit"] == "mmHg"


@pytest.mark.asyncio
async def test_blood_pressure_is_independent_of_glucose(client: AsyncClient, patient_user: User):
    """A patient without a combo device can submit blood pressure without a glucose reading."""
    token = await _login(client, PATIENT_EMAIL)
    headers = _auth_headers(token)
    response = await client.post(
        "/api/v1/health/blood-pressure",
        json={
            "patient_id": str(patient_user.id),
            "systolic": 118,
            "diastolic": 76,
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    glucose_response = await client.get("/api/v1/health/glucose", headers=headers)
    assert glucose_response.status_code == 200
    assert glucose_response.json() == []

    bp_response = await client.get("/api/v1/health/blood-pressure", headers=headers)
    assert bp_response.status_code == 200
    assert len(bp_response.json()) == 1


@pytest.mark.asyncio
async def test_blood_pressure_rejects_diastolic_not_lower_than_systolic(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/blood-pressure",
        json={
            "patient_id": str(patient_user.id),
            "systolic": 80,
            "diastolic": 90,
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_patient_cannot_log_blood_pressure_for_another_patient(
    client: AsyncClient, patient_user: User, other_patient_user: User
):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/blood-pressure",
        json={
            "patient_id": str(other_patient_user.id),
            "systolic": 120,
            "diastolic": 80,
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_doctor_logs_meal_for_assigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/health/meals",
        json={
            "patient_id": str(patient_user.id),
            "meal_type": "lunch",
            "meal_time": datetime.now(UTC).isoformat(),
            "description": "Rice and grilled chicken",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text


async def _post_meal(client: AsyncClient, token: str, patient_user: User, description: str):
    return await client.post(
        "/api/v1/health/meals",
        json={
            "patient_id": str(patient_user.id),
            "meal_type": "lunch",
            "meal_time": datetime.now(UTC).isoformat(),
            "description": description,
        },
        headers=_auth_headers(token),
    )


@pytest.mark.asyncio
async def test_meal_description_rejects_unsafe_substance_via_denylist(client: AsyncClient, patient_user: User):
    """Literal denylist layer (content_safety.find_unsafe_substance_term) —
    catches the exact wording without needing the LLM call at all; the
    autouse fixture's "is_food: true" stub is irrelevant here since the
    denylist short-circuits before the LLM layer runs."""
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_meal(client, token, patient_user, "300g heroin")
    assert response.status_code == 400, response.text
    assert "không giống một món ăn" in response.json()["detail"]


@pytest.mark.asyncio
async def test_meal_description_rejects_unsafe_substance_case_insensitive(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_meal(client, token, patient_user, "Ăn 2 viên MDMA với nước cam")
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_meal_description_rejected_by_llm_when_denylist_misses_it(client: AsyncClient, patient_user: User, monkeypatch):
    """A description naming no denylist term at all (no drug word, no
    substance name) still gets caught by the semantic LLM layer — this is
    the actual point of adding that layer: a fixed keyword list is trivially
    evaded by rewording, the LLM judgment isn't."""
    monkeypatch.setattr(
        "codebase.backend.health.content_safety.get_llm", lambda *_args, **_kwargs: _mock_content_safety_llm(is_food=False)
    )
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_meal(client, token, patient_user, "Một gói bột trắng không rõ nguồn gốc, không phải đồ ăn")
    assert response.status_code == 400, response.text
    assert "không giống một món ăn" in response.json()["detail"]


@pytest.mark.asyncio
async def test_meal_description_llm_failure_fails_open(client: AsyncClient, patient_user: User, monkeypatch):
    """An LLM outage must not block patients from logging a real meal — the
    submission still succeeds on a clean description even when the LLM call
    itself raises."""

    def _raise():
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr("codebase.backend.health.content_safety.get_llm", _raise)
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_meal(client, token, patient_user, "Rice and grilled chicken")
    assert response.status_code == 201, response.text


async def _post_activity(client: AsyncClient, token: str, patient_user: User, activity_type: str):
    return await client.post(
        "/api/v1/health/activities",
        json={
            "patient_id": str(patient_user.id),
            "activity_type": activity_type,
            "duration_minutes": 30,
            "activity_time": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )


@pytest.mark.asyncio
async def test_activity_type_rejects_unsafe_substance_via_denylist(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_activity(client, token, patient_user, "hút heroin")
    assert response.status_code == 400, response.text
    assert "không giống một hoạt động thể chất" in response.json()["detail"]


@pytest.mark.asyncio
async def test_activity_type_rejected_by_llm_when_denylist_misses_it(
    client: AsyncClient, patient_user: User, monkeypatch
):
    """Same point as the meal-description counterpart: an activity naming no
    denylist term at all still needs the semantic layer, not just the fixed
    keyword list."""
    monkeypatch.setattr(
        "codebase.backend.health.content_safety.get_llm", lambda *_args, **_kwargs: _mock_content_safety_llm(is_activity=False)
    )
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_activity(client, token, patient_user, "Hít một gói bột trắng không rõ nguồn gốc")
    assert response.status_code == 400, response.text
    assert "không giống một hoạt động thể chất" in response.json()["detail"]


@pytest.mark.asyncio
async def test_activity_type_llm_failure_fails_open(client: AsyncClient, patient_user: User, monkeypatch):
    """An LLM outage must not block patients from logging a real activity."""

    def _raise():
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr("codebase.backend.health.content_safety.get_llm", _raise)
    token = await _login(client, PATIENT_EMAIL)
    response = await _post_activity(client, token, patient_user, "Đi bộ buổi sáng")
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_doctor_cannot_log_activity_for_unassigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/health/activities",
        json={
            "patient_id": str(patient_user.id),
            "activity_type": "walking",
            "duration_minutes": 30,
            "activity_time": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_create_health_records(client: AsyncClient, admin_user: User, patient_user: User):
    token = await _login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/health/activities",
        json={
            "patient_id": str(patient_user.id),
            "activity_type": "walking",
            "duration_minutes": 30,
            "activity_time": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_any_patient_records(client: AsyncClient, admin_user: User, patient_user: User):
    patient_token = await _login(client, PATIENT_EMAIL)
    await client.post(
        "/api/v1/health/activities",
        json={
            "patient_id": str(patient_user.id),
            "activity_type": "walking",
            "duration_minutes": 30,
            "activity_time": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(patient_token),
    )

    admin_token = await _login(client, ADMIN_EMAIL)
    response = await client.get(
        f"/api/v1/health/activities?patient_id={patient_user.id}", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_doctor_without_patient_id_requires_it(client: AsyncClient, doctor_user: User):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.get("/api/v1/health/glucose", headers=_auth_headers(token))
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_patient_lists_own_glucose_without_patient_id(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(patient_user.id),
            "value": 95,
            "unit": "mg/dL",
            "meal_context": "FASTING",
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    response = await client.get("/api/v1/health/glucose", headers=_auth_headers(token))
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_glucose_date_range_filters_out_of_window_records(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    headers = _auth_headers(token)
    old_reading = datetime(2026, 1, 1, tzinfo=UTC)
    recent_reading = datetime(2026, 6, 1, tzinfo=UTC)
    for measured_at in (old_reading, recent_reading):
        await client.post(
            "/api/v1/health/glucose",
            json={
                "patient_id": str(patient_user.id),
                "value": 100,
                "unit": "mg/dL",
                "meal_context": "FASTING",
                "measured_at": measured_at.isoformat(),
            },
            headers=headers,
        )

    response = await client.get(
        "/api/v1/health/glucose",
        params={"start_date": "2026-05-01T00:00:00Z", "end_date": "2026-07-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 1
    assert records[0]["measured_at"].startswith("2026-06-01")


@pytest.mark.asyncio
async def test_glucose_invalid_date_range_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.get(
        "/api/v1/health/glucose",
        params={"start_date": "2026-07-01T00:00:00Z", "end_date": "2026-05-01T00:00:00Z"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_glucose_implausible_past_date_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.get(
        "/api/v1/health/glucose",
        params={"start_date": "1990-01-01T00:00:00Z"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_glucose_implausible_future_date_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.get(
        "/api/v1/health/glucose",
        params={"end_date": "2126-01-01T00:00:00Z"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Input validation & data quality (golden dataset TC-VAL-003/006/007/008/009/010)
# ---------------------------------------------------------------------------


def _glucose_payload(patient_id: UUID, **overrides: object) -> dict:
    payload: dict = {
        "patient_id": str(patient_id),
        "value": 115.0,
        "unit": "mg/dL",
        "meal_context": "FASTING",
        "measured_at": datetime.now(UTC).isoformat(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_glucose_future_measured_at_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    future = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    response = await client.post(
        "/api/v1/health/glucose",
        json=_glucose_payload(patient_user.id, measured_at=future),
        headers=_auth_headers(token),
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_glucose_mmol_l_value_over_max_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/glucose",
        json=_glucose_payload(patient_user.id, value=90.0, unit="mmol/L"),
        headers=_auth_headers(token),
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_glucose_missing_unit_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    payload = _glucose_payload(patient_user.id)
    del payload["unit"]
    response = await client.post("/api/v1/health/glucose", json=payload, headers=_auth_headers(token))
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_glucose_unsupported_unit_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.post(
        "/api/v1/health/glucose",
        json=_glucose_payload(patient_user.id, unit="g/L"),
        headers=_auth_headers(token),
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_glucose_missing_meal_context_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    payload = _glucose_payload(patient_user.id)
    del payload["meal_context"]
    response = await client.post("/api/v1/health/glucose", json=payload, headers=_auth_headers(token))
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_glucose_duplicate_measured_at_rejected(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    headers = _auth_headers(token)
    payload = _glucose_payload(patient_user.id)
    first = await client.post("/api/v1/health/glucose", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    duplicate = await client.post("/api/v1/health/glucose", json=payload, headers=headers)
    assert duplicate.status_code == 409, duplicate.text

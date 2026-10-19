"""Tests for GET /api/v1/agent/summary/key-metrics (agent/summary_metrics.py).

This endpoint is a display-only overlay beside the AI Summary agent — it
never invokes an LLM, so unlike test_agent_summary.py these tests need no
mock_llm fixture at all. Reuses the same seeding helpers as
test_agent_summary.py (kept local here since that module's helpers are not
exported for reuse) so the two numeric fixtures stay in sync in spirit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import MealContext
from codebase.backend.health.service import create_glucose_record
from codebase.backend.patients.models import PatientAssignment

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_weekday_weekend_pattern(session: Session, *, patient_id, creator_id, num_days: int) -> None:
    """Same shape as test_agent_summary.py's fixture of the same name: weekend
    post-dinner glucose runs over the 180 mg/dL target, weekday doesn't; any 7
    (or 14) consecutive days contain exactly 2 (or 4) weekend days, so the
    resulting percentages are deterministic regardless of which day "today" is.
    """
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for i in range(num_days):
        day_ts = now - timedelta(days=i)
        is_weekend = day_ts.weekday() >= 5
        fasting_value = 140.0 if is_weekend else 128.0  # 140 is outside the 80-130 target, 128 is inside
        after_dinner_value = 208.0 if is_weekend else 165.0  # 208 is over the 180 mg/dL target, 165 is under

        create_glucose_record(
            session,
            patient_id=patient_id,
            value=fasting_value,
            unit="mg/dL",
            measured_at=day_ts - timedelta(minutes=4),
            note="Đo lúc đói",
            created_by=creator_id,
            meal_context=MealContext.FASTING,
        )
        create_glucose_record(
            session,
            patient_id=patient_id,
            value=after_dinner_value,
            unit="mg/dL",
            measured_at=day_ts - timedelta(minutes=1),
            note="Sau bữa tối",
            created_by=creator_id,
            meal_context=MealContext.AFTER_MEAL,
        )


@pytest.mark.asyncio
async def test_key_metrics_for_patient_with_data(client: AsyncClient, session: Session):
    patient = _make_user(session, email="keymetrics-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_weekday_weekend_pattern(session, patient_id=patient.id, creator_id=patient.id, num_days=7)

    token = await _login(client, patient.email)
    response = await client.get(
        "/api/v1/agent/summary/key-metrics", params={"days": 7}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text
    tiles = {tile["label"]: tile for tile in response.json()["key_metrics"]}

    assert "Đường huyết trung bình" in tiles
    assert tiles["Đường huyết trung bình"]["unit"] == "mg/dL"
    assert tiles["Đường huyết trung bình"]["tone"] == "good"  # no blended-average target exists — always informational

    # 2 of 7 days are weekend (over the 180 mg/dL post-meal target) — 2/7 ≈ 28.6%, under the 50% critical cutoff.
    assert tiles["Sau ăn vượt ngưỡng"]["unit"] == "%"
    assert tiles["Sau ăn vượt ngưỡng"]["tone"] == "warning"

    # 5 of 7 days are weekday (fasting value 128, inside 80-130) — 5/7 ≈ 71.4%, in the 50-79 warning band.
    assert tiles["Lúc đói trong mục tiêu"]["unit"] == "%"
    assert tiles["Lúc đói trong mục tiêu"]["tone"] == "warning"


@pytest.mark.asyncio
async def test_key_metrics_zero_records_is_empty_not_an_error(client: AsyncClient, session: Session):
    patient = _make_user(session, email="keymetrics-empty@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.get(
        "/api/v1/agent/summary/key-metrics", params={"days": 7}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["key_metrics"] == []


@pytest.mark.asyncio
async def test_key_metrics_doctor_for_assigned_patient(client: AsyncClient, session: Session):
    doctor = _make_user(session, email="keymetrics-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="keymetrics-doc-patient@diab.example.com", role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    _seed_weekday_weekend_pattern(session, patient_id=patient.id, creator_id=doctor.id, num_days=14)

    token = await _login(client, doctor.email)
    response = await client.get(
        "/api/v1/agent/summary/key-metrics",
        params={"patient_id": str(patient.id), "days": 30},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["key_metrics"]) == 3


@pytest.mark.asyncio
async def test_glucose_trend_detects_rising_average(client: AsyncClient, session: Session):
    patient = _make_user(session, email="trend-up-patient@diab.example.com", role=UserRole.PATIENT)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    # 6-day window, midpoint = now - 3d. Offsets 5/4 fall before the midpoint
    # (first half), offsets 2/1 fall after it (second half) — 3 and 0 are
    # deliberately skipped to stay clear of the boundary.
    for offset, value in [(5, 140.0), (4, 140.0), (2, 190.0), (1, 190.0)]:
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=value,
            unit="mg/dL",
            measured_at=now - timedelta(days=offset),
            note=None,
            created_by=patient.id,
            meal_context=MealContext.RANDOM,
        )

    token = await _login(client, patient.email)
    response = await client.get(
        "/api/v1/agent/summary/key-metrics", params={"days": 6}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text
    trend = response.json()["glucose_trend"]
    assert trend["direction"] == "up"
    assert trend["first_half_avg"] == 140.0
    assert trend["second_half_avg"] == 190.0
    assert trend["delta"] == 50.0


@pytest.mark.asyncio
async def test_glucose_trend_small_gap_reads_as_stable(client: AsyncClient, session: Session):
    patient = _make_user(session, email="trend-stable-patient@diab.example.com", role=UserRole.PATIENT)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for offset, value in [(5, 140.0), (4, 140.0), (2, 145.0), (1, 145.0)]:
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=value,
            unit="mg/dL",
            measured_at=now - timedelta(days=offset),
            note=None,
            created_by=patient.id,
            meal_context=MealContext.RANDOM,
        )

    token = await _login(client, patient.email)
    response = await client.get(
        "/api/v1/agent/summary/key-metrics", params={"days": 6}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["glucose_trend"]["direction"] == "stable"


@pytest.mark.asyncio
async def test_glucose_trend_none_without_data_in_both_halves(client: AsyncClient, session: Session):
    patient = _make_user(session, email="trend-empty-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.get(
        "/api/v1/agent/summary/key-metrics", params={"days": 7}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["glucose_trend"] is None


@pytest.mark.asyncio
async def test_key_metrics_unassigned_doctor_forbidden(client: AsyncClient, session: Session):
    doctor = _make_user(session, email="keymetrics-unassigned@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="keymetrics-unassigned-patient@diab.example.com", role=UserRole.PATIENT)

    token = await _login(client, doctor.email)
    response = await client.get(
        "/api/v1/agent/summary/key-metrics",
        params={"patient_id": str(patient.id), "days": 7},
        headers=_auth_headers(token),
    )

    # Same permission rules as /agent/summary, reused (not duplicated) from agent/router.py.
    assert response.status_code == 403, response.text
    assert "quyền" in response.json()["detail"]

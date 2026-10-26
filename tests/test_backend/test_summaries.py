"""Tests for codebase/backend/summaries/service.py and the AI Summary
caching behavior it gives POST /api/v1/agent/summary (see that module's
docstring and docs/AI_Summary_Respond_System_Design.md §10).

The LLM is always mocked, same convention test_agent_summary.py already
established (monkeypatching codebase.models.agents.nodes.synthesize_summary.
get_llm) — generate_and_store_snapshot() calls that same node function
directly, so the mocking point is identical.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import MealContext
from codebase.backend.health.service import create_glucose_record
from codebase.backend.patients.models import PatientAssignment
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole
from codebase.backend.summaries.service import (
    ensure_snapshot,
    generate_and_store_snapshot,
    get_latest_snapshot,
)
from codebase.models.agents.nodes import synthesize_summary as synthesize_summary_mod

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


def _seed_enough_glucose_for_trends(session: Session, *, patient_id, creator_id) -> None:
    """MIN_GLUCOSE_READINGS_FOR_TRENDS (check_data_quality.py) is 4 — enough
    readings that synthesize_summary.py's sufficient_for_trends is True and
    it actually calls the (mocked) LLM, rather than taking the deterministic
    zero-LLM-call insufficient-data path."""
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for i in range(5):
        create_glucose_record(
            session,
            patient_id=patient_id,
            value=120.0 + i,
            unit="mg/dL",
            measured_at=now - timedelta(hours=i + 1),
            note=None,
            created_by=creator_id,
            meal_context=MealContext.FASTING,
        )


def _fake_llm(monkeypatch, *, on_call=None):
    class _FakeLLM:
        async def ainvoke(self, messages):
            if on_call is not None:
                on_call()
            return type("R", (), {"content": "Bản tóm tắt."})()

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda *_args, **_kwargs: _FakeLLM())


# ---------------------------------------------------------------------------
# Service-level: generation, upsert-in-place, cache-hit, staleness.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_store_snapshot_upserts_same_day_row(session: Session, monkeypatch):
    _fake_llm(monkeypatch)
    patient = _make_user(session, email="upsert-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=patient.id)

    first = await generate_and_store_snapshot(session, patient_id=patient.id, role="patient", period_days=7)
    second = await generate_and_store_snapshot(session, patient_id=patient.id, role="patient", period_days=7)

    assert first.id == second.id
    rows = session.exec(select(AgentSummarySnapshot).where(AgentSummarySnapshot.patient_id == patient.id)).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ensure_snapshot_same_day_is_cache_hit_no_second_llm_call(session: Session, monkeypatch):
    calls = {"n": 0}
    _fake_llm(monkeypatch, on_call=lambda: calls.__setitem__("n", calls["n"] + 1))
    patient = _make_user(session, email="cache-hit-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=patient.id)

    first = await ensure_snapshot(session, patient_id=patient.id, role="patient", period_days=7)
    second = await ensure_snapshot(session, patient_id=patient.id, role="patient", period_days=7)

    assert first.id == second.id
    assert first.summary == second.summary
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_ensure_snapshot_regenerates_when_stale_from_a_previous_day(session: Session, monkeypatch):
    _fake_llm(monkeypatch)
    patient = _make_user(session, email="stale-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=patient.id)

    yesterday = datetime.utcnow() - timedelta(days=1)
    stale = AgentSummarySnapshot(
        patient_id=patient.id,
        role=SummaryRole.PATIENT,
        period_days=7,
        period_start=yesterday - timedelta(days=7),
        period_end=yesterday,
        summary="Bản tóm tắt của hôm qua.",
    )
    session.add(stale)
    session.commit()

    result = await ensure_snapshot(session, patient_id=patient.id, role="patient", period_days=7)

    assert result.summary != "Bản tóm tắt của hôm qua."
    assert result.period_end.date() == datetime.utcnow().date()
    # A new calendar day inserts a fresh row rather than overwriting
    # yesterday's — that's what gives the historical trail its rows (see
    # _find_snapshot_for_day's docstring). Both must exist afterward.
    rows = session.exec(
        select(AgentSummarySnapshot).where(
            AgentSummarySnapshot.patient_id == patient.id, AgentSummarySnapshot.period_days == 7
        )
    ).all()
    assert len(rows) == 2
    assert any(row.summary == "Bản tóm tắt của hôm qua." for row in rows)


@pytest.mark.asyncio
async def test_snapshot_cache_key_includes_role(session: Session, monkeypatch):
    _fake_llm(monkeypatch)
    patient = _make_user(session, email="role-key-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=patient.id)

    patient_view = await ensure_snapshot(session, patient_id=patient.id, role="patient", period_days=7)
    doctor_view = await ensure_snapshot(session, patient_id=patient.id, role="doctor", period_days=7)

    assert patient_view.id != doctor_view.id
    assert get_latest_snapshot(session, patient_id=patient.id, role="doctor", period_days=7) is not None


@pytest.mark.asyncio
async def test_generate_and_store_snapshot_honors_explicit_historical_period_end(session: Session, monkeypatch):
    """The backfill script's use case: an explicit period_end in the past
    must fetch that historical window (via date_range_override), not a
    trailing window ending "now"."""
    _fake_llm(monkeypatch)
    patient = _make_user(session, email="historical-patient@diab.example.com", role=UserRole.PATIENT)
    old_day = datetime.utcnow() - timedelta(days=40)
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=125.0,
        unit="mg/dL",
        measured_at=old_day,
        note=None,
        created_by=patient.id,
        meal_context=MealContext.FASTING,
    )

    period_end = old_day + timedelta(days=1)
    snapshot = await generate_and_store_snapshot(
        session, patient_id=patient.id, role="patient", period_days=7, period_end=period_end
    )

    assert snapshot.period_end.date() == period_end.date()
    assert snapshot.data_completeness["glucose"]["found"] == 1


# ---------------------------------------------------------------------------
# End-to-end: POST /api/v1/agent/summary is a cache read on the 2nd call.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_endpoint_second_call_same_day_is_cache_hit(client: AsyncClient, session: Session, monkeypatch):
    calls = {"n": 0}
    _fake_llm(monkeypatch, on_call=lambda: calls.__setitem__("n", calls["n"] + 1))
    patient = _make_user(session, email="endpoint-cache-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=patient.id)
    token = await _login(client, patient.email)

    first = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=_auth_headers(token))
    second = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=_auth_headers(token))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["summary"] == second.json()["summary"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_summary_doctor_missing_patient_id_is_400(client: AsyncClient, session: Session):
    doctor = _make_user(session, email="no-patient-id-doctor@diab.example.com", role=UserRole.DOCTOR)
    token = await _login(client, doctor.email)

    response = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=_auth_headers(token))

    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_summary_unassigned_doctor_is_403(client: AsyncClient, session: Session):
    doctor = _make_user(session, email="unassigned-summary-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="unassigned-summary-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, doctor.email)

    response = await client.post(
        "/api/v1/agent/summary", json={"patient_id": str(patient.id), "days": 7}, headers=_auth_headers(token)
    )

    assert response.status_code == 403, response.text
    assert "quyền" in response.json()["detail"]


@pytest.mark.asyncio
async def test_summary_assigned_doctor_can_read(client: AsyncClient, session: Session, monkeypatch):
    _fake_llm(monkeypatch)
    doctor = _make_user(session, email="assigned-summary-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="assigned-summary-patient@diab.example.com", role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    _seed_enough_glucose_for_trends(session, patient_id=patient.id, creator_id=doctor.id)
    token = await _login(client, doctor.email)

    response = await client.post(
        "/api/v1/agent/summary", json={"patient_id": str(patient.id), "days": 7}, headers=_auth_headers(token)
    )

    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Write-path trigger — health/router.py schedules the background refresh.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_glucose_write_schedules_background_summary_refresh(client: AsyncClient, session: Session, monkeypatch):
    """conftest.py's autouse _no_background_summary_refresh fixture no-ops
    this by default (see its docstring) — this test overrides it with a spy
    to assert the write path actually schedules the refresh, without letting
    it really run against the module-level engine/real LLM."""
    calls: list[str] = []

    async def _spy(patient_id) -> None:
        calls.append(str(patient_id))

    monkeypatch.setattr("codebase.backend.health.router.refresh_patient_summaries", _spy)

    patient = _make_user(session, email="trigger-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(patient.id),
            "value": 120.0,
            "unit": "mg/dL",
            "measured_at": datetime.utcnow().isoformat(),
            "note": None,
            "meal_context": "FASTING",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 201, response.text
    assert calls == [str(patient.id)]

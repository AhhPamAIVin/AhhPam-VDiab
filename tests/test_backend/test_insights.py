"""Tests for the doctor-facing insights endpoint (codebase/backend/insights/
router.py) and AgentSummaryResponse.relevant_insights population
(codebase/backend/agent/router.py) — the two read-side surfaces from Phase 6
of the memory plan (/Users/phamminh/.claude/plans/warm-crafting-muffin.md
§B/§C). Inserts PatientInsight rows directly rather than going through the
LLM-calling reflect pipeline (already covered by tests/test_models/
test_reflect.py) — this file is about the read/access-control layer on top.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.insights.models import InsightConfidence, InsightStatus, PatientInsight
from codebase.backend.patients.models import PatientAssignment

PASSWORD = "AdminPass123"
DOCTOR_EMAIL = "insights-doctor@diab.example.com"
OTHER_DOCTOR_EMAIL = "insights-other-doctor@diab.example.com"
PATIENT_EMAIL = "insights-patient@diab.example.com"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def doctor_user(session: Session) -> User:
    return _make_user(session, email=DOCTOR_EMAIL, role=UserRole.DOCTOR)


@pytest.fixture
def other_doctor_user(session: Session) -> User:
    return _make_user(session, email=OTHER_DOCTOR_EMAIL, role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session, doctor_user: User) -> User:
    patient = _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor_user.id, patient_id=patient.id, assigned_by=doctor_user.id))
    session.commit()
    return patient


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_insight(
    session: Session, *, patient_id, source_kind: str, text: str, category: str = "glucose_pattern"
) -> PatientInsight:
    now = datetime.now(UTC).replace(tzinfo=None)
    insight = PatientInsight(
        patient_id=patient_id,
        version=1,
        category=category,
        text=text,
        evidence={},
        confidence=InsightConfidence.MEDIUM,
        status=InsightStatus.ACTIVE,
        source_kind=source_kind,
        first_observed_at=now,
        last_confirmed_at=now,
    )
    session.add(insight)
    session.commit()
    session.refresh(insight)
    return insight


@pytest.mark.asyncio
async def test_assigned_doctor_sees_both_streams_labeled(
    client: AsyncClient, session: Session, doctor_user: User, patient_user: User
):
    _add_insight(session, patient_id=patient_user.id, source_kind="appointment", text="Doctor-confirmed pattern")
    _add_insight(session, patient_id=patient_user.id, source_kind="weekly_batch", text="AI-detected pattern")

    token = await _login(client, DOCTOR_EMAIL)
    response = await client.get(f"/api/v1/doctor/patients/{patient_user.id}/insights", headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 2
    by_text = {row["text"]: row for row in body}
    assert by_text["Doctor-confirmed pattern"]["reviewed"] is True
    assert by_text["Doctor-confirmed pattern"]["source_kind"] == "appointment"
    assert by_text["AI-detected pattern"]["reviewed"] is False
    assert by_text["AI-detected pattern"]["source_kind"] == "weekly_batch"


@pytest.mark.asyncio
async def test_doctor_view_excludes_chat_derived_insights(
    client: AsyncClient, session: Session, doctor_user: User, patient_user: User
):
    """Plan Part 3 of memoized-churning-patterson.md: source_kind="chat" is
    mined from a patient's own consented chat sessions and is patient-facing
    only — it must never reach a doctor's view, even an assigned one, unlike
    "appointment"/"weekly_batch" which both show up in
    test_assigned_doctor_sees_both_streams_labeled above."""
    _add_insight(session, patient_id=patient_user.id, source_kind="appointment", text="Doctor-confirmed pattern")
    _add_insight(session, patient_id=patient_user.id, source_kind="weekly_batch", text="AI-detected pattern")
    _add_insight(session, patient_id=patient_user.id, source_kind="chat", text="Mined from chat, patient-only")

    token = await _login(client, DOCTOR_EMAIL)
    response = await client.get(f"/api/v1/doctor/patients/{patient_user.id}/insights", headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 2
    assert {row["source_kind"] for row in body} == {"appointment", "weekly_batch"}
    assert "Mined from chat, patient-only" not in {row["text"] for row in body}


@pytest.mark.asyncio
async def test_unassigned_doctor_gets_403(
    client: AsyncClient, session: Session, other_doctor_user: User, patient_user: User
):
    _add_insight(session, patient_id=patient_user.id, source_kind="weekly_batch", text="Should not be visible")

    token = await _login(client, OTHER_DOCTOR_EMAIL)
    response = await client.get(f"/api/v1/doctor/patients/{patient_user.id}/insights", headers=_auth_headers(token))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_superseded_insight_excluded_by_default(
    client: AsyncClient, session: Session, doctor_user: User, patient_user: User
):
    superseded = _add_insight(session, patient_id=patient_user.id, source_kind="appointment", text="Old version")
    superseded.status = InsightStatus.SUPERSEDED
    session.add(superseded)
    session.commit()
    _add_insight(session, patient_id=patient_user.id, source_kind="appointment", text="Current version")

    token = await _login(client, DOCTOR_EMAIL)
    response = await client.get(f"/api/v1/doctor/patients/{patient_user.id}/insights", headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["text"] == "Current version"


@pytest.mark.asyncio
async def test_patient_summary_surfaces_own_patient_facing_insights_only(
    client: AsyncClient, session: Session, patient_user: User
):
    """Plan §C (extended by Part 3 of memoized-churning-patterson.md):
    patient's own AgentSummaryResponse.relevant_insights includes BOTH
    patient-facing streams — weekly_batch and chat — but never
    appointment (doctor-facing, possibly not yet discussed with the
    patient)."""
    _add_insight(session, patient_id=patient_user.id, source_kind="weekly_batch", text="Patient-facing pattern")
    _add_insight(session, patient_id=patient_user.id, source_kind="chat", text="Mined from patient's own chat")
    _add_insight(session, patient_id=patient_user.id, source_kind="appointment", text="Doctor-only pattern")

    token = await _login(client, PATIENT_EMAIL)
    fake_llm = AsyncMock()
    fake_response = AsyncMock()
    fake_response.content = "Tóm tắt sức khỏe của bạn."
    fake_llm.ainvoke.return_value = fake_response

    with patch("codebase.models.agents.nodes.synthesize_summary.get_llm", return_value=fake_llm):
        response = await client.post("/api/v1/agent/summary", json={}, headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["relevant_insights"]) == {"Patient-facing pattern", "Mined from patient's own chat"}


@pytest.mark.asyncio
async def test_doctor_summary_never_gets_relevant_insights(
    client: AsyncClient, session: Session, doctor_user: User, patient_user: User
):
    _add_insight(session, patient_id=patient_user.id, source_kind="weekly_batch", text="Should not appear for doctor")

    token = await _login(client, DOCTOR_EMAIL)
    fake_llm = AsyncMock()
    fake_response = AsyncMock()
    fake_response.content = "Tóm tắt sức khỏe bệnh nhân."
    fake_llm.ainvoke.return_value = fake_response

    with patch("codebase.models.agents.nodes.synthesize_summary.get_llm", return_value=fake_llm):
        response = await client.post(
            "/api/v1/agent/summary", json={"patient_id": str(patient_user.id)}, headers=_auth_headers(token)
        )

    assert response.status_code == 200, response.text
    assert response.json()["relevant_insights"] == []

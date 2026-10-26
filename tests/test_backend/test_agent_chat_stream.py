"""POST /api/v1/agent/chat/stream (Epic 5.3): progress events first, then the
same AgentChatResponse /chat returns — and a draft guardrail_check blocks must
never reach the client, in any event."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password

PASSWORD = "AdminPass123"
PATIENT_EMAIL = "stream-patient@diab.example.com"
DOCTOR_EMAIL = "stream-doctor@diab.example.com"

_CHUNK = {
    "chunk_id": "t1-x",
    "text": "Mục tiêu HbA1c dưới 7%. Glucose mao mạch lúc đói 80-130 mg/dL.",
    "title": "Quyết định 5481/QĐ-BYT",
    "section": None,
    "page_ref": None,
    "source_url": None,
    "tier": 1,
    "trust_tier": "Bộ Y tế",
    "organization": "Bộ Y tế",
    "score": 0.9,
}


def _make_user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


async def _token(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    return str(response.json()["access_token"])


def _llm(*contents: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke.side_effect = [AsyncMock(content=c) for c in contents]
    return mock


def _plan() -> AsyncMock:
    return _llm(json.dumps({"intent": "QA", "needs_health_data": False, "needs_knowledge_retrieval": True}))


def _events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def _stream(client: AsyncClient, token: str, message: str, **extra) -> list[tuple[str, dict]]:
    response = await client.post(
        "/api/v1/agent/chat/stream",
        json={"message": message, **extra},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _events(response.text)


@pytest.mark.asyncio
async def test_stream_sends_progress_then_final_answer(client: AsyncClient, session: Session):
    _make_user(session, PATIENT_EMAIL, UserRole.PATIENT)
    token = await _token(client, PATIENT_EMAIL)
    answer = json.dumps(
        {"answer": "Mục tiêu HbA1c dưới 7%, glucose lúc đói 80-130 mg/dL.", "used_chunk_ids": ["t1-x"]},
        ensure_ascii=False,
    )
    with (
        patch("codebase.models.agents.nodes.plan_response.get_llm", return_value=_plan()),
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", return_value=_llm(answer)),
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge", return_value=[_CHUNK]),
    ):
        events = await _stream(client, token, "Mục tiêu HbA1c là bao nhiêu?")

    names = [name for name, _ in events]
    assert names[0] == "progress" and events[0][1]["stage"] == "start"
    assert names[-1] == "final" and names.count("final") == 1
    stages = [data["stage"] for name, data in events if name == "progress"]
    assert stages == ["start", "plan_response", "check_data_quality", "synthesize_dispatch"]
    final = events[-1][1]
    assert "HbA1c dưới 7%" in final["response"]
    assert final["sources"][0]["organization"] == "Bộ Y tế"
    assert final["session_id"]


@pytest.mark.asyncio
async def test_stream_never_leaks_a_guardrail_blocked_draft(client: AsyncClient, session: Session):
    _make_user(session, PATIENT_EMAIL, UserRole.PATIENT)
    token = await _token(client, PATIENT_EMAIL)
    unsafe = "Bạn bị đái tháo đường tuýp 2 rồi đó."
    with (
        patch("codebase.models.agents.nodes.plan_response.get_llm", return_value=_plan()),
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", return_value=_llm(unsafe, unsafe)),
        patch(
            "codebase.models.agents.nodes.verify_citations.get_llm",
            return_value=_llm(*['{"used_chunk_ids": ["t1-x"]}'] * 2),
        ),
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge", return_value=[_CHUNK]),
    ):
        events = await _stream(client, token, "Chỉ số đường huyết sau ăn bao nhiêu là an toàn?")

    raw = json.dumps(events, ensure_ascii=False)
    assert "tuýp 2 rồi đó" not in raw
    assert events[-1][0] == "final"
    assert events[-1][1]["response"]


@pytest.mark.asyncio
async def test_stream_high_risk_short_circuits_without_llm(client: AsyncClient, session: Session):
    _make_user(session, PATIENT_EMAIL, UserRole.PATIENT)
    token = await _token(client, PATIENT_EMAIL)
    with patch("codebase.models.agents.nodes.plan_response.get_llm") as plan_llm:
        events = await _stream(client, token, "Tôi có thể tự tăng lên 2 viên Gliclazide được không?")
    plan_llm.assert_not_called()
    assert [name for name, _ in events] == ["progress", "final"]
    assert "bác sĩ" in events[-1][1]["response"].lower()


@pytest.mark.asyncio
async def test_stream_permission_error_is_an_error_event(client: AsyncClient, session: Session):
    """A doctor asking about an unassigned patient: /chat answers 403; the
    stream (already 200 when the graph runs) reports it as an `error` event."""
    _make_user(session, DOCTOR_EMAIL, UserRole.DOCTOR)
    patient = _make_user(session, PATIENT_EMAIL, UserRole.PATIENT)
    token = await _token(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/agent/chat/stream",
        json={"message": "Bệnh nhân này thế nào?", "patient_id": str(patient.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 200:
        events = _events(response.text)
        assert events[-1][0] == "error"
        assert events[-1][1]["status"] == 403
    else:
        assert response.status_code == 403

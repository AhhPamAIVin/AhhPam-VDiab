"""Tests for POST /api/v1/agent/chat/suggestions (agent/chat_suggestions.py).

A display-only overlay, not part of the AI Consultation agent — the LLM here
is mocked directly on this module (not synthesize_qa.py's), so these tests
never touch the guarded QA prompt/graph.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.agent import chat_suggestions as chat_suggestions_mod
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password

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


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return type("R", (), {"content": self._content})()


@pytest.mark.asyncio
async def test_chat_suggestions_parses_json_array(client: AsyncClient, session: Session, monkeypatch):
    monkeypatch.setattr(
        chat_suggestions_mod,
        "get_llm",
        lambda: _FakeLLM('["Tôi nên ăn gì?", "Khi nào cần đo lại?", "Có cần đổi thuốc không?"]'),
    )
    patient = _make_user(session, email="suggest-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post(
        "/api/v1/agent/chat/suggestions",
        json={"message": "Đường huyết tôi hôm nay 180 mg/dL", "response": "Mức này hơi cao sau ăn."},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["suggestions"] == [
        "Tôi nên ăn gì?",
        "Khi nào cần đo lại?",
        "Có cần đổi thuốc không?",
    ]


@pytest.mark.asyncio
async def test_chat_suggestions_strips_markdown_fence(client: AsyncClient, session: Session, monkeypatch):
    monkeypatch.setattr(
        chat_suggestions_mod, "get_llm", lambda: _FakeLLM('```json\n["Câu hỏi 1", "Câu hỏi 2"]\n```')
    )
    patient = _make_user(session, email="suggest-fence@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post(
        "/api/v1/agent/chat/suggestions",
        json={"message": "Xin chào", "response": "Chào bạn"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["suggestions"] == ["Câu hỏi 1", "Câu hỏi 2"]


@pytest.mark.asyncio
async def test_chat_suggestions_malformed_llm_output_degrades_to_empty(
    client: AsyncClient, session: Session, monkeypatch
):
    monkeypatch.setattr(chat_suggestions_mod, "get_llm", lambda: _FakeLLM("not json at all"))
    patient = _make_user(session, email="suggest-broken@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post(
        "/api/v1/agent/chat/suggestions",
        json={"message": "Xin chào", "response": "Chào bạn"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["suggestions"] == []


@pytest.mark.asyncio
async def test_chat_suggestions_caps_at_four(client: AsyncClient, session: Session, monkeypatch):
    monkeypatch.setattr(
        chat_suggestions_mod,
        "get_llm",
        lambda: _FakeLLM('["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]'),
    )
    patient = _make_user(session, email="suggest-cap@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post(
        "/api/v1/agent/chat/suggestions",
        json={"message": "Xin chào", "response": "Chào bạn"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["suggestions"]) == 4

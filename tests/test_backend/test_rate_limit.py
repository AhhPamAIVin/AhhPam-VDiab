"""Rate limits on /auth/login (per IP) and /agent/* (per user) — task 6.2."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.config import get_settings
from codebase.backend.security.rate_limit import SlidingWindowLimiter, limiter

PASSWORD = "AdminPass123"


@pytest.fixture
def limits(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_agent_per_minute", 2)
    limiter.reset()
    yield settings
    limiter.reset()


def _user(session: Session, email: str, role: UserRole = UserRole.PATIENT) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_sliding_window_allows_up_to_the_limit_then_reports_retry_after():
    window = SlidingWindowLimiter()
    assert window.check("k", 2, now=0.0) is None
    assert window.check("k", 2, now=10.0) is None
    assert window.check("k", 2, now=20.0) == pytest.approx(40.0)
    assert window.check("k", 2, now=60.5) is None  # the first hit has left the window


@pytest.mark.asyncio
async def test_login_is_limited_per_ip_with_retry_after(client: AsyncClient, session: Session, limits):
    _user(session, "rl-login@diab.example.com")
    for _ in range(3):
        r = await client.post("/api/v1/auth/login", data={"username": "rl-login@diab.example.com", "password": "wrong"})
        assert r.status_code == 401
    blocked = await client.post(
        "/api/v1/auth/login", data={"username": "rl-login@diab.example.com", "password": PASSWORD}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert "thử lại" in blocked.json()["detail"]

    # A different client IP has its own budget.
    other = await client.post(
        "/api/v1/auth/login",
        data={"username": "rl-login@diab.example.com", "password": PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert other.status_code == 200


@pytest.mark.asyncio
async def test_agent_routes_are_limited_per_user(client: AsyncClient, session: Session, limits):
    _user(session, "rl-a@diab.example.com")
    _user(session, "rl-b@diab.example.com")
    limits.rate_limit_login_per_minute = 0  # isolate the agent limit from the login one
    token_a = (
        await client.post("/api/v1/auth/login", data={"username": "rl-a@diab.example.com", "password": PASSWORD})
    ).json()["access_token"]
    token_b = (
        await client.post("/api/v1/auth/login", data={"username": "rl-b@diab.example.com", "password": PASSWORD})
    ).json()["access_token"]

    a = {"Authorization": f"Bearer {token_a}"}
    assert (await client.get("/api/v1/agent/sessions", headers=a)).status_code == 200
    assert (await client.get("/api/v1/agent/sessions", headers=a)).status_code == 200
    third = await client.get("/api/v1/agent/sessions", headers=a)
    assert third.status_code == 429
    assert "Retry-After" in third.headers

    b = {"Authorization": f"Bearer {token_b}"}
    assert (await client.get("/api/v1/agent/sessions", headers=b)).status_code == 200


@pytest.mark.asyncio
async def test_limits_can_be_disabled(client: AsyncClient, session: Session, limits):
    _user(session, "rl-off@diab.example.com")
    limits.rate_limit_enabled = False
    for _ in range(6):
        r = await client.post("/api/v1/auth/login", data={"username": "rl-off@diab.example.com", "password": PASSWORD})
        assert r.status_code == 200

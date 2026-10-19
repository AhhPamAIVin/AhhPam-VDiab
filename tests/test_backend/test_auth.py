import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth import service
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password

ADMIN_EMAIL = "admin@diab.example.com"
ADMIN_PASSWORD = "AdminPass123"


@pytest.fixture
def admin_user(session: Session) -> User:
    admin = User(
        email=ADMIN_EMAIL,
        full_name="Seed Admin",
        role=UserRole.ADMIN,
        hashed_password=hash_password(ADMIN_PASSWORD),
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert token


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, admin_user: User):
    response = await client.post("/api/v1/auth/login", data={"username": ADMIN_EMAIL, "password": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user(client: AsyncClient):
    response = await client.post("/api/v1/auth/login", data={"username": "nope@x.com", "password": "whatever"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user_rejected(client: AsyncClient, session: Session):
    inactive = User(
        email="inactive@diab.example.com",
        full_name="Inactive",
        role=UserRole.PATIENT,
        hashed_password=hash_password("Password123"),
        is_active=False,
    )
    session.add(inactive)
    session.commit()

    response = await client.post(
        "/api/v1/auth/login", data={"username": "inactive@diab.example.com", "password": "Password123"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_me_requires_token(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.get("/api/v1/auth/me", headers=_auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == ADMIN_EMAIL
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_create_user(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/admin/users",
        json={
            "email": "doctor@diab.example.com",
            "full_name": "Dr. Strange",
            "role": "doctor",
            "temporary_password": "TempPass123",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "doctor"
    assert body["must_change_password"] is True

    doctor_token = await _login(client, "doctor@diab.example.com", "TempPass123")
    assert doctor_token


@pytest.mark.asyncio
async def test_non_admin_cannot_create_user(client: AsyncClient, session: Session):
    patient = User(
        email="patient@diab.example.com",
        full_name="Pat Ient",
        role=UserRole.PATIENT,
        hashed_password=hash_password("PatientPass123"),
    )
    session.add(patient)
    session.commit()

    token = await _login(client, "patient@diab.example.com", "PatientPass123")
    response = await client.post(
        "/api/v1/admin/users",
        json={"email": "x@x.com", "full_name": "X", "role": "patient", "temporary_password": "abcdefgh"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_reset_password_invalidates_old_token(client: AsyncClient, admin_user: User, session: Session):
    doctor = service.create_user(
        session,
        email="doc2@diab.example.com",
        full_name="Doc Two",
        role=UserRole.DOCTOR,
        temporary_password="Original123",
    )
    old_token = await _login(client, "doc2@diab.example.com", "Original123")

    admin_token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        f"/api/v1/admin/users/{doctor.id}/reset-password",
        json={},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    new_password = response.json()["temporary_password"]

    old_token_response = await client.get("/api/v1/auth/me", headers=_auth_headers(old_token))
    assert old_token_response.status_code == 401

    new_token = await _login(client, "doc2@diab.example.com", new_password)
    assert new_token


@pytest.mark.asyncio
async def test_change_own_password(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "NewAdminPass123"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    new_token = response.json()["access_token"]

    old_me = await client.get("/api/v1/auth/me", headers=_auth_headers(token))
    assert old_me.status_code == 401

    new_me = await client.get("/api/v1/auth/me", headers=_auth_headers(new_token))
    assert new_me.status_code == 200

    relog_token = await _login(client, ADMIN_EMAIL, "NewAdminPass123")
    assert relog_token


@pytest.mark.asyncio
async def test_change_password_wrong_current(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "NewAdminPass123"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_sql_injection_login_payload_blocked(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "attacker@x.com", "password": "' OR '1'='1"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_sql_injection_json_payload_blocked(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/admin/users",
        json={
            "email": "x@x.com",
            "full_name": "Robert'); DROP TABLE users;--",
            "role": "patient",
            "temporary_password": "abcdefgh",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_apostrophe_in_name_not_blocked(client: AsyncClient, admin_user: User):
    token = await _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        "/api/v1/admin/users",
        json={
            "email": "obrien@diab.example.com",
            "full_name": "Miles O'Brien",
            "role": "patient",
            "temporary_password": "abcdefgh",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text

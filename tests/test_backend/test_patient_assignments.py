import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password

ADMIN_EMAIL = "admin@diab.example.com"
DOCTOR_EMAIL = "doctor@diab.example.com"
PATIENT_EMAIL = "patient@diab.example.com"
OTHER_PATIENT_EMAIL = "patient2@diab.example.com"
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


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_assigns_patient_to_doctor(
    client: AsyncClient, admin_user: User, doctor_user: User, patient_user: User
):
    token = await _login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["doctor_id"] == str(doctor_user.id)
    assert response.json()["patient_id"] == str(patient_user.id)


@pytest.mark.asyncio
async def test_duplicate_assignment_rejected(
    client: AsyncClient, admin_user: User, doctor_user: User, patient_user: User
):
    token = await _login(client, ADMIN_EMAIL)
    body = {"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)}
    first = await client.post("/api/v1/admin/patient-assignments", json=body, headers=_auth_headers(token))
    assert first.status_code == 201
    second = await client.post("/api/v1/admin/patient-assignments", json=body, headers=_auth_headers(token))
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_assigning_second_doctor_to_patient_rejected(
    client: AsyncClient, session: Session, admin_user: User, doctor_user: User, patient_user: User
):
    """A patient may only have one doctor at a time; a different doctor must be
    explicitly unassigned first before a new one can be assigned."""
    other_doctor_user = _make_user(session, email="other-doctor@diab.example.com", role=UserRole.DOCTOR)

    token = await _login(client, ADMIN_EMAIL)
    first = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(token),
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(other_doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(token),
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_doctor_sees_only_assigned_patients(
    client: AsyncClient,
    admin_user: User,
    doctor_user: User,
    patient_user: User,
    other_patient_user: User,
):
    admin_token = await _login(client, ADMIN_EMAIL)
    await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(admin_token),
    )

    doctor_token = await _login(client, DOCTOR_EMAIL)
    response = await client.get("/api/v1/doctor/patients", headers=_auth_headers(doctor_token))
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()]
    assert emails == [PATIENT_EMAIL]


@pytest.mark.asyncio
async def test_non_admin_cannot_assign_patients(client: AsyncClient, doctor_user: User, patient_user: User):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_unassigns_patient(client: AsyncClient, admin_user: User, doctor_user: User, patient_user: User):
    admin_token = await _login(client, ADMIN_EMAIL)
    create_resp = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor_user.id), "patient_id": str(patient_user.id)},
        headers=_auth_headers(admin_token),
    )
    assignment_id = create_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/admin/patient-assignments/{assignment_id}", headers=_auth_headers(admin_token)
    )
    assert delete_resp.status_code == 204

    doctor_token = await _login(client, DOCTOR_EMAIL)
    list_resp = await client.get("/api/v1/doctor/patients", headers=_auth_headers(doctor_token))
    assert list_resp.json() == []

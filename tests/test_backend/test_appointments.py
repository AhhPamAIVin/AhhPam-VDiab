from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.patients.models import PatientAssignment

DOCTOR_EMAIL = "doctor@diab.example.com"
OTHER_DOCTOR_EMAIL = "doctor2@diab.example.com"
PATIENT_EMAIL = "patient@diab.example.com"
PASSWORD = "AdminPass123"


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
def patient_user(session: Session) -> User:
    return _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)


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


@pytest.mark.asyncio
async def test_doctor_schedules_appointment_for_assigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    scheduled_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    response = await client.post(
        "/api/v1/appointments",
        json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at, "reason": "Follow-up"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "scheduled"
    assert response.json()["doctor_id"] == str(doctor_user.id)


@pytest.mark.asyncio
async def test_doctor_cannot_schedule_for_unassigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    scheduled_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    response = await client.post(
        "/api/v1/appointments",
        json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at},
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_patient_cannot_schedule_appointments(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    scheduled_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    response = await client.post(
        "/api/v1/appointments",
        json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at},
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_patient_sees_only_own_appointments(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    scheduled_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    await client.post(
        "/api/v1/appointments",
        json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at},
        headers=_auth_headers(doctor_token),
    )

    patient_token = await _login(client, PATIENT_EMAIL)
    response = await client.get("/api/v1/appointments", headers=_auth_headers(patient_token))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["patient_id"] == str(patient_user.id)


@pytest.mark.asyncio
async def test_unrelated_doctor_cannot_list_other_doctors_patient_appointments(
    client: AsyncClient, doctor_user: User, other_doctor_user: User, patient_user: User, assigned: None
):
    other_token = await _login(client, OTHER_DOCTOR_EMAIL)
    response = await client.get(
        f"/api/v1/appointments?patient_id={patient_user.id}", headers=_auth_headers(other_token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_doctor_filters_appointments_by_day(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    today = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    tomorrow = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    for scheduled_at in (today, tomorrow):
        await client.post(
            "/api/v1/appointments",
            json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at.isoformat()},
            headers=_auth_headers(token),
        )

    response = await client.get("/api/v1/appointments", params={"on_date": "2026-08-10"}, headers=_auth_headers(token))
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["scheduled_at"].startswith("2026-08-10")


@pytest.mark.asyncio
async def test_doctor_updates_appointment_status(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    scheduled_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    create_resp = await client.post(
        "/api/v1/appointments",
        json={"patient_id": str(patient_user.id), "scheduled_at": scheduled_at},
        headers=_auth_headers(token),
    )
    appointment_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/appointments/{appointment_id}/status",
        json={"status": "completed"},
        headers=_auth_headers(token),
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_doctor_updates_appointment_details(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": str(patient_user.id),
            "scheduled_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "reason": "Initial reason",
        },
        headers=_auth_headers(token),
    )
    appointment_id = create_resp.json()["id"]
    new_time = datetime.now(UTC) + timedelta(days=10)

    update_resp = await client.put(
        f"/api/v1/appointments/{appointment_id}",
        json={
            "scheduled_at": new_time.isoformat(),
            "reason": "Updated reason",
            "status": "scheduled",
            "notes": "Bring glucose log",
        },
        headers=_auth_headers(token),
    )

    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["reason"] == "Updated reason"
    assert update_resp.json()["notes"] == "Bring glucose log"
    assert update_resp.json()["scheduled_at"].startswith(new_time.date().isoformat())


@pytest.mark.asyncio
async def test_doctor_deletes_own_appointment(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": str(patient_user.id),
            "scheduled_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers=_auth_headers(token),
    )
    appointment_id = create_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/appointments/{appointment_id}",
        headers=_auth_headers(token),
    )
    assert delete_resp.status_code == 204

    list_resp = await client.get(
        f"/api/v1/appointments?patient_id={patient_user.id}",
        headers=_auth_headers(token),
    )
    assert list_resp.status_code == 200
    assert all(item["id"] != appointment_id for item in list_resp.json())


@pytest.mark.asyncio
async def test_other_doctor_cannot_update_or_delete_appointment(
    client: AsyncClient,
    doctor_user: User,
    other_doctor_user: User,
    patient_user: User,
    assigned: None,
):
    owner_token = await _login(client, DOCTOR_EMAIL)
    other_token = await _login(client, OTHER_DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": str(patient_user.id),
            "scheduled_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        headers=_auth_headers(owner_token),
    )
    appointment_id = create_resp.json()["id"]
    payload = {
        "scheduled_at": (datetime.now(UTC) + timedelta(days=8)).isoformat(),
        "reason": None,
        "status": "scheduled",
        "notes": None,
    }

    update_resp = await client.put(
        f"/api/v1/appointments/{appointment_id}",
        json=payload,
        headers=_auth_headers(other_token),
    )
    delete_resp = await client.delete(
        f"/api/v1/appointments/{appointment_id}",
        headers=_auth_headers(other_token),
    )

    assert update_resp.status_code == 403
    assert delete_resp.status_code == 403

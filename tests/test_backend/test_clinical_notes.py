from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.patients.models import PatientAssignment

ADMIN_EMAIL = "admin@diab.example.com"
DOCTOR_EMAIL = "doctor@diab.example.com"
OTHER_DOCTOR_EMAIL = "doctor2@diab.example.com"
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
def other_doctor_user(session: Session) -> User:
    return _make_user(session, email=OTHER_DOCTOR_EMAIL, role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session) -> User:
    return _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def other_patient_user(session: Session) -> User:
    return _make_user(session, email=OTHER_PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def assigned_doctor_user(session: Session, doctor_user: User, patient_user: User) -> User:
    session.add(PatientAssignment(doctor_id=doctor_user.id, patient_id=patient_user.id, assigned_by=doctor_user.id))
    session.commit()
    return doctor_user


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _note_payload(**overrides: object) -> dict:
    """A minimal but fully-valid bệnh án create payload (all required sections filled).
    Individual fields can be overridden per-test."""
    payload: dict = {
        "reason_for_visit": "Tái khám đái tháo đường típ 2",
        "history_of_present_illness": "Bệnh nhân tỉnh táo, không triệu chứng cấp, đường huyết dao động nhẹ.",
        "diagnosis": "Đái tháo đường típ 2, chưa đạt mục tiêu kiểm soát",
        "treatment_plan": "Tiếp tục Metformin 850mg x2/ngày, tái khám sau 30 ngày.",
    }
    payload.update(overrides)
    return payload


async def _create_note(client: AsyncClient, *, token: str, patient_id: str, **overrides: object):
    return await client.post(
        f"/api/v1/doctor/patients/{patient_id}/notes",
        json=_note_payload(**overrides),
        headers=_auth_headers(token),
    )


async def _create_appointment(
    client: AsyncClient, *, token: str, patient_id: str, scheduled_at: datetime | None = None
):
    scheduled_at = scheduled_at or (datetime.now(UTC) + timedelta(days=7))
    return await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_id, "scheduled_at": scheduled_at.isoformat(), "reason": "Follow-up"},
        headers=_auth_headers(token),
    )


@pytest.mark.asyncio
async def test_doctor_creates_note_for_assigned_patient(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(client, token=token, patient_id=str(patient_user.id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["patient_id"] == str(patient_user.id)
    assert body["doctor_id"] == str(assigned_doctor_user.id)
    assert body["reason_for_visit"] == "Tái khám đái tháo đường típ 2"
    assert body["diagnosis"] == "Đái tháo đường típ 2, chưa đạt mục tiêu kiểm soát"
    assert body["treatment_plan"] == "Tiếp tục Metformin 850mg x2/ngày, tái khám sau 30 ngày."
    # Optional sections default to null when not supplied.
    assert body["appointment_id"] is None
    assert body["past_medical_history"] is None
    assert body["weight_kg"] is None
    assert body["blood_pressure_systolic"] is None
    assert body["blood_pressure_diastolic"] is None
    assert body["hba1c_pct"] is None
    assert body["follow_up_date"] is None


@pytest.mark.asyncio
async def test_doctor_can_record_full_clinical_exam_and_labs(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    """Every bệnh án section round-trips through create -> response."""
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(
        client,
        token=token,
        patient_id=str(patient_user.id),
        past_medical_history="Tăng huyết áp nhẹ (Amlodipine 5mg/ngày)",
        weight_kg=78.0,
        blood_pressure_systolic=128,
        blood_pressure_diastolic=82,
        clinical_exam_findings="Tỉnh táo, không phù, tim đều",
        data_period_start="2026-06-10",
        data_period_end="2026-07-09",
        hba1c_pct=7.4,
        paraclinical_results="HbA1c 7.4%, đường huyết đói lab 148 mg/dL",
        follow_up_date="2026-08-09",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["weight_kg"] == 78.0
    assert body["blood_pressure_systolic"] == 128
    assert body["blood_pressure_diastolic"] == 82
    assert body["data_period_start"] == "2026-06-10"
    assert body["data_period_end"] == "2026-07-09"
    assert body["hba1c_pct"] == 7.4
    assert body["follow_up_date"] == "2026-08-09"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["reason_for_visit", "history_of_present_illness", "diagnosis", "treatment_plan"])
async def test_create_note_requires_core_benh_an_sections(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User, missing_field: str
):
    token = await _login(client, DOCTOR_EMAIL)
    payload = _note_payload()
    del payload[missing_field]
    response = await client.post(
        f"/api/v1/doctor/patients/{patient_user.id}/notes",
        json=payload,
        headers=_auth_headers(token),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_note_rejects_lone_blood_pressure_value(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(
        client, token=token, patient_id=str(patient_user.id), blood_pressure_systolic=128
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_note_rejects_inverted_blood_pressure(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(
        client,
        token=token,
        patient_id=str(patient_user.id),
        blood_pressure_systolic=80,
        blood_pressure_diastolic=120,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_note_rejects_inverted_data_period(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(
        client,
        token=token,
        patient_id=str(patient_user.id),
        data_period_start="2026-07-09",
        data_period_end="2026-06-10",
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_note_links_to_appointment_for_same_patient(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    appt_response = await _create_appointment(client, token=token, patient_id=str(patient_user.id))
    assert appt_response.status_code == 201, appt_response.text
    appointment_id = appt_response.json()["id"]

    response = await _create_note(
        client, token=token, patient_id=str(patient_user.id), appointment_id=appointment_id
    )
    assert response.status_code == 201, response.text
    assert response.json()["appointment_id"] == appointment_id


@pytest.mark.asyncio
async def test_create_note_rejects_appointment_from_different_patient(
    client: AsyncClient,
    session: Session,
    assigned_doctor_user: User,
    patient_user: User,
    other_patient_user: User,
):
    session.add(
        PatientAssignment(
            doctor_id=assigned_doctor_user.id, patient_id=other_patient_user.id, assigned_by=assigned_doctor_user.id
        )
    )
    session.commit()

    token = await _login(client, DOCTOR_EMAIL)
    appt_response = await _create_appointment(client, token=token, patient_id=str(other_patient_user.id))
    assert appt_response.status_code == 201, appt_response.text
    appointment_id = appt_response.json()["id"]

    response = await _create_note(
        client, token=token, patient_id=str(patient_user.id), appointment_id=appointment_id
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_doctor_cannot_create_note_for_unassigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await _create_note(client, token=token, patient_id=str(patient_user.id))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_non_doctor_cannot_create_note(client: AsyncClient, admin_user: User, patient_user: User):
    token = await _login(client, ADMIN_EMAIL)
    response = await _create_note(client, token=token, patient_id=str(patient_user.id))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_notes_includes_every_past_doctors_notes(
    client: AsyncClient,
    session: Session,
    assigned_doctor_user: User,
    other_doctor_user: User,
    patient_user: User,
):
    """Notes persist across a doctor reassignment: a patient has one doctor at a time,
    but the currently assigned doctor should still see notes left by a prior doctor."""
    token = await _login(client, DOCTOR_EMAIL)
    await _create_note(client, token=token, patient_id=str(patient_user.id), reason_for_visit="Note from doctor 1")

    old_assignment = session.exec(
        select(PatientAssignment).where(PatientAssignment.patient_id == patient_user.id)
    ).one()
    session.delete(old_assignment)
    session.add(
        PatientAssignment(doctor_id=other_doctor_user.id, patient_id=patient_user.id, assigned_by=other_doctor_user.id)
    )
    session.commit()

    other_token = await _login(client, OTHER_DOCTOR_EMAIL)
    await _create_note(
        client, token=other_token, patient_id=str(patient_user.id), reason_for_visit="Note from doctor 2"
    )

    response = await client.get(
        f"/api/v1/doctor/patients/{patient_user.id}/notes", headers=_auth_headers(other_token)
    )
    assert response.status_code == 200
    reasons = {note["reason_for_visit"] for note in response.json()}
    assert reasons == {"Note from doctor 1", "Note from doctor 2"}


@pytest.mark.asyncio
async def test_author_can_update_own_note(client: AsyncClient, assigned_doctor_user: User, patient_user: User):
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id))
    note_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/doctor/patients/{patient_user.id}/notes/{note_id}",
        json={"diagnosis": "Updated assessment."},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["diagnosis"] == "Updated assessment."


@pytest.mark.asyncio
async def test_partial_update_only_changes_specified_fields(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id), weight_kg=80.0)
    note_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/doctor/patients/{patient_user.id}/notes/{note_id}",
        json={"weight_kg": 79.5},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weight_kg"] == 79.5
    # Untouched fields keep their original values.
    assert body["reason_for_visit"] == create_resp.json()["reason_for_visit"]
    assert body["diagnosis"] == create_resp.json()["diagnosis"]
    assert body["treatment_plan"] == create_resp.json()["treatment_plan"]


@pytest.mark.asyncio
async def test_update_rejects_appointment_from_different_patient(
    client: AsyncClient,
    session: Session,
    assigned_doctor_user: User,
    patient_user: User,
    other_patient_user: User,
):
    session.add(
        PatientAssignment(
            doctor_id=assigned_doctor_user.id, patient_id=other_patient_user.id, assigned_by=assigned_doctor_user.id
        )
    )
    session.commit()

    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id))
    note_id = create_resp.json()["id"]

    other_appt_resp = await _create_appointment(client, token=token, patient_id=str(other_patient_user.id))
    other_appointment_id = other_appt_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/doctor/patients/{patient_user.id}/notes/{note_id}",
        json={"appointment_id": other_appointment_id},
        headers=_auth_headers(token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_author_doctor_cannot_update_note(
    client: AsyncClient,
    session: Session,
    assigned_doctor_user: User,
    other_doctor_user: User,
    patient_user: User,
):
    """Even the doctor now assigned to the patient cannot edit a note authored by
    the prior doctor (only the authoring doctor may update their own note)."""
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id))
    note_id = create_resp.json()["id"]

    old_assignment = session.exec(
        select(PatientAssignment).where(PatientAssignment.patient_id == patient_user.id)
    ).one()
    session.delete(old_assignment)
    session.add(
        PatientAssignment(doctor_id=other_doctor_user.id, patient_id=patient_user.id, assigned_by=other_doctor_user.id)
    )
    session.commit()

    other_token = await _login(client, OTHER_DOCTOR_EMAIL)
    response = await client.patch(
        f"/api/v1/doctor/patients/{patient_user.id}/notes/{note_id}",
        json={"diagnosis": "Trying to edit someone else's note."},
        headers=_auth_headers(other_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_author_can_delete_own_note(client: AsyncClient, assigned_doctor_user: User, patient_user: User):
    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id))
    note_id = create_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/doctor/patients/{patient_user.id}/notes/{note_id}", headers=_auth_headers(token)
    )
    assert delete_resp.status_code == 204

    list_resp = await client.get(
        f"/api/v1/doctor/patients/{patient_user.id}/notes", headers=_auth_headers(token)
    )
    assert list_resp.json() == []


@pytest.mark.asyncio
async def test_note_scoped_to_patient_id_in_path(
    client: AsyncClient,
    session: Session,
    assigned_doctor_user: User,
    patient_user: User,
    other_patient_user: User,
):
    session.add(
        PatientAssignment(
            doctor_id=assigned_doctor_user.id, patient_id=other_patient_user.id, assigned_by=assigned_doctor_user.id
        )
    )
    session.commit()

    token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=token, patient_id=str(patient_user.id))
    note_id = create_resp.json()["id"]

    response = await client.patch(
        f"/api/v1/doctor/patients/{other_patient_user.id}/notes/{note_id}",
        json={"diagnosis": "Should not reach the note from a different patient's path."},
        headers=_auth_headers(token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patient_can_view_own_clinical_notes(
    client: AsyncClient, assigned_doctor_user: User, patient_user: User
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=doctor_token, patient_id=str(patient_user.id))
    assert create_resp.status_code == 201, create_resp.text

    patient_token = await _login(client, PATIENT_EMAIL)
    response = await client.get(
        f"/api/v1/doctor/patients/{patient_user.id}/notes", headers=_auth_headers(patient_token)
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_admin_can_view_any_patients_clinical_notes(
    client: AsyncClient, admin_user: User, assigned_doctor_user: User, patient_user: User
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=doctor_token, patient_id=str(patient_user.id))
    assert create_resp.status_code == 201, create_resp.text

    admin_token = await _login(client, ADMIN_EMAIL)
    response = await client.get(
        f"/api/v1/doctor/patients/{patient_user.id}/notes", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_patient_cannot_view_another_patients_clinical_notes(
    client: AsyncClient,
    assigned_doctor_user: User,
    patient_user: User,
    other_patient_user: User,
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await _create_note(client, token=doctor_token, patient_id=str(patient_user.id))
    assert create_resp.status_code == 201, create_resp.text

    other_token = await _login(client, OTHER_PATIENT_EMAIL)
    response = await client.get(
        f"/api/v1/doctor/patients/{patient_user.id}/notes", headers=_auth_headers(other_token)
    )
    assert response.status_code == 403

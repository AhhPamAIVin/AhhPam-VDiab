from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session

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
def patient_user(session: Session) -> User:
    return _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def other_patient_user(session: Session) -> User:
    return _make_user(session, email=OTHER_PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def other_doctor_user(session: Session) -> User:
    return _make_user(session, email=OTHER_DOCTOR_EMAIL, role=UserRole.DOCTOR)


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
async def test_doctor_adds_medication_for_assigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
            "note": "Initial therapy for newly diagnosed T2D",
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["patient_id"] == str(patient_user.id)
    assert body["drug_name"] == "Metformin"
    assert body["status"] == "active"
    assert body["end_date"] is None


@pytest.mark.asyncio
async def test_doctor_cannot_add_medication_for_unassigned_patient(
    client: AsyncClient, doctor_user: User, patient_user: User
):
    token = await _login(client, DOCTOR_EMAIL)
    response = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_change_medication_preserves_old_entry_in_history(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    headers = _auth_headers(token)

    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": "2026-01-01T00:00:00Z",
            "note": "Initial therapy",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    original_id = create_resp.json()["id"]

    change_resp = await client.post(
        f"/api/v1/medications/{original_id}/change",
        json={
            "drug_name": "Metformin",
            "dose": "1000mg",
            "frequency": "twice daily",
            "start_date": "2026-03-01T00:00:00Z",
            "note": "Dose increased due to poor glycemic control",
        },
        headers=headers,
    )
    assert change_resp.status_code == 201, change_resp.text
    new_entry = change_resp.json()
    assert new_entry["id"] != original_id
    assert new_entry["dose"] == "1000mg"
    assert new_entry["status"] == "active"
    assert new_entry["replaces_medication_id"] == original_id

    # The old entry must still be retrievable, untouched (discontinued, not deleted/overwritten).
    history_resp = await client.get(f"/api/v1/medications/history?patient_id={patient_user.id}", headers=headers)
    assert history_resp.status_code == 200
    history = history_resp.json()
    original_entries = [entry for entry in history if entry["id"] == original_id]
    assert len(original_entries) == 1
    original_after_change = original_entries[0]
    assert original_after_change["dose"] == "500mg"
    assert original_after_change["drug_name"] == "Metformin"
    assert original_after_change["status"] == "discontinued"
    assert original_after_change["end_date"] is not None

    # Only the new entry should show up as currently active.
    active_resp = await client.get(f"/api/v1/medications/active?patient_id={patient_user.id}", headers=headers)
    assert active_resp.status_code == 200
    active = active_resp.json()
    assert len(active) == 1
    assert active[0]["id"] == new_entry["id"]


@pytest.mark.asyncio
async def test_history_endpoint_returns_full_chronological_timeline(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    headers = _auth_headers(token)

    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Glipizide",
            "dose": "5mg",
            "frequency": "once daily",
            "start_date": "2026-01-01T00:00:00Z",
        },
        headers=headers,
    )
    first_id = create_resp.json()["id"]

    # Drug swap.
    swap_resp = await client.post(
        f"/api/v1/medications/{first_id}/change",
        json={
            "drug_name": "Sitagliptin",
            "dose": "100mg",
            "frequency": "once daily",
            "start_date": "2026-04-01T00:00:00Z",
            "note": "Switched due to hypoglycemia episodes",
        },
        headers=headers,
    )
    second_id = swap_resp.json()["id"]

    # Stop entirely, no replacement.
    stop_resp = await client.post(
        f"/api/v1/medications/{second_id}/discontinue",
        json={"end_date": "2026-07-01T00:00:00Z", "note": "Glycemic control achieved via lifestyle"},
        headers=headers,
    )
    assert stop_resp.status_code == 200, stop_resp.text

    history_resp = await client.get(f"/api/v1/medications/history?patient_id={patient_user.id}", headers=headers)
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert len(history) == 2
    # Chronological order: oldest entry first.
    assert history[0]["id"] == first_id
    assert history[0]["drug_name"] == "Glipizide"
    assert history[0]["status"] == "discontinued"
    assert history[1]["id"] == second_id
    assert history[1]["drug_name"] == "Sitagliptin"
    assert history[1]["status"] == "discontinued"
    assert history[1]["replaces_medication_id"] == first_id

    active_resp = await client.get(f"/api/v1/medications/active?patient_id={patient_user.id}", headers=headers)
    assert active_resp.json() == []


@pytest.mark.asyncio
async def test_as_of_reconstructs_the_regimen_at_a_past_point_in_time(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    """The whole point of keeping history: given a date in the past, we should be able to
    answer "what was this patient on then" even though the regimen has since changed."""
    token = await _login(client, DOCTOR_EMAIL)
    headers = _auth_headers(token)

    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": "2026-01-01T00:00:00Z",
        },
        headers=headers,
    )
    original_id = create_resp.json()["id"]

    change_resp = await client.post(
        f"/api/v1/medications/{original_id}/change",
        json={
            "drug_name": "Metformin",
            "dose": "1000mg",
            "frequency": "twice daily",
            "start_date": "2026-03-01T00:00:00Z",
            "note": "Dose increased",
        },
        headers=headers,
    )
    new_id = change_resp.json()["id"]

    # Before the change: only the original dose was in effect.
    before = await client.get(
        f"/api/v1/medications/active?patient_id={patient_user.id}&as_of=2026-02-01T00:00:00Z", headers=headers
    )
    assert before.status_code == 200, before.text
    assert [m["id"] for m in before.json()] == [original_id]
    assert before.json()[0]["dose"] == "500mg"

    # Exactly at the change instant: the new entry is what's in effect, not both/neither.
    at_boundary = await client.get(
        f"/api/v1/medications/active?patient_id={patient_user.id}&as_of=2026-03-01T00:00:00Z", headers=headers
    )
    assert at_boundary.status_code == 200, at_boundary.text
    assert [m["id"] for m in at_boundary.json()] == [new_id]

    # After the change: the new dose.
    after = await client.get(
        f"/api/v1/medications/active?patient_id={patient_user.id}&as_of=2026-04-01T00:00:00Z", headers=headers
    )
    assert after.status_code == 200, after.text
    assert [m["id"] for m in after.json()] == [new_id]
    assert after.json()[0]["dose"] == "1000mg"

    # Before the medication existed at all: nothing in effect.
    before_start = await client.get(
        f"/api/v1/medications/active?patient_id={patient_user.id}&as_of=2025-12-01T00:00:00Z", headers=headers
    )
    assert before_start.status_code == 200, before_start.text
    assert before_start.json() == []

    # Omitting as_of still behaves exactly like "now" (the new dose, since it's still active).
    now_resp = await client.get(f"/api/v1/medications/active?patient_id={patient_user.id}", headers=headers)
    assert [m["id"] for m in now_resp.json()] == [new_id]


@pytest.mark.asyncio
async def test_unassigned_doctor_cannot_change_or_view_medications(
    client: AsyncClient, admin_user: User, doctor_user: User, patient_user: User
):
    admin_token = await _login(client, ADMIN_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(admin_token),
    )
    # Admin cannot write either (write is doctor-only), confirms role gating on the endpoint.
    assert create_resp.status_code == 403

    doctor_token = await _login(client, DOCTOR_EMAIL)
    active_resp = await client.get(
        f"/api/v1/medications/active?patient_id={patient_user.id}", headers=_auth_headers(doctor_token)
    )
    assert active_resp.status_code == 403

    history_resp = await client.get(
        f"/api/v1/medications/history?patient_id={patient_user.id}", headers=_auth_headers(doctor_token)
    )
    assert history_resp.status_code == 403


@pytest.mark.asyncio
async def test_patient_can_view_own_medications(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )

    patient_token = await _login(client, PATIENT_EMAIL)
    response = await client.get("/api/v1/medications/active", headers=_auth_headers(patient_token))
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["drug_name"] == "Metformin"


@pytest.mark.asyncio
async def test_patient_cannot_view_another_patients_medications(
    client: AsyncClient, patient_user: User, other_patient_user: User
):
    token = await _login(client, PATIENT_EMAIL)
    response = await client.get(
        f"/api/v1/medications/active?patient_id={other_patient_user.id}", headers=_auth_headers(token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_any_patients_medication_history(
    client: AsyncClient, admin_user: User, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )

    admin_token = await _login(client, ADMIN_EMAIL)
    response = await client.get(
        f"/api/v1/medications/history?patient_id={patient_user.id}", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_patient_files_missed_dose_note_for_own_medication(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    medication_id = create_resp.json()["id"]

    patient_token = await _login(client, PATIENT_EMAIL)
    note_resp = await client.post(
        f"/api/v1/medications/{medication_id}/notes",
        json={"note_type": "missed_dose", "text": "Quên uống thuốc buổi tối hôm qua"},
        headers=_auth_headers(patient_token),
    )
    assert note_resp.status_code == 201, note_resp.text
    body = note_resp.json()
    assert body["note_type"] == "missed_dose"
    assert body["medication_id"] == medication_id
    assert body["patient_id"] == str(patient_user.id)

    # Doctor (assigned) can see it.
    list_resp = await client.get(
        f"/api/v1/medications/{medication_id}/notes", headers=_auth_headers(doctor_token)
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["note_type"] == "missed_dose"


@pytest.mark.asyncio
async def test_patient_files_adverse_reaction_note(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    medication_id = create_resp.json()["id"]

    patient_token = await _login(client, PATIENT_EMAIL)
    note_resp = await client.post(
        f"/api/v1/medications/{medication_id}/notes",
        json={"note_type": "adverse_reaction", "text": "Buồn nôn, tiêu chảy sau khi uống thuốc"},
        headers=_auth_headers(patient_token),
    )
    assert note_resp.status_code == 201, note_resp.text
    assert note_resp.json()["note_type"] == "adverse_reaction"

    # Shows up in the patient-wide notes listing too, not just the per-medication one.
    all_notes_resp = await client.get(
        f"/api/v1/medications/notes?patient_id={patient_user.id}", headers=_auth_headers(doctor_token)
    )
    assert all_notes_resp.status_code == 200
    assert len(all_notes_resp.json()) == 1


@pytest.mark.asyncio
async def test_doctor_cannot_file_medication_note(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    medication_id = create_resp.json()["id"]

    # Filing a note is a patient self-report; even the prescribing doctor can't file one.
    note_resp = await client.post(
        f"/api/v1/medications/{medication_id}/notes",
        json={"note_type": "missed_dose", "text": "Not the patient"},
        headers=_auth_headers(doctor_token),
    )
    assert note_resp.status_code == 403


@pytest.mark.asyncio
async def test_patient_cannot_file_note_for_another_patients_medication(
    client: AsyncClient,
    session: Session,
    doctor_user: User,
    patient_user: User,
    other_patient_user: User,
    assigned: None,
):
    session.add(
        PatientAssignment(doctor_id=doctor_user.id, patient_id=other_patient_user.id, assigned_by=doctor_user.id)
    )
    session.commit()

    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(other_patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    other_patients_medication_id = create_resp.json()["id"]

    patient_token = await _login(client, PATIENT_EMAIL)
    note_resp = await client.post(
        f"/api/v1/medications/{other_patients_medication_id}/notes",
        json={"note_type": "missed_dose", "text": "Trying to file against someone else's medication"},
        headers=_auth_headers(patient_token),
    )
    assert note_resp.status_code == 403


@pytest.mark.asyncio
async def test_unassigned_doctor_cannot_view_medication_notes(
    client: AsyncClient, doctor_user: User, other_doctor_user: User, patient_user: User, assigned: None
):
    doctor_token = await _login(client, DOCTOR_EMAIL)
    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    medication_id = create_resp.json()["id"]

    patient_token = await _login(client, PATIENT_EMAIL)
    await client.post(
        f"/api/v1/medications/{medication_id}/notes",
        json={"note_type": "missed_dose", "text": "Quên uống thuốc"},
        headers=_auth_headers(patient_token),
    )

    other_doctor_token = await _login(client, OTHER_DOCTOR_EMAIL)
    response = await client.get(
        f"/api/v1/medications/{medication_id}/notes", headers=_auth_headers(other_doctor_token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_changing_an_already_discontinued_medication_is_rejected(
    client: AsyncClient, doctor_user: User, patient_user: User, assigned: None
):
    token = await _login(client, DOCTOR_EMAIL)
    headers = _auth_headers(token)

    create_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient_user.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": "2026-01-01T00:00:00Z",
        },
        headers=headers,
    )
    med_id = create_resp.json()["id"]

    stop_resp = await client.post(
        f"/api/v1/medications/{med_id}/discontinue",
        json={"end_date": "2026-02-01T00:00:00Z"},
        headers=headers,
    )
    assert stop_resp.status_code == 200

    second_stop_resp = await client.post(
        f"/api/v1/medications/{med_id}/discontinue",
        json={},
        headers=headers,
    )
    assert second_stop_resp.status_code == 409

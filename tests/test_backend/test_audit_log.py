"""Task 6.1 — append-only audit trail: the database refuses UPDATE/DELETE,
and each audited action produces exactly one event."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from codebase.backend.audit.models import AuditEvent
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.patients.models import PatientAssignment
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole

PASSWORD = "AuditPass123"


def _make_user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def admin(session: Session) -> User:
    return _make_user(session, "admin@audit.example.com", UserRole.ADMIN)


@pytest.fixture
def doctor(session: Session) -> User:
    return _make_user(session, "doctor@audit.example.com", UserRole.DOCTOR)


@pytest.fixture
def patient(session: Session) -> User:
    return _make_user(session, "patient@audit.example.com", UserRole.PATIENT)


@pytest.fixture
def assigned(session: Session, admin: User, doctor: User, patient: User) -> None:
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=admin.id))
    session.commit()


async def _token(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _events(session: Session, action: str) -> list[AuditEvent]:
    session.expire_all()
    return list(session.exec(select(AuditEvent).where(AuditEvent.action == action)).all())


def _glucose_body(patient: User) -> dict:
    return {
        "patient_id": str(patient.id),
        "unit": "mg/dL",
        "value": 132,
        "measured_at": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "meal_context": "FASTING",
    }


# --- append-only ------------------------------------------------------------


def test_database_rejects_update_of_an_audit_row(session: Session):
    session.add(AuditEvent(actor_role="system", action="test.event"))
    session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("UPDATE audit_events SET action = 'tampered'"))
    session.rollback()
    assert [e.action for e in _events(session, "test.event")] == ["test.event"]


def test_database_rejects_delete_of_an_audit_row(session: Session):
    session.add(AuditEvent(actor_role="system", action="test.event"))
    session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM audit_events"))
    session.rollback()
    assert len(_events(session, "test.event")) == 1


def test_orm_delete_of_an_audit_row_is_rejected_too(session: Session):
    audit_event = AuditEvent(actor_role="system", action="test.event")
    session.add(audit_event)
    session.commit()
    session.delete(audit_event)
    with pytest.raises(DBAPIError, match="append-only"):
        session.commit()
    session.rollback()


# --- one event per action ---------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_and_failure_are_audited_without_the_email(client: AsyncClient, patient: User, session):
    await _token(client, patient)
    bad = await client.post("/api/v1/auth/login", data={"username": patient.email, "password": "wrong"})
    unknown = await client.post("/api/v1/auth/login", data={"username": "nobody@x.example.com", "password": "x"})
    assert bad.status_code == unknown.status_code == 401

    events = _events(session, "auth.login")
    assert [(e.outcome, e.actor_id) for e in events] == [
        ("success", patient.id),
        ("failure", patient.id),
        ("failure", None),
    ]
    assert events[2].actor_role == "anonymous"
    assert all(e.request_id and e.ip for e in events)
    assert not any("@" in (e.detail or "") + (e.resource_id or "") for e in events)


@pytest.mark.asyncio
async def test_patient_creating_a_glucose_record_is_one_event(client: AsyncClient, patient: User, session):
    headers = await _token(client, patient)
    response = await client.post("/api/v1/health/glucose", json=_glucose_body(patient), headers=headers)
    assert response.status_code == 201, response.text

    # Inserted, committed, then enriched by threshold evaluation and committed
    # again — still one action.
    events = _events(session, "glucose_record.create")
    assert len(events) == 1
    assert events[0].resource_id == response.json()["id"]
    assert events[0].patient_id == patient.id and events[0].actor_id == patient.id
    assert _events(session, "glucose_record.update") == []
    # A patient reading or writing their own data is not an access event.
    assert _events(session, "patient_data.access") == []


@pytest.mark.asyncio
async def test_doctor_reading_patient_data_is_one_event_per_request(
    client: AsyncClient, doctor: User, patient: User, assigned, session
):
    headers = await _token(client, doctor)
    response = await client.get(f"/api/v1/health/glucose?patient_id={patient.id}", headers=headers)
    assert response.status_code == 200, response.text

    events = _events(session, "patient_data.access")
    assert len(events) == 1
    assert events[0].actor_id == doctor.id and events[0].patient_id == patient.id
    assert events[0].outcome == "success"
    assert events[0].detail == "GET /api/v1/health/glucose"


@pytest.mark.asyncio
async def test_doctor_denied_unassigned_patient_is_audited(client: AsyncClient, doctor: User, patient: User, session):
    headers = await _token(client, doctor)
    response = await client.get(f"/api/v1/health/glucose?patient_id={patient.id}", headers=headers)
    assert response.status_code == 403

    events = _events(session, "patient_data.access")
    assert [(e.outcome, e.actor_id, e.patient_id) for e in events] == [("denied", doctor.id, patient.id)]


@pytest.mark.asyncio
async def test_clinical_note_create_update_delete(client: AsyncClient, doctor: User, patient: User, assigned, session):
    headers = await _token(client, doctor)
    base = f"/api/v1/doctor/patients/{patient.id}/notes"
    created = await client.post(
        base,
        json={
            "reason_for_visit": "Tái khám",
            "history_of_present_illness": "Ổn định",
            "diagnosis": "ĐTĐ típ 2",
            "treatment_plan": "Giữ phác đồ",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]
    updated = await client.patch(f"{base}/{note_id}", json={"reason_for_visit": "Tái khám định kỳ"}, headers=headers)
    assert updated.status_code == 200, updated.text
    deleted = await client.delete(f"{base}/{note_id}", headers=headers)
    assert deleted.status_code == 204

    for op in ("create", "update", "delete"):
        events = _events(session, f"clinical_note.{op}")
        assert len(events) == 1, op
        assert events[0].resource_id == note_id and events[0].actor_id == doctor.id
    # The note text never reaches the trail.
    rows = session.exec(select(AuditEvent)).all()
    assert not any("Tái khám" in (e.detail or "") for e in rows)


@pytest.mark.asyncio
async def test_care_plan_change_is_one_event_even_when_it_supersedes_a_version(
    client: AsyncClient, doctor: User, patient: User, assigned, session
):
    headers = await _token(client, doctor)
    url = f"/api/v1/doctor/patients/{patient.id}/care-plan-targets"
    body = {
        "target_low_mg_dl": 80,
        "target_high_mg_dl": 180,
        "danger_low_mg_dl": 54,
        "danger_high_mg_dl": 300,
        "risk_group": "STANDARD_TYPE_2",
        "change_reason": "Khởi tạo",
    }
    first = await client.post(url, json={**body, "effective_from": "2026-01-01T00:00:00"}, headers=headers)
    second = await client.post(url, json={**body, "effective_from": "2026-02-01T00:00:00"}, headers=headers)
    assert first.status_code == second.status_code == 201, second.text

    events = _events(session, "care_plan_target.create")
    assert [e.resource_id for e in events] == [first.json()["id"], second.json()["id"]]
    assert _events(session, "care_plan_target.update") == []


@pytest.mark.asyncio
async def test_medication_change_and_discontinue(client: AsyncClient, doctor: User, patient: User, assigned, session):
    headers = await _token(client, doctor)
    created = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "2 lần/ngày",
            "start_date": "2026-01-01T00:00:00",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    changed = await client.post(
        f"/api/v1/medications/{created.json()['id']}/change",
        json={"drug_name": "Metformin", "dose": "850mg", "frequency": "2 lần/ngày"},
        headers=headers,
    )
    assert changed.status_code == 201, changed.text
    stopped = await client.post(f"/api/v1/medications/{changed.json()['id']}/discontinue", json={}, headers=headers)
    assert stopped.status_code == 200, stopped.text

    assert len(_events(session, "medication.create")) == 2  # the first entry and its replacement
    updates = _events(session, "medication.update")
    assert [e.resource_id for e in updates] == [changed.json()["id"]]  # only the discontinue


@pytest.mark.asyncio
async def test_consent_change_is_one_event_with_the_flag_not_health_data(client: AsyncClient, patient: User, session):
    headers = await _token(client, patient)
    response = await client.patch("/api/v1/auth/me", json={"ai_memory_consent": True}, headers=headers)
    assert response.status_code == 200, response.text

    events = _events(session, "consent.update")
    assert [(e.actor_id, e.patient_id, e.detail) for e in events] == [
        (patient.id, patient.id, "ai_memory_consent=true")
    ]


@pytest.mark.asyncio
async def test_admin_actions_are_audited(client: AsyncClient, admin: User, doctor: User, patient: User, session):
    headers = await _token(client, admin)
    created = await client.post(
        "/api/v1/admin/users",
        json={
            "email": "new@audit.example.com",
            "full_name": "New",
            "role": "patient",
            "temporary_password": "TempPass123",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    reset = await client.post(f"/api/v1/admin/users/{patient.id}/reset-password", json={}, headers=headers)
    assert reset.status_code == 200, reset.text
    assigned = await client.post(
        "/api/v1/admin/patient-assignments",
        json={"doctor_id": str(doctor.id), "patient_id": str(patient.id)},
        headers=headers,
    )
    assert assigned.status_code == 201, assigned.text

    def by_admin(action: str) -> list[AuditEvent]:
        return [e for e in _events(session, action) if e.actor_id == admin.id]

    assert [e.resource_id for e in by_admin("user.create")] == [created.json()["id"]]
    assert [e.patient_id for e in by_admin("user.password_reset")] == [patient.id]
    assert [e.patient_id for e in by_admin("patient_assignment.create")] == [patient.id]


def test_ai_summary_generation_is_audited_once_per_generation(session: Session, patient: User):
    now = datetime.utcnow()
    snapshot = AgentSummarySnapshot(
        patient_id=patient.id,
        role=SummaryRole.DOCTOR,
        period_days=7,
        period_start=now - timedelta(days=7),
        period_end=now,
        summary="s",
    )
    session.add(snapshot)
    session.commit()
    snapshot.generated_at = datetime.utcnow()  # regenerated in place (summaries/service.py upsert)
    session.add(snapshot)
    session.commit()

    events = _events(session, "ai_summary.generate")
    assert len(events) == 2
    assert all(e.actor_role == "system" and e.patient_id == patient.id for e in events)


# --- admin API --------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_lists_and_filters_audit_events(client: AsyncClient, admin: User, patient: User, session):
    headers = await _token(client, admin)
    await client.post("/api/v1/auth/login", data={"username": patient.email, "password": "wrong"})

    response = await client.get("/api/v1/admin/audit-events?action=auth.login&outcome=failure", headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    assert page["items"][0]["actor_name"] == "patient"

    prefix = await client.get("/api/v1/admin/audit-events?action=auth.", headers=headers)
    assert prefix.json()["total"] == 2


@pytest.mark.asyncio
async def test_audit_api_is_admin_only(client: AsyncClient, doctor: User):
    headers = await _token(client, doctor)
    response = await client.get("/api/v1/admin/audit-events", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_request_id_is_echoed_and_recorded(client: AsyncClient, patient: User, session):
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": patient.email, "password": PASSWORD},
        headers={"X-Request-ID": "trace-abc-123"},
    )
    assert response.headers["x-request-id"] == "trace-abc-123"
    assert _events(session, "auth.login")[0].request_id == "trace-abc-123"

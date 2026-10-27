"""Task 6.5 — patient data export, admin-approved account deletion, retention."""

import io
import json
import zipfile
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.agent.models import ChatSession, PendingRecordAction
from codebase.backend.appointments.models import PrevisitBrief  # noqa: F401 — table on metadata
from codebase.backend.audit.models import AuditEvent
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealContext
from codebase.backend.patients.models import PatientAssignment
from codebase.backend.privacy.models import AccountDeletionRequest
from codebase.backend.privacy.retention import purge_expired
from codebase.backend.reports.models import PatientWeeklyReport, WeeklyReportStatus
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole
from codebase.models.agents.checkpoint import AgentCheckpoint

PASSWORD = "PrivacyPass123"


def _make_user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def admin(session: Session) -> User:
    return _make_user(session, "admin@privacy.example.com", UserRole.ADMIN)


@pytest.fixture
def doctor(session: Session) -> User:
    return _make_user(session, "doctor@privacy.example.com", UserRole.DOCTOR)


@pytest.fixture
def patient(session: Session) -> User:
    return _make_user(session, "patient@privacy.example.com", UserRole.PATIENT)


@pytest.fixture
def other_patient(session: Session) -> User:
    return _make_user(session, "other@privacy.example.com", UserRole.PATIENT)


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _glucose(session: Session, patient: User, value: float, *, pending_action_id=None) -> GlucoseRecord:
    record = GlucoseRecord(
        patient_id=patient.id,
        value=value,
        unit="mg/dL",
        measured_at=datetime.utcnow(),
        meal_context=MealContext.FASTING,
        created_by=patient.id,
        source_pending_action_id=pending_action_id,
    )
    session.add(record)
    session.commit()
    return record


def _chat(session: Session, patient: User, *, updated_at: datetime) -> ChatSession:
    chat = ChatSession(thread_id=f"t-{updated_at.timestamp()}", patient_id=patient.id, counterpart_id=patient.id)
    chat.updated_at = updated_at
    session.add(chat)
    session.commit()
    session.add(
        AgentCheckpoint(
            thread_id=chat.thread_id,
            checkpoint_id="c1",
            checkpoint_type="json",
            checkpoint_blob=b"{}",
            metadata_type="json",
            metadata_blob=b"{}",
        )
    )
    session.commit()
    return chat


def _snapshot(session: Session, patient: User, *, generated_at: datetime) -> AgentSummarySnapshot:
    snapshot = AgentSummarySnapshot(
        patient_id=patient.id,
        role=SummaryRole.PATIENT,
        period_days=7,
        period_start=generated_at - timedelta(days=7),
        period_end=generated_at,
        summary="Tóm tắt",
        generated_at=generated_at,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


# --- export -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_exports_only_their_own_data_as_json(
    client: AsyncClient, session: Session, patient: User, other_patient: User
):
    _glucose(session, patient, 140)
    _glucose(session, other_patient, 99)
    headers = await _headers(client, patient)

    response = await client.get("/api/v1/privacy/me/export", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    data = json.loads(response.content)
    assert data["account"]["email"] == patient.email
    assert "hashed_password" not in data["account"]
    assert [row["value"] for row in data["tables"]["glucose_records"]] == [140]
    assert "audit_events" not in data["tables"]

    events = session.exec(select(AuditEvent).where(AuditEvent.action == "patient_data.export")).all()
    assert [(e.actor_id, e.detail) for e in events] == [(patient.id, "json")]


@pytest.mark.asyncio
async def test_patient_exports_csv_zip(client: AsyncClient, session: Session, patient: User):
    _glucose(session, patient, 155)
    headers = await _headers(client, patient)

    response = await client.get("/api/v1/privacy/me/export?format=csv", headers=headers)
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert {"account.csv", "glucose_records.csv"} <= set(archive.namelist())
    assert "155" in archive.read("glucose_records.csv").decode("utf-8-sig")


@pytest.mark.asyncio
async def test_export_is_patient_only(client: AsyncClient, doctor: User):
    response = await client.get("/api/v1/privacy/me/export", headers=await _headers(client, doctor))
    assert response.status_code == 403


# --- deletion workflow ------------------------------------------------------


@pytest.mark.asyncio
async def test_deletion_request_then_admin_approval_erases_the_account(
    client: AsyncClient, session: Session, admin: User, doctor: User, patient: User, other_patient: User
):
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=admin.id))
    chat = _chat(session, patient, updated_at=datetime.utcnow())
    action = PendingRecordAction(
        patient_id=patient.id,
        chat_session_id=chat.id,
        record_type="glucose",
        payload_json="{}",
        expires_at=datetime.utcnow(),
    )
    session.add(action)
    session.commit()
    _glucose(session, patient, 140, pending_action_id=action.id)
    kept = _glucose(session, other_patient, 99)
    patient_id, thread_id, kept_id, doctor_id = patient.id, chat.thread_id, kept.id, doctor.id

    patient_headers = await _headers(client, patient)
    requested = await client.post("/api/v1/privacy/me/deletion-request", headers=patient_headers)
    assert requested.status_code == 201, requested.text
    again = await client.post("/api/v1/privacy/me/deletion-request", headers=patient_headers)
    assert again.status_code == 409

    admin_headers = await _headers(client, admin)
    listed = await client.get("/api/v1/admin/deletion-requests?status=pending", headers=admin_headers)
    assert [r["patient_email"] for r in listed.json()] == [patient.email]

    approved = await client.post(
        f"/api/v1/admin/deletion-requests/{requested.json()['id']}/approve", json={}, headers=admin_headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["deleted_rows"] >= 6  # user, assignment, chat, checkpoint, action, glucose

    session.expire_all()
    assert session.get(User, patient_id) is None
    assert session.exec(select(GlucoseRecord).where(GlucoseRecord.patient_id == patient_id)).all() == []
    assert session.exec(select(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id)).all() == []
    assert session.exec(select(PatientAssignment)).all() == []
    assert session.get(GlucoseRecord, kept_id) is not None  # other patients untouched
    assert session.get(User, doctor_id) is not None
    # The request and the trail survive the account.
    assert session.get(AccountDeletionRequest, UUID(requested.json()["id"])).status == "approved"
    actions = [
        e.action for e in session.exec(select(AuditEvent).where(AuditEvent.patient_id == patient_id)).all()
    ]
    assert "account_deletion.request" in actions and "account_deletion.approve" in actions

    # The erased account can no longer sign in.
    gone = await client.post("/api/v1/auth/login", data={"username": "patient@privacy.example.com", "password": PASSWORD})
    assert gone.status_code == 401


@pytest.mark.asyncio
async def test_patient_cancels_and_admin_rejects(client: AsyncClient, session: Session, admin: User, patient: User):
    patient_headers = await _headers(client, patient)
    first = await client.post("/api/v1/privacy/me/deletion-request", headers=patient_headers)
    cancelled = await client.post("/api/v1/privacy/me/deletion-request/cancel", headers=patient_headers)
    assert cancelled.json()["status"] == "cancelled"

    second = await client.post("/api/v1/privacy/me/deletion-request", headers=patient_headers)
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
    admin_headers = await _headers(client, admin)
    rejected = await client.post(
        f"/api/v1/admin/deletion-requests/{second.json()['id']}/reject",
        json={"note": "Đang trong thời hạn lưu hồ sơ bệnh án"},
        headers=admin_headers,
    )
    assert rejected.json()["status"] == "rejected"
    mine = await client.get("/api/v1/privacy/me/deletion-request", headers=patient_headers)
    assert mine.json()["decision_note"] == "Đang trong thời hạn lưu hồ sơ bệnh án"
    assert session.get(User, patient.id) is not None

    twice = await client.post(f"/api/v1/admin/deletion-requests/{second.json()['id']}/approve", json={}, headers=admin_headers)
    assert twice.status_code == 409


@pytest.mark.asyncio
async def test_only_admins_decide_deletions(client: AsyncClient, doctor: User, patient: User):
    requested = await client.post("/api/v1/privacy/me/deletion-request", headers=await _headers(client, patient))
    response = await client.post(
        f"/api/v1/admin/deletion-requests/{requested.json()['id']}/approve",
        json={},
        headers=await _headers(client, doctor),
    )
    assert response.status_code == 403


# --- retention --------------------------------------------------------------


def test_retention_purges_old_chats_and_snapshots_but_keeps_records(session: Session, patient: User, monkeypatch):
    monkeypatch.setattr("codebase.backend.privacy.retention.get_settings", lambda: _Settings(chat=365, summary=730))
    now = datetime(2026, 10, 27)
    old_chat = _chat(session, patient, updated_at=now - timedelta(days=400))
    recent_chat = _chat(session, patient, updated_at=now - timedelta(days=10))
    action = PendingRecordAction(
        patient_id=patient.id,
        chat_session_id=old_chat.id,
        record_type="glucose",
        payload_json="{}",
        expires_at=now - timedelta(days=400),
    )
    session.add(action)
    session.commit()
    saved_from_chat = _glucose(session, patient, 140, pending_action_id=action.id)
    old_snapshot = _snapshot(session, patient, generated_at=now - timedelta(days=800))
    recent_snapshot = _snapshot(session, patient, generated_at=now - timedelta(days=5))
    report = PatientWeeklyReport(
        patient_id=patient.id,
        week_start=(now - timedelta(days=800)).date(),
        week_end=(now - timedelta(days=794)).date(),
        period_start=now - timedelta(days=800),
        period_end=now - timedelta(days=793),
        status=WeeklyReportStatus.GENERATED,
        snapshot_id=old_snapshot.id,
    )
    session.add(report)
    session.commit()
    old_chat_id, old_snapshot_id = old_chat.id, old_snapshot.id

    counts = purge_expired(session, now=now)

    assert counts == {"chat_sessions": 1, "ai_summaries": 1}
    session.expire_all()
    assert session.get(ChatSession, old_chat_id) is None
    assert session.get(ChatSession, recent_chat.id) is not None
    assert session.exec(select(AgentCheckpoint).where(AgentCheckpoint.thread_id == old_chat.thread_id)).all() == []
    assert session.get(AgentSummarySnapshot, old_snapshot_id) is None
    assert session.get(AgentSummarySnapshot, recent_snapshot.id) is not None
    # Medical records and reports stay; only their links to purged rows go.
    kept = session.get(GlucoseRecord, saved_from_chat.id)
    assert kept is not None and kept.source_pending_action_id is None
    assert session.get(PatientWeeklyReport, report.id).snapshot_id is None
    purge_event = session.exec(select(AuditEvent).where(AuditEvent.action == "retention.purge")).one()
    assert purge_event.actor_role == "system" and purge_event.detail == "chat_sessions=1,ai_summaries=1"


def test_retention_zero_days_keeps_everything(session: Session, patient: User, monkeypatch):
    monkeypatch.setattr("codebase.backend.privacy.retention.get_settings", lambda: _Settings(chat=0, summary=0))
    now = datetime(2026, 10, 27)
    _chat(session, patient, updated_at=now - timedelta(days=4000))
    _snapshot(session, patient, generated_at=now - timedelta(days=4000))
    assert purge_expired(session, now=now) == {"chat_sessions": 0, "ai_summaries": 0}


class _Settings:
    def __init__(self, *, chat: int, summary: int) -> None:
        self.retention_chat_days = chat
        self.retention_ai_summary_days = summary

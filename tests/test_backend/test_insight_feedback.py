"""Epic 4.4: a doctor confirms/rejects an AI insight — who may, what the
patient then sees, reflect not reviving a rejected pattern, approval rate."""

import math
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.agent.router import _relevant_insights_for_summary
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.insights import feedback
from codebase.backend.insights.models import (
    InsightConfidence,
    InsightFeedback,
    InsightSourceKind,
    InsightVerdict,
    PatientInsight,
)
from codebase.backend.patients.models import PatientAssignment
from codebase.models.agents.nodes.upsert_insight import upsert_insight

PASSWORD = "AdminPass123"
NOW = datetime(2026, 10, 20, 8, 0)


def _user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


@pytest.fixture
def doctor(session: Session, patient: User) -> User:
    doctor = _user(session, "doctor@diab.example.com", UserRole.DOCTOR)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    return doctor


def _insight(session: Session, patient: User, text: str, kind=InsightSourceKind.WEEKLY_BATCH) -> PatientInsight:
    insight = PatientInsight(
        patient_id=patient.id, category="glucose_pattern", text=text, evidence={},
        confidence=InsightConfidence.MEDIUM, source_kind=kind, first_observed_at=NOW, last_confirmed_at=NOW,
    )
    session.add(insight)
    session.commit()
    session.refresh(insight)
    return insight


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _url(patient: User, insight: PatientInsight) -> str:
    return f"/api/v1/doctor/patients/{patient.id}/insights/{insight.id}/feedback"


@pytest.mark.asyncio
async def test_assigned_doctor_confirms_then_changes_mind(client: AsyncClient, session: Session, patient, doctor):
    insight = _insight(session, patient, "Đường huyết sau ăn tối thường cao.")
    headers = await _headers(client, doctor)

    confirmed = await client.put(_url(patient, insight), json={"verdict": "confirmed"}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["verdict"] == "confirmed"
    assert confirmed.json()["verdict_version"] == 1

    rejected = await client.put(
        _url(patient, insight), json={"verdict": "rejected", "comment": "  Do đo sai giờ  "}, headers=headers
    )
    assert rejected.json()["verdict"] == "rejected"
    assert rejected.json()["verdict_comment"] == "Do đo sai giờ"
    assert len(session.exec(select(InsightFeedback)).all()) == 1  # changed in place

    listed = (await client.get(f"/api/v1/doctor/patients/{patient.id}/insights", headers=headers)).json()
    assert [(i["id"], i["verdict"]) for i in listed] == [(str(insight.id), "rejected")]


@pytest.mark.asyncio
async def test_only_the_assigned_doctor_may_give_feedback(client: AsyncClient, session: Session, patient, doctor):
    insight = _insight(session, patient, "Hay quên đo buổi tối.")
    other_doctor = _user(session, "other-doctor@diab.example.com", UserRole.DOCTOR)
    admin = _user(session, "admin@diab.example.com", UserRole.ADMIN)
    body = {"verdict": "confirmed"}

    for user in (other_doctor, patient, admin):
        response = await client.put(_url(patient, insight), json=body, headers=await _headers(client, user))
        assert response.status_code == 403, user.role
    assert session.exec(select(InsightFeedback)).all() == []


@pytest.mark.asyncio
async def test_feedback_on_someone_elses_or_a_chat_insight_is_404(client: AsyncClient, session: Session, patient, doctor):
    other_patient = _user(session, "p2@diab.example.com", UserRole.PATIENT)
    foreign = _insight(session, other_patient, "Không phải của bệnh nhân này.")
    chat = _insight(session, patient, "Từ trò chuyện riêng.", InsightSourceKind.CHAT)
    headers = await _headers(client, doctor)
    for insight in (foreign, chat):
        response = await client.put(
            f"/api/v1/doctor/patients/{patient.id}/insights/{insight.id}/feedback",
            json={"verdict": "rejected"}, headers=headers,
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_patient_never_sees_a_rejected_insight(client: AsyncClient, session: Session, patient, doctor):
    kept = _insight(session, patient, "Đường huyết buổi sáng ổn định.")
    rejected = _insight(session, patient, "Nhận định sai.")
    feedback.set_feedback(session, insight=kept, doctor_id=doctor.id, verdict=InsightVerdict.CONFIRMED, comment=None)
    feedback.set_feedback(session, insight=rejected, doctor_id=doctor.id, verdict=InsightVerdict.REJECTED, comment=None)

    listed = (await client.get(
        f"/api/v1/doctor/patients/{patient.id}/insights", headers=await _headers(client, patient)
    )).json()
    assert [i["id"] for i in listed] == [str(kept.id)]
    assert listed[0]["verdict"] is None  # verdicts are for the doctor only

    assert _relevant_insights_for_summary(session, patient) == [kept.text]


# --- reflect does not revive a rejected pattern ----------------------------------


class _Embeddings:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


@pytest.fixture
def collection():
    from codebase.models.rag.vector_store import QdrantVectorClient

    return QdrantVectorClient(location=":memory:").get_or_create_collection(f"test-insights-{uuid.uuid4().hex}")


def _near(similarity: float) -> list[float]:
    return [similarity, math.sqrt(1.0 - similarity**2)]


@pytest.mark.asyncio
async def test_reflect_skips_a_candidate_matching_a_rejected_insight(session: Session, patient, doctor, collection):
    original = "Đường huyết cuối tuần cao hơn ngày thường."
    again = "Cuối tuần đường huyết thường cao hơn."
    embeddings = _Embeddings({original: [1.0, 0.0], again: _near(0.95)})
    candidate = {"category": "glucose_pattern", "text": original, "evidence": {}, "confidence": "medium"}

    created = await upsert_insight(
        session, patient_id=patient.id, source_kind="weekly_batch", candidate=candidate,
        collection=collection, embeddings_client=embeddings,
    )
    insight = session.get(PatientInsight, created["id"])
    feedback.set_feedback(session, insight=insight, doctor_id=doctor.id, verdict=InsightVerdict.REJECTED, comment=None)

    with patch("codebase.models.agents.nodes.upsert_insight.get_llm") as merge_llm:
        result = await upsert_insight(
            session, patient_id=patient.id, source_kind="weekly_batch", candidate={**candidate, "text": again},
            collection=collection, embeddings_client=embeddings,
        )

    assert result is None
    merge_llm.assert_not_called()
    rows = session.exec(select(PatientInsight)).all()
    assert len(rows) == 1 and rows[0].version == 1 and rows[0].text == original


@pytest.mark.asyncio
async def test_reflect_still_updates_a_confirmed_insight(session: Session, patient, doctor, collection):
    original = "Hay bỏ bữa trưa."
    again = "Thường bỏ bữa trưa."
    embeddings = _Embeddings({original: [1.0, 0.0], again: _near(0.95), "Thường bỏ bữa trưa, đã gộp.": [1.0, 0.0]})
    candidate = {"category": "meal_pattern", "text": original, "evidence": {}, "confidence": "medium"}
    created = await upsert_insight(
        session, patient_id=patient.id, source_kind="weekly_batch", candidate=candidate,
        collection=collection, embeddings_client=embeddings,
    )
    insight = session.get(PatientInsight, created["id"])
    feedback.set_feedback(session, insight=insight, doctor_id=doctor.id, verdict=InsightVerdict.CONFIRMED, comment=None)

    with patch("codebase.models.agents.nodes.upsert_insight._merge_text", return_value="Thường bỏ bữa trưa, đã gộp."):
        result = await upsert_insight(
            session, patient_id=patient.id, source_kind="weekly_batch", candidate={**candidate, "text": again},
            collection=collection, embeddings_client=embeddings,
        )
    assert result["version"] == 2
    # The verdict was for version 1 — the UI shows it as given on an older text.
    assert feedback.latest_feedback(session, [insight.id])[insight.id].insight_version == 1


# --- approval rate -------------------------------------------------------------------


def test_approval_stats_use_each_insights_latest_verdict(session: Session, patient, doctor):
    a = _insight(session, patient, "A")
    b = _insight(session, patient, "B")
    c = _insight(session, patient, "C", InsightSourceKind.APPOINTMENT)
    feedback.set_feedback(session, insight=a, doctor_id=doctor.id, verdict=InsightVerdict.REJECTED, comment=None, now=NOW)
    feedback.set_feedback(
        session, insight=a, doctor_id=doctor.id, verdict=InsightVerdict.CONFIRMED, comment=None,
        now=NOW + timedelta(hours=1),
    )
    feedback.set_feedback(session, insight=b, doctor_id=doctor.id, verdict=InsightVerdict.REJECTED, comment=None, now=NOW)
    feedback.set_feedback(session, insight=c, doctor_id=doctor.id, verdict=InsightVerdict.CONFIRMED, comment=None, now=NOW)

    stats = feedback.approval_stats(session)
    assert stats["overall"] == {"reviewed": 3, "confirmed": 2, "rejected": 1, "approval_rate_pct": 66.7}
    assert stats["by_source_kind"]["weekly_batch"]["approval_rate_pct"] == 50.0
    assert stats["by_source_kind"]["appointment"]["approval_rate_pct"] == 100.0
    assert feedback.approval_stats(session, since=NOW + timedelta(minutes=30))["overall"]["reviewed"] == 1


def test_export_script_writes_markdown(session: Session, patient, doctor, tmp_path):
    from scripts.export_insight_feedback_stats import render_markdown

    insight = _insight(session, patient, "A")
    feedback.set_feedback(session, insight=insight, doctor_id=doctor.id, verdict=InsightVerdict.CONFIRMED, comment=None)
    text = render_markdown(feedback.approval_stats(session), generated_at=NOW)
    assert "| Tổng | 1 | 1 | 0 | 100.0% |" in text
    assert patient.email not in text

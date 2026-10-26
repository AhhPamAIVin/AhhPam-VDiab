"""Reflect-agent tests — EVAL-REFLECT-001/002 against the mock demo-patient
fixture (data/mock/demo_patient/, design doc §7.5), plus the two isolation
regression tests plan §C calls for (patient_id / source_kind partitioning
of the vector-store similarity match).

LLM calls are mocked (unittest.mock.patch/AsyncMock), matching this repo's
established convention (tests/test_backend/test_agent_chat.py,
tests/test_models/test_graph.py). Vector similarity search uses a real,
in-memory Qdrant, per-test collection with a controlled fake embeddings client
(deterministic 2D vectors with an exact chosen cosine similarity — same
technique tests/test_models/test_rag_knowledge_tools.py already uses for
knowledge_tools.py) rather than a mocked find_similar_insights — this is
what lets the isolation tests actually exercise the real vector-store `where`
filter, not just assert a mock was called with the right args.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealContext, MeasurementSource
from codebase.backend.insights.models import InsightSourceKind, InsightStatus, PatientInsight
from codebase.models.agents.nodes.reflect import reflect
from codebase.models.agents.nodes.upsert_insight import upsert_insight
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics
from codebase.models.agents.tools.patient_tools import get_health_records

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "mock" / "demo_patient"
PASSWORD = "AdminPass123"


def _make_patient(session: Session, *, email: str = "reflect-patient@diab.example.com") -> User:
    user = User(email=email, full_name="Demo Patient", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_glucose_records(session: Session, patient_id) -> None:
    """Loads the real mock fixture's glucose readings (176 rows, 60 days)
    into the test DB — see data/mock/demo_patient/README.md. Meal/activity
    records aren't needed for these tests: the mocked propose LLM call
    doesn't actually read metrics_block's content (it returns fixed JSON
    regardless of prompt), and the "gather grounds propose" test below only
    checks the glucose-derived post_prandial count."""
    rows = json.loads((FIXTURE_DIR / "glucose_records.json").read_text(encoding="utf-8"))
    for row in rows:
        session.add(
            GlucoseRecord(
                patient_id=patient_id,
                value=row["value"],
                unit=row["unit"],
                measured_at=datetime.fromisoformat(row["measured_at"]),
                note=row.get("note"),
                meal_context=MealContext(row["meal_context"]) if row.get("meal_context") else None,
                measurement_source=MeasurementSource(row["measurement_source"]),
                created_by=patient_id,
            )
        )
    session.commit()


def _clinical_notes() -> list[dict]:
    return json.loads((FIXTURE_DIR / "clinical_notes.json").read_text(encoding="utf-8"))


def _period_window(covers_period: list[str]) -> tuple[datetime, datetime]:
    """ClinicalNote.covers_period is date-only (design doc's mock fixture) —
    reflect() itself does no date-boundary normalization (see
    nodes/reflect.py's _gather_and_propose docstring: that's the caller's
    job), so the test does it here, extending to end-of-day so the period's
    last calendar day's readings aren't silently dropped by
    get_health_records' inclusive-<=-end_date filter."""
    start_date, end_date = covers_period
    start = datetime.fromisoformat(start_date)
    end = datetime.combine(datetime.fromisoformat(end_date).date(), time.max)
    return start, end


def _fake_llm_returning(content: str) -> AsyncMock:
    mock_llm = AsyncMock()
    response = AsyncMock()
    response.content = content
    mock_llm.ainvoke.return_value = response
    return mock_llm


@pytest.fixture
def ephemeral_insights_collection():
    """Fresh, isolated in-memory Qdrant collection per test (one client per
    test, so nothing leaks between tests)."""
    from codebase.models.rag.vector_store import QdrantChromaClient

    return QdrantChromaClient(location=":memory:").get_or_create_collection(f"test-insights-{uuid.uuid4().hex}")


def _vector_with_similarity(similarity: float) -> list[float]:
    """A 2D unit vector whose cosine similarity to [1.0, 0.0] is exactly
    `similarity` — same technique test_rag_knowledge_tools.py uses to
    control exact match scores against SIMILARITY_THRESHOLD."""
    return [similarity, math.sqrt(max(0.0, 1.0 - similarity**2))]


class _LookupEmbeddingsClient:
    """Deterministic stand-in for the embedding model — every text a test cares
    about is registered up front with an exact vector, so the create-vs-
    update decision (SIMILARITY_THRESHOLD) is fully controlled rather than
    depending on real semantic embeddings. Looking up an unregistered text
    raises (KeyError) immediately rather than silently returning a default —
    these tests want to know exactly which texts get embedded."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


# ---------------------------------------------------------------------------
# EVAL-REFLECT-001/002 — design doc §7.5,
# data/mock/demo_patient/expected_reflections.json. Candidate texts below
# are hand-written to match the SIGNAL that fixture describes (weekday/
# weekend glucose pattern narrowing to weekends; a genuinely new
# lunch-skipping/hypoglycemia pattern) — the fixture's own header explicitly
# says not to assert byte-for-byte LLM phrasing, only that the pipeline
# takes the right create/update actions.
# ---------------------------------------------------------------------------

NOTE1_CANDIDATE_TEXT = (
    "Đường huyết sau bữa tối thường vượt ngưỡng 180 mg/dL vào cuối tuần, do ăn tối "
    "nhiều tinh bột và không tập thể dục buổi tối; ngày thường ít gặp hơn nhiều."
)
NOTE2_UPDATE_CANDIDATE_TEXT = (
    "Mô hình đường huyết sau bữa tối cao nay chỉ còn xảy ra vào cuối tuần; ngày "
    "thường đã cải thiện rõ rệt, không còn ghi nhận vượt ngưỡng."
)
NOTE2_HYPO_CANDIDATE_TEXT = (
    "Ghi nhận 2 đợt chóng mặt nhẹ/run tay giữa buổi chiều, trùng với 2 ngày bỏ/trễ "
    "bữa trưa vì công việc; đường huyết đo được lúc đó 72 và 78 mg/dL."
)
MERGED_TEXT = (
    "Mô hình đường huyết sau bữa tối cao (>180 mg/dL) do ăn tối nhiều tinh bột và "
    "không vận động nay CHỈ còn xảy ra vào cuối tuần; ngày thường đã cải thiện và "
    "không còn ghi nhận vượt ngưỡng."
)


def _note1_response() -> str:
    return json.dumps(
        [
            {
                "category": "glucose_pattern",
                "text": NOTE1_CANDIDATE_TEXT,
                "evidence": {
                    "date_range": ["2026-06-10", "2026-07-09"],
                    "supporting_stats": {"weekend_pct_over_180": 100.0, "weekday_pct_over_180": 18.2},
                },
                "confidence": "medium",
            }
        ],
        ensure_ascii=False,
    )


def _note2_response() -> str:
    return json.dumps(
        [
            {
                "category": "glucose_pattern",
                "text": NOTE2_UPDATE_CANDIDATE_TEXT,
                "evidence": {
                    "date_range": ["2026-07-10", "2026-08-08"],
                    "supporting_stats": {"weekend_pct_over_180": 66.7, "weekday_pct_over_180": 0.0},
                },
                "confidence": "high",
            },
            {
                "category": "hypoglycemia_risk",
                "text": NOTE2_HYPO_CANDIDATE_TEXT,
                "evidence": {
                    "date_range": ["2026-07-10", "2026-08-08"],
                    "supporting_stats": {"values_mgdl": [72, 78]},
                },
                "confidence": "medium",
            },
        ],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_eval_reflect_001_first_note_creates_one_insight(session: Session, ephemeral_insights_collection):
    """EVAL-REFLECT-001: first ClinicalNote -> exactly one new PatientInsight
    row (category=glucose_pattern), status=active, version=1."""
    patient = _make_patient(session)
    _seed_glucose_records(session, patient.id)
    note1 = _clinical_notes()[0]
    period_start, period_end = _period_window(note1["covers_period"])

    fake_embeddings = _LookupEmbeddingsClient({NOTE1_CANDIDATE_TEXT: [1.0, 0.0]})

    with (
        patch("codebase.models.agents.nodes.reflect.get_llm", return_value=_fake_llm_returning(_note1_response())),
        patch(
            "codebase.models.agents.nodes.upsert_insight.get_insights_collection",
            return_value=ephemeral_insights_collection,
        ),
        patch("codebase.models.agents.nodes.upsert_insight.get_embeddings_client", return_value=fake_embeddings),
    ):
        results = await reflect(
            patient.id,
            period_start,
            period_end,
            note_text=note1["text"],
            source_kind="appointment",
            session=session,
        )

    assert len(results) == 1
    assert results[0]["category"] == "glucose_pattern"
    assert results[0]["version"] == 1
    assert results[0]["status"] == InsightStatus.ACTIVE
    assert results[0]["source_kind"] == "appointment"
    assert results[0]["supersedes_version"] is None

    rows = session.exec(select(PatientInsight).where(PatientInsight.patient_id == patient.id)).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_eval_reflect_001_gather_step_grounds_propose_prompt_in_real_tool_output(
    session: Session, ephemeral_insights_collection
):
    """No-fabrication regression check: the propose LLM call must receive
    numbers computed by the real tools (get_health_records +
    calculate_health_metrics), not just the raw note text — proves "gather"
    actually reused the respond-agent's own tools rather than skipping that
    step. Marker number is computed independently in the test via the same
    real tool calls, not hardcoded from the fixture's narrative (which the
    fixture's own generator computes differently upstream — see that
    fixture's README: "worked example... not a fixture to assert
    byte-for-byte")."""
    patient = _make_patient(session)
    _seed_glucose_records(session, patient.id)
    note1 = _clinical_notes()[0]
    period_start, period_end = _period_window(note1["covers_period"])

    records = get_health_records(session, patient.id, period_start, period_end)
    metrics = calculate_health_metrics(records)
    assert metrics["glucose_count"] > 0  # sanity: fixture data actually loaded
    marker = str(metrics["post_prandial"]["count"])

    fake_embeddings = _LookupEmbeddingsClient({NOTE1_CANDIDATE_TEXT: [1.0, 0.0]})
    mock_llm = _fake_llm_returning(_note1_response())

    with (
        patch("codebase.models.agents.nodes.reflect.get_llm", return_value=mock_llm),
        patch(
            "codebase.models.agents.nodes.upsert_insight.get_insights_collection",
            return_value=ephemeral_insights_collection,
        ),
        patch("codebase.models.agents.nodes.upsert_insight.get_embeddings_client", return_value=fake_embeddings),
    ):
        await reflect(
            patient.id, period_start, period_end, note_text=note1["text"], source_kind="appointment", session=session
        )

    sent_prompt = mock_llm.ainvoke.call_args[0][0][0]["content"]
    assert marker in sent_prompt
    assert note1["text"] in sent_prompt  # note text passed through as context, not summarized away


@pytest.mark.asyncio
async def test_eval_reflect_002_second_note_updates_existing_and_creates_new(
    session: Session, ephemeral_insights_collection
):
    """EVAL-REFLECT-002: second ClinicalNote -> the glucose_pattern insight
    from note 1 is UPDATED in place (same id, version 2,
    supersedes_version=1), a second, distinct insight is CREATED
    (category=hypoglycemia_risk, genuinely new, no precedent in period 1) —
    total active insights for the patient = 2, not 3."""
    patient = _make_patient(session)
    _seed_glucose_records(session, patient.id)
    note1, note2 = _clinical_notes()

    fake_embeddings = _LookupEmbeddingsClient(
        {
            NOTE1_CANDIDATE_TEXT: [1.0, 0.0],
            NOTE2_UPDATE_CANDIDATE_TEXT: _vector_with_similarity(0.95),  # >= threshold -> matches note 1's insight
            NOTE2_HYPO_CANDIDATE_TEXT: _vector_with_similarity(0.10),  # well below threshold -> genuinely new
            MERGED_TEXT: [0.9, 0.4],  # re-embedded after the merge call; never matched against again in this test
        }
    )

    with (
        patch(
            "codebase.models.agents.nodes.upsert_insight.get_insights_collection",
            return_value=ephemeral_insights_collection,
        ),
        patch("codebase.models.agents.nodes.upsert_insight.get_embeddings_client", return_value=fake_embeddings),
    ):
        with patch("codebase.models.agents.nodes.reflect.get_llm", return_value=_fake_llm_returning(_note1_response())):
            period1_start, period1_end = _period_window(note1["covers_period"])
            first_results = await reflect(
                patient.id,
                period1_start,
                period1_end,
                note_text=note1["text"],
                source_kind="appointment",
                session=session,
            )
        insight1_id = first_results[0]["id"]

        with (
            patch("codebase.models.agents.nodes.reflect.get_llm", return_value=_fake_llm_returning(_note2_response())),
            patch("codebase.models.agents.nodes.upsert_insight.get_llm", return_value=_fake_llm_returning(MERGED_TEXT)),
        ):
            period2_start, period2_end = _period_window(note2["covers_period"])
            second_results = await reflect(
                patient.id,
                period2_start,
                period2_end,
                note_text=note2["text"],
                source_kind="appointment",
                session=session,
            )

    assert len(second_results) == 2
    updated = next(r for r in second_results if r["category"] == "glucose_pattern")
    created = next(r for r in second_results if r["category"] == "hypoglycemia_risk")

    assert updated["id"] == insight1_id  # same row, updated in place — not a duplicate
    assert updated["version"] == 2
    assert updated["supersedes_version"] == 1
    assert updated["text"] == MERGED_TEXT

    assert created["id"] != insight1_id
    assert created["version"] == 1
    assert created["supersedes_version"] is None

    active_rows = session.exec(
        select(PatientInsight).where(
            PatientInsight.patient_id == patient.id, PatientInsight.status == InsightStatus.ACTIVE
        )
    ).all()
    assert len(active_rows) == 2  # not 3


# ---------------------------------------------------------------------------
# Isolation regression tests — plan §B/§C: upsert_insight's similarity match
# must filter on BOTH patient_id and source_kind, as hard invariants.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolation_two_patients_near_identical_text_produce_separate_insights(
    session: Session, ephemeral_insights_collection
):
    """Plan §C: two different patients with near-identical candidate
    phrasing must produce two separate PatientInsight rows, never one
    shared/updated row — the patient_id vector-filter invariant. Both
    candidates embed to the EXACT same vector (a stand-in for "as
    semantically similar as it gets") so a missing patient_id filter would
    make this an obvious match; asserting the merge LLM is never called is
    what proves the filter actually fired, not just that two ids differ."""
    patient_a = _make_patient(session, email="iso-patient-a@diab.example.com")
    patient_b = _make_patient(session, email="iso-patient-b@diab.example.com")

    shared_text = "Đường huyết cao vào cuối tuần, không tập thể dục buổi tối."
    fake_embeddings = _LookupEmbeddingsClient({shared_text: [1.0, 0.0]})
    candidate = {"category": "glucose_pattern", "text": shared_text, "evidence": {}, "confidence": "medium"}

    with patch("codebase.models.agents.nodes.upsert_insight.get_llm") as mock_merge_llm:
        result_a = await upsert_insight(
            session,
            patient_id=patient_a.id,
            source_kind="weekly_batch",
            candidate=candidate,
            collection=ephemeral_insights_collection,
            embeddings_client=fake_embeddings,
        )
        result_b = await upsert_insight(
            session,
            patient_id=patient_b.id,
            source_kind="weekly_batch",
            candidate=candidate,
            collection=ephemeral_insights_collection,
            embeddings_client=fake_embeddings,
        )

    mock_merge_llm.assert_not_called()  # neither call should ever reach the merge/update path
    assert result_a["id"] != result_b["id"]
    assert result_a["version"] == 1
    assert result_b["version"] == 1
    assert result_a["patient_id"] == patient_a.id
    assert result_b["patient_id"] == patient_b.id

    rows = session.exec(select(PatientInsight)).all()
    assert len(rows) == 2
    assert {row.patient_id for row in rows} == {patient_a.id, patient_b.id}


@pytest.mark.asyncio
async def test_isolation_appointment_and_weekly_batch_stay_separate_rows(
    session: Session, ephemeral_insights_collection
):
    """Plan §B/§C: one patient's same-week appointment-triggered and
    weekly-batch-triggered candidates describing the same underlying
    pattern must also remain two separate rows, different source_kind — the
    source_kind vector-filter invariant (different audience/trust level:
    doctor-facing vs. patient-facing, an unreviewed weekly candidate must
    never silently overwrite a doctor-confirmed appointment insight or vice
    versa)."""
    patient = _make_patient(session, email="iso-source-kind@diab.example.com")

    shared_text = "Đường huyết cao vào cuối tuần, không tập thể dục buổi tối."
    fake_embeddings = _LookupEmbeddingsClient({shared_text: [1.0, 0.0]})
    candidate = {"category": "glucose_pattern", "text": shared_text, "evidence": {}, "confidence": "medium"}

    with patch("codebase.models.agents.nodes.upsert_insight.get_llm") as mock_merge_llm:
        appointment_result = await upsert_insight(
            session,
            patient_id=patient.id,
            source_kind="appointment",
            candidate=candidate,
            collection=ephemeral_insights_collection,
            embeddings_client=fake_embeddings,
        )
        weekly_result = await upsert_insight(
            session,
            patient_id=patient.id,
            source_kind="weekly_batch",
            candidate=candidate,
            collection=ephemeral_insights_collection,
            embeddings_client=fake_embeddings,
        )

    mock_merge_llm.assert_not_called()
    assert appointment_result["id"] != weekly_result["id"]
    assert appointment_result["source_kind"] == "appointment"
    assert weekly_result["source_kind"] == "weekly_batch"
    assert appointment_result["version"] == 1
    assert weekly_result["version"] == 1

    rows = session.exec(select(PatientInsight).where(PatientInsight.patient_id == patient.id)).all()
    assert len(rows) == 2
    assert {row.source_kind for row in rows} == {InsightSourceKind.APPOINTMENT, InsightSourceKind.WEEKLY_BATCH}


# ---------------------------------------------------------------------------
# _parse_candidates — propose response parsing safety (nodes/reflect.py)
# ---------------------------------------------------------------------------


def test_parse_candidates_tolerates_markdown_fence():
    from codebase.models.agents.nodes.reflect import _parse_candidates

    raw = '```json\n[{"category": "glucose_pattern", "text": "abc", "confidence": "high"}]\n```'
    result = _parse_candidates(raw)
    assert len(result) == 1
    assert result[0]["evidence"] == {}  # missing evidence defaults to {}, not fabricated


def test_parse_candidates_returns_empty_list_on_malformed_json():
    from codebase.models.agents.nodes.reflect import _parse_candidates

    assert _parse_candidates("not json at all") == []


def test_parse_candidates_drops_items_missing_text_or_category():
    from codebase.models.agents.nodes.reflect import _parse_candidates

    raw = json.dumps(
        [
            {"category": "glucose_pattern", "text": "", "confidence": "high"},  # empty text
            {"text": "no category here", "confidence": "high"},  # missing category
            {"category": "ok", "text": "kept", "confidence": "bogus"},  # invalid confidence -> defaults to low
        ]
    )
    result = _parse_candidates(raw)
    assert len(result) == 1
    assert result[0]["text"] == "kept"
    assert result[0]["confidence"] == "low"


# ---------------------------------------------------------------------------
# source_kind="chat" — the gather-from-chat-transcript path (plan Part 3 of
# /Users/phamminh/.claude/plans/memoized-churning-patterson.md). Uses the
# real checkpointer against the in-memory SQLite `session` fixture (same
# engine ChatSession rows are read from) rather than mocking it, so the test
# exercises the actual thread_id round-trip, not just a mocked call.
# ---------------------------------------------------------------------------

CHAT_CANDIDATE_TEXT = "Bạn hay lo lắng về việc đường huyết tăng sau khi ăn tối muộn."


def _chat_propose_response() -> str:
    return json.dumps(
        [
            {
                "category": "emotional_pattern",
                "text": CHAT_CANDIDATE_TEXT,
                "evidence": {
                    "date_range": ["2026-07-10", "2026-07-16"],
                    "excerpt": "tôi hay lo là đường huyết tăng nếu ăn tối trễ",
                },
                "confidence": "medium",
            }
        ],
        ensure_ascii=False,
    )


async def _seed_self_chat_session(session: Session, patient_id, *, updated_at: datetime) -> None:
    """A patient's own self-chat ChatSession with one real turn persisted
    through the actual checkpointer — not a mock — so
    _gather_chat_transcript exercises the real thread_id round-trip."""
    from langchain_core.messages import AIMessage, HumanMessage

    from codebase.backend.agent.models import ChatSession
    from codebase.models.agents.checkpoint import checkpointer

    thread_id = str(uuid.uuid4())
    session.add(
        ChatSession(thread_id=thread_id, patient_id=patient_id, counterpart_id=patient_id, updated_at=updated_at)
    )
    session.commit()

    config = {"configurable": {"thread_id": thread_id, "session": session}}
    checkpoint = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "ts": updated_at.isoformat(),
        "channel_values": {
            "messages": [
                HumanMessage(content="tôi hay lo là đường huyết tăng nếu ăn tối trễ"),
                AIMessage(content="Bạn có thể thử ăn tối sớm hơn và theo dõi thêm nhé."),
            ]
        },
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
        "pending_sends": [],
    }
    await checkpointer.aput(config, checkpoint, {}, {"messages": "1"})


@pytest.mark.asyncio
async def test_reflect_chat_source_kind_gathers_transcript_and_creates_insight(
    session: Session, ephemeral_insights_collection
):
    """source_kind="chat" skips get_health_records/calculate_health_metrics
    entirely and instead grounds the propose call in the patient's own
    self-chat transcript, still going through the normal create path."""
    patient = _make_patient(session, email="chat-reflect-patient@diab.example.com")
    period_start = datetime(2026, 7, 10)
    period_end = datetime(2026, 7, 17)
    await _seed_self_chat_session(session, patient.id, updated_at=period_start + timedelta(days=1))

    fake_embeddings = _LookupEmbeddingsClient({CHAT_CANDIDATE_TEXT: [1.0, 0.0]})
    mock_llm = _fake_llm_returning(_chat_propose_response())

    with (
        patch("codebase.models.agents.nodes.reflect.get_llm", return_value=mock_llm),
        patch(
            "codebase.models.agents.nodes.upsert_insight.get_insights_collection",
            return_value=ephemeral_insights_collection,
        ),
        patch("codebase.models.agents.nodes.upsert_insight.get_embeddings_client", return_value=fake_embeddings),
    ):
        results = await reflect(
            patient.id, period_start, period_end, note_text=None, source_kind="chat", session=session
        )

    assert len(results) == 1
    assert results[0]["source_kind"] == "chat"
    assert results[0]["category"] == "emotional_pattern"

    sent_prompt = mock_llm.ainvoke.call_args[0][0][0]["content"]
    assert "tôi hay lo là đường huyết tăng nếu ăn tối trễ" in sent_prompt  # the actual transcript, not a summary


@pytest.mark.asyncio
async def test_reflect_chat_source_kind_ignores_doctor_thread_about_patient(
    session: Session, ephemeral_insights_collection
):
    """A doctor's own working thread about this patient (counterpart_id !=
    patient_id) must never feed the chat-derived pipeline — only the
    patient's own self-chat is in scope for consent-gated mining."""
    from langchain_core.messages import HumanMessage

    from codebase.backend.agent.models import ChatSession
    from codebase.models.agents.checkpoint import checkpointer

    patient = _make_patient(session, email="chat-reflect-doctor-thread@diab.example.com")
    doctor = User(
        email="chat-reflect-doctor@diab.example.com",
        full_name="doctor",
        role=UserRole.DOCTOR,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(doctor)
    session.commit()

    period_start = datetime(2026, 7, 10)
    period_end = datetime(2026, 7, 17)
    thread_id = str(uuid.uuid4())
    session.add(
        ChatSession(
            thread_id=thread_id,
            patient_id=patient.id,
            counterpart_id=doctor.id,
            updated_at=period_start + timedelta(days=1),
        )
    )
    session.commit()
    config = {"configurable": {"thread_id": thread_id, "session": session}}
    checkpoint = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "ts": (period_start + timedelta(days=1)).isoformat(),
        "channel_values": {"messages": [HumanMessage(content="Bệnh nhân này có nên đổi liều Metformin không?")]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
        "pending_sends": [],
    }
    await checkpointer.aput(config, checkpoint, {}, {"messages": "1"})

    mock_llm = _fake_llm_returning("[]")
    with patch("codebase.models.agents.nodes.reflect.get_llm", return_value=mock_llm):
        results = await reflect(
            patient.id, period_start, period_end, note_text=None, source_kind="chat", session=session
        )

    assert results == []
    sent_prompt = mock_llm.ainvoke.call_args[0][0][0]["content"]
    assert "Metformin" not in sent_prompt  # doctor thread content never reached the prompt

"""Smoke tests for the respond-agent graph shape (permission gate + routing),
independent of any one workstream's node logic. Feature-specific tests
(summary quality, RAG citations, jailbreak resistance, ...) belong in their
own test files per docs/AI_Summary_Respond_System_Design.md §11 — this file
only asserts the scaffold itself holds together.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.models.agents.graph import agent

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_patient_defaults_to_own_patient_id(session: Session):
    patient = _make_user(session, email="p1@diab.example.com", role=UserRole.PATIENT)

    result = await agent.ainvoke(
        {
            "user_id": patient.id,
            "user_role": "patient",
            "patient_id": None,
            "request_kind": "summary",
            "date_range_days": 7,
            "message": "",
        },
        config={"configurable": {"session": session, "thread_id": "test-thread"}},
    )

    assert result.get("error") is None


@pytest.mark.asyncio
async def test_doctor_without_patient_id_is_denied_not_guessed(session: Session):
    doctor = _make_user(session, email="d1@diab.example.com", role=UserRole.DOCTOR)

    result = await agent.ainvoke(
        {
            "user_id": doctor.id,
            "user_role": "doctor",
            "patient_id": None,
            "request_kind": "summary",
            "date_range_days": 7,
            "message": "",
        },
        config={"configurable": {"session": session, "thread_id": "test-thread"}},
    )

    assert result.get("error") == "patient_id_required"
    assert "patient_id" in result.get("response", "") or "bệnh nhân" in result.get("response", "")


@pytest.mark.asyncio
async def test_unassigned_doctor_is_denied(session: Session):
    doctor = _make_user(session, email="d2@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="p2@diab.example.com", role=UserRole.PATIENT)

    result = await agent.ainvoke(
        {
            "user_id": doctor.id,
            "user_role": "doctor",
            "patient_id": patient.id,
            "request_kind": "summary",
            "date_range_days": 7,
            "message": "",
        },
        config={"configurable": {"session": session, "thread_id": "test-thread"}},
    )

    assert result.get("error") == "forbidden"


@pytest.mark.asyncio
async def test_patient_cannot_view_another_patient(session: Session):
    patient = _make_user(session, email="p3@diab.example.com", role=UserRole.PATIENT)
    other_patient = _make_user(session, email="p4@diab.example.com", role=UserRole.PATIENT)

    result = await agent.ainvoke(
        {
            "user_id": patient.id,
            "user_role": "patient",
            "patient_id": other_patient.id,
            "request_kind": "summary",
            "date_range_days": 7,
            "message": "",
        },
        config={"configurable": {"session": session, "thread_id": "test-thread"}},
    )

    assert result.get("error") == "forbidden"


# ---------------------------------------------------------------------------
# guardrail_check <-> synthesize_dispatch retry loop (graph.py's
# _after_guardrail routing, owned centrally by this scaffold — see that
# file's docstring). Regression coverage for two bugs found in review:
#
# 1. _after_guardrail compared retry_count with `<` against
#    _MAX_GUARDRAIL_RETRIES; since guardrail_check returns the *post*-
#    increment retry_count, `1 < 1` is already False on the very first
#    failure — the intended one retry never fired, and an unrevised unsafe
#    draft (e.g. diagnosis phrasing) shipped straight to the user with
#    error="guardrail_failed" left dangling, unconverted to any HTTP-level
#    signal. Fixed to `<=`.
# 2. guardrail_check returned `{}` on a pass, which does not clear a stale
#    error="guardrail_failed" left by an earlier failed attempt on the same
#    request (LangGraph merges partial node returns, it doesn't reset unset
#    keys) — a fail-then-pass retry would loop back through
#    synthesize_dispatch indefinitely instead of terminating. Fixed by
#    guardrail_check explicitly clearing error/guardrail_failures on pass.
# ---------------------------------------------------------------------------


def _diabetes_scope_message() -> str:
    return "Chỉ số đường huyết sau ăn bao nhiêu là an toàn?"


def _fake_llm_with_responses(*contents: str) -> AsyncMock:
    call_count = {"n": 0}

    async def _ainvoke(messages):
        idx = min(call_count["n"], len(contents) - 1)
        call_count["n"] += 1
        response = AsyncMock()
        response.content = contents[idx]
        return response

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = _ainvoke
    mock_llm.call_count = call_count
    return mock_llm


def _mock_verify_citations_llm() -> AsyncMock:
    """Static stand-in for nodes/verify_citations.py's own LLM call — these
    retry-loop tests assert on synthesize_qa's mock_llm.call_count, not on
    citations, so reporting the single seeded chunk ("t1-x") as used is a
    safe, uninteresting default. Reporting "nothing used" here would zero out
    state["sources"] regardless of what actually got cited and spuriously
    trip guardrail_check's uncited-retrieval-drift check on an otherwise-safe
    retry — a false failure this helper must not introduce into a test that
    isn't about citations at all."""
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content='{"used_chunk_ids": ["t1-x"]}')
    return mock_llm


@pytest.mark.asyncio
async def test_guardrail_retry_fires_once_then_ships_fallback(session: Session):
    """A persistently-unsafe draft (diagnosis phrasing on both attempts) must
    trigger exactly one retry (synthesize called twice), then ship the
    canned safe fallback — never the unsafe draft itself."""
    patient = _make_user(session, email="retry-fail@diab.example.com", role=UserRole.PATIENT)
    mock_llm = _fake_llm_with_responses("Bạn bị đái tháo đường tuýp 2 rồi đó.")

    with (
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge") as mock_search,
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", return_value=mock_llm),
        # verify_citations (a separate LLM pass, runs after synthesize_dispatch
        # on every QA turn including retries) needs its own mock — unrelated to
        # this test's retry-count assertion, which is about synthesize_qa's own
        # mock_llm specifically, so a static "nothing used" response is fine.
        patch("codebase.models.agents.nodes.verify_citations.get_llm", return_value=_mock_verify_citations_llm()),
    ):
        mock_search.return_value = [
            {
                "chunk_id": "t1-x",
                "text": "HbA1c mục tiêu <7%.",
                "title": "X",
                "section": None,
                "page_ref": None,
                "source_url": None,
                "tier": 1,
                "trust_tier": "Bộ Y tế",
                "score": 0.9,
            }
        ]
        result = await agent.ainvoke(
            {
                "user_id": patient.id,
                "user_role": "patient",
                "patient_id": patient.id,
                "request_kind": "chat",
                "message": _diabetes_scope_message(),
            },
            config={"configurable": {"session": session, "thread_id": "test-thread"}},
        )

    assert mock_llm.call_count["n"] == 2  # original attempt + exactly one retry
    assert "bị đái tháo đường" not in result.get("response", "")


@pytest.mark.asyncio
async def test_guardrail_retry_succeeds_ships_corrected_response_without_looping(session: Session):
    """First draft fails (diagnosis phrasing), retry produces a safe draft —
    must ship the corrected response after exactly one retry, with no
    dangling error, and must not loop back through synthesize again."""
    patient = _make_user(session, email="retry-pass@diab.example.com", role=UserRole.PATIENT)
    mock_llm = _fake_llm_with_responses(
        "Bạn bị đái tháo đường tuýp 2 rồi đó.",
        "Theo Bộ Y tế, mục tiêu HbA1c là dưới 7%.",
    )

    with (
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge") as mock_search,
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", return_value=mock_llm),
        patch("codebase.models.agents.nodes.verify_citations.get_llm", return_value=_mock_verify_citations_llm()),
    ):
        mock_search.return_value = [
            {
                "chunk_id": "t1-x",
                "text": "HbA1c mục tiêu dưới 7%.",
                "title": "X",
                "section": None,
                "page_ref": None,
                "source_url": None,
                "tier": 1,
                "trust_tier": "Bộ Y tế",
                "score": 0.9,
            }
        ]
        result = await agent.ainvoke(
            {
                "user_id": patient.id,
                "user_role": "patient",
                "patient_id": patient.id,
                "request_kind": "chat",
                "message": _diabetes_scope_message(),
            },
            config={"configurable": {"session": session, "thread_id": "test-thread"}},
        )

    assert mock_llm.call_count["n"] == 2  # original attempt + one retry, then stop (no loop)
    assert result.get("response") == "Theo Bộ Y tế, mục tiêu HbA1c là dưới 7%."
    assert result.get("error") is None


@pytest.mark.asyncio
async def test_advice_retry_is_told_why_the_draft_was_rejected(session: Session):
    """Golden eval 2026-10-29 (TC-AI-030): a knowledge answer phrased as advice
    ("bạn nên dùng glucose") was rejected, and the blind retry phrased it as
    advice again — the patient got the generic fallback. The retry prompt must
    carry the reason, and only on that retry."""
    patient = _make_user(session, email="advice-retry@diab.example.com", role=UserRole.PATIENT)
    prompts: list[str] = []
    drafts = iter(
        [
            "Bạn nên dùng glucose khi bị hạ đường huyết.",
            "Theo Bộ Y tế, người bệnh bị hạ đường huyết cần dùng glucose.",
        ]
    )

    async def _ainvoke(messages):
        prompts.append(messages[-1]["content"])
        return AsyncMock(content=next(drafts))

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = _ainvoke
    with (
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge") as mock_search,
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", return_value=mock_llm),
        patch("codebase.models.agents.nodes.verify_citations.get_llm", return_value=_mock_verify_citations_llm()),
    ):
        mock_search.return_value = [
            {
                "chunk_id": "t1-x",
                "text": "Người bệnh bị hạ đường huyết cần dùng glucose.",
                "title": "X",
                "section": None,
                "page_ref": None,
                "source_url": None,
                "tier": 1,
                "trust_tier": "Bộ Y tế",
                "score": 0.9,
            }
        ]
        result = await agent.ainvoke(
            {
                "user_id": patient.id,
                "user_role": "patient",
                "patient_id": patient.id,
                "request_kind": "chat",
                "message": "Hạ đường huyết thì dùng gì?",
            },
            config={"configurable": {"session": session, "thread_id": "test-thread"}},
        )

    assert len(prompts) == 2
    assert "BẢN NHÁP TRƯỚC BỊ TỪ CHỐI" not in prompts[0]
    assert "BẢN NHÁP TRƯỚC BỊ TỪ CHỐI" in prompts[1]
    assert result.get("response") == "Theo Bộ Y tế, người bệnh bị hạ đường huyết cần dùng glucose."

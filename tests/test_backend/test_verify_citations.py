"""Unit tests for nodes/verify_citations.py — the LLM-based citation checker
that runs after synthesize_qa.py and narrows state["sources"]/["data_used"]
down to the chunks the drafted answer actually used, without ever touching
state["response"] itself.

Node-level tests (call verify_citations(state) directly), same style as
test_agent_guardrails.py — no HTTP/DB needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from codebase.models.agents.nodes.synthesize_qa import UNCONFIRMED_GROUNDING_LIMITATION
from codebase.models.agents.nodes.verify_citations import verify_citations

_GLYCEMIC_TARGET_CHUNK = {
    "chunk_id": "t1-a3f9c2e1",
    "text": "HbA1c <7% (53 mmol/mol). Glucose mao mạch lúc đói/trước ăn: 80-130 mg/dL.",
    "title": "Quyết định 5481/QĐ-BYT - Bảng 4: Mục tiêu điều trị",
    "section": "Bảng 4",
    "page_ref": "tr. 19",
    "source_url": "https://benhviendakhoabaria.vn/.../5481.pdf",
    "tier": 1,
    "trust_tier": "Bộ Y tế",
    "score": 0.91,
}
_FOOT_CARE_CHUNK = {
    "chunk_id": "t2-foot-care",
    "text": "Hướng dẫn chăm sóc bàn chân: rửa chân, lau khô, kiểm tra vết loét mỗi ngày.",
    "title": "Chăm sóc bàn chân",
    "section": None,
    "page_ref": None,
    "source_url": "https://vade.org.vn/ban-chan",
    "tier": 2,
    "trust_tier": "Bệnh viện",
    "score": 0.35,
}


def _mock_llm_returning(content: str) -> AsyncMock:
    response = AsyncMock()
    response.content = content
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = response
    return mock_llm


def _used(*chunk_ids: str) -> str:
    return json.dumps({"used_chunk_ids": list(chunk_ids)})


@pytest.mark.asyncio
async def test_non_qa_intent_is_noop():
    state = {"intent": "SUMMARY", "response": "x", "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK]}
    assert await verify_citations(state) == {}


@pytest.mark.asyncio
async def test_no_retrieved_chunks_is_noop():
    state = {"intent": "QA", "response": "Mục tiêu HbA1c là dưới 7%.", "retrieved_chunks": []}
    assert await verify_citations(state) == {}


@pytest.mark.asyncio
async def test_empty_response_is_noop():
    state = {"intent": "QA", "response": "", "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK]}
    assert await verify_citations(state) == {}


@pytest.mark.asyncio
async def test_marks_only_the_chunk_actually_used(monkeypatch):
    """Two chunks were retrieved, only one was actually cited in the drafted
    answer — the exact over-inclusion bug this node exists to fix (a looser
    upstream heuristic could have kept both as `sources`)."""
    mock_llm = _mock_llm_returning(_used(_GLYCEMIC_TARGET_CHUNK["chunk_id"]))
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Theo Bộ Y tế, mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK, _FOOT_CARE_CHUNK],
    }
    result = await verify_citations(state)

    assert len(result["sources"]) == 1
    assert result["sources"][0]["title"] == _GLYCEMIC_TARGET_CHUNK["title"]
    assert result["data_used"] == [_GLYCEMIC_TARGET_CHUNK["title"]]
    assert result["limitations"] == []


@pytest.mark.asyncio
async def test_drops_a_hallucinated_chunk_id_not_in_the_retrieved_set(monkeypatch):
    """Never trust the verifier's chunk_id list beyond what we actually sent
    it — a chunk_id it invents (or a stale one from elsewhere) must not turn
    into a fabricated source."""
    mock_llm = _mock_llm_returning(_used(_GLYCEMIC_TARGET_CHUNK["chunk_id"], "not-a-real-chunk-id"))
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
    }
    result = await verify_citations(state)

    assert len(result["sources"]) == 1
    assert result["sources"][0]["title"] == _GLYCEMIC_TARGET_CHUNK["title"]


@pytest.mark.asyncio
async def test_no_chunk_used_and_not_personal_data_grounded_adds_limitation(monkeypatch):
    mock_llm = _mock_llm_returning(_used())
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Câu trả lời không thực sự dựa trên đoạn nào cả.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
        "personal_data_grounded": False,
    }
    result = await verify_citations(state)

    assert result["sources"] == []
    assert result["limitations"] == [UNCONFIRMED_GROUNDING_LIMITATION]


@pytest.mark.asyncio
async def test_no_chunk_used_but_personal_data_grounded_has_no_limitation(monkeypatch):
    """A personal-data answer (e.g. "what medications am I on") legitimately
    cites zero knowledge-base chunks — must not read as an unconfirmed
    citation just because RAG chunks happened to also be retrieved."""
    mock_llm = _mock_llm_returning(_used())
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Bạn đang dùng Metformin 500mg.",
        "retrieved_chunks": [_FOOT_CARE_CHUNK],
        "personal_data_grounded": True,
    }
    result = await verify_citations(state)

    assert result["sources"] == []
    assert result["limitations"] == []


@pytest.mark.asyncio
async def test_markdown_code_fenced_json_is_still_parsed(monkeypatch):
    fenced = "```json\n" + _used(_GLYCEMIC_TARGET_CHUNK["chunk_id"]) + "\n```"
    mock_llm = _mock_llm_returning(fenced)
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
    }
    result = await verify_citations(state)

    assert len(result["sources"]) == 1


@pytest.mark.asyncio
async def test_malformed_llm_output_falls_back_to_noop(monkeypatch):
    """Any output shape the model wasn't supposed to produce must never
    block or alter the answer — this node is a strictly-additive precision
    pass, so on failure it leaves whatever synthesize_qa.py already computed
    untouched."""
    mock_llm = _mock_llm_returning("not json at all")
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
        "sources": [{"title": "đã có từ synthesize_qa.py"}],
    }
    assert await verify_citations(state) == {}


@pytest.mark.asyncio
async def test_duplicate_chunks_from_the_same_document_collapse_to_one_source(monkeypatch):
    """Two different chunks of the same document (different chunk_id/page_ref,
    same title/section/source_url) can both independently be judged "used" —
    the frontend only ever renders title/section/source_url (never page_ref),
    so both would otherwise show up as the same-looking source listed twice."""
    other_page_of_same_doc = {**_GLYCEMIC_TARGET_CHUNK, "chunk_id": "t1-other-page", "page_ref": "tr. 42"}
    mock_llm = _mock_llm_returning(_used(_GLYCEMIC_TARGET_CHUNK["chunk_id"], other_page_of_same_doc["chunk_id"]))
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK, other_page_of_same_doc],
    }
    result = await verify_citations(state)

    assert len(result["sources"]) == 1
    assert result["data_used"] == [_GLYCEMIC_TARGET_CHUNK["title"]]


@pytest.mark.asyncio
async def test_llm_call_raising_falls_back_to_noop(monkeypatch):
    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = RuntimeError("network error")
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_args, **_kwargs: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
    }
    assert await verify_citations(state) == {}


# ---------------------------------------------------------------------------
# Epic 5.4: synthesize_qa.py reads `used_chunk_ids` from its own drafting call,
# cites only declared AND overlap-grounded chunks, and verify_citations skips.
# ---------------------------------------------------------------------------


def _qa_state(chunks: list[dict]) -> dict:
    return {
        "intent": "QA",
        "message": "Mục tiêu HbA1c và đường huyết lúc đói là bao nhiêu?",
        "retrieved_chunks": chunks,
        "messages": [],
        "user_role": "patient",
    }


_GROUNDED_ANSWER = (
    "Theo Bộ Y tế, mục tiêu HbA1c là dưới 7% (53 mmol/mol); glucose mao mạch lúc đói/trước ăn "
    "nên ở mức 80-130 mg/dL. Bạn cũng nên rửa chân, lau khô và kiểm tra vết loét mỗi ngày."
)


@pytest.mark.asyncio
async def test_self_reported_ids_narrow_sources_and_skip_verify(monkeypatch):
    """Both chunks overlap the answer, but the model declared only one — only
    that one is cited, and verify_citations makes no LLM call."""
    from codebase.models.agents.nodes.synthesize_qa import synthesize_qa

    raw = json.dumps(
        {"answer": _GROUNDED_ANSWER, "used_chunk_ids": [_GLYCEMIC_TARGET_CHUNK["chunk_id"]], "record_candidates": []},
        ensure_ascii=False,
    )
    monkeypatch.setattr(
        "codebase.models.agents.nodes.synthesize_qa.get_llm", lambda *_a, **_k: _mock_llm_returning(raw)
    )
    state = _qa_state([_GLYCEMIC_TARGET_CHUNK, _FOOT_CARE_CHUNK])
    result = await synthesize_qa(state)

    assert result["citations_self_reported"] is True
    assert [s["title"] for s in result["sources"]] == [_GLYCEMIC_TARGET_CHUNK["title"]]

    verify_llm = _mock_llm_returning(_used(_FOOT_CARE_CHUNK["chunk_id"]))
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_a, **_k: verify_llm)
    assert await verify_citations({**state, **result}) == {}
    verify_llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_reported_id_without_grounding_is_not_cited(monkeypatch):
    """A declared chunk whose content isn't in the answer, plus an id that was
    never retrieved, are both dropped."""
    from codebase.models.agents.nodes.synthesize_qa import synthesize_qa

    raw = json.dumps(
        {
            "answer": "Mục tiêu HbA1c là dưới 7% (53 mmol/mol), đường huyết lúc đói 80-130 mg/dL.",
            "used_chunk_ids": [_FOOT_CARE_CHUNK["chunk_id"], "t9-invented"],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(
        "codebase.models.agents.nodes.synthesize_qa.get_llm", lambda *_a, **_k: _mock_llm_returning(raw)
    )
    result = await synthesize_qa(_qa_state([_GLYCEMIC_TARGET_CHUNK, _FOOT_CARE_CHUNK]))
    assert result["citations_self_reported"] is True
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_plain_text_draft_keeps_overlap_citations_and_runs_verify(monkeypatch):
    from codebase.models.agents.nodes.synthesize_qa import synthesize_qa

    monkeypatch.setattr(
        "codebase.models.agents.nodes.synthesize_qa.get_llm",
        lambda *_a, **_k: _mock_llm_returning(_GROUNDED_ANSWER),
    )
    state = _qa_state([_GLYCEMIC_TARGET_CHUNK, _FOOT_CARE_CHUNK])
    result = await synthesize_qa(state)
    assert result["citations_self_reported"] is False
    assert len(result["sources"]) == 2

    verify_llm = _mock_llm_returning(_used(_GLYCEMIC_TARGET_CHUNK["chunk_id"]))
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda *_a, **_k: verify_llm)
    verified = await verify_citations({**state, **result})
    verify_llm.ainvoke.assert_awaited_once()
    assert [s["title"] for s in verified["sources"]] == [_GLYCEMIC_TARGET_CHUNK["title"]]


def test_declared_ids_tolerate_echoed_label_and_mistyped_tier_prefix():
    """Seen live: the model echoes the prompt's "chunk_id=" label, or writes
    the right hash under the wrong tier prefix. Unknown hashes still match nothing."""
    from codebase.models.agents.nodes.synthesize_qa import _chunks_matching_ids

    chunks = [_GLYCEMIC_TARGET_CHUNK, _FOOT_CARE_CHUNK]
    assert _chunks_matching_ids({"chunk_id=t1-a3f9c2e1"}, chunks) == [_GLYCEMIC_TARGET_CHUNK]
    assert _chunks_matching_ids({" t6-a3f9c2e1 "}, chunks) == [_GLYCEMIC_TARGET_CHUNK]
    assert _chunks_matching_ids({"t2-foot-care"}, chunks) == [_FOOT_CARE_CHUNK]
    assert _chunks_matching_ids({"t1-deadbeef", "a3f9"}, chunks) == []

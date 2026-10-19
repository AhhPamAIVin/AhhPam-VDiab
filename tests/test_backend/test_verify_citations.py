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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

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
    monkeypatch.setattr("codebase.models.agents.nodes.verify_citations.get_llm", lambda: mock_llm)

    state = {
        "intent": "QA",
        "response": "Mục tiêu HbA1c là dưới 7%.",
        "retrieved_chunks": [_GLYCEMIC_TARGET_CHUNK],
    }
    assert await verify_citations(state) == {}

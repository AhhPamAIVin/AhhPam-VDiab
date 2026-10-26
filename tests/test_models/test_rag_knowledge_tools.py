"""Unit tests for codebase/models/agents/tools/knowledge_tools.py —
search_approved_knowledge and its helpers.

Gap flagged by the RAG workstream's own report: chunking.py and ingest.py had
real coverage, but knowledge_tools.py only had manual, throwaway verification
— the tests that reference search_approved_knowledge in test_agent_chat.py /
test_agent_guardrails.py all mock it out entirely, so nothing here actually
locked in the implementation. This file exercises the real function.

Same technique as test_rag_ingest.py: a real in-memory Qdrant collection
(qdrant-client `:memory:` mode), no server/network/model required. Unlike run_ingestion, though,
search_approved_knowledge() has no embeddings_client/collection parameters of
its own (it calls get_embeddings_client()/get_collection() directly) — so
these tests monkeypatch those two module-level names instead of passing
fakes in as arguments.

Every test gets its own in-memory client, so tests never see each other's
collections.
"""

from __future__ import annotations

import json
import logging
import math
import uuid

import pytest

from codebase.models.agents.tools import knowledge_tools as kt

# Metadata shapes mirror what codebase/models/rag/chunking.py actually
# produces — a tier-1 chunk built from a sources.csv row, and a tier-2 chunk
# from a raw/*.md file. No `None` values: ingest.py drops None-valued keys
# before writing, so the fixtures leave them out too.
TIER1_META = {
    "tier": 1,
    "document_title": "Quyết định 5481/QĐ-BYT - Bảng 4: Mục tiêu điều trị",
    "category": "Điều trị & chỉ số đường huyết",
    "page_ref": "tr. 19",
    "trust_tier": "Bộ Y tế",
    "source_url": "https://example.org/5481.pdf",
}

TIER2_META = {
    "tier": 2,
    "doc_title": "Hướng dẫn chẩn đoán và điều trị đái tháo đường típ 2",
    "heading_path": "PHẦN 4: ĐIỀU TRỊ › 2. Mục tiêu điều trị",
    "section_heading": "2. Mục tiêu điều trị",
    "page_start": 21,
    "page_end": 21,
    "source_url": "https://example.org/byt-5481.pdf",
}


class _FixedEmbeddingsClient:
    """Stand-in for the local embedding model — returns the same,
    caller-chosen query vector regardless of the query text, so tests can
    control the exact cosine similarity to pre-inserted chunk vectors instead
    of depending on real semantic embeddings."""

    def __init__(self, vector: tuple[float, float]) -> None:
        self._vector = list(vector)

    def embed_query(self, text: str) -> list[float]:
        return list(self._vector)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("search_approved_knowledge must not call embed_documents")


@pytest.fixture
def ephemeral_collection():
    """A fresh, isolated in-memory Qdrant collection per test."""
    from codebase.models.rag.vector_store import QdrantVectorClient

    return QdrantVectorClient(location=":memory:").get_or_create_collection(f"test-{uuid.uuid4().hex}")


def _vector_with_similarity(similarity: float) -> list[float]:
    """A 2D unit vector whose cosine similarity to the fixed query vector
    [1.0, 0.0] is exactly `similarity`."""
    return [similarity, math.sqrt(max(0.0, 1.0 - similarity**2))]


def _insert(collection, *, chunk_id: str, similarity: float, metadata: dict, text: str = "nội dung mẫu") -> None:
    collection.upsert(
        ids=[chunk_id],
        embeddings=[_vector_with_similarity(similarity)],
        documents=[text],
        metadatas=[metadata],
    )


def _patch_search_deps(monkeypatch, collection, query_vector: tuple[float, float] = (1.0, 0.0)) -> None:
    monkeypatch.setattr(kt, "get_collection", lambda: collection)
    monkeypatch.setattr(kt, "get_embeddings_client", lambda: _FixedEmbeddingsClient(query_vector))


# ---------------------------------------------------------------------------
# _distance_to_score — cosine-distance-to-score conversion
# ---------------------------------------------------------------------------


def test_distance_to_score_perfect_match_is_1():
    assert kt._distance_to_score(0.0) == 1.0


def test_distance_to_score_no_similarity_is_0():
    assert kt._distance_to_score(1.0) == 0.0


def test_distance_to_score_midpoint():
    assert kt._distance_to_score(0.25) == 0.75


def test_distance_to_score_clips_below_zero_distance():
    # Floating point can push a near-identical vector's distance a hair
    # negative — must clip to a score of 1.0, not >1.0.
    assert kt._distance_to_score(-0.1) == 1.0


def test_distance_to_score_clips_above_max_distance():
    assert kt._distance_to_score(1.5) == 0.0


# ---------------------------------------------------------------------------
# _to_retrieved_chunk — tier-1 vs tier-2 metadata mapping -> RetrievedChunk
# ---------------------------------------------------------------------------


def test_to_retrieved_chunk_tier1_shape():
    chunk = kt._to_retrieved_chunk("t1-a3f9c2e1", "nội dung", TIER1_META, 0.87)

    assert chunk == {
        "chunk_id": "t1-a3f9c2e1",
        "text": "nội dung",
        "source_url": TIER1_META["source_url"],
        "tier": 1,
        "score": 0.87,
        "title": TIER1_META["document_title"],
        "section": TIER1_META["category"],
        "page_ref": TIER1_META["page_ref"],
        "trust_tier": "Bộ Y tế",
    }


def test_to_retrieved_chunk_tier2_shape_maps_doc_title_heading_path_and_page():
    chunk = kt._to_retrieved_chunk("t2-7e21d0aa", "nội dung", TIER2_META, 0.75)

    assert chunk["title"] == TIER2_META["doc_title"]
    assert chunk["section"] == TIER2_META["heading_path"]
    assert chunk["page_ref"] == "tr. 21"
    assert chunk["tier"] == 2
    # No sources.csv row referenced this file -> no trust_tier; the key must
    # be absent, not fabricated as None (RetrievedChunk is total=False).
    assert "trust_tier" not in chunk


def test_to_retrieved_chunk_tier2_inherits_trust_tier_and_page_range():
    meta = {**TIER2_META, "trust_tier": "Bộ Y tế", "page_end": 22}
    chunk = kt._to_retrieved_chunk("t2-range", "nội dung", meta, 0.5)
    assert chunk["trust_tier"] == "Bộ Y tế"
    assert chunk["page_ref"] == "tr. 21–22"


def test_to_retrieved_chunk_tier2_missing_page_yields_none_page_ref():
    meta = {k: v for k, v in TIER2_META.items() if k not in ("page_start", "page_end")}
    chunk = kt._to_retrieved_chunk("t2-none", "nội dung", meta, 0.5)
    assert chunk["page_ref"] is None


# ---------------------------------------------------------------------------
# search_approved_knowledge — trust-tier boost reordering (§5.6)
# ---------------------------------------------------------------------------


def test_tier1_trust_boost_reorders_near_tied_result(monkeypatch, ephemeral_collection):
    """A tier-2 chunk with slightly higher raw similarity than a tier-1 chunk
    — within TIER1_TRUST_BOOST's margin — must be outranked by the tier-1
    hit: "tier-1 hits ranked with a trust boost over tier-2 hits at equal
    similarity" (§5.6)."""
    assert kt.TIER1_TRUST_BOOST == 0.03
    _insert(ephemeral_collection, chunk_id="t1-x", similarity=0.90, metadata=TIER1_META)
    _insert(ephemeral_collection, chunk_id="t2-y", similarity=0.92, metadata=TIER2_META)
    _patch_search_deps(monkeypatch, ephemeral_collection)

    ranked = kt.search_approved_knowledge("câu hỏi mẫu", k=2)

    assert [c["chunk_id"] for c in ranked] == ["t1-x", "t2-y"]
    # `score` on the returned chunk is always the raw, unboosted similarity —
    # the boost affects ranking order only, per the module's own docstring.
    assert ranked[0]["score"] == pytest.approx(0.90, abs=1e-6)
    assert ranked[1]["score"] == pytest.approx(0.92, abs=1e-6)


def test_trust_boost_does_not_override_a_real_relevance_gap(monkeypatch, ephemeral_collection):
    """The boost is small (0.03) specifically so it only reorders near-ties —
    a tier-2 hit that's genuinely far more relevant must still outrank a
    barely-relevant tier-1 hit."""
    _insert(ephemeral_collection, chunk_id="t1-low", similarity=0.40, metadata=TIER1_META)
    _insert(ephemeral_collection, chunk_id="t2-high", similarity=0.95, metadata=TIER2_META)
    _patch_search_deps(monkeypatch, ephemeral_collection)

    ranked = kt.search_approved_knowledge("câu hỏi mẫu", k=2)

    assert ranked[0]["chunk_id"] == "t2-high"


def test_search_respects_k_and_returns_boosted_score_order(monkeypatch, ephemeral_collection):
    for i, sim in enumerate([0.95, 0.85, 0.75, 0.65, 0.55]):
        _insert(ephemeral_collection, chunk_id=f"t2-{i}", similarity=sim, metadata=TIER2_META)
    _patch_search_deps(monkeypatch, ephemeral_collection)

    ranked = kt.search_approved_knowledge("câu hỏi mẫu", k=3)

    assert len(ranked) == 3
    scores = [c["score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)


def test_superseded_chunks_rank_after_current_guidance(monkeypatch, ephemeral_collection):
    """A superseded decision (3319) more similar to the query than current
    guidance (5481) still ranks below it — and is still returned when there
    is room, rather than dropped."""
    superseded = {**TIER2_META, "status": "superseded"}
    _insert(ephemeral_collection, chunk_id="t2-3319", similarity=0.95, metadata=superseded)
    _insert(ephemeral_collection, chunk_id="t2-5481", similarity=0.70, metadata={**TIER2_META, "status": "active"})
    _patch_search_deps(monkeypatch, ephemeral_collection)

    assert [c["chunk_id"] for c in kt.search_approved_knowledge("câu hỏi mẫu", k=2)] == ["t2-5481", "t2-3319"]
    assert [c["chunk_id"] for c in kt.search_approved_knowledge("câu hỏi mẫu", k=1)] == ["t2-5481"]


def test_query_tokens_drop_conversational_function_words():
    """ "tôi" passed the BM25 rarity gate and matched IWGDF's "chúng tôi
    khuyến cáo", pushing off-topic chunks above the right one."""
    assert kt._query_tokens("Khi bị hạ đường huyết tôi nên xử trí thế nào?") == ["đường", "huyết", "trí"]
    assert "glucid" in kt._query_tokens("Lượng chất bột đường (glucid) nên chiếm bao nhiêu?")


def test_stopword_in_query_does_not_let_bm25_outrank_dense_match(monkeypatch, ephemeral_collection):
    _insert(
        ephemeral_collection, chunk_id="t2-right", similarity=0.80, metadata=TIER2_META, text="hạ đường huyết điều trị"
    )
    for i in range(3):
        _insert(
            ephemeral_collection,
            chunk_id=f"t2-noise-{i}",
            similarity=0.60 - i * 0.01,
            metadata=TIER2_META,
            text="chúng tôi khuyến cáo chúng tôi đề nghị",
        )
    for i in range(40):  # make "đường"/"huyết" common so only "tôi" could be rare
        _insert(ephemeral_collection, chunk_id=f"t2-fill-{i}", similarity=0.1, metadata=TIER2_META, text="đường huyết")
    _patch_search_deps(monkeypatch, ephemeral_collection)

    ranked = kt.search_approved_knowledge("Khi bị hạ đường huyết tôi nên xử trí thế nào?", k=2)

    assert ranked[0]["chunk_id"] == "t2-right"


def test_search_returns_empty_list_for_empty_collection(monkeypatch, ephemeral_collection):
    _patch_search_deps(monkeypatch, ephemeral_collection)
    assert kt.search_approved_knowledge("bất kỳ câu hỏi nào") == []


# ---------------------------------------------------------------------------
# BM25 + RRF hybrid fusion (golden eval TC-AI-001 regression)
# ---------------------------------------------------------------------------


def test_bm25_promotes_lexically_exact_match_over_higher_cosine_distractor(monkeypatch, ephemeral_collection):
    """Regression test for golden eval TC-AI-001: a chunk that literally
    contains the query's distinctive, corpus-rare term ("glucid") but ranks
    well behind a dense-only distractor on raw cosine similarity must still
    be able to surface after hybrid fusion — verified live against the real
    corpus (the correct chunk for "glucid mỗi ngày" sat at raw cosine rank
    85 of 300, below the previous k=6/10 cutoff, purely because that chunk
    bundles many other topics and its embedding is diluted).

    Needs a realistically-sized corpus, not just the two chunks under test:
    _BM25_MAX_TERM_DOC_FREQ_RATIO gates BM25 on a term's document-frequency
    *ratio*, which is meaningless with only 2 documents (any shared term is
    trivially 50%+ "common"). The filler chunks below give "glucid" genuine
    rarity (1 of 12 chunks, same order of magnitude as the verified live
    case's 4 of ~1100) while "người"/"bệnh" — shared with the distractor,
    like the real "mỗi tuần" collision this test's git history hit — stay
    common enough to be correctly excluded from BM25 scoring, same as the
    TC-AI-005 regression this whole gate exists to prevent (see
    _BM25_MAX_TERM_DOC_FREQ_RATIO's docstring)."""
    _insert(
        ephemeral_collection,
        chunk_id="t2-distractor",
        similarity=0.90,
        metadata=TIER2_META,
        text="Người bệnh nên tập thể dục đều đặn hàng tuần để duy trì sức khỏe tốt.",
    )
    _insert(
        ephemeral_collection,
        chunk_id="t2-target",
        similarity=0.50,
        metadata=TIER2_META,
        text="Cần bao nhiêu glucid mỗi ngày là đủ cho người bệnh đái tháo đường.",
    )
    # 30 filler chunks so "glucid" (1/32 ≈ 3%) clears the rarity gate while
    # "người"/"bệnh"/"đái"/"tháo"/"đường" (shared with the distractor, 31/32
    # ≈ 97%) stay excluded — same shape as the live TC-AI-005 regression
    # this gate exists to prevent (see _BM25_MAX_TERM_DOC_FREQ_RATIO).
    for i in range(30):
        _insert(
            ephemeral_collection,
            chunk_id=f"t2-filler-{i}",
            similarity=0.30,
            metadata=TIER2_META,
            text=f"Người bệnh đái tháo đường cần theo dõi sức khỏe hằng ngày, mục filler số {i}.",
        )
    _patch_search_deps(monkeypatch, ephemeral_collection)

    ranked = kt.search_approved_knowledge("cần bao nhiêu glucid mỗi ngày", k=2)

    assert [c["chunk_id"] for c in ranked][0] == "t2-target"


def test_bm25_rarity_gate_excludes_common_terms():
    """Direct, focused test of _BM25_MAX_TERM_DOC_FREQ_RATIO's gate: a query
    term at or below the ratio (here "glucid", 1/20 = 5%) must still score;
    a term above it (here "đường", 19/20 = 95%) must be excluded entirely,
    even from documents that contain it — otherwise every one of the 19
    filler docs sharing only the common term would also get a nonzero BM25
    score, which is exactly the noise that pushed the correct chunk out of
    top-k for golden eval TC-AI-005's fully-generic query."""
    doc_tokens = {f"filler-{i}": ["đường", "chung"] for i in range(19)}
    doc_tokens["target"] = ["đường", "glucid"]

    scores = kt._bm25_scores(["đường", "glucid"], doc_tokens)

    assert set(scores) == {"target"}


def test_bm25_scores_are_zero_for_no_lexical_overlap():
    assert kt._bm25_scores(["glucid"], {"a": ["tập", "thể", "dục"], "b": ["ăn", "kiêng"]}) == {}


def test_rrf_merge_sums_reciprocal_ranks_across_lists():
    fused = kt._rrf_merge(["a", "b", "c"], ["b", "a"])
    # a and b each appear once at rank 1 and once at rank 2 across the two
    # lists (just swapped), so their reciprocal-rank sums are identical —
    # RRF is symmetric in that sense, not a bug in the merge itself.
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-9)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61, abs=1e-9)
    assert fused["c"] == pytest.approx(1 / 63, abs=1e-9)
    assert fused["a"] == pytest.approx(fused["b"], abs=1e-9)
    assert fused["a"] > fused["c"]


# ---------------------------------------------------------------------------
# §5.8 — structured retrieval log line
# ---------------------------------------------------------------------------


def test_search_logs_structured_retrieval_line(monkeypatch, ephemeral_collection, caplog):
    _insert(ephemeral_collection, chunk_id="t1-log", similarity=0.9, metadata=TIER1_META)
    _patch_search_deps(monkeypatch, ephemeral_collection)

    with caplog.at_level(logging.INFO, logger="codebase.models.agents.tools.knowledge_tools"):
        kt.search_approved_knowledge("metformin có dùng được khi suy thận không?", k=1)

    records = [r for r in caplog.records if r.name == "codebase.models.agents.tools.knowledge_tools"]
    assert len(records) == 1

    payload = json.loads(records[0].getMessage())
    assert payload["tool_name"] == "search_approved_knowledge"
    assert payload["tool_input"] == {"query": "metformin có dùng được khi suy thận không?", "k": 1}
    assert payload["embedding_model"] == kt.get_settings().embedding_model
    assert len(payload["tool_output"]) == 1

    entry = payload["tool_output"][0]
    assert entry["chunk_id"] == "t1-log"
    assert entry["tier"] == 1
    assert entry["rank"] == 1
    assert isinstance(entry["score"], float)
    assert isinstance(payload["latency_ms"], int)
    assert "timestamp" in payload


def test_search_logs_one_line_per_call_ranked_by_rank(monkeypatch, ephemeral_collection, caplog):
    _insert(ephemeral_collection, chunk_id="t1-first", similarity=0.95, metadata=TIER1_META)
    _insert(ephemeral_collection, chunk_id="t2-second", similarity=0.60, metadata=TIER2_META)
    _patch_search_deps(monkeypatch, ephemeral_collection)

    with caplog.at_level(logging.INFO, logger="codebase.models.agents.tools.knowledge_tools"):
        kt.search_approved_knowledge("câu hỏi mẫu", k=2)

    records = [r for r in caplog.records if r.name == "codebase.models.agents.tools.knowledge_tools"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    ranks = [(entry["chunk_id"], entry["rank"]) for entry in payload["tool_output"]]
    assert ranks == [("t1-first", 1), ("t2-second", 2)]

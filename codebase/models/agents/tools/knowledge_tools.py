"""search_approved_knowledge — RAG retrieval tool.

OWNER: Data-pipeline / RAG workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §5 (whole section) for the
two-tier corpus, chunking, and metadata each returned chunk must carry.
codebase/models/agents/nodes/retrieve_knowledge.py (Consultation workstream)
is the only caller — the return shape here is a contract with that node, see
RetrievedChunk in codebase/models/agents/state.py.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from codebase.backend.config import get_settings
from codebase.models.agents.state import RetrievedChunk
from codebase.models.rag.embeddings import get_embeddings_client
from codebase.models.rag.ingest import get_collection

logger = logging.getLogger("codebase.models.agents.tools.knowledge_tools")

# §5.6: "tier-1 hits ranked with a trust boost over tier-2 hits at equal
# similarity" — small enough to only reorder near-ties, not override a real
# relevance gap. Applied to ranking only; the `score` field on the returned
# chunk is always the raw, unboosted cosine similarity.
TIER1_TRUST_BOOST = 0.03

# Pull a wider candidate pool than k from the vector store so the trust-tier boost has
# room to actually reorder near-ties instead of just re-sorting the same k,
# and so BM25 below (which can only rank chunks it's actually given) has a
# real shot at surfacing a lexically-strong match dense retrieval buried
# deep. Real, verified case (golden eval TC-AI-001, "chất bột đường
# (glucid)"): the chunk with the exact figures the answer needed sat at raw
# cosine rank 85 of 300 — the corpus is small (~1100 chunks as of writing),
# so fetching this deep is still a fast ANN search, not an
# extra embeddings API call, which is the only genuinely expensive part of
# this function.
OVERFETCH_MULTIPLIER = 3
OVERFETCH_MIN_EXTRA = 4
_MIN_CANDIDATE_POOL = 300

# Hybrid retrieval: this collection is pure dense (embedding + cosine
# similarity) retrieval, which is exactly what misses the case above — a
# chunk that bundles many distinct facts into one ~1800-char paragraph
# (protein %, glucid sourcing, glucid %, food-GI intro, start of the lipid
# section) has its embedding diluted across all of them, so it scores low
# for a query about any *one* fact even when that fact's exact wording is
# sitting right there in the text (same root cause TIER1_TRUST_BOOST's
# docstring and this function's k docstring already name). BM25 is the
# classic complementary signal: pure lexical/term-frequency matching,
# weighted by how rare a term is across the corpus, blind to embeddings
# entirely — "glucid" appearing in only 4 of ~1100 chunks scores it highly
# regardless of what else that chunk talks about. Computed fresh per call
# (not cached): corpus is small enough that tokenizing every fetched
# document is fast next to the embeddings API round-trip this function
# already makes, and caching would need per-collection invalidation anyway
# (tests swap in a fresh ephemeral collection per case) — not caching
# sidesteps that rather than getting it subtly wrong.
#
# Dense and BM25 scores live on different, incompatible scales (bounded
# cosine similarity vs. unbounded term-frequency-weighted sum), so they're
# combined by Reciprocal Rank Fusion — standard, scale-free hybrid-search
# fusion: each ranking contributes 1/(_RRF_K + rank) per chunk, summed
# across both rankings. A chunk absent from a ranking contributes 0 from it,
# so a chunk strong in only one signal (dense-similar and lexically unlike,
# or vice versa) can still surface, while a chunk strong in *both* naturally
# outranks either alone. _RRF_K=60 is the standard default from the
# original RRF paper (Cormack et al. 2009) — large enough that fusion isn't
# dominated by whichever list happens to rank something #1.
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60
_MIN_TOKEN_LEN = 3
_WORD_RE = re.compile(r"\d+(?:[.,]\d+)?|\w+", re.UNICODE)

# Real, verified regression during this same fix: without a rarity gate, BM25
# actively hurt a fully-generic query ("Chỉ số HbA1c là gì và mục tiêu điều
# trị chung cho người trưởng thành là bao nhiêu?", golden eval TC-AI-005).
# Every one of that query's terms is common corpus-wide (hba1c 10%, mục 16%,
# tiêu 23%, điều 45%, trị 43%, người 31%...), so ~90% of the candidate pool
# got *some* nonzero BM25 score purely from incidental overlap — noise, not
# signal — which was enough to push the single best chunk (the correct,
# curated, tier-1 5481 "Bảng 4: Mục tiêu điều trị" table entry, dense_rank=1)
# down to bm25_rank=197 (it's short and tabular, not prose-heavy, so it
# doesn't accumulate term-frequency mass) and out of the fused top-k
# entirely, in favor of superseded 3280/3319 documents that happened to
# repeat the query's common vocabulary more.
#
# BM25 only earns its keep on TC-AI-001's "glucid" (0.4% of chunks) — a
# genuinely rare, distinctive term dense embeddings under-weighted because
# it's one fact bundled into a chunk that's mostly about other things. This
# gate makes that the *only* case BM25 activates for: a query term only
# counts toward BM25 scoring if it's rare enough, corpus-wide, to actually
# be distinctive. A generic query where nothing clears the bar leaves
# _bm25_scores returning {} — bm25_order is then empty and RRF fusion
# reduces to the existing dense+tier1 ranking, unchanged from before this
# whole hybrid-retrieval change.
_BM25_MAX_TERM_DOC_FREQ_RATIO = 0.05


# Conversational function words dropped from the *query* before BM25. The
# rarity gate below alone doesn't catch them: guideline prose rarely says
# "tôi"/"nào", so they pass as "rare, distinctive" terms. Verified live on
# bge-m3/Qdrant: "Khi bị hạ đường huyết tôi nên xử trí thế nào?" — dense
# ranked QĐ 5481 PHẦN 6 "HẠ ĐƯỜNG HUYẾT" first (0.77), but "tôi" matched
# IWGDF's "chúng tôi khuyến cáo" and RRF put four off-topic IWGDF chunks on
# top; the answer then failed citation checks and fell back to a refusal.
_QUERY_STOPWORDS = frozenset(
    "tôi mình bạn chúng những nên cần phải thế nào sao khi nếu thì không được của với cho các một này như "
    "bao nhiêu làm hay hoặc đang rất nhưng để từ trong theo hỏi muốn biết xin giúp thường nhất hơn nhiều "
    "khác cũng vậy thôi chưa người".split()
)


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if len(w) >= _MIN_TOKEN_LEN]


def _query_tokens(query: str) -> list[str]:
    return [w for w in _tokenize(query) if w not in _QUERY_STOPWORDS]


def _bm25_scores(query_tokens: list[str], doc_tokens: dict[str, list[str]]) -> dict[str, float]:
    """Classic Okapi BM25 over exactly the candidate pool search_approved_knowledge
    already fetched from the vector store (not the whole collection) — that pool is
    already sized (_MIN_CANDIDATE_POOL) to be deep enough that a chunk BM25
    should promote is actually in it. `doc_tokens` keyed by chunk_id."""
    n_docs = len(doc_tokens)
    if not n_docs or not query_tokens:
        return {}

    doc_freq: dict[str, int] = {}
    doc_len: dict[str, int] = {}
    term_freq_by_doc: dict[str, dict[str, int]] = {}
    for chunk_id, tokens in doc_tokens.items():
        doc_len[chunk_id] = len(tokens)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        term_freq_by_doc[chunk_id] = counts
        for token in counts:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    avg_len = (sum(doc_len.values()) / n_docs) or 1.0

    # Rarity gate — see _BM25_MAX_TERM_DOC_FREQ_RATIO's docstring above.
    unique_query_tokens = {
        term for term in set(query_tokens) if doc_freq.get(term, 0) / n_docs <= _BM25_MAX_TERM_DOC_FREQ_RATIO
    }
    if not unique_query_tokens:
        return {}

    idf: dict[str, float] = {}
    for term in unique_query_tokens:
        df = doc_freq.get(term, 0)
        # BM25's own IDF variant (with the trailing +1): unlike plain
        # log(N/df), this stays positive even for a term in >50% of
        # documents, so a common-but-not-universal term still contributes a
        # small positive weight instead of penalizing a chunk for containing
        # it.
        idf[term] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)

    scores: dict[str, float] = {}
    for chunk_id, counts in term_freq_by_doc.items():
        length_norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len[chunk_id] / avg_len)
        total = 0.0
        for term in unique_query_tokens:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            total += idf[term] * (tf * (_BM25_K1 + 1)) / (tf + length_norm)
        if total > 0:
            scores[chunk_id] = total
    return scores


def _rrf_merge(*ranked_id_lists: list[str]) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranked_ids in ranked_id_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
    return fused


def _distance_to_score(distance: float) -> float:
    """The collection is cosine space and the vector store reports
    distance = 1 - cosine_similarity (see vector_store.QdrantCollection.query).
    Clipped defensively — floating point can push this a hair outside [0, 1]."""
    return max(0.0, min(1.0, 1.0 - distance))


def _to_retrieved_chunk(chunk_id: str, text: str, metadata: dict[str, Any], score: float) -> RetrievedChunk:
    tier = metadata.get("tier")
    chunk: RetrievedChunk = {
        "chunk_id": chunk_id,
        "text": text,
        "source_url": metadata.get("source_url"),
        "tier": tier,
        "score": score,
    }
    if metadata.get("organization"):
        chunk["organization"] = metadata["organization"]
    if tier == 1:
        chunk["title"] = metadata.get("document_title")
        chunk["section"] = metadata.get("category")
        chunk["page_ref"] = metadata.get("page_ref")
        trust_tier = metadata.get("trust_tier")
        if trust_tier is not None:
            chunk["trust_tier"] = trust_tier
    else:
        chunk["title"] = metadata.get("doc_title")
        chunk["section"] = metadata.get("heading_path") or metadata.get("section_heading")
        chunk["page_ref"] = _page_ref(metadata.get("page_start"), metadata.get("page_end"))
        # Tier-2 chunks inherit trust_tier (Bộ Y tế / Bệnh viện / Nước ngoài)
        # from the sources.csv rows that reference their file; left unset
        # (RetrievedChunk is total=False) when no row does, never fabricated.
        trust_tier = metadata.get("trust_tier")
        if trust_tier is not None:
            chunk["trust_tier"] = trust_tier
    return chunk


def _page_ref(page_start: int | None, page_end: int | None) -> str | None:
    if page_start is None:
        return None
    if page_end is None or page_end == page_start:
        return f"tr. {page_start}"
    return f"tr. {page_start}–{page_end}"


def search_approved_knowledge(query: str, k: int = 6) -> list[RetrievedChunk]:
    """Embed `query` with the local embedding model, similarity-search the
    knowledge-base collection built by codebase/models/rag/ingest.py, return chunks with full metadata
    (chunk_id, tier, trust_tier, page_ref/section, source_url — §5.3).

    Must log the retrieval per §5.8 (query, embedding_model, per-chunk score
    and rank) — that log is what the "uncited retrieval" guardrail check and
    the sources.csv-based eval (§11) both depend on.

    Returns [] if the collection is empty (nothing ingested yet) or the
    query yields no hits — callers (retrieve_knowledge.py) already treat an
    empty list as "found nothing", not an error, so this is safe.

    k default bumped 4 -> 6 after a real E2E test against the ingested corpus
    (not a mock): asking for the general adult HbA1c target retrieved three
    tier-2 chunks about the *individualized, stricter* target (a real but
    different fact — same document, different clause) ranked above the
    correct tier-1 "Bảng 4: Mục tiêu điều trị" chunk, which only appears at
    rank 6 (raw cosine ~0.588 vs. ~0.63-0.65 for the individualized-target
    chunks — the small deliberate trust-tier boost in this function isn't
    enough to close that gap, see the comment above TIER1_TRUST_BOOST). At
    k=4 the correct source was silently excluded and the model answered with
    only the individualized figure, presented as if it were the general one.
    k=6 was verified to include it for that query. This was a stopgap, not a
    real fix for the underlying cause (tier-1 chunks bundle multiple distinct
    facts per row — HbA1c + glucose + BP + lipid targets all in one chunk,
    see §5.1 — which dilutes their embedding's specificity for any one of
    those facts against narrower single-topic tier-2 chunks); the actual fix
    is the BM25 hybrid signal below (see its docstring) — this k default and
    _MIN_CANDIDATE_POOL both still matter as the depth BM25 has to work with.
    """
    start = time.monotonic()
    embeddings_client = get_embeddings_client()
    collection = get_collection()

    query_embedding = embeddings_client.embed_query(query)
    n_results = max(k * OVERFETCH_MULTIPLIER, k + OVERFETCH_MIN_EXTRA, _MIN_CANDIDATE_POOL)
    raw = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["metadatas", "documents", "distances"],
    )

    ids = raw["ids"][0] if raw.get("ids") else []
    documents = raw["documents"][0] if raw.get("documents") else []
    metadatas = raw["metadatas"][0] if raw.get("metadatas") else []
    distances = raw["distances"][0] if raw.get("distances") else []

    if not ids:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_retrieval(query=query, k=k, ranked=[], latency_ms=latency_ms)
        return []

    chunk_by_id: dict[str, RetrievedChunk] = {}
    dense_scored: list[tuple[float, str]] = []
    for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances, strict=True):
        score = _distance_to_score(distance)
        chunk_by_id[chunk_id] = _to_retrieved_chunk(chunk_id, text, metadata, score)
        tier_boost = TIER1_TRUST_BOOST if metadata.get("tier") == 1 else 0.0
        dense_scored.append((score + tier_boost, chunk_id))
    dense_scored.sort(key=lambda pair: pair[0], reverse=True)
    dense_order = [chunk_id for _, chunk_id in dense_scored]

    query_tokens = _query_tokens(query)
    doc_tokens = {chunk_id: _tokenize(text) for chunk_id, text in zip(ids, documents, strict=True)}
    bm25_scores = _bm25_scores(query_tokens, doc_tokens)
    bm25_order = sorted(bm25_scores, key=lambda chunk_id: bm25_scores[chunk_id], reverse=True)

    # If BM25 found no lexical overlap at all (bm25_order == []), fusion
    # reduces to dense_order's own ranking unchanged — RRF with one empty
    # input list just sums each dense-ranked chunk's own 1/(K+rank) once,
    # which preserves that list's relative order.
    fused = _rrf_merge(dense_order, bm25_order)
    # Superseded documents (QĐ 3280/3319, replaced by 5481 — ingest marks
    # status="superseded") rank after every current-guidance chunk: they repeat
    # most of 5481's vocabulary, so on a live end-to-end run they took 1-2 of
    # the top 4 slots for HbA1c/diagnosis questions. Demoted, not dropped, so
    # a question only they answer still gets an answer (their text says they
    # are superseded).
    superseded = {chunk_id for chunk_id, meta in zip(ids, metadatas, strict=True) if meta.get("status") == "superseded"}
    ranked_ids = sorted(fused, key=lambda chunk_id: (chunk_id in superseded, -fused[chunk_id]))[:k]
    ranked = [chunk_by_id[chunk_id] for chunk_id in ranked_ids]

    latency_ms = int((time.monotonic() - start) * 1000)
    _log_retrieval(query=query, k=k, ranked=ranked, latency_ms=latency_ms)
    return ranked


def _log_retrieval(*, query: str, k: int, ranked: list[RetrievedChunk], latency_ms: int) -> None:
    """§5.8: structured retrieval log. `session_id` and the later-appended
    `cited_chunk_ids` field belong to the general tool-call audit trace
    (PRD §30.1) that isn't scaffolded yet and isn't this function's own
    signature — logged here is exactly what §5.8's example carries that
    *is* available at this call site: query (as its length), k,
    embedding_model, and each returned chunk's id/tier/score/rank."""
    payload = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tool_name": "search_approved_knowledge",
        # The query is built from the patient's own question — its length, not
        # its text, goes to the application log (task 6.4: no PHI in logs).
        "tool_input": {"query_chars": len(query), "k": k},
        "embedding_model": get_settings().embedding_model,
        "tool_output": [
            {
                "chunk_id": chunk.get("chunk_id"),
                "tier": chunk.get("tier"),
                "score": round(chunk.get("score", 0.0), 4),
                "rank": rank,
            }
            for rank, chunk in enumerate(ranked, start=1)
        ],
        "latency_ms": latency_ms,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))

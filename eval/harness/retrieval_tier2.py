"""Chunk-level retrieval eval over the long documents (tier 2).

retrieval.py scores document *titles* for the 30 AI scenarios and
respond_agent/retrieval_recall.py scores the 53 sources.csv fact cards by
chunk_id; neither checks whether the chunk that actually carries an answer
from raw/*.md comes back. Gold labels here are (source_file, evidence) pairs
(eval/data/retrieval_tier2_cases.json), matched on normalized text, so they
stay valid when the chunker changes chunk boundaries or ids.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from codebase.models.agents.nodes.retrieve_knowledge import _RETRIEVAL_K

_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """Lower-case, NFC, all whitespace/punctuation removed — tolerant of PDF
    line breaks, repaired syllables and chunk-prefix formatting."""
    return _NON_ALNUM.sub("", unicodedata.normalize("NFC", text).lower())


def _matches(chunk: dict[str, Any], gold: dict[str, str]) -> bool:
    return chunk.get("source_file") == gold["source_file"] and normalize(gold["evidence"]) in normalize(
        chunk.get("text") or ""
    )


def validate_cases(cases: list[dict[str, Any]], documents_by_file: dict[str, list[str]]) -> list[dict[str, str]]:
    """Every gold (source_file, evidence) must exist in that file's indexed
    chunks, or the label can never be hit. Returns the problems found."""
    problems = []
    for case in cases:
        for gold in case["gold"]:
            texts = documents_by_file.get(gold["source_file"])
            if texts is None:
                problems.append({"id": case["id"], "issue": f"{gold['source_file']} not indexed"})
            elif not any(normalize(gold["evidence"]) in normalize(t) for t in texts):
                problems.append({"id": case["id"], "issue": f"evidence {gold['evidence']!r} not in {gold['source_file']}"})
    return problems


def _documents_by_file(collection: Any) -> dict[str, list[str]]:
    got = collection.get(where={"tier": 2}, include=["metadatas", "documents"])
    out: dict[str, list[str]] = {}
    for meta, doc in zip(got["metadatas"], got["documents"], strict=True):
        out.setdefault(meta.get("source_file"), []).append(doc or "")
    return out


def evaluate_retrieval_tier2(cases: list[dict[str, Any]], *, k: int = _RETRIEVAL_K) -> dict[str, Any]:
    from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge
    from codebase.models.rag.ingest import get_collection

    collection = get_collection()
    problems = validate_cases(cases, _documents_by_file(collection))
    metadata_by_id: dict[str, dict] = {}

    details = []
    for case in cases:
        retrieved = search_approved_knowledge(case["question"], k=k)
        ids = [c["chunk_id"] for c in retrieved if c.get("chunk_id") not in metadata_by_id]
        if ids:
            got = collection.get(ids=ids, include=["metadatas"])
            metadata_by_id.update(zip(got["ids"], got["metadatas"], strict=True))
        enriched = [{**c, "source_file": (metadata_by_id.get(c["chunk_id"]) or {}).get("source_file")} for c in retrieved]
        rank = next((i for i, c in enumerate(enriched, 1) if any(_matches(c, g) for g in case["gold"])), None)
        gold_files = {g["source_file"] for g in case["gold"]}
        doc_rank = next((i for i, c in enumerate(enriched, 1) if c["source_file"] in gold_files), None)
        details.append(
            {
                "id": case["id"],
                "question": case["question"],
                "rank": rank,
                "doc_rank": doc_rank,
                "retrieved": [
                    {"chunk_id": c["chunk_id"], "tier": c.get("tier"), "source_file": c["source_file"], "section": c.get("section")}
                    for c in enriched
                ],
            }
        )

    n = len(details)

    def hit_at(cutoff: int, key: str = "rank") -> float:
        return sum(1 for d in details if d[key] is not None and d[key] <= cutoff) / n if n else 0.0

    return {
        "kind": "retrieval_eval",
        "dataset": "retrieval_tier2_cases.json",
        "k": k,
        "cases": n,
        "label_problems": problems,
        "hit_at_1": hit_at(1),
        "hit_at_3": hit_at(3),
        "hit_at_k": hit_at(k),
        "mrr": sum(1 / d["rank"] for d in details if d["rank"]) / n if n else 0.0,
        "doc_hit_at_k": hit_at(k, "doc_rank"),
        "details": details,
    }

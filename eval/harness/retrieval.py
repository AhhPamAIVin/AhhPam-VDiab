from __future__ import annotations

import re
import unicodedata
from typing import Any

from codebase.models.agents.nodes.retrieve_knowledge import _RETRIEVAL_K
from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge


def _normalize(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


def _title_matches(expected: str, actual: str) -> bool:
    left = _normalize(expected)
    return any(left in part or (part and part in left) for part in map(_normalize, actual.split(" | ")))


def _organizations(chunk_ids: list[str | None]) -> dict[str, str]:
    from codebase.models.rag.ingest import get_collection

    ids = [cid for cid in chunk_ids if cid]
    if not ids:
        return {}
    got = get_collection().get(ids=ids, include=["metadatas"])
    return {cid: (meta or {}).get("organization") or "" for cid, meta in zip(got["ids"], got["metadatas"], strict=True)}


def evaluate_retrieval(cases: list[dict[str, Any]], *, k: int = _RETRIEVAL_K) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    source_hits = source_total = 0
    for case in cases:
        behavior = case.get("expected_behavior", {})
        required = behavior.get("required_source_titles") or []
        if not behavior.get("must_cite_sources") or not required:
            continue
        chunks = search_approved_knowledge(case["prompt"], k=k)
        # Labels name either a document ("Quyết định 5481/QĐ-BYT") or its
        # publisher ("Hội Nội tiết & Đái tháo đường Việt Nam (VADE)"), so match
        # against both. Title-only matching scored the right Bệnh viện Nội tiết
        # TW / VADE articles as misses.
        organizations = _organizations([chunk.get("chunk_id") for chunk in chunks])
        titles = [
            f"{chunk.get('title') or ''} | {organizations.get(chunk.get('chunk_id'), '')}" for chunk in chunks
        ]
        ranks: dict[str, int | None] = {}
        for expected in required:
            rank = next((index for index, title in enumerate(titles, 1) if _title_matches(expected, title)), None)
            ranks[expected] = rank
            source_total += 1
            if rank is not None:
                source_hits += 1
        first_rank = min((rank for rank in ranks.values() if rank is not None), default=None)
        reciprocal_ranks.append(1 / first_rank if first_rank else 0)
        details.append(
            {
                "id": case["id"],
                "required_source_ranks": ranks,
                "retrieved_titles": titles,
                "all_required_hit": all(rank is not None for rank in ranks.values()),
            }
        )
    return {
        "kind": "retrieval_eval",
        "k": k,
        "cases": len(details),
        "case_hit_rate": sum(item["all_required_hit"] for item in details) / len(details) if details else 0,
        "source_recall_at_k": source_hits / source_total if source_total else 0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0,
        "details": details,
    }

"""Citation-verification node — a second, independent LLM pass that reads the
already-drafted QA answer plus every RAG-retrieved chunk and marks which
chunks the answer actually used. Runs strictly after synthesize_qa.py has
produced state["response"] and NEVER touches that field — its only job is to
correct state["sources"]/state["data_used"]/state["limitations"] for
downstream consumers (respond.py -> AgentChatResponse, the "Nguồn tham khảo"
list the frontend renders).

OWNER: AI Consultation workstream. Do not edit from other workstreams.

Why this exists alongside synthesize_qa.py's own citation logic
(_chunk_is_grounded_in_response): that check is a cheap, deterministic
word-overlap heuristic — good enough to catch "cited nothing at all" for the
guardrail's drift check, but loose enough that a chunk sharing only common
vocabulary with the response (not the specific fact actually used) can clear
its threshold and get listed as a source the answer never really drew on.
That is the over-inclusion this node exists to fix: an LLM judge reading the
finished answer and the full chunk text can tell "topically related" apart
from "actually used" in a way word overlap can't.

Trust model: never take the verifier's own output at face value beyond
"which of the chunk_ids I was given did it pick" — any chunk_id in its
response that doesn't match one we actually sent is dropped rather than
trusted (§ same "don't just trust self-report" principle synthesize_qa.py's
own docstring names, applied one layer further out). Any failure of this
node (LLM error, unparseable output) must never block or alter the answer
itself — it's a strictly-additive precision pass on the sources list, so on
failure this returns {} and leaves whatever synthesize_qa.py already computed
in place, same as if this node were never wired into the graph at all.
"""

from __future__ import annotations

import json
import logging

from codebase.models.agents.nodes.synthesize_qa import (
    UNCONFIRMED_GROUNDING_LIMITATION,
    _dedupe_citations,
    _format_chunks_block,
    _tier_rank,
    _to_citation,
)
from codebase.models.agents.state import AgentState
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Bạn là bộ kiểm định trích dẫn (citation checker) cho một trợ lý AI tư vấn đái tháo "
    "đường. Nhiệm vụ DUY NHẤT của bạn là xác định, trong danh sách đoạn tài liệu được cung "
    "cấp, đoạn nào ĐÃ THỰC SỰ được dùng làm căn cứ để viết ra câu trả lời cho trước — không "
    "phải đoạn nào chỉ cùng chủ đề chung chung. Bạn không đánh giá câu trả lời có đúng hay "
    "an toàn không, không được sửa hay bình luận về câu trả lời — chỉ phân loại từng đoạn và "
    "trả về kết quả theo đúng định dạng JSON được yêu cầu, không kèm bất kỳ văn bản nào khác."
)

_USER_TEMPLATE = """CÂU TRẢ LỜI CUỐI CÙNG (dữ liệu để đối chiếu — KHÔNG được thay đổi hay diễn giải lại):
\"\"\"
{response}
\"\"\"

DANH SÁCH CÁC ĐOẠN TÀI LIỆU ĐÃ TRUY XUẤT (RAG), mỗi đoạn có một chunk_id duy nhất:
{chunks_block}

Với MỖI đoạn ở trên, hãy tự hỏi: nội dung CỤ THỂ của đoạn này (số liệu, khuyến nghị, định nghĩa, \
sự kiện...) có thực sự xuất hiện hoặc được dùng làm căn cứ trong CÂU TRẢ LỜI CUỐI CÙNG ở trên hay \
không? Một đoạn chỉ nói về CÙNG CHỦ ĐỀ với câu hỏi nhưng nội dung cụ thể của nó KHÔNG xuất hiện trong \
câu trả lời thì KHÔNG được tính là đã dùng.

Trả lời DUY NHẤT một đối tượng JSON theo đúng định dạng sau, không kèm giải thích, không kèm markdown, \
không kèm dấu ```:
{{"used_chunk_ids": ["<chunk_id đã dùng>", ...]}}

Nếu không đoạn nào thực sự được dùng, trả về {{"used_chunk_ids": []}}. Chỉ liệt kê những chunk_id có \
thật trong danh sách trên — không tự tạo chunk_id mới."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if "\n" in text:
        first_line, rest = text.split("\n", 1)
        if first_line.strip().lower() in {"json", ""}:
            text = rest
    return text.strip()


def _parse_used_chunk_ids(content: str) -> set[str] | None:
    """Returns None (caller must fall back) on any shape the model wasn't
    supposed to produce — malformed JSON, wrong top-level type, a
    used_chunk_ids that isn't a list. Never raises."""
    try:
        parsed = json.loads(_strip_code_fence(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    used_ids = parsed.get("used_chunk_ids")
    if not isinstance(used_ids, list):
        return None
    return {str(cid) for cid in used_ids if isinstance(cid, (str, int))}


async def verify_citations(state: AgentState) -> dict:
    """Second, independent LLM pass that re-derives `sources`/`data_used`
    from which retrieved chunks the drafted answer actually used — never
    touches state["response"], and fails open (returns {}) on any LLM/parse
    error so it can only tighten citations, never break the answer."""
    if state.get("intent") != "QA":
        return {}

    retrieved_chunks = state.get("retrieved_chunks") or []
    response_text = state.get("response") or ""
    if not retrieved_chunks or not response_text:
        # Nothing to verify: RAG was skipped/empty (synthesize_qa.py already
        # set sources=[] in both cases) or there's no drafted answer yet.
        return {}

    ordered_chunks = sorted(retrieved_chunks, key=_tier_rank)
    prompt = _USER_TEMPLATE.format(response=response_text, chunks_block=_format_chunks_block(ordered_chunks))

    try:
        llm = get_llm()
        result = await llm.ainvoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
        content = getattr(result, "content", None) or str(result) or ""
    except Exception:
        logger.warning("verify_citations LLM call failed, keeping upstream sources unchanged", exc_info=True)
        return {}

    used_chunk_ids = _parse_used_chunk_ids(content)
    if used_chunk_ids is None:
        logger.warning("verify_citations got unparseable output, keeping upstream sources unchanged: %r", content)
        return {}

    # Defensive against a hallucinated chunk_id that was never actually sent
    # — only ever narrows to chunks we ourselves retrieved this turn.
    used_chunks = [c for c in ordered_chunks if c.get("chunk_id") in used_chunk_ids]

    sources = _dedupe_citations([_to_citation(c) for c in used_chunks])
    data_used = list(dict.fromkeys(c.get("title", "") for c in used_chunks if c.get("title")))
    personal_data_grounded = bool(state.get("personal_data_grounded"))
    limitations = [] if (sources or personal_data_grounded) else [UNCONFIRMED_GROUNDING_LIMITATION]

    return {"sources": sources, "data_used": data_used, "limitations": limitations}

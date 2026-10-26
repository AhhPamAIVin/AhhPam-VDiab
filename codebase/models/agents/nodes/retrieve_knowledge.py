"""RAG retrieval call site.

OWNER: AI Consultation workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §5.6. No-op for the SUMMARY
intent — the summary path doesn't retrieve knowledge-base content. Also a
no-op when nodes/plan_response.py's LLM judgment set
state["skip_knowledge_retrieval"] — a pure personal-data lookup ("tôi đang
dùng thuốc gì") or a direct/no-tool answer doesn't need RAG at all, and
running it anyway used to pull back semantically-similar-but-irrelevant
chunks purely from keyword overlap ("thuốc"), which synthesize_qa/
guardrail.py then had to specifically account for downstream. Calls
`search_approved_knowledge` (tools/knowledge_tools.py), which is owned by the
data-pipeline / RAG workstream — that tool returns [] until ingestion (§5) is
wired up, which is safe here: synthesize_qa.py should say it found nothing
rather than fabricate, not treat an empty list as an error.

Searches on state["knowledge_query"] (plan_response.py's rewritten,
standalone version of the question) when set, falling back to the raw
state["message"] otherwise — matters for short follow-ups, which are a poor
embedding query verbatim.

`search_approved_knowledge` is a synchronous function that does blocking I/O
(a local embedding-model call, then a vector-store query) — run via `asyncio.to_thread`
so it can't block the event loop this `async def` node runs on. Without this,
a slow embedding call would stall every other concurrent request the same
FastAPI process is serving, not just this one.

`_RETRIEVAL_K` overrides the tool's own default (6): verified live (golden
eval TC-AI-007, chỉ số GI câu hỏi) that a fact required by the golden answer
— "ăn trái cây nguyên miếng, không ép lấy nước" — sits in the KB at rank 10
for that query, past the default cutoff; raising k to 10 pulls it (and the
5481-vs-KB cross-reference chunk at rank 9) into context. Extra retrieved
chunks the model doesn't end up using are harmless: synthesize_qa.py only
cites a chunk if the drafted response is actually grounded in its text
(`_chunk_is_grounded_in_response`), so a wider net doesn't inflate `sources`
with unused chunks, it just gives the model more to draw from.
"""

from __future__ import annotations

import asyncio

from codebase.models.agents.state import AgentState
from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge

_RETRIEVAL_K = 10


async def retrieve_knowledge(state: AgentState) -> dict:
    """RAG call site for QA — searches approved knowledge chunks on
    plan_response.py's rewritten query (or the raw message), skipped
    entirely for SUMMARY or when skip_knowledge_retrieval was set. See
    module docstring for why k is widened past the tool's own default."""
    if state.get("intent") != "QA":
        return {}
    if state.get("skip_knowledge_retrieval"):
        return {"retrieved_chunks": []}

    query = state.get("knowledge_query") or state.get("message", "")
    chunks = await asyncio.to_thread(search_approved_knowledge, query, _RETRIEVAL_K)
    return {"retrieved_chunks": chunks}

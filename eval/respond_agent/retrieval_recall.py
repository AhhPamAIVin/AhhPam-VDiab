"""RAG retrieval-recall eval — Phase 1 of docs/AI_Eval_Metrics_Plan.md.

Scores codebase.models.agents.tools.knowledge_tools.search_approved_knowledge (the
real retrieval path the respond-agent calls, hybrid dense+BM25) against
data/knowledge_base/sources.csv's ~53 embeddable rows — each already a verified
(question-worthy fact -> chunk_id) ground-truth pair, exactly the near-free eval set
docs/AI_Summary_Respond_System_Design.md §11 earmarks for this.

Two scores per row:
  - exact_chunk_hit: did the row's own chunk_id come back in the top-k? (deterministic,
    cheap, always-on signal)
  - ragas_context_recall: Ragas' NonLLMContextRecall — a string-similarity comparison of
    retrieved chunk text against the row's key_content, so a retrieved chunk that overlaps
    the right fact without being the literal same row still earns partial credit, where
    exact_chunk_hit would score 0.

Logs a real experiment to LangSmith (dataset "rag-retrieval-recall-v1") and writes a local
JSON report under eval/results/. Questions are cached in
eval/data/retrieval_recall_questions.json so runs are deterministic; re-run with
--regenerate-questions after sources.csv changes.

Run (from repo root):
    python -m eval.respond_agent.retrieval_recall
    python -m eval.respond_agent.retrieval_recall --k 8 --report /tmp/retrieval.json
    python -m eval.respond_agent.retrieval_recall --regenerate-questions
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before langsmith/langchain read LANGCHAIN_*/OPENAI_API_KEY from os.environ

from eval.respond_agent._ragas_compat import patch_ragas_vertexai_import  # noqa: E402

patch_ragas_vertexai_import()  # must run before the first `import ragas` below

from langsmith import Client  # noqa: E402
from ragas.dataset_schema import SingleTurnSample  # noqa: E402
from ragas.metrics import NonLLMContextRecall  # noqa: E402

from codebase.models.agents.tools.knowledge_tools import search_approved_knowledge  # noqa: E402
from codebase.models.llm import get_llm  # noqa: E402
from codebase.models.rag.chunking import build_tier1_chunk, classify_tier1_skip  # noqa: E402
from codebase.models.rag.ingest import REPO_ROOT, SOURCES_CSV_PATH, read_sources_csv  # noqa: E402

QUESTIONS_PATH = REPO_ROOT / "eval" / "data" / "retrieval_recall_questions.json"
REPORT_DIR = REPO_ROOT / "eval" / "results"
DATASET_NAME = "rag-retrieval-recall-v1"

_QUESTION_SYSTEM_PROMPT = (
    "Bạn giúp tạo dữ liệu đánh giá cho một hệ thống RAG tư vấn đái tháo đường. Với một đoạn "
    "nội dung đã được xác minh, hãy đặt DUY NHẤT một câu hỏi tự nhiên mà một bệnh nhân hoặc "
    "bác sĩ có thể hỏi trợ lý AI, sao cho câu trả lời đầy đủ và chính xác cho câu hỏi đó nằm "
    "trọn trong đoạn nội dung này. Không thêm giải thích, không đánh số, chỉ trả về câu hỏi."
)
_QUESTION_USER_TEMPLATE = 'Đoạn nội dung:\n"""\n{key_content}\n"""'

_recall_metric = NonLLMContextRecall()


@dataclass(frozen=True)
class EligibleRow:
    row_id: int
    chunk_id: str
    key_content: str
    document_title: str


def load_eligible_rows() -> list[EligibleRow]:
    """Same eligibility rule ingest.py's own _ingest_tier1 uses (classify_tier1_skip),
    so this eval's row set always matches what's actually embedded in Chroma — not a
    looser "non-empty key_content" guess that could drift from the real corpus."""
    rows = []
    for row_id, row in enumerate(read_sources_csv()):
        if classify_tier1_skip(row) is not None:
            continue
        key_content = (row.get("key_content") or "").strip()
        if not key_content:
            continue
        chunk = build_tier1_chunk(row, row_id)
        rows.append(
            EligibleRow(
                row_id=row_id,
                chunk_id=chunk["chunk_id"],
                key_content=key_content,
                document_title=row.get("document_title", ""),
            )
        )
    return rows


def _load_questions() -> dict[str, str]:
    if QUESTIONS_PATH.exists():
        return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return {}


def _save_questions(questions: dict[str, str]) -> None:
    QUESTIONS_PATH.write_text(json.dumps(questions, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


async def _generate_question(key_content: str) -> str:
    try:
        llm = get_llm()
        result = await llm.ainvoke(
            [
                {"role": "system", "content": _QUESTION_SYSTEM_PROMPT},
                {"role": "user", "content": _QUESTION_USER_TEMPLATE.format(key_content=key_content)},
            ]
        )
        content = getattr(result, "content", None) or str(result) or ""
    except Exception:
        content = ""
    question = content.strip().strip('"')
    return question or key_content[:120]


async def ensure_questions(rows: list[EligibleRow], *, regenerate: bool) -> dict[str, str]:
    questions = {} if regenerate else _load_questions()
    missing = [row for row in rows if row.chunk_id not in questions]
    if missing:
        print(f"Generating {len(missing)} question(s) via LLM (caching to {QUESTIONS_PATH})...")
        for row in missing:
            questions[row.chunk_id] = await _generate_question(row.key_content)
        _save_questions(questions)
    return questions


def ensure_dataset(client: Client, rows: list[EligibleRow], questions: dict[str, str]) -> None:
    if client.has_dataset(dataset_name=DATASET_NAME):
        print(f"LangSmith dataset {DATASET_NAME!r} already exists, reusing (not re-populating).")
        return
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Retrieval recall for search_approved_knowledge, ground-truthed against "
            "data/knowledge_base/sources.csv (see docs/AI_Summary_Respond_System_Design.md §11)."
        ),
    )
    examples = [
        {
            "inputs": {"question": questions[row.chunk_id]},
            "outputs": {
                "expected_chunk_id": row.chunk_id,
                "reference_context": row.key_content,
                "document_title": row.document_title,
            },
        }
        for row in rows
    ]
    client.create_examples(dataset_id=dataset.id, examples=examples)
    print(f"Created LangSmith dataset {DATASET_NAME!r} with {len(examples)} examples.")


def _target(inputs: dict, *, k: int) -> dict:
    chunks = search_approved_knowledge(inputs["question"], k=k)
    return {
        "retrieved_chunk_ids": [c.get("chunk_id") for c in chunks],
        "retrieved_contexts": [c.get("text", "") for c in chunks],
    }


def _exact_chunk_hit(run, example) -> dict:
    expected = (example.outputs or {}).get("expected_chunk_id")
    actual_ids = (run.outputs or {}).get("retrieved_chunk_ids") or []
    return {"key": "exact_chunk_hit", "score": int(expected in actual_ids)}


def _ragas_context_recall(run, example) -> dict:
    reference = (example.outputs or {}).get("reference_context")
    retrieved = (run.outputs or {}).get("retrieved_contexts") or []
    if not reference or not retrieved:
        return {"key": "ragas_context_recall", "score": 0.0}
    sample = SingleTurnSample(retrieved_contexts=retrieved, reference_contexts=[reference])
    try:
        score = _recall_metric.single_turn_score(sample)
    except Exception as exc:  # noqa: BLE001 - one bad sample shouldn't kill the whole run
        print(f"  ragas scoring failed for one example: {exc}")
        score = 0.0
    return {"key": "ragas_context_recall", "score": score}


def _write_report(rows: list[EligibleRow], questions: dict[str, str], k: int, report_path: Path) -> None:
    per_row = []
    hits = 0
    recalls = []
    for row in rows:
        outputs = _target({"question": questions[row.chunk_id]}, k=k)
        hit = row.chunk_id in outputs["retrieved_chunk_ids"]
        hits += int(hit)
        sample = SingleTurnSample(
            retrieved_contexts=outputs["retrieved_contexts"], reference_contexts=[row.key_content]
        )
        try:
            recall = _recall_metric.single_turn_score(sample)
        except Exception:
            recall = 0.0
        recalls.append(recall)
        per_row.append(
            {
                "chunk_id": row.chunk_id,
                "question": questions[row.chunk_id],
                "document_title": row.document_title,
                "exact_hit": hit,
                "ragas_context_recall": recall,
                "retrieved_chunk_ids": outputs["retrieved_chunk_ids"],
            }
        )

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "k": k,
        "num_rows": len(rows),
        "aggregate_exact_hit_rate": hits / len(rows) if rows else 0.0,
        "aggregate_ragas_context_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        "rows": per_row,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nAggregate exact-hit@{k}: {report['aggregate_exact_hit_rate']:.3f}")
    print(f"Aggregate ragas context-recall: {report['aggregate_ragas_context_recall']:.3f}")
    print(f"Wrote report to {report_path}")


async def main_async(args: argparse.Namespace) -> None:
    rows = load_eligible_rows()
    print(f"Loaded {len(rows)} eligible sources.csv row(s) from {SOURCES_CSV_PATH}")
    questions = await ensure_questions(rows, regenerate=args.regenerate_questions)

    client = Client()
    ensure_dataset(client, rows, questions)

    client.evaluate(
        partial(_target, k=args.k),
        data=DATASET_NAME,
        evaluators=[_exact_chunk_hit, _ragas_context_recall],
        experiment_prefix="retrieval-recall",
    )

    report_path = (
        Path(args.report) if args.report else REPORT_DIR / f"retrieval_recall_{int(datetime.now(UTC).timestamp())}.json"
    )
    _write_report(rows, questions, args.k, report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval-recall eval (LangSmith + Ragas)")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--regenerate-questions", action="store_true")
    parser.add_argument("--report", default=None, help="optional path to write a JSON report")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

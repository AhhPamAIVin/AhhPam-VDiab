# VDiab evaluation workflow

The project separates ordinary testing, dataset integrity, deterministic
production-code evaluation, retrieval evaluation, LLM evaluation, and human
review. A dataset declaration is never counted as proof that product code
passes.

## Pull-request checks

```bash
pytest tests/ -m "not eval and not slow"
pytest tests/ --cov=codebase --cov-branch --cov-report=term-missing
python eval/run_eval.py --output eval/results/latest.json
```

Use `--strict` for a release-readiness gate. It intentionally fails while a
required capability is missing or an executed golden case fails.

## Slow/on-demand evaluation

Retrieval requires the local embedding model (BAAI/bge-m3, cached after the
first download) and an ingested Qdrant collection (`make db-up && make ingest`):

```bash
python eval/run_eval.py --retrieval --output eval/results/latest-retrieval.json
```

Agent E2E runs against a seeded deployment:

```bash
EVAL_BASE_URL=http://localhost:8100 \
EVAL_PATIENT_TOKEN=... \
EVAL_DOCTOR_TOKEN=... \
EVAL_PATIENT_ID=... \
python eval/run_agent_e2e.py
```

RAGAS is optional and deliberately excluded from normal CI:

```bash
pip install -r eval/requirements.txt
python eval/run_ragas.py
```

## Evidence report

```bash
python eval/generate_evidence.py
```

This creates `EVALUATION_EVIDENCE.md`, `traceability.csv`, and a
`human_review.csv` template. Clinician ratings must be entered by real
reviewers. Do not generate or infer them automatically.

Required reported metrics:

- pytest pass/fail and line/branch coverage;
- safety recall and false-positive slices;
- retrieval hit-rate/recall@k/MRR;
- RAGAS faithfulness, answer relevance, context precision, context recall;
- citation correctness, forbidden-content violations, latency average/P95;
- clinician approval rate and qualitative comments.

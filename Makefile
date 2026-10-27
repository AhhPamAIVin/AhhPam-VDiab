.PHONY: setup bootstrap langfuse-up langfuse-down langfuse-logs run test test-fast test-integration test-coverage eval eval-retrieval eval-report lint format typecheck check clean up down restart db-up db-down db-reset seed-admin logs migrate-nhanes-data db-reset-migrate ai-chat backfill-insights ingest reindex-insights backfill-chat-sessions backfill-summaries stt-run stt-benchmark stt-up stt-down stt-fetch-testset stt-wer-benchmark tts-run tts-up tts-down eval-retrieval-recall eval-guardrail-judge eval-trajectory eval-golden-scenarios eval-ai-summary-golden export-weekly-report-review eval-watch-classifier eval-personal-data-faithfulness eval-all

# One-time local environment: .venv (Python 3.11) + backend deps + frontend
# deps + .env files copied from their .example templates. See scripts/setup.sh.
setup:
	bash scripts/setup.sh

# Fill the database (schema, admin, NHANES demo data, thresholds, showcase
# panel) and the vector store (RAG corpus). Idempotent. Needs `make db-up`.
bootstrap:
	python -m scripts.bootstrap_db $(BOOTSTRAP_ARGS)

# ---- Langfuse (self-hosted LLM tracing, replaces LangSmith) — UI at
# http://localhost:3300, login from LANGFUSE_INIT_USER_* in .env ----

langfuse-up:
	docker compose -f docker-compose.langfuse.yml up -d

langfuse-down:
	docker compose -f docker-compose.langfuse.yml down

langfuse-logs:
	docker compose -f docker-compose.langfuse.yml logs -f langfuse-web langfuse-worker

run:
	uvicorn codebase.backend.main:app --reload --host 0.0.0.0 --port 8100

test:
	pytest tests/ -v

test-fast:
	pytest tests/ -v -m "not eval and not slow"

test-integration:
	pytest tests/ -v -m integration

test-coverage:
	python eval/run_test_evidence.py --fail-under 60

eval:
	python eval/run_eval.py --strict --output eval/results/latest.json

eval-retrieval:
	python eval/run_eval.py --retrieval --output eval/results/latest-retrieval.json

eval-report:
	python eval/generate_evidence.py --results eval/results --output docs/evaluation/EVALUATION_EVIDENCE.md

lint:
	ruff check codebase/ tests/

format:
	ruff format codebase/ tests/

typecheck:
	mypy codebase/

check: lint format test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +

# ---- Docker Compose (backend + database) ----

up:
	docker compose up -d --build

down:
	docker compose down

restart: down up

logs:
	docker compose logs -f

db-up:
	docker compose up -d db qdrant

db-down:
	docker compose stop db qdrant

# Wipe the database container and its volume, then bring up a fresh one.
# The backend recreates the schema (SQLModel.metadata.create_all) on its next start.
db-reset:
	docker compose stop db
	docker compose rm -f db
	-docker volume rm diab_db
	docker compose up -d db

# Bootstrap the first Admin account from ADMIN_SEED_* in .env. Safe to re-run.
seed-admin:
	python -m scripts.seed_admin

# Import the synthetic NHANES/simulator demo dataset (patients, doctors,
# assignments, medications, glucose, meals, clinical notes) into the existing
# tables. Idempotent — safe to re-run. Needs an Admin account to already
# exist (see seed-admin) since patient_assignments.assigned_by references one.
migrate-nhanes-data:
	python -m scripts.migrate_nhanes_demo_data

# Wipe the dev database, recreate the schema, seed the admin account, then
# import the full NHANES/simulator demo dataset — one command. Also the only
# reliable way to pick up the meal_context enum's EVENT_RECOVERY addition:
# Postgres enum types are created fresh by create_all, never altered by the
# additive column patcher in db.py.
#
# Restarting backend here (before the two host scripts) matters: each of
# seed_admin/migrate_nhanes_demo_data calls create_db_and_tables() itself,
# but that only creates tables for models *that script's own process* has
# imported — a partial set. backend's lifespan is the only process that
# imports every model (via main.py's router imports), so it's the only
# reliable place to create the full schema, e.g. notifications, against a
# freshly wiped volume. --wait blocks until the Dockerfile HEALTHCHECK
# passes, so the schema is guaranteed to exist before the scripts below run.
db-reset-migrate: db-reset
	@echo "Waiting for database to accept connections..."
	@until docker compose exec -T db pg_isready >/dev/null 2>&1; do sleep 1; done
	@echo "Restarting backend to (re)create the full schema..."
	docker compose up -d --force-recreate --wait backend
	python -m scripts.seed_admin
	python -m scripts.migrate_nhanes_demo_data

# Interactive terminal chat against the real respond-agent (AI Summary + AI
# Consultation), picking a doctor/patient from the demo dataset above. Talks
# to Postgres directly — the backend server doesn't need to be running.
ai-chat:
	python -m scripts.ai_chat_cli

# One-off backfill of PatientInsight rows (source_kind="weekly_batch") for
# patients already seeded by migrate-nhanes-data above — that script writes
# ~60 days of history directly into Postgres, bypassing both PatientInsight
# triggers, so without this every freshly-seeded patient shows zero insights
# until real time catches up. NOT run automatically by migrate-nhanes-data —
# a full run is one LLM call per (patient, week), so validate with
# BACKFILL_ARGS="--dry-run" or "--limit 5" first.
backfill-insights:
	python -m scripts.backfill_patient_insights $(BACKFILL_ARGS)

# Knowledge base -> local embeddings (BAAI/bge-m3) -> Qdrant. Incremental;
# INGEST_ARGS="--rebuild" drops the collection first. Needs `make db-up`.
ingest:
	python -m codebase.models.rag.ingest $(INGEST_ARGS)

# Re-embed existing ACTIVE patient insights after an embedding-model or
# vector-store change (no LLM calls; Postgres is the source of truth).
reindex-insights:
	python -m scripts.reindex_patient_insights $(REINDEX_ARGS)

# One-off backfill of ChatSession rows for chat threads that existed before
# the multi-session model (GET /api/v1/agent/sessions) landed — those
# threads' conversation history is still in agent_checkpoints, but without a
# ChatSession row pointing at them they're unreachable through the new
# list/restore UI. Validate with BACKFILL_ARGS="--dry-run" first.
backfill-chat-sessions:
	python -m scripts.backfill_chat_sessions $(BACKFILL_ARGS)

# One-off backfill of AgentSummarySnapshot rows (role="patient", 7-day
# windows) for patients already seeded by migrate-nhanes-data above — same
# reasoning as backfill-insights: that script writes history directly into
# Postgres, bypassing both the write-path summary trigger and the lazy
# read-fallback, so without this every freshly-seeded patient only ever gets
# the current day's snapshot, not a historical trail. NOT run automatically
# by migrate-nhanes-data — validate with BACKFILL_ARGS="--dry-run" or
# "--limit 5" first.
backfill-summaries:
	python -m scripts.backfill_agent_summaries $(BACKFILL_ARGS)

# ---- STT streaming service (codebase/stt-service) — standalone, GPU-only,
# never touches codebase/backend. See codebase/stt-service/README.md. ----

stt-run:
	cd codebase/stt-service && uvicorn app.main:app --host 0.0.0.0 --port 8300

# Compare erax / whisper / qwen_omni on Vietnamese audio. Pass
# BENCHMARK_ARGS="--engines erax whisper" to skip qwen_omni, etc.
stt-benchmark:
	cd codebase/stt-service && python -m eval.benchmark $(BENCHMARK_ARGS)

stt-up:
	docker compose -f docker-compose.stt.yml up -d --build

stt-down:
	docker compose -f docker-compose.stt.yml down

# Fetch a small real Vietnamese ASR test set (FLEURS vi_vn test split) for
# WER scoring. Pass FETCH_ARGS="--count 50" for a bigger sample.
stt-fetch-testset:
	cd codebase/stt-service && python -m eval.fetch_testset $(FETCH_ARGS)

# Score erax / whisper / qwen_omni's WER against the fetched test set. Run
# stt-fetch-testset first. Pass BENCHMARK_ARGS="--engines erax whisper".
stt-wer-benchmark:
	cd codebase/stt-service && python -m eval.wer_benchmark $(BENCHMARK_ARGS)

# ---- TTS service (codebase/tts-service) — standalone, REST-only (no GPU
# needed for the phase-1 OpenAI engine), never touches codebase/backend. See
# codebase/tts-service/README.md. ----

tts-run:
	cd codebase/tts-service && uvicorn app.main:app --host 0.0.0.0 --port 8400

tts-up:
	docker compose -f docker-compose.tts.yml up -d --build

tts-down:
	docker compose -f docker-compose.tts.yml down

# ---- Respond-agent/RAG evaluation (LangSmith + Ragas) — see
# docs/AI_Eval_Metrics_Plan.md and eval/. Each logs a real experiment to
# LangSmith (needs LANGCHAIN_API_KEY in .env) and writes a local JSON report
# under eval/results/. Pass EVAL_ARGS="--report /tmp/x.json" etc. ----

# RAG retrieval recall against data/knowledge_base/sources.csv, scored with
# Ragas' NonLLMContextRecall. Needs the Qdrant collection already ingested
# (`make db-up` + `python -m codebase.models.rag.ingest`) and OPENAI_API_KEY
# (for one-time eval question generation).
eval-retrieval-recall:
	python -m eval.respond_agent.retrieval_recall $(EVAL_ARGS)

# Independent LLM safety judge cross-checked against guardrail.py's
# deterministic verdict. No DB/vector-store dependency.
eval-guardrail-judge:
	python -m eval.respond_agent.guardrail_judge $(EVAL_ARGS)

# Agent routing/trajectory correctness — which graph nodes and tools actually
# fired vs. expected. LLM/RAG calls mocked throughout; no Qdrant/Postgres
# dependency (checkpointer runs against an in-memory SQLite session).
# --local scores without LangSmith (tracing moved to Langfuse); run the module
# directly without --local to log a LangSmith experiment when a key is available.
eval-trajectory:
	python -m eval.respond_agent.trajectory --local $(EVAL_ARGS)

# Golden-dataset end-to-end quality — eval/data/ai_scenarios_30.json's 30 non-
# jailbreak cases run through the REAL graph (real OpenAI + Qdrant calls, no
# mocking), scored against each case's own expected_behavior fields.
eval-golden-scenarios:
	python -m eval.respond_agent.golden_scenarios --local $(EVAL_ARGS)

# AI Summary golden (eval/data/ai_summary_cases.json, 8 cases) through the real
# summary graph, reflect() and the permission gate; real LLM + local embeddings,
# in-memory SQLite and Qdrant. Each expectation is PASS / FAIL / NOT_SUPPORTED.
eval-ai-summary-golden:
	python -m eval.respond_agent.ai_summary_golden $(EVAL_ARGS)

# Weekly reports for clinician review (Epic 2.6) -> docs/evaluation/weekly_report_review.csv
# (same columns as human_review.csv). EVAL_ARGS="--generate" runs last week's job first.
export-weekly-report-review:
	python -m scripts.export_weekly_report_review $(EVAL_ARGS)

# Offline eval/CI gate for the personalized-WATCH ML classifier surface
# (data/NHANES/final/train_watch_model.py, train_threshold_regression.py,
# artifacts/personalized_watch/*) — plain sklearn/pytest, unrelated to
# LangSmith/Ragas, kept in its own eval/watch_classifier/ subpackage separate
# from the LangGraph respond-agent evals above. Re-runs the existing
# leakage/danger-precedence tests plus threshold assertions on the live
# v2_quantile_regression model's metrics.json. See
# eval/watch_classifier/gate.py and V1_HGB_LEAKAGE_FINDING.md.
eval-watch-classifier:
	python -m eval.watch_classifier.gate $(EVAL_ARGS)

# Personal-data QA faithfulness — seeds a patient with known glucose/medication/
# doctor data, asks the REAL agent (real OpenAI + DB calls) personal-data
# questions, checks the response states the correct values. Distinct from
# eval-golden-scenarios (fresh empty patients, no real data to be faithful to)
# and from tests/test_backend/test_tool_data_fetch_correctness.py (proves the DB
# fetch itself is correct, never invokes the LLM) — this is the missing link:
# does the LLM correctly REPORT the real data the tools handed it.
eval-personal-data-faithfulness:
	python -m eval.respond_agent.personal_data_faithfulness $(EVAL_ARGS)

eval-all: eval-retrieval-recall eval-guardrail-judge eval-trajectory eval-golden-scenarios eval-ai-summary-golden eval-watch-classifier eval-personal-data-faithfulness

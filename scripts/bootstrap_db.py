"""One-shot local bootstrap: fill an empty (or partially seeded) Postgres +
vector store with everything the app needs for a realistic demo.

Steps, in order (every one is idempotent, so re-running is safe):

1. Create the full schema. Imports codebase.backend.main first so *every*
   model is registered on SQLModel.metadata — same reason the Makefile's
   db-reset-migrate restarts the backend before seeding. Every later step
   runs in this same process (runpy, not a subprocess) for the same reason:
   run standalone, a seed script's own create_db_and_tables() only sees the
   models it imported, and e.g. migrate_nhanes_demo_data then fails with
   NoReferencedTableError on activity_records -> pending_record_actions.
2. scripts.seed_admin                      — Admin from ADMIN_SEED_* in .env
3. scripts.migrate_nhanes_demo_data        — patients, doctors, meds, glucose, meals, notes
4. scripts.backfill_care_plan_targets      — PatientProfile + doctor-approved thresholds
5. scripts.backfill_threshold_evaluations  — threshold_status for seeded glucose rows
6. scripts.seed_demo_doctor_showcase       — 3 NORMAL / 1 WATCH / 1 DANGER demo panel
7. codebase.models.rag.ingest              — knowledge base -> embeddings -> Chroma/Qdrant
                                             (VECTOR_STORE in .env; needs OPENAI_API_KEY)

Not included: the LLM-heavy backfills (make backfill-insights /
backfill-summaries) — one LLM call per patient-week, run them by hand.

Usage:

    python -m scripts.bootstrap_db [--skip-rag] [--skip-showcase]
"""

from __future__ import annotations

import argparse
import runpy
import sys
import time

STEPS: list[tuple[str, str]] = [
    ("seed admin", "scripts.seed_admin"),
    ("migrate NHANES demo data", "scripts.migrate_nhanes_demo_data"),
    ("backfill care plan targets", "scripts.backfill_care_plan_targets"),
    ("backfill threshold evaluations", "scripts.backfill_threshold_evaluations"),
    ("seed demo doctor showcase", "scripts.seed_demo_doctor_showcase"),
    ("ingest RAG knowledge base", "codebase.models.rag.ingest"),
]


def create_full_schema() -> None:
    import codebase.backend.main  # noqa: F401 — registers every model on SQLModel.metadata
    from codebase.backend.db import create_db_and_tables

    create_db_and_tables()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-rag", action="store_true", help="Skip the RAG ingestion step (no OpenAI calls).")
    parser.add_argument("--skip-showcase", action="store_true", help="Skip the demo doctor showcase panel.")
    args = parser.parse_args()

    print("==> create full schema", flush=True)
    create_full_schema()

    for name, module in STEPS:
        if args.skip_rag and name.startswith("ingest RAG"):
            continue
        if args.skip_showcase and name.startswith("seed demo doctor"):
            continue
        print(f"==> {name}", flush=True)
        started = time.monotonic()
        sys.argv = [module]  # each step parses its own (default) CLI args
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        print(f"    done in {time.monotonic() - started:.1f}s", flush=True)

    print("Bootstrap complete.")


if __name__ == "__main__":
    main()

"""CI check: startup schema bootstrap on a real Postgres.

The test suite runs on SQLite, which has no native enum types and treats
indexes differently — exactly where the drift bugs fixed in db.py lived
(missing enum values, a stale UNIQUE index). This script, run against an
empty Postgres:

1. bootstraps the schema twice (create_db_and_tables must be idempotent);
2. recreates the old drift by hand — the stale UNIQUE index on
   care_plan_targets.patient_id and a notification_type enum without
   GLUCOSE_ALERT — and checks the next bootstrap repairs both.

    python -m scripts.check_schema_bootstrap
"""

from __future__ import annotations

import sys

from sqlalchemy import inspect, text

import codebase.backend.main  # noqa: F401 — registers every model on SQLModel.metadata
from codebase.backend.db import create_db_and_tables, engine


def _enum_labels(type_name: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid WHERE t.typname = :n"),
            {"n": type_name},
        ).all()
    return {r[0] for r in rows}


def _patient_index_unique() -> bool:
    indexes = {ix["name"]: ix for ix in inspect(engine).get_indexes("care_plan_targets")}
    return bool(indexes["ix_care_plan_targets_patient_id"].get("unique"))


def main() -> int:
    if engine.dialect.name != "postgresql":
        print("needs a Postgres database (DB_HOST/DB_PORT/...)", file=sys.stderr)
        return 2

    create_db_and_tables()
    create_db_and_tables()
    print("bootstrap twice: ok")

    # Recreate an old database: notifications typed before GLUCOSE_ALERT
    # existed, and the initial commit's UNIQUE index on patient_id.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE notifications ALTER COLUMN notification_type TYPE text"))
        conn.execute(text("DROP TYPE notification_type"))
        labels = sorted(_enum_labels("preference_notification_type") - {"GLUCOSE_ALERT"})
        conn.execute(text("CREATE TYPE notification_type AS ENUM (" + ", ".join(f"'{label}'" for label in labels) + ")"))
        conn.execute(
            text(
                "ALTER TABLE notifications ALTER COLUMN notification_type TYPE notification_type "
                "USING notification_type::notification_type"
            )
        )
        conn.execute(text('DROP INDEX "ix_care_plan_targets_patient_id"'))
        conn.execute(text('CREATE UNIQUE INDEX "ix_care_plan_targets_patient_id" ON care_plan_targets (patient_id)'))
    assert "GLUCOSE_ALERT" not in _enum_labels("notification_type") and _patient_index_unique()
    print("drift recreated: notification_type without GLUCOSE_ALERT, UNIQUE patient_id index")

    create_db_and_tables()
    problems = []
    if "GLUCOSE_ALERT" not in _enum_labels("notification_type"):
        problems.append("notification_type still lacks GLUCOSE_ALERT")
    if _patient_index_unique():
        problems.append("ix_care_plan_targets_patient_id is still UNIQUE")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print("drift repaired by create_db_and_tables: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

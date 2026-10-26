"""One-off: convert glucose rows stored in mmol/L to mg/dL.

create_glucose_record now normalizes on write (health/service.py), but rows
saved before that kept their mmol/L value, which the DANGER gate, the ML WATCH
band and every "mg/dL" average read as mg/dL. Idempotent — converted rows are
mg/dL afterwards and are not touched again. Their stored threshold_status was
computed from the wrong number; re-evaluating history is out of scope here.

    python -m scripts.normalize_glucose_units [--dry-run]
"""

from __future__ import annotations

import argparse

from sqlmodel import Session, select

from codebase.backend.db import engine
from codebase.backend.health.models import GlucoseRecord
from codebase.backend.health.schemas import MG_DL_PER_MMOL_L


def normalize(*, dry_run: bool = False) -> int:
    with Session(engine) as session:
        rows = session.exec(select(GlucoseRecord).where(GlucoseRecord.unit == "mmol/L")).all()
        for row in rows:
            row.value = round(row.value * MG_DL_PER_MMOL_L, 1)
            row.unit = "mg/dL"
            session.add(row)
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    count = normalize(dry_run=args.dry_run)
    print(f"{'would convert' if args.dry_run else 'converted'} {count} mmol/L glucose rows to mg/dL")


if __name__ == "__main__":
    main()

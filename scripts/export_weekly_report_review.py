"""Export weekly patient reports for clinician review (Epic 2.6).

Writes the latest N reports in the columns of docs/evaluation/human_review.csv
(case_id, category, prompt, response, ..., clinician_pass, safety_pass,
rating_1_5, comment, reviewer, reviewed_at) so a doctor can rate how clear and
safe they are. The "prompt" column carries the deterministic figures the text
was written from; the "response" column is exactly what the patient sees
(summary + tip). No names or ids that identify a patient are exported.

    python -m scripts.export_weekly_report_review                 # existing reports
    python -m scripts.export_weekly_report_review --generate      # run last week's job first (calls the LLM)
    python -m scripts.export_weekly_report_review --limit 10 --out docs/evaluation/weekly_report_review.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, col, select

import codebase.backend.main  # noqa: F401 — registers every model
from codebase.backend.db import engine
from codebase.backend.reports.models import PatientWeeklyReport, WeeklyReportStatus
from codebase.backend.reports.service import run_weekly_report_tick

COLUMNS = [
    "case_id", "category", "prompt", "response", "sources", "latency_ms",
    "clinician_pass", "safety_pass", "rating_1_5", "comment", "reviewer", "reviewed_at",
]  # fmt: skip
DEFAULT_OUT = Path("docs/evaluation/weekly_report_review.csv")


def _figures(stats: dict) -> str:
    """The stats a reviewer needs to check the text against, as short JSON."""
    keep = {
        "glucose": {k: (stats.get("glucose") or {}).get(k) for k in ("count", "avg", "min", "max", "low_count", "in_target_pct")},
        "change": stats.get("change"),
        "days_logged": stats.get("days_logged"),
        "meal_count": stats.get("meal_count"),
        "top_foods": (stats.get("meals") or {}).get("top_foods"),
        "medications": {
            k: (stats.get("medications") or {}).get(k) for k in ("day_rate_pct", "days_taken", "days_missed")
        },
    }
    return json.dumps(keep, ensure_ascii=False)


def review_rows(session: Session, limit: int) -> list[dict]:
    reports = session.exec(
        select(PatientWeeklyReport)
        .where(PatientWeeklyReport.status != WeeklyReportStatus.FAILED)
        .order_by(col(PatientWeeklyReport.created_at).desc())
        .limit(limit)
    ).all()
    return [
        {
            "case_id": f"WR-{index:03d}",
            "category": "WEEKLY_REPORT",
            "prompt": f"Tuần {report.week_start:%d/%m}–{report.week_end:%d/%m/%Y}. Số liệu: {_figures(report.stats)}",
            "response": "\n\n".join(
                part for part in (report.summary, f"Việc nên làm: {report.tip}" if report.tip else "") if part
            ),
            **{column: "" for column in COLUMNS[4:]},
        }
        for index, report in enumerate(reports, start=1)
    ]


def export(limit: int, out: Path) -> int:
    with Session(engine) as session:
        rows = review_rows(session, limit)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--generate", action="store_true", help="run last week's report job first (calls the LLM)")
    args = parser.parse_args()
    if args.generate:
        with Session(engine) as session:
            print("generate:", asyncio.run(run_weekly_report_tick(session, now_utc=datetime.utcnow())))
    count = export(args.limit, args.out)
    print(f"wrote {count} report(s) to {args.out}")


if __name__ == "__main__":
    main()

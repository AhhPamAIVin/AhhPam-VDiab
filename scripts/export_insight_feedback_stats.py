"""Export the clinician approval rate of AI insights (Epic 4.4).

Doctors confirm or reject insights on the patient page; this writes the share
confirmed — over each insight's latest verdict, overall and per source_kind —
to docs/evaluation/insight_feedback_stats.md. Counts only: no insight text,
names or ids.

    python -m scripts.export_insight_feedback_stats
    python -m scripts.export_insight_feedback_stats --since 2026-11-01 --out docs/evaluation/insight_feedback_stats.md
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from sqlmodel import Session

import codebase.backend.main  # noqa: F401 — registers every model
from codebase.backend.db import engine
from codebase.backend.insights.feedback import approval_stats

DEFAULT_OUT = Path("docs/evaluation/insight_feedback_stats.md")
_KIND_LABELS = {"appointment": "Từ ghi chú khám (appointment)", "weekly_batch": "AI tự phát hiện (weekly_batch)"}


def _row(label: str, rate: dict) -> str:
    pct = "—" if rate["approval_rate_pct"] is None else f"{rate['approval_rate_pct']}%"
    return f"| {label} | {rate['reviewed']} | {rate['confirmed']} | {rate['rejected']} | {pct} |"


def render_markdown(stats: dict, *, generated_at: datetime, since: datetime | None = None) -> str:
    scope = f"từ {since:%d/%m/%Y}" if since else "toàn bộ"
    lines = [
        "# Tỷ lệ bác sĩ chấp thuận nhận định AI",
        "",
        f"Xuất lúc {generated_at:%d/%m/%Y %H:%M} (UTC), phạm vi: {scope}. "
        "Mỗi nhận định tính theo phản hồi gần nhất của bác sĩ; tỷ lệ = xác nhận / (xác nhận + bác bỏ).",
        "",
        "| Nhóm | Đã đánh giá | Xác nhận | Bác bỏ | Tỷ lệ chấp thuận |",
        "|---|---|---|---|---|",
        _row("Tổng", stats["overall"]),
    ]
    lines += [_row(_KIND_LABELS.get(kind, kind), rate) for kind, rate in stats["by_source_kind"].items()]
    lines += ["", "Tạo bởi `python -m scripts.export_insight_feedback_stats`.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=datetime.fromisoformat, default=None, help="only verdicts given since (ISO date)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    with Session(engine) as session:
        stats = approval_stats(session, since=args.since)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(stats, generated_at=datetime.utcnow(), since=args.since), encoding="utf-8")
    print(f"{stats['overall']['reviewed']} insight(s) reviewed, written to {args.out}")


if __name__ == "__main__":
    main()

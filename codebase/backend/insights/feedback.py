"""Doctor feedback on AI insights (Epic 4.4).

The latest verdict on an insight (any doctor; a patient has one assigned
doctor at a time) decides what happens to it: REJECTED hides it from the
patient (agent/router.py, insights/router.py) and stops reflect from
updating or re-creating it (nodes/upsert_insight.py). approval_stats() is the
"clinician approval rate" written to docs/evaluation/ by
scripts/export_insight_feedback_stats.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, select

from codebase.backend.insights.models import InsightFeedback, InsightVerdict, PatientInsight


def latest_feedback(session: Session, insight_ids: Iterable[UUID]) -> dict[UUID, InsightFeedback]:
    ids = list(insight_ids)
    if not ids:
        return {}
    rows = session.exec(
        select(InsightFeedback)
        .where(col(InsightFeedback.insight_id).in_(ids))
        .order_by(col(InsightFeedback.updated_at))
    ).all()
    return {row.insight_id: row for row in rows}  # later rows win


def rejected_insight_ids(session: Session, patient_id: UUID) -> set[UUID]:
    rows = session.exec(
        select(InsightFeedback).where(InsightFeedback.patient_id == patient_id).order_by(col(InsightFeedback.updated_at))
    ).all()
    latest = {row.insight_id: row.verdict for row in rows}
    return {insight_id for insight_id, verdict in latest.items() if verdict == InsightVerdict.REJECTED}


def is_rejected(session: Session, insight_id: UUID) -> bool:
    feedback = latest_feedback(session, [insight_id]).get(insight_id)
    return feedback is not None and feedback.verdict == InsightVerdict.REJECTED


def set_feedback(
    session: Session,
    *,
    insight: PatientInsight,
    doctor_id: UUID,
    verdict: InsightVerdict,
    comment: str | None,
    now: datetime | None = None,
) -> InsightFeedback:
    now = now or datetime.utcnow()
    feedback = session.exec(
        select(InsightFeedback).where(InsightFeedback.insight_id == insight.id, InsightFeedback.doctor_id == doctor_id)
    ).first()
    if feedback is None:
        feedback = InsightFeedback(
            insight_id=insight.id,
            patient_id=insight.patient_id,
            doctor_id=doctor_id,
            verdict=verdict,
            insight_version=insight.version,
            created_at=now,
        )
    feedback.verdict = verdict
    feedback.comment = (comment or "").strip() or None
    feedback.insight_version = insight.version
    feedback.updated_at = now
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


def approval_stats(session: Session, *, since: datetime | None = None) -> dict:
    """Confirmed / (confirmed + rejected) over each insight's latest verdict,
    overall and per source_kind."""
    statement = select(InsightFeedback, PatientInsight.source_kind).join(
        PatientInsight, col(PatientInsight.id) == col(InsightFeedback.insight_id)
    )
    if since is not None:
        statement = statement.where(col(InsightFeedback.updated_at) >= since)
    latest: dict[UUID, tuple[InsightVerdict, str]] = {}
    for feedback, source_kind in session.exec(statement.order_by(col(InsightFeedback.updated_at))).all():
        latest[feedback.insight_id] = (feedback.verdict, str(getattr(source_kind, "value", source_kind)))

    def _rate(verdicts: list[InsightVerdict]) -> dict:
        confirmed = sum(1 for v in verdicts if v == InsightVerdict.CONFIRMED)
        rejected = len(verdicts) - confirmed
        return {
            "reviewed": len(verdicts),
            "confirmed": confirmed,
            "rejected": rejected,
            "approval_rate_pct": round(100 * confirmed / len(verdicts), 1) if verdicts else None,
        }

    by_kind: dict[str, list[InsightVerdict]] = {}
    for verdict, kind in latest.values():
        by_kind.setdefault(kind, []).append(verdict)
    return {
        "overall": _rate([verdict for verdict, _ in latest.values()]),
        "by_source_kind": {kind: _rate(verdicts) for kind, verdicts in sorted(by_kind.items())},
    }

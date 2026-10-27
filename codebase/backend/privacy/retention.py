"""Retention job (task 6.5) — docs/DATA_RETENTION.md is the policy this
implements. Daily, from insights/scheduler.py:

- chat conversations untouched for `retention_chat_days`: the session, its
  LangGraph checkpoints and its pending record actions. Records the patient
  saved from a chat stay (their link to the chat is cleared);
- AI summary snapshots generated more than `retention_ai_summary_days` ago.
  Pre-visit briefs and weekly reports keep their own copy of the text, only
  the link to the snapshot is cleared.

Health records, medications, clinical notes and care plans are never purged
by age: they are the medical record. 0 days turns one purge off.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlmodel import Session

from codebase.backend.agent.models import ChatSession, PendingRecordAction
from codebase.backend.audit import service as audit
from codebase.backend.config import get_settings
from codebase.backend.privacy.service import detach_references
from codebase.backend.summaries.models import AgentSummarySnapshot
from codebase.models.agents.checkpoint import AgentCheckpoint, AgentCheckpointBlob, AgentCheckpointWrite

logger = logging.getLogger(__name__)


def purge_expired(session: Session, *, now: datetime) -> dict[str, int]:
    settings = get_settings()
    counts = {"chat_sessions": 0, "ai_summaries": 0}

    if settings.retention_chat_days > 0:
        cutoff = now - timedelta(days=settings.retention_chat_days)
        expired = select(ChatSession.id).where(ChatSession.updated_at < cutoff)
        threads = select(ChatSession.thread_id).where(ChatSession.updated_at < cutoff)
        actions = select(PendingRecordAction.id).where(PendingRecordAction.chat_session_id.in_(expired))
        detach_references(session, PendingRecordAction, actions)
        session.execute(delete(PendingRecordAction).where(PendingRecordAction.chat_session_id.in_(expired)))
        for model in (AgentCheckpointWrite, AgentCheckpointBlob, AgentCheckpoint):
            session.execute(delete(model).where(model.thread_id.in_(threads)))
        counts["chat_sessions"] = session.execute(delete(ChatSession).where(ChatSession.updated_at < cutoff)).rowcount

    if settings.retention_ai_summary_days > 0:
        cutoff = now - timedelta(days=settings.retention_ai_summary_days)
        expired = select(AgentSummarySnapshot.id).where(AgentSummarySnapshot.generated_at < cutoff)
        detach_references(session, AgentSummarySnapshot, expired)
        counts["ai_summaries"] = session.execute(
            delete(AgentSummarySnapshot).where(AgentSummarySnapshot.generated_at < cutoff)
        ).rowcount

    session.commit()
    if any(counts.values()):
        audit.record(
            session,
            "retention.purge",
            detail=",".join(f"{name}={count}" for name, count in counts.items()),
        )
    return counts


def run_retention_tick() -> None:
    """Cron target — own session, outside any request (actor: system)."""
    from codebase.backend.db import engine

    with Session(engine) as session:
        counts = purge_expired(session, now=datetime.utcnow())
    logger.info("retention purge: %s", counts)

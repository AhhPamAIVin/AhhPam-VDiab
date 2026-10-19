"""Backfill ChatSession rows for chat threads that existed before the
multi-session model landed (/Users/phamminh/.claude/plans/
memoized-churning-patterson.md, Part 1).

Before that change, /api/v1/agent/chat used one continuous, deterministic
thread per (patient, counterpart) pair — `f"{patient_id}:{counterpart_id}"`,
stored directly as AgentCheckpoint.thread_id (codebase/models/agents/
checkpoint.py) with no ChatSession row at all (that table didn't exist yet).
Those threads still have real conversation history sitting in
agent_checkpoints; without this script they'd be silently unreachable
through the new list/restore UI (GET /api/v1/agent/sessions), since nothing
would point a ChatSession at their thread_id.

Deliberately does NOT touch the `...:summary` namespace (codebase/backend/
agent/router.py's `_summary_thread_id`) — that was never a user-facing
conversation and still isn't; only two-segment "{patient}:{counterpart}"
thread ids qualify.

Idempotent: skips any thread_id a ChatSession already exists for, so
re-running is a no-op for already-backfilled threads — same convention as
this repo's other scripts/backfill_*.py scripts.

Usage:

    python -m scripts.backfill_chat_sessions [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from uuid import UUID

from langchain_core.messages import HumanMessage
from sqlmodel import Session, func, select

from codebase.backend.agent.models import ChatSession
from codebase.backend.db import create_db_and_tables, engine
from codebase.models.agents.checkpoint import AgentCheckpoint, checkpointer

logger = logging.getLogger("scripts.backfill_chat_sessions")

_TITLE_MAX_LENGTH = 60
_SUMMARY_SUFFIX = ":summary"


def _derive_title(message: str) -> str:
    # Same heuristic as agent/router.py's _derive_title — small enough
    # (and offline-script-vs-request-endpoint different enough in context)
    # that duplicating beats importing a router's private helper.
    stripped = " ".join(message.split())
    return stripped if len(stripped) <= _TITLE_MAX_LENGTH else stripped[: _TITLE_MAX_LENGTH - 1].rstrip() + "…"


def _parse_legacy_thread_id(thread_id: str) -> tuple[UUID, UUID] | None:
    """Old chat thread ids are exactly "{patient_id}:{counterpart_id}" — two
    UUIDs, one colon. Anything else (the ":summary" namespace, or a
    new-scheme fresh uuid4() thread_id with no colon at all) doesn't
    qualify and is left alone."""
    if thread_id.endswith(_SUMMARY_SUFFIX):
        return None
    parts = thread_id.split(":")
    if len(parts) != 2:
        return None
    try:
        return UUID(parts[0]), UUID(parts[1])
    except ValueError:
        return None


def _legacy_thread_ids(session: Session) -> list[tuple[str, datetime, datetime]]:
    """Distinct thread_id + first/last checkpoint timestamp for every thread
    already in agent_checkpoints — the min/max become the backfilled
    ChatSession's created_at/updated_at, so the restored list orders
    old conversations by when they actually happened."""
    statement = select(
        AgentCheckpoint.thread_id,
        func.min(AgentCheckpoint.created_at),
        func.max(AgentCheckpoint.created_at),
    ).group_by(AgentCheckpoint.thread_id)  # type: ignore[arg-type]
    return list(session.exec(statement).all())  # type: ignore[arg-type]


async def _title_for_thread(session: Session, thread_id: str) -> str | None:
    # Passing `session` (already open for this run) rather than letting
    # checkpointer fall back to its own fresh Session(engine) per thread —
    # same engine either way here, just avoids opening N extra connections.
    checkpoint_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "session": session}})
    if checkpoint_tuple is None:
        return None
    messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
    first_human = next((message for message in messages if isinstance(message, HumanMessage)), None)
    return _derive_title(first_human.content) if first_human else None


async def run_backfill(*, dry_run: bool = False) -> tuple[int, int]:
    create_db_and_tables()
    prepared = 0
    inserted = 0

    with Session(engine) as session:
        existing_thread_ids = set(session.exec(select(ChatSession.thread_id)).all())

        for thread_id, first_seen, last_seen in _legacy_thread_ids(session):
            if thread_id in existing_thread_ids:
                continue
            parsed = _parse_legacy_thread_id(thread_id)
            if parsed is None:
                continue
            patient_id, counterpart_id = parsed
            prepared += 1
            if dry_run:
                continue

            session.add(
                ChatSession(
                    thread_id=thread_id,
                    patient_id=patient_id,
                    counterpart_id=counterpart_id,
                    title=await _title_for_thread(session, thread_id),
                    created_at=first_seen,
                    updated_at=last_seen,
                )
            )
            inserted += 1

        if not dry_run:
            session.commit()

    return prepared, inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    prepared, inserted = asyncio.run(run_backfill(dry_run=args.dry_run))
    verb = "would insert" if args.dry_run else "inserted"
    logger.info("chat_sessions: %d legacy threads found, %d %s", prepared, inserted, verb)


if __name__ == "__main__":
    main()

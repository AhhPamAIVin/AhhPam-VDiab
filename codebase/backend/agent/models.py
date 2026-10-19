"""ChatSession — one listable/restorable AI conversation.

Plan: /Users/phamminh/.claude/plans/memoized-churning-patterson.md Part 1.
Replaces the old "one continuous thread per (patient, counterpart)" scheme
(router.py's now-removed `_thread_id()`) with true multiple sessions per
pair, ChatGPT-style: a patient/doctor can start a new conversation and later
list/restore any past one.

`id` (used in URLs) and `thread_id` (the LangGraph checkpointer's key, see
codebase/models/agents/checkpoint.py) are deliberately separate columns, not
the same UUID reused twice: new sessions get a fresh `thread_id = str(uuid4())`,
but scripts/backfill_chat_sessions.py can point a backfilled row at an
already-existing pre-multi-session thread (the old `f"{patient_id}:
{counterpart_id}"` composite key) without any special-casing anywhere else —
every caller just reads `session.thread_id`.

Isolation mirrors the invariant the old composite key used to provide
structurally: `counterpart_id` is the doctor's id, or equals `patient_id` for
a patient's own self-chat — so a doctor's sessions about a patient, a
different doctor's sessions about the same patient, and the patient's own
self-chat are three disjoint session sets, filterable by `counterpart_id ==
current_user.id` alone (see agent/router.py's list/messages endpoints).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: str = Field(index=True, unique=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    counterpart_id: UUID = Field(foreign_key="users.id", index=True)
    title: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PendingRecordAction(SQLModel, table=True):
    """Patient-confirmed bridge between free-text chat and existing write services."""

    __tablename__ = "pending_record_actions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    chat_session_id: UUID = Field(foreign_key="chat_sessions.id", index=True)
    record_type: str = Field(max_length=40)
    payload_json: str
    missing_fields_json: str = Field(default="[]")
    status: str = Field(default="pending", max_length=20, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    completed_at: datetime | None = None
    saved_record_id: UUID | None = Field(default=None)

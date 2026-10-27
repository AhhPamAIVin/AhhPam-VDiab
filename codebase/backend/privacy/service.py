"""Patient data rights (task 6.5): export everything held about a patient,
and erase an account once an admin approved the request.

Both walk SQLModel.metadata instead of a hand-kept table list, so a table
added later is covered without anyone remembering to come back here:
- export: every table with a `patient_id` column (minus the ones below);
- erase: every row that references the user through a foreign key, and every
  row that references *those* rows, children first.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Table, delete, or_, select, update
from sqlmodel import Session, SQLModel

from codebase.backend.agent.models import ChatSession
from codebase.backend.auth.models import User, UserRole
from codebase.models.agents.checkpoint import AgentCheckpoint, AgentCheckpointBlob, AgentCheckpointWrite

logger = logging.getLogger(__name__)

# Not the patient's data: the audit trail (other people's actions, IPs) and
# the deletion workflow's own bookkeeping.
_EXPORT_EXCLUDED_TABLES = {"audit_events", "account_deletion_requests"}
_USER_EXPORT_FIELDS = (
    "id",
    "email",
    "full_name",
    "role",
    "is_active",
    "ai_memory_consent",
    "meal_photo_ai_consent",
    "weekly_report_enabled",
    "created_at",
    "updated_at",
)
_CHECKPOINT_MODELS = (AgentCheckpointWrite, AgentCheckpointBlob, AgentCheckpoint)


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return None  # serialized blobs (checkpoints) — transcripts are exported decoded instead
    return value


def _patient_tables() -> list[Table]:
    return [
        table
        for table in SQLModel.metadata.sorted_tables
        if "patient_id" in table.c and table.name not in _EXPORT_EXCLUDED_TABLES
    ]


async def _chat_transcripts(session: Session, patient_id: UUID) -> list[dict[str, Any]]:
    """The patient's own conversations with the assistant, as readable text
    (the checkpoint tables only hold serialized graph state)."""
    from codebase.backend.agent.router import _message_role
    from codebase.models.agents.checkpoint import checkpointer

    chats = session.execute(
        select(ChatSession).where(ChatSession.patient_id == patient_id, ChatSession.counterpart_id == patient_id)
    ).scalars()
    transcripts = []
    for chat in chats:
        checkpoint_tuple = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": chat.thread_id, "session": session}}
        )
        raw = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", []) if checkpoint_tuple else []
        transcripts.append(
            {
                "session_id": str(chat.id),
                "title": chat.title,
                "created_at": _plain(chat.created_at),
                "messages": [
                    {"role": role, "content": message.content}
                    for message in raw
                    if (role := _message_role(message)) is not None
                ],
            }
        )
    return transcripts


async def collect_patient_data(session: Session, patient: User) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _patient_tables():
        rows = session.execute(select(table).where(table.c.patient_id == patient.id)).mappings().all()
        tables[table.name] = [{key: _plain(value) for key, value in row.items()} for row in rows]
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "account": {name: _plain(getattr(patient, name)) for name in _USER_EXPORT_FIELDS},
        "tables": tables,
        "chat_transcripts": await _chat_transcripts(session, patient.id),
    }


def to_json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def to_csv_zip(data: dict[str, Any]) -> bytes:
    """One CSV per table (UTF-8 with BOM so Excel shows Vietnamese), plus
    the account and the chat transcripts. Nested values stay JSON text."""

    def write_csv(archive: zipfile.ZipFile, name: str, rows: list[dict[str, Any]]) -> None:
        buffer = io.StringIO()
        columns = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()}
            )
        archive.writestr(name, "﻿" + buffer.getvalue())

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        write_csv(archive, "account.csv", [data["account"]])
        for table_name, rows in data["tables"].items():
            if rows:
                write_csv(archive, f"{table_name}.csv", rows)
        messages = [
            {"session_id": t["session_id"], "title": t["title"], **message}
            for t in data["chat_transcripts"]
            for message in t["messages"]
        ]
        if messages:
            write_csv(archive, "chat_messages.csv", messages)
    return output.getvalue()


# --- erasure ---------------------------------------------------------------


def _rows_referencing_user(user_id: UUID) -> dict[Table, Any]:
    """table -> WHERE clause selecting every row that hangs off the user,
    directly (any FK to users.id) or through another such row."""
    users = SQLModel.metadata.tables["users"]
    conditions: dict[Table, Any] = {users: users.c.id == user_id}
    for table in SQLModel.metadata.sorted_tables:  # parents before children
        if table is users:
            continue
        clauses = []
        for fk in table.foreign_keys:
            parent = fk.column.table
            if parent is users:
                clauses.append(fk.parent == user_id)
            elif parent in conditions:
                clauses.append(fk.parent.in_(select(fk.column).where(conditions[parent])))
        if clauses:
            conditions[table] = or_(*clauses)
    return conditions


def delete_patient_account(session: Session, patient_id: UUID) -> int:
    """Erase the patient's account and everything referencing it, in one
    transaction; then their insight vectors in Qdrant. Returns the number of
    database rows removed. The caller commits.

    Kept: audit events (identifiers only, no content) and the deletion
    request itself. Backups still hold the data until they age out
    (docs/DATA_RETENTION.md, "Backups")."""
    patient = session.get(User, patient_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if patient.role != UserRole.PATIENT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only patient accounts can be erased here")

    removed = 0
    # LangGraph checkpoints are keyed by thread_id, no foreign key to follow.
    threads = select(ChatSession.thread_id).where(ChatSession.patient_id == patient_id)
    for model in _CHECKPOINT_MODELS:
        removed += session.execute(delete(model).where(model.thread_id.in_(threads))).rowcount or 0

    conditions = _rows_referencing_user(patient_id)
    session.expunge_all()  # rows are about to vanish underneath the identity map
    for table in reversed(SQLModel.metadata.sorted_tables):  # children before parents
        if table in conditions:
            removed += session.execute(delete(table).where(conditions[table])).rowcount or 0
    return removed


def delete_patient_vectors(patient_id: UUID) -> None:
    """After the database commit: a failure here leaves orphan vectors (no
    longer readable through the app, which reads insights from SQL), never a
    half-deleted account."""
    from codebase.models.agents.tools.insight_tools import get_insights_collection

    try:
        get_insights_collection().delete(where={"patient_id": str(patient_id)})
    except Exception:
        logger.exception("could not delete insight vectors of an erased account — remove them by hand")


def detach_references(session: Session, parent: type[SQLModel], parent_ids: Any) -> None:
    """Null every nullable foreign key pointing at the given rows of
    `parent`, so they can be deleted without taking the referencing record
    with them (a glucose record saved from a chat outlives that chat)."""
    parent_table = parent.__table__
    for table in SQLModel.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table is parent_table and fk.parent.nullable:
                session.execute(update(table).where(fk.parent.in_(parent_ids)).values({fk.parent.name: None}))

"""SQLModel-backed LangGraph checkpointer — short-term (conversation) memory.

Hand-rolled instead of `pip install langgraph-checkpoint-postgres` so every
table in this app goes through SQLModel/`codebase/backend/db.py`'s `engine`,
not a second Postgres client — see
docs/AI_Summary_Respond_System_Design.md and the memory-planning conversation
this came out of for the full reasoning.

Schema and algorithm are a direct port of `langgraph.checkpoint.memory.
InMemorySaver` (the reference implementation LangGraph ships) onto three
SQLModel tables instead of its in-memory `storage`/`blobs`/`writes` dicts —
the same shape the official `langgraph-checkpoint-postgres` package's own
Postgres schema uses. `put_writes` is NOT optional: it records each node's
output within a single graph superstep and is called on every `ainvoke`,
not just on interrupt-based resume — verified against the installed
`langgraph` version before writing this, since an earlier draft of the plan
assumed otherwise.

Session handling follows the same convention `resolve_context.py` documents
for itself: the sync methods pull the request-scoped `Session` out of
`config["configurable"]["session"]` (set by `codebase/backend/agent/router.py`
from FastAPI's `Depends(get_session)`) instead of opening a new one against
the module-level `engine` — that's what lets tests' in-memory SQLite
override (`tests/conftest.py`) actually reach the checkpointer too, the same
way it reaches every other node/tool. Falls back to a fresh `Session(engine)`
only when no session is present in config (e.g. `delete_thread`, which
`BaseCheckpointSaver`'s own signature doesn't pass a config for, or a
standalone script invoking the graph outside the request lifecycle).

Async methods call straight into the sync ones — no `asyncio.to_thread`
hop. Every other node in this graph does its (also-sync) DB I/O inline
inside an `async def` the same way; wrapping just this one caller's calls in
a thread pool would be an inconsistent, unnecessary pattern for what are
lightweight local ORM calls, and would mean a *different* thread sometimes
touching the same request-scoped `Session` object nodes elsewhere in the
same graph run touch directly.

Deliberately unimplemented (base class default raises `NotImplementedError`,
and nothing in this app calls them): `prune`, `copy_thread`,
`delete_for_runs`, `get_delta_channel_history` (the base class's own default
implementation covers it generically via `get_tuple`/`parent_config` — no
override needed since this app's `messages` channel is a plain
`add_messages` reducer, never a `DeltaChannel`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Field, Session, SQLModel, delete, select

from codebase.backend.db import engine


class AgentCheckpoint(SQLModel, table=True):
    """One checkpoint (superstep snapshot) for a conversation thread.

    Mirrors `InMemorySaver.storage[thread_id][checkpoint_ns][checkpoint_id]`.
    `checkpoint`/`checkpoint_metadata` are LangGraph's own serializer output
    (`serde.dumps_typed` -> `(type, bytes)`) — stored as a type tag plus the
    raw bytes so `loads_typed` can round-trip them exactly.
    """

    __tablename__ = "agent_checkpoints"

    thread_id: str = Field(primary_key=True, index=True)
    checkpoint_ns: str = Field(primary_key=True, default="")
    checkpoint_id: str = Field(primary_key=True)
    parent_checkpoint_id: str | None = Field(default=None)
    checkpoint_type: str
    checkpoint_blob: bytes
    metadata_type: str
    metadata_blob: bytes
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentCheckpointBlob(SQLModel, table=True):
    """One versioned channel value (e.g. one version of `messages`).

    Mirrors `InMemorySaver.blobs[(thread_id, checkpoint_ns, channel, version)]`.
    """

    __tablename__ = "agent_checkpoint_blobs"

    thread_id: str = Field(primary_key=True, index=True)
    checkpoint_ns: str = Field(primary_key=True, default="")
    channel: str = Field(primary_key=True)
    version: str = Field(primary_key=True)
    blob_type: str
    blob: bytes


class AgentCheckpointWrite(SQLModel, table=True):
    """One pending write within a superstep, before its checkpoint lands.

    Mirrors `InMemorySaver.writes[(thread_id, checkpoint_ns, checkpoint_id)]
    [(task_id, write_idx)]`.
    """

    __tablename__ = "agent_checkpoint_writes"

    thread_id: str = Field(primary_key=True, index=True)
    checkpoint_ns: str = Field(primary_key=True, default="")
    checkpoint_id: str = Field(primary_key=True)
    task_id: str = Field(primary_key=True)
    write_idx: int = Field(primary_key=True)
    channel: str
    value_type: str
    value_blob: bytes
    task_path: str = Field(default="")


@contextmanager
def _session_scope(config: RunnableConfig | None):
    session = ((config or {}).get("configurable") or {}).get("session")
    if session is not None:
        yield session
    else:
        with Session(engine) as fresh_session:
            yield fresh_session


class SQLModelCheckpointSaver(BaseCheckpointSaver[str]):
    """Checkpointer backing the respond-agent's conversation threads."""

    def _load_blobs(
        self, session: Session, thread_id: str, checkpoint_ns: str, versions: ChannelVersions
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for channel, version in versions.items():
            row = session.get(AgentCheckpointBlob, (thread_id, checkpoint_ns, channel, str(version)))
            if row is None or row.blob_type == "empty":
                continue
            result[channel] = self.serde.loads_typed((row.blob_type, row.blob))
        return result

    def _pending_writes(
        self, session: Session, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        rows = session.exec(
            select(AgentCheckpointWrite).where(
                AgentCheckpointWrite.thread_id == thread_id,
                AgentCheckpointWrite.checkpoint_ns == checkpoint_ns,
                AgentCheckpointWrite.checkpoint_id == checkpoint_id,
            )
        ).all()
        return [(row.task_id, row.channel, self.serde.loads_typed((row.value_type, row.value_blob))) for row in rows]

    def _to_tuple(self, session: Session, row: AgentCheckpoint) -> CheckpointTuple:
        checkpoint: Checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint_blob))
        metadata: CheckpointMetadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
        channel_values = self._load_blobs(session, row.thread_id, row.checkpoint_ns, checkpoint["channel_versions"])
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint={**checkpoint, "channel_values": channel_values},
            metadata=metadata,
            pending_writes=self._pending_writes(session, row.thread_id, row.checkpoint_ns, row.checkpoint_id),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        with _session_scope(config) as session:
            if checkpoint_id:
                row = session.get(AgentCheckpoint, (thread_id, checkpoint_ns, checkpoint_id))
            else:
                row = session.exec(
                    select(AgentCheckpoint)
                    .where(AgentCheckpoint.thread_id == thread_id, AgentCheckpoint.checkpoint_ns == checkpoint_ns)
                    .order_by(AgentCheckpoint.checkpoint_id.desc())  # type: ignore[attr-defined]
                    .limit(1)
                ).first()
            if row is None:
                return None
            return self._to_tuple(session, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        # `config` (a thread_id) is always provided by every caller in this app — the
        # config-less "list every thread" path exists only for parity with the
        # BaseCheckpointSaver contract, not because anything here calls it that way.
        with _session_scope(config) as session:
            statement = select(AgentCheckpoint)
            if config is not None:
                statement = statement.where(AgentCheckpoint.thread_id == config["configurable"]["thread_id"])
                checkpoint_ns = config["configurable"].get("checkpoint_ns")
                if checkpoint_ns is not None:
                    statement = statement.where(AgentCheckpoint.checkpoint_ns == checkpoint_ns)
                checkpoint_id = get_checkpoint_id(config)
                if checkpoint_id:
                    statement = statement.where(AgentCheckpoint.checkpoint_id == checkpoint_id)
            if before is not None:
                before_id = get_checkpoint_id(before)
                if before_id:
                    statement = statement.where(AgentCheckpoint.checkpoint_id < before_id)
            statement = statement.order_by(
                AgentCheckpoint.thread_id,  # type: ignore[attr-defined]
                AgentCheckpoint.checkpoint_ns,  # type: ignore[attr-defined]
                AgentCheckpoint.checkpoint_id.desc(),  # type: ignore[attr-defined]
            )
            rows = session.exec(statement).all()
            yielded = 0
            for row in rows:
                if limit is not None and yielded >= limit:
                    break
                if filter:
                    metadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
                    if not all(value == metadata.get(key) for key, value in filter.items()):
                        continue
                yielded += 1
                yield self._to_tuple(session, row)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id: str = checkpoint["id"]
        channel_values: dict[str, Any] = dict(checkpoint.get("channel_values", {}))
        checkpoint_without_values = {k: v for k, v in checkpoint.items() if k != "channel_values"}

        with _session_scope(config) as session:
            # Batched upsert, not a per-channel session.get()-then-add() loop —
            # two real production incidents in this one method:
            #
            # 1. session.get() autoflushes any pending change first by default,
            #    so a per-channel loop flushed each *previous* iteration's
            #    still-uncommitted row as its own separate round trip, with the
            #    transaction held open across the whole loop. Caught live via
            #    pg_stat_activity: connections sitting "idle in transaction" for
            #    minutes on this exact `INSERT INTO agent_checkpoint_blobs`
            #    shape, `wait_event: ClientRead` (Postgres waiting on the app
            #    between flushes, not a lock) — the same pattern fixed in
            #    notifications/service.py's revive_due_snoozes/
            #    renudge_stale_pending.
            # 2. Fixing #1 alone (wrapping the loop in session.no_autoflush)
            #    stopped the multi-minute hangs but left chat at a flat ~35s:
            #    every one of a chat turn's ~9 graph nodes calls put() once,
            #    and AgentState has 15+ channels, so even a "clean" no-autoflush
            #    version was still one session.get() + one write *per channel*
            #    — many small sequential round trips at Railway-sfo <->
            #    Supabase-ap-southeast-2 cross-region latency, per node.
            #    version is always freshly generated per channel update
            #    (LangGraph never reuses one), so the existence check was
            #    almost always wasted work anyway. One INSERT ... ON CONFLICT
            #    DO UPDATE covering every changed channel replaces up to 2x
            #    len(new_versions) round trips with one.
            blob_rows = [
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "channel": channel,
                    "version": str(version),
                    **dict(
                        zip(
                            ("blob_type", "blob"),
                            self.serde.dumps_typed(channel_values[channel])
                            if channel in channel_values
                            else ("empty", b""),
                        )
                    ),
                }
                for channel, version in new_versions.items()
            ]
            if blob_rows:
                upsert = pg_insert(AgentCheckpointBlob).values(blob_rows)
                upsert = upsert.on_conflict_do_update(
                    index_elements=["thread_id", "checkpoint_ns", "channel", "version"],
                    set_={"blob_type": upsert.excluded.blob_type, "blob": upsert.excluded.blob},
                )
                session.execute(upsert)

            checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint_without_values)
            full_metadata = get_checkpoint_metadata(config, metadata)
            metadata_type, metadata_blob = self.serde.dumps_typed(full_metadata)
            parent_checkpoint_id = config["configurable"].get("checkpoint_id")

            # checkpoint_id is a fresh id generated per put() call (LangGraph
            # never reuses one), so this row is in practice always a first-time
            # insert — same reasoning as the blob upsert above, just for a
            # single row instead of a per-channel loop, saving the one
            # always-empty existence-check round trip.
            checkpoint_upsert = pg_insert(AgentCheckpoint).values(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
                parent_checkpoint_id=parent_checkpoint_id,
                checkpoint_type=checkpoint_type,
                checkpoint_blob=checkpoint_blob,
                metadata_type=metadata_type,
                metadata_blob=metadata_blob,
            )
            checkpoint_upsert = checkpoint_upsert.on_conflict_do_update(
                index_elements=["thread_id", "checkpoint_ns", "checkpoint_id"],
                set_={
                    "parent_checkpoint_id": checkpoint_upsert.excluded.parent_checkpoint_id,
                    "checkpoint_type": checkpoint_upsert.excluded.checkpoint_type,
                    "checkpoint_blob": checkpoint_upsert.excluded.checkpoint_blob,
                    "metadata_type": checkpoint_upsert.excluded.metadata_type,
                    "metadata_blob": checkpoint_upsert.excluded.metadata_blob,
                },
            )
            session.execute(checkpoint_upsert)
            session.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id: str = config["configurable"]["checkpoint_id"]

        with _session_scope(config) as session:
            rows = []
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                value_type, value_blob = self.serde.dumps_typed(value)
                rows.append(
                    {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                        "task_id": task_id,
                        "write_idx": write_idx,
                        "channel": channel,
                        "value_type": value_type,
                        "value_blob": value_blob,
                        "task_path": task_path,
                    }
                )
            if not rows:
                return

            # Batched upsert, not a per-write session.get()-then-add() loop —
            # the exact same round-trip-per-row anti-pattern already fixed in
            # put() above, just not yet applied here. It looked harmless
            # ("far fewer rows per call than put()") until query-count
            # instrumentation after the put() fix shipped showed a single
            # node's task can carry 6+ writes (one per state channel it
            # touched), each costing its own session.get() round trip at
            # Railway-sfo <-> Supabase-ap-southeast-2 latency — it had become
            # the new dominant cost once put() itself stopped being one.
            #
            # Non-special (non-negative) indices are write-once: a retried
            # task re-sending the same writes must not overwrite what's
            # already recorded, so ON CONFLICT DO NOTHING. Special sentinel
            # channels (__error__, __interrupt__, ...) always take the latest
            # value instead — same rule InMemorySaver uses — so ON CONFLICT
            # DO UPDATE. Splitting by sign also drops the old existing_idxs
            # pre-check query entirely: DO NOTHING makes that check atomic
            # instead of a separate round trip.
            once_rows = [r for r in rows if r["write_idx"] >= 0]
            overwrite_rows = [r for r in rows if r["write_idx"] < 0]

            if once_rows:
                insert_once = pg_insert(AgentCheckpointWrite).values(once_rows)
                insert_once = insert_once.on_conflict_do_nothing(
                    index_elements=["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_idx"],
                )
                session.execute(insert_once)

            if overwrite_rows:
                upsert = pg_insert(AgentCheckpointWrite).values(overwrite_rows)
                upsert = upsert.on_conflict_do_update(
                    index_elements=["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_idx"],
                    set_={
                        "channel": upsert.excluded.channel,
                        "value_type": upsert.excluded.value_type,
                        "value_blob": upsert.excluded.value_blob,
                        "task_path": upsert.excluded.task_path,
                    },
                )
                session.execute(upsert)

            session.commit()

    def delete_thread(self, thread_id: str) -> None:
        # No RunnableConfig on this method's signature (BaseCheckpointSaver
        # contract) — always the module-level engine, same as
        # notifications/scheduler.py's background tick. Not on any request path.
        with Session(engine) as session:
            session.exec(delete(AgentCheckpointWrite).where(AgentCheckpointWrite.thread_id == thread_id))  # type: ignore[call-overload]
            session.exec(delete(AgentCheckpointBlob).where(AgentCheckpointBlob.thread_id == thread_id))  # type: ignore[call-overload]
            session.exec(delete(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id))  # type: ignore[call-overload]
            session.commit()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)


checkpointer = SQLModelCheckpointSaver()

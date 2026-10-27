import logging
from collections.abc import Generator

from sqlalchemy import UniqueConstraint, event, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import AddConstraint, CreateColumn
from sqlmodel import Session, SQLModel, create_engine

from codebase.backend.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
# pool_pre_ping: a pooled connection the server closed (the idle-in-transaction
# timeout below, or the pooler) is replaced at checkout instead of failing the
# request that happens to draw it.
# hide_parameters: a failed statement's error text (logged with its traceback
# by main.py's exception handler) leaves out the bound values — a glucose
# value, a note, a name (task 6.4: no PHI in logs).
engine = create_engine(settings.database_url, echo=False, pool_pre_ping=True, hide_parameters=True)


@event.listens_for(engine, "connect")
def _set_idle_in_transaction_timeout(dbapi_connection, connection_record) -> None:
    # Hard backstop, not a fix for any specific bug: this incident has now
    # recurred multiple times from different call sites (notifications/
    # service.py's revive_due_snoozes/renudge_stale_pending, checkpoint.py's
    # put()/put_writes()) — a per-row loop that calls session.add()/get()
    # without no_autoflush holds one open transaction across many individual
    # round trips with no commit until the very end, and at Railway-sfo <->
    # Supabase-ap-southeast-2 latency that's long enough for the connection
    # pool to starve before the loop ever finishes. Every instance found so
    # far has been fixed to a single batched statement, but nothing stops a
    # *future* call site from reintroducing the same shape. This makes
    # Postgres itself kill any connection that sits idle-in-transaction past
    # 30s, freeing it back to the pool regardless of which application code
    # caused it — a structural guarantee that doesn't depend on having found
    # every occurrence, current or future.
    #
    # A plain `SET` here, not `connect_args={"options": "-c ..."}` (tried
    # first, verified live NOT to work): the DB host is Supabase's pgbouncer-
    # compatible pooler, which does not forward arbitrary libpq startup
    # parameters to the real backend — `SHOW idle_in_transaction_session_
    # timeout` on a connection opened that way still read back the server
    # default (0 = disabled). A `SET` issued as a normal query over the
    # connection goes through the pooler like any other statement, so it
    # actually reaches the backend session.
    cursor = dbapi_connection.cursor()
    cursor.execute("SET idle_in_transaction_session_timeout = 30000")
    cursor.close()


def create_db_and_tables() -> None:
    """Create any tables missing from the database, then patch any existing
    tables that are missing columns present in the current models.

    `metadata.create_all` alone only creates missing *tables* — it never
    alters a table that already exists, so a persisted dev/staging volume
    silently drifts out of sync the moment a column is added to a model
    (see the glucose_records/meal_context incident). This closes that gap
    without pulling in a full migration tool — missing columns, missing
    Postgres enum values, and index drift (see each helper) — additive or
    relaxing only; never drops/renames/alters existing columns.
    """
    from codebase.backend.audit.models import ensure_append_only

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    _add_missing_enum_values()
    _repair_indexes()
    with engine.begin() as conn:
        ensure_append_only(conn)


def _repair_indexes() -> None:
    """Index/constraint drift: create_all never alters an index that already
    exists. Verified live: care_plan_targets kept the initial commit's UNIQUE
    index on patient_id after the model moved to versioned rows (unique on
    (patient_id, version)), so every second care-plan version for a patient
    failed with a UniqueViolation (HTTP 500).

    Only safe changes: an index the model no longer marks unique is recreated
    non-unique (relaxing), and missing indexes / unique constraints are
    created. Tightening an existing index, or a constraint the current data
    violates, is logged and skipped — that needs a deliberate migration."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        db_indexes = {ix["name"]: ix for ix in inspector.get_indexes(table.name)}
        db_uniques = {uc["name"] for uc in inspector.get_unique_constraints(table.name)} | {
            name for name, ix in db_indexes.items() if ix.get("unique")
        }
        for index in table.indexes:
            current = db_indexes.get(index.name)
            try:
                with engine.begin() as conn:
                    if current is None:
                        index.create(conn)
                    elif bool(current.get("unique")) and not index.unique:
                        conn.execute(text(f'DROP INDEX IF EXISTS "{index.name}"'))
                        index.create(conn)
                        logger.warning("relaxed index %s.%s from UNIQUE to non-unique", table.name, index.name)
                    elif index.unique and not current.get("unique"):
                        logger.warning("index %s.%s should be UNIQUE — needs a migration", table.name, index.name)
            except Exception:
                logger.exception("could not repair index %s.%s", table.name, index.name)
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint) or not constraint.name or constraint.name in db_uniques:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(AddConstraint(constraint))
            except Exception:
                logger.exception("could not add unique constraint %s.%s", table.name, constraint.name)


def missing_enum_values(existing: dict[str, set[str]]) -> dict[str, list[str]]:
    """Enum type name -> labels the models declare but the database type
    lacks. `existing` maps each database enum type to its current labels."""
    missing: dict[str, list[str]] = {}
    for table in SQLModel.metadata.sorted_tables:
        for column in table.columns:
            enum_type = column.type
            name = getattr(enum_type, "name", None)
            labels = getattr(enum_type, "enums", None)
            if not name or not labels or name not in existing:
                continue  # not a native enum, or created complete by create_all just now
            for label in labels:
                if label not in existing[name] and label not in missing.setdefault(name, []):
                    missing[name].append(label)
    return {name: labels for name, labels in missing.items() if labels}


def _add_missing_enum_values() -> None:
    """Same drift as _add_missing_columns, for Postgres ENUM types: create_all
    never alters an existing type, so a value added to a Python enum later
    (NotificationType.GLUCOSE_ALERT) is rejected by an older database —
    verified live: every WATCH/DANGER glucose alert failed with "invalid input
    value for enum notification_type". Additive only (ADD VALUE IF NOT EXISTS)."""
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid")
        ).all()
    existing: dict[str, set[str]] = {}
    for type_name, label in rows:
        existing.setdefault(type_name, set()).add(label)
    to_add = missing_enum_values(existing)
    if not to_add:
        return
    # ALTER TYPE ... ADD VALUE outside a transaction block: a value added
    # inside a transaction can't be used until it commits anyway.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for type_name, labels in to_add.items():
            for label in labels:
                escaped = label.replace("'", "''")
                conn.execute(text(f"ALTER TYPE \"{type_name}\" ADD VALUE IF NOT EXISTS '{escaped}'"))


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # just created above by create_all — already complete
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing_columns:
                    _add_column(conn, table.name, column)


def _add_column(conn, table_name: str, column) -> None:
    # Native schema types (e.g. Postgres ENUM) must exist before a column can reference them.
    if hasattr(column.type, "create"):
        column.type.create(conn, checkfirst=True)

    if not column.nullable and column.server_default is None:
        raise RuntimeError(
            f"Cannot auto-add NOT NULL column '{table_name}.{column.name}' without a server_default — "
            "retrofitting a NOT NULL column onto an existing table needs a value for its existing rows. "
            "Add server_default=... to the column so this can backfill it, or write an explicit migration."
        )

    ddl = str(CreateColumn(column).compile(dialect=conn.dialect))
    conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {ddl.strip()}'))


def get_session() -> Generator[Session, None, None]:
    session = Session(engine)
    try:
        yield session
    finally:
        _close_session(session)


def _close_session(session: Session) -> None:
    """FastAPI tears a yield dependency down only after the request's
    BackgroundTasks finish, so a write endpoint that schedules
    refresh_patient_summaries (6 sequential LLM calls) keeps this session's
    connection idle-in-transaction well past the 30s server timeout set
    above; Postgres kills it and the closing ROLLBACK raises. Verified live:
    that exception escaped after the response was sent, uvicorn dropped the
    keep-alive connection, and the client's *next* request failed with
    "Server disconnected". The connection is already gone and nothing was
    pending, so discard it and carry on."""
    try:
        session.close()
    except DBAPIError as exc:
        logger.warning("discarding a dead DB connection at session close: %s", exc.orig or exc)
        try:
            session.invalidate()
        except Exception:
            pass

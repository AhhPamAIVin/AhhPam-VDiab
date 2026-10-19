from collections.abc import Generator

from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateColumn
from sqlmodel import Session, SQLModel, create_engine

from codebase.backend.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, echo=False)


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
    (see the glucose_records/meal_context incident). This closes that one
    gap without pulling in a full migration tool: additive only (add
    column), never drops/renames/alters existing columns.
    """
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


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
    with Session(engine) as session:
        yield session

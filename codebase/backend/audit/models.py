from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DDL, event
from sqlmodel import Field, SQLModel


class AuditEvent(SQLModel, table=True):
    """One security-relevant action (task 6.1). Identifiers only — never the
    content of a record (no values, names, phone numbers, free text), so the
    audit trail itself is not a second copy of the PHI it protects.

    No foreign keys on purpose: events must outlive the accounts and records
    they mention (an approved account deletion removes the user row, the
    events about it stay). Append-only is enforced by the database, see the
    triggers below."""

    __tablename__ = "audit_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    actor_id: UUID | None = Field(default=None, index=True)
    # admin | doctor | patient | system (scheduler/scripts) | anonymous (failed login, unknown email)
    actor_role: str = Field(max_length=16)
    action: str = Field(max_length=64, index=True)
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=64)
    patient_id: UUID | None = Field(default=None, index=True)
    request_id: str | None = Field(default=None, max_length=64)
    ip: str | None = Field(default=None, max_length=64)
    # success | failure | denied
    outcome: str = Field(default="success", max_length=16)
    # Non-PHI context only: the route template of a read, the consent flag
    # that changed. Never a health value or free text.
    detail: str | None = Field(default=None, max_length=255)


# Append-only at the database level: application code (and anyone holding the
# app's DB credentials through it) cannot rewrite or erase history. The table
# owner can still drop the triggers — separating the owner role from the app
# role is the next step (docs/BACKUP_RESTORE.md, "Audit log").
_PG_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = 'audit_events is append-only (' || TG_OP || ' blocked)';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;
CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
"""

# SQLite (tests) has no TRUNCATE; the same guarantee for UPDATE/DELETE.
_SQLITE_APPEND_ONLY = [
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only (UPDATE blocked)'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only (DELETE blocked)'); END""",
]

event.listen(AuditEvent.__table__, "after_create", DDL(_PG_APPEND_ONLY).execute_if(dialect="postgresql"))
for _statement in _SQLITE_APPEND_ONLY:
    event.listen(AuditEvent.__table__, "after_create", DDL(_statement).execute_if(dialect="sqlite"))


def ensure_append_only(connection) -> None:
    """Re-apply the Postgres triggers on every startup (idempotent), so a
    table created before the triggers existed — or one whose triggers were
    dropped — gets them back. Called from db.create_db_and_tables."""
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(_PG_APPEND_ONLY)

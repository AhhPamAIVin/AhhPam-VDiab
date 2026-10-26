"""codebase/backend/db.py — additive schema-drift repair.

The Postgres-specific ALTER TYPE path can't run on the SQLite test database,
so these tests pin the pure part: deciding which enum labels an existing
database is missing. Verified live first: a dev database created before
NotificationType.GLUCOSE_ALERT / CarePlanTargetStatus.SUPERSEDED existed
rejected every glucose alert and every second care-plan update.
"""

from __future__ import annotations

import codebase.backend.main  # noqa: F401 — registers every model on SQLModel.metadata
from codebase.backend.db import missing_enum_values
from codebase.backend.notifications.models import NotificationType


def _labels(enum_cls) -> set[str]:
    return {member.name for member in enum_cls}


def test_reports_label_added_to_python_enum_after_db_type_was_created():
    existing = {"notification_type": _labels(NotificationType) - {"GLUCOSE_ALERT"}}
    assert missing_enum_values(existing) == {"notification_type": ["GLUCOSE_ALERT"]}


def test_nothing_missing_when_db_type_is_complete():
    assert missing_enum_values({"notification_type": _labels(NotificationType)}) == {}


def test_types_not_in_the_database_are_left_to_create_all():
    assert missing_enum_values({}) == {}


def test_session_close_on_a_dead_connection_does_not_raise():
    """get_session's teardown runs after BackgroundTasks; by then Postgres may
    have killed the idle-in-transaction connection and the closing ROLLBACK
    raises. That must not escape (it dropped the client's keep-alive
    connection, failing their next request)."""
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    from codebase.backend.db import _close_session

    session = MagicMock()
    session.close.side_effect = OperationalError("ROLLBACK", {}, Exception("connection abort"))
    _close_session(session)
    session.invalidate.assert_called_once()

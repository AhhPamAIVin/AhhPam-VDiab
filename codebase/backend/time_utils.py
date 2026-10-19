"""Shared UTC-aware datetime serialization for API response schemas.

Every `datetime` this app stores is naive but semantically UTC — DB defaults
use `datetime.utcnow()`, and every frontend write path converts to UTC via
`.toISOString()` before sending. Pydantic's default JSON serialization of a
naive `datetime` omits any timezone marker (e.g. `"2026-08-14T16:14:00"`,
no `Z`/offset), so the browser's `new Date(...)` parses it as *local* time
instead of UTC — a correct 16:14 UTC value round-trips as if it meant 16:14
local, silently shifting every displayed time by the browser's UTC offset
(verified live: a 23:14 VN-local entry was displayed back as 16:14).

`UtcDatetime` makes the UTC-ness explicit in the wire format: naive values
get `Z` appended on the way out, timezone-aware values pass through as-is.
Use it in place of `datetime` on response (`*Public`) schema fields — not on
`*Create`/input schemas, which don't need this (incoming ISO strings already
carry their own timezone marker from the client).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_as_utc_iso, return_type=str)]

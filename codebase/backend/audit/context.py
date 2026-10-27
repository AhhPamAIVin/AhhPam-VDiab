"""Who is doing what, for the audit trail (task 6.1).

AuditContextMiddleware opens one RequestContext per HTTP request and keeps it
in a ContextVar; get_current_user fills in the actor. A mutable holder rather
than separate ContextVar.set() calls: sync dependencies run in the threadpool
on a *copy* of the context, so a set() made there would never reach the
endpoint, while a mutation of the shared holder does.

Outside a request (schedulers, scripts) there is no context and events are
recorded as actor_role="system".
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from codebase.backend.security.client_ip import client_ip_from_headers

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass
class RequestContext:
    request_id: str
    ip: str | None
    method: str
    scope: dict[str, Any]
    actor_id: UUID | None = None
    actor_role: str | None = None
    # What this request already recorded — see audit/service.py's
    # de-duplication (record_patient_access, _fold).
    seen: set[tuple[str, ...]] = field(default_factory=set)
    created: set[tuple[str, str]] = field(default_factory=set)

    @property
    def route(self) -> str | None:
        """Route template (/api/v1/doctor/patients/{patient_id}/notes) — the
        concrete path with each matched path parameter put back as its name;
        never the query string. Rebuilt rather than read from the matched
        route: with nested routers FastAPI's route object only knows its own
        suffix ("/notes")."""
        path = self.scope.get("path")
        if not path:
            return None
        segments = path.split("/")
        for name, value in (self.scope.get("path_params") or {}).items():
            segments = [f"{{{name}}}" if segment == str(value) else segment for segment in segments]
        return "/".join(segments)


_current: ContextVar[RequestContext | None] = ContextVar("audit_request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


def set_actor(actor_id: UUID, role: str) -> None:
    context = _current.get()
    if context is not None:
        context.actor_id = actor_id
        context.actor_role = role


class AuditContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming responses and the
    BackgroundTasks that run after them stay inside the same context — an AI
    summary refreshed after a write is attributed to that request."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        client = scope.get("client")
        context = RequestContext(
            request_id=request_id,
            ip=client_ip_from_headers(headers, client[0] if client else None),
            method=scope.get("method", ""),
            scope=scope,
        )

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode("latin-1"))]
            await send(message)

        token = _current.set(context)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _current.reset(token)

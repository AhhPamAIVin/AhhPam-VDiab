import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import unquote_plus

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("codebase.backend.security.sql_injection_guard")

_SCANNABLE_CONTENT_TYPES = ("application/json", "application/x-www-form-urlencoded")

_SQLI_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bunion\b\s+\bselect\b",
        r"\bselect\b.{0,40}\bfrom\b",
        r"\bdrop\b\s+\btable\b",
        r"\binsert\b\s+\binto\b",
        r"'\s*(or|and)\s*'?\w+'?\s*=\s*'?\w+",  # tautology, e.g. ' OR '1'='1
        r"'\s*(--|#|/\*)",  # comment-based truncation, e.g. admin'--
        r";\s*(drop|delete|update|insert|alter)\b",  # stacked queries
        r"\bxp_cmdshell\b",
    )
]


def _find_sqli_pattern(raw_text: str) -> str | None:
    decoded = unquote_plus(raw_text)
    for pattern in _SQLI_PATTERNS:
        if pattern.search(decoded):
            return pattern.pattern
    return None


class SQLInjectionGuardMiddleware(BaseHTTPMiddleware):
    """Reject requests whose query string or body match common SQL-injection signatures.

    This is defense-in-depth, not the primary protection: every DB query in this
    codebase goes through the SQLModel/SQLAlchemy ORM (parameterized queries),
    which is what actually prevents SQL injection. This middleware only adds a
    detect-and-block layer for obviously malicious input before it reaches a route.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        matched = _find_sqli_pattern(request.url.query)

        content_type = request.headers.get("content-type", "")
        if matched is None and any(ct in content_type for ct in _SCANNABLE_CONTENT_TYPES):
            body_bytes = await request.body()
            matched = _find_sqli_pattern(body_bytes.decode("utf-8", errors="ignore"))

        if matched:
            logger.warning(
                "Blocked possible SQL injection on %s %s (pattern=%r, client=%s)",
                request.method,
                request.url.path,
                matched,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(status_code=400, content={"detail": "Request rejected: invalid input detected"})

        return await call_next(request)

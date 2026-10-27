"""Per-key sliding-window rate limits (task 6.2).

Two limits, both configurable (config.py / .env):
- POST /auth/login: per client IP — brute-force protection.
- /agent/*: per authenticated user — every call there can cost several LLM
  requests, and the shared provider quota was exhausted more than once during
  live testing.

In-process: each worker counts on its own, so with N workers a client can get
up to N x the limit. That bounds abuse without a Redis dependency; a shared
store is the upgrade path if the app ever runs many workers.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, Request, status

from codebase.backend.auth.dependencies import get_current_user
from codebase.backend.auth.models import User
from codebase.backend.config import get_settings
from codebase.backend.security.client_ip import client_ip_from_headers

WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, *, now: float | None = None) -> float | None:
        """Record one hit for `key`. Returns None if allowed, otherwise the
        seconds until the oldest hit in the window expires (Retry-After)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.0, hits[0] + WINDOW_SECONDS - now)
            hits.append(now)
            if len(self._hits) > 10_000:  # drop idle keys so memory stays bounded
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    return client_ip_from_headers(request.headers, request.client.host if request.client else None)


def _enforce(key: str, limit: int) -> None:
    if limit <= 0:
        return
    retry_after = limiter.check(key, limit)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Bạn thao tác quá nhanh. Vui lòng thử lại sau ít phút.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def limit_login(request: Request) -> None:
    settings = get_settings()
    if settings.rate_limit_enabled:
        _enforce(f"login:{client_ip(request)}", settings.rate_limit_login_per_minute)


def limit_agent(current_user: User = Depends(get_current_user)) -> None:
    settings = get_settings()
    if settings.rate_limit_enabled:
        _enforce(f"agent:{current_user.id}", settings.rate_limit_agent_per_minute)

"""Langfuse tracing for every LangChain/LangGraph call (replaces LangSmith).

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set;
otherwise every helper here is a no-op, so tests and keyless dev runs never
try to reach a Langfuse server.

One shared CallbackHandler is attached in two places:
- get_llm() (codebase/models/llm.py), so every LLM call is traced, including
  the ones made outside the respond-agent graph (insights scheduler, scripts);
- the respond-agent's graph invocation (codebase/backend/agent/router.py), so a
  chat turn shows up as one trace with each node and LLM call nested under it.
LangChain de-duplicates the same handler instance when it is both inherited
and attached locally, so nested LLM calls are not double-reported.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from codebase.backend.config import get_settings

logger = logging.getLogger("codebase.models.tracing")


def langfuse_enabled() -> bool:
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@lru_cache
def get_langfuse_handler() -> Any | None:
    if not langfuse_enabled():
        return None
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("LANGFUSE_* keys are set but the langfuse package is not installed; tracing disabled")
        return None

    settings = get_settings()
    # Initializes the SDK's singleton client for this public key; the handler
    # below looks it up by the same key instead of reading os.environ (which
    # pydantic-settings never populates from .env).
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_host,
        environment=settings.app_env,
    )
    return CallbackHandler(public_key=settings.langfuse_public_key)


def tracing_callbacks() -> list[Any]:
    handler = get_langfuse_handler()
    return [handler] if handler is not None else []


def flush_tracing() -> None:
    """Send any buffered spans — call on app shutdown and at the end of scripts."""
    if get_langfuse_handler.cache_info().currsize == 0 or get_langfuse_handler() is None:
        return
    from langfuse import get_client

    get_client(public_key=get_settings().langfuse_public_key).flush()

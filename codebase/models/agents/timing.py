"""Per-node wall-clock timing for the respond-agent graph (Epic 5.1).

graph.py wraps every node with `timed_node(name, fn)`; each run logs one line
per node on the `codebase.models.agents.timing` logger:

    agent_node_timing node=synthesize_dispatch duration_ms=2310.4 intent=QA

Deliberately logs only the node name, the duration and the routing intent —
never the message, the response or any patient field (Epic 6.4: no PHI in
technical logs). The intent read is the post-node value when the node set one,
so classify_intent/plan_response report the intent they decided.

`collect_node_timings()` is for in-process benchmarks: inside the `with`
block every timed node also appends `(node, duration_ms, intent)` to the
yielded list. A ContextVar keeps concurrent requests (and parallel branches
of the same run, which share the caller's context) from mixing lists.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

logger = logging.getLogger(__name__)

NodeTiming = tuple[str, float, str | None]

_collector: ContextVar[list[NodeTiming] | None] = ContextVar("agent_node_timings", default=None)


@contextmanager
def collect_node_timings() -> Iterator[list[NodeTiming]]:
    timings: list[NodeTiming] = []
    token = _collector.set(timings)
    try:
        yield timings
    finally:
        _collector.reset(token)


def _record(node: str, started: float, state: dict, update: object) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    intent = None
    if isinstance(update, dict):
        intent = update.get("intent")
    intent = intent or state.get("intent")
    logger.info("agent_node_timing node=%s duration_ms=%.1f intent=%s", node, duration_ms, intent)
    timings = _collector.get()
    if timings is not None:
        timings.append((node, duration_ms, intent))


def timed_node(name: str, fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    """Wrap an async node, keeping whether it takes LangGraph's `config`
    argument — LangGraph decides to pass `config` by inspecting the signature,
    so the wrapper must expose exactly the same parameters."""
    if "config" in inspect.signature(fn).parameters:

        @wraps(fn)
        async def with_config(state, config):
            started = time.perf_counter()
            update = await fn(state, config)
            _record(name, started, state, update)
            return update

        return with_config

    @wraps(fn)
    async def without_config(state):
        started = time.perf_counter()
        update = await fn(state)
        _record(name, started, state, update)
        return update

    return without_config

"""Lazy-load and cache TTS engine instances by name.

Same shape as codebase/stt-service/app/engines/registry.py: keeps at most
settings.MAX_RESIDENT_ENGINES loaded at once, evicting the least-recently-used
engine first. Irrelevant to today's single stateless `openai` engine, but
this is exactly the mechanism a phase-2 GPU-resident engine needs.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.config import settings
from app.engines.openai_engine import OpenAITTSEngine

logger = logging.getLogger(__name__)


def _build_f5_engine():
    # Deferred import: f5_engine.py pulls in torch/f5_tts, only installed via
    # the opt-in requirements-f5.txt — a phase-1-only (openai-only) install
    # must never fail just importing this module.
    from app.engines.f5_engine import F5TTSEngine

    return F5TTSEngine()


def _build_vieneu_engine():
    # Deferred import, same reasoning — requirements-vieneu.txt is opt-in.
    from app.engines.vieneu_engine import VieneuTTSEngine

    return VieneuTTSEngine()


_BUILDERS = {
    "openai": lambda: OpenAITTSEngine(),
    "f5_tts_vietnamese_vivoice": _build_f5_engine,
    "vieneu_tts": _build_vieneu_engine,
}


class EngineRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resident: OrderedDict[str, object] = OrderedDict()

    def available_engines(self) -> list[str]:
        return list(_BUILDERS.keys())

    def get(self, name: str):
        """Blocking. Returns a loaded engine, evicting an LRU one if needed."""
        if name not in _BUILDERS:
            raise ValueError(f"Unknown TTS engine '{name}'. Choose one of {self.available_engines()}")

        with self._lock:
            if name in self._resident:
                self._resident.move_to_end(name)
                return self._resident[name]

            while len(self._resident) >= settings.MAX_RESIDENT_ENGINES:
                evict_name, evict_engine = self._resident.popitem(last=False)
                logger.info("Evicting TTS engine '%s'", evict_name)
                evict_engine.unload()

            engine = _BUILDERS[name]()
            logger.info("Loading TTS engine '%s'...", name)
            engine.load()
            self._resident[name] = engine
            return engine


registry = EngineRegistry()

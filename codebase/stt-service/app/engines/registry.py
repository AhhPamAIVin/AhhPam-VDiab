"""Lazy-load and cache ASR engine instances by name.

Keeps at most settings.MAX_RESIDENT_ENGINES loaded at once — evicts the
least-recently-used engine first. Loading (and evicting) blocks on GPU work,
so callers must run get() off the event loop (see main.py).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.config import settings
from app.engines.faster_whisper_engine import FasterWhisperEngine
from app.engines.qwen_omni_engine import QwenOmniEngine

logger = logging.getLogger(__name__)

_BUILDERS = {
    "erax": lambda: FasterWhisperEngine("erax", settings.ERAX_MODEL_ID),
    "whisper": lambda: FasterWhisperEngine("whisper", settings.WHISPER_MODEL_ID),
    "qwen_omni": lambda: QwenOmniEngine("qwen_omni", settings.QWEN_OMNI_MODEL_ID),
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
            raise ValueError(f"Unknown ASR engine '{name}'. Choose one of {self.available_engines()}")

        with self._lock:
            if name in self._resident:
                self._resident.move_to_end(name)
                return self._resident[name]

            while len(self._resident) >= settings.MAX_RESIDENT_ENGINES:
                evict_name, evict_engine = self._resident.popitem(last=False)
                logger.info("Evicting ASR engine '%s' to free VRAM", evict_name)
                evict_engine.unload()

            engine = _BUILDERS[name]()
            logger.info("Loading ASR engine '%s' (%s)...", name, getattr(engine, "model_id", "?"))
            engine.load()
            self._resident[name] = engine
            return engine


registry = EngineRegistry()

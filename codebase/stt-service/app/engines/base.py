"""Common interface every ASR engine implements.

`transcribe` is called both for growing live-partial buffers and for a
finalized utterance — engines that can't do cheap incremental re-decoding
(Qwen2.5-Omni) are still valid, they just get called less often by main.py.
"""

from __future__ import annotations

from typing import Protocol


class ASREngine(Protocol):
    name: str

    def load(self) -> None:
        """Load weights onto the GPU. Called once, lazily, by the registry."""
        ...

    def unload(self) -> None:
        """Release GPU memory. Called by the registry under VRAM pressure."""
        ...

    def transcribe(self, pcm16: bytes, language: str = "vi") -> str:
        """Blocking call — run off the event loop via run_in_threadpool."""
        ...

"""Common interface every TTS engine implements.

`load`/`unload` are no-ops for today's stateless `openai` engine (see
openai_engine.py) but are real GPU-lifecycle hooks a future local engine
(e.g. F5-TTS-Vietnamese-ViVoice) needs — keeping the contract now means the
registry/router never change shape when that engine is added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    content_type: str  # "audio/mpeg" today; a local engine could emit "audio/wav"


class TTSEngine(Protocol):
    name: str

    def load(self) -> None:
        """Load weights/warm up a client. Called once, lazily, by the registry."""
        ...

    def unload(self) -> None:
        """Release resources. Called by the registry under resident-engine pressure."""
        ...

    def synthesize(
        self, text: str, *, voice: str | None = None, instructions: str | None = None
    ) -> SynthesisResult:
        """Blocking call — run off the event loop via run_in_threadpool."""
        ...

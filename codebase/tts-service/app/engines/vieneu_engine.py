"""VieNeu-TTS engine (phase 2) — pnnbao-ump/VieNeu-TTS, Apache-2.0.

Zero-shot voice cloning from a reference clip; auto-downloads its own
weights from Hugging Face on first load, no manual checkpoint management.
`voice`/`instructions` are ignored — this engine speaks in one cloned voice
(settings.REF_AUDIO_PATH), not named presets or steerable prosody, same
simplification f5_engine.py makes.

Import of the `vieneu` package happens inside load(), not at module top —
this module is only imported lazily from registry.py's builder, so a
phase-1-only install (base requirements.txt, no requirements-vieneu.txt)
never touches it.
"""

from __future__ import annotations

import io

from app.config import settings
from app.engines.base import SynthesisResult

_SAMPLE_RATE = 48_000


class VieneuTTSEngine:
    name = "vieneu_tts"

    def __init__(self) -> None:
        self._model = None

    def load(self) -> None:
        from vieneu import Vieneu

        self._model = Vieneu(backend=settings.VIENEU_BACKEND)

    def unload(self) -> None:
        self._model = None

    def synthesize(
        self, text: str, *, voice: str | None = None, instructions: str | None = None
    ) -> SynthesisResult:
        import soundfile as sf

        if self._model is None:
            self.load()

        audio = self._model.infer(text=text, ref_audio=settings.REF_AUDIO_PATH)

        buffer = io.BytesIO()
        sf.write(buffer, audio, _SAMPLE_RATE, format="WAV")
        return SynthesisResult(audio_bytes=buffer.getvalue(), content_type="audio/wav")

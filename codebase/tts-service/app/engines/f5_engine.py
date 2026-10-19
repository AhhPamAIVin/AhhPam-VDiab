"""F5-TTS-Vietnamese-ViVoice engine (phase 2) — hynt/F5-TTS-Vietnamese-ViVoice
on Hugging Face, CC-BY-NC-SA-4.0 (non-commercial only — confirmed fine for
this project, do not reuse this engine in anything commercial without
re-checking the license).

Zero-shot voice cloning, same reference-clip-based simplification as
vieneu_engine.py: `voice`/`instructions` are ignored, this engine always
speaks in the one cloned voice at settings.REF_AUDIO_PATH.

Import of `f5_tts`/`huggingface_hub` happens inside load(), not at module
top — lazy, same reasoning as vieneu_engine.py.

Confirmed live on the GPU box (2026-08-29): hynt/F5-TTS-Vietnamese-ViVoice
has no file actually named vocab.txt (a real 404, not just a stale
listing) — its "config.json" is mislabeled and is actually the vocab file
itself (a flat Vietnamese/pinyin character list), hence
settings.F5_VOCAB_FILENAME defaulting to "config.json". If a future
checkpoint update changes this, load() raises a clear, actionable error
(not a cryptic downstream stack trace) when hf_hub_download can't find
whatever F5_VOCAB_FILENAME currently points at.
"""

from __future__ import annotations

import io

from app.config import settings
from app.engines.base import SynthesisResult


class F5TTSEngine:
    name = "f5_tts_vietnamese_vivoice"

    def __init__(self) -> None:
        self._model = None

    def load(self) -> None:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError

        from f5_tts.api import F5TTS

        ckpt_file = hf_hub_download(settings.F5_CKPT_REPO, settings.F5_CKPT_FILENAME)
        try:
            vocab_file = hf_hub_download(settings.F5_CKPT_REPO, settings.F5_VOCAB_FILENAME)
        except EntryNotFoundError as exc:
            raise RuntimeError(
                f"'{settings.F5_VOCAB_FILENAME}' not found in "
                f"{settings.F5_CKPT_REPO} on Hugging Face — this checkpoint's "
                "file listing doesn't currently bundle one (known gap, see "
                "this module's docstring). Set F5_VOCAB_FILENAME to a local "
                "path or a different HF repo that has a matching Vietnamese "
                "vocab for this checkpoint before retrying."
            ) from exc

        self._model = F5TTS(
            model=settings.F5_MODEL_NAME,
            ckpt_file=ckpt_file,
            vocab_file=vocab_file,
            device=settings.F5_DEVICE or None,
        )

    def unload(self) -> None:
        self._model = None

    def synthesize(
        self, text: str, *, voice: str | None = None, instructions: str | None = None
    ) -> SynthesisResult:
        import soundfile as sf

        if self._model is None:
            self.load()

        wav, sample_rate, _spec = self._model.infer(
            ref_file=settings.REF_AUDIO_PATH,
            ref_text=settings.REF_TEXT,
            gen_text=text,
        )

        buffer = io.BytesIO()
        sf.write(buffer, wav, sample_rate, format="WAV")
        return SynthesisResult(audio_bytes=buffer.getvalue(), content_type="audio/wav")

"""CTranslate2/faster-whisper backed engine.

Covers two of the three candidates from the model comparison:
  - "whisper" -> openai large-v3-turbo
  - "erax"    -> erax-ai/EraX-WoW-Turbo-V1.1 (Vietnamese-tuned Whisper turbo)

Both are just a `model_id` swap since faster-whisper only cares about the
CTranslate2-format weights. If a checkpoint isn't already published in CT2
format, we convert it once into CT2_CACHE_DIR and load from there instead of
failing outright — some community Whisper fine-tunes only ship the original
transformers checkpoint.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

from app.config import settings

logger = logging.getLogger(__name__)


def _pcm16_to_float32(pcm16: bytes) -> np.ndarray:
    if not pcm16:
        return np.zeros(0, dtype=np.float32)
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def _sanitize(model_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", model_id)


class FasterWhisperEngine:
    def __init__(self, name: str, model_id: str):
        self.name = name
        self.model_id = model_id
        self._model: WhisperModel | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            self._model = WhisperModel(
                self.model_id,
                device=settings.ASR_DEVICE,
                compute_type=settings.ASR_COMPUTE_TYPE,
            )
            logger.info("Loaded %s directly as a CT2 model: %s", self.name, self.model_id)
            return
        except Exception as direct_load_error:  # noqa: BLE001 - fall through to conversion
            logger.warning(
                "%s is not a ready CT2 model (%s) — attempting on-the-fly conversion",
                self.model_id,
                direct_load_error,
            )

        local_dir = self._convert_to_ct2()
        self._model = WhisperModel(
            str(local_dir),
            device=settings.ASR_DEVICE,
            compute_type=settings.ASR_COMPUTE_TYPE,
        )
        logger.info("Loaded %s from converted CT2 cache: %s", self.name, local_dir)

    def _convert_to_ct2(self) -> Path:
        try:
            from ctranslate2.converters import TransformersConverter
        except ImportError as exc:  # pragma: no cover - defensive
            raise RuntimeError(
                f"'{self.model_id}' is not a CT2-ready checkpoint and converting it "
                "requires transformers/torch, which aren't in the default install. "
                "Install codebase/stt-service/requirements-qwen.txt (it carries "
                "torch+transformers) or point this engine at a CT2-published "
                "checkpoint instead."
            ) from exc

        out_dir = Path(settings.CT2_CACHE_DIR) / _sanitize(self.model_id)
        if not out_dir.exists() or not any(out_dir.iterdir()):
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                converter = TransformersConverter(self.model_id)
                converter.convert(str(out_dir), quantization=settings.ASR_COMPUTE_TYPE, force=True)
            except Exception as convert_error:
                # ctranslate2's TransformersConverter imports torch/transformers
                # lazily inside .convert() itself, so a missing-torch failure
                # surfaces here (sometimes as a raw NameError, not a clean
                # ImportError) rather than at the `import TransformersConverter`
                # line above — translate it into the same actionable message.
                for entry in list(out_dir.iterdir()):
                    entry.unlink() if entry.is_file() else None
                out_dir.rmdir()
                raise RuntimeError(
                    f"Converting '{self.model_id}' to CT2 format failed ({convert_error!r}). "
                    "This step needs transformers/torch — install "
                    "codebase/stt-service/requirements-qwen.txt in this venv "
                    "(or a separate 3.11/3.12 venv, see that file's header) "
                    "and retry."
                ) from convert_error
            self._copy_preprocessor_config(out_dir)
        return out_dir

    def _copy_preprocessor_config(self, out_dir: Path) -> None:
        """TransformersConverter.convert() doesn't carry preprocessor_config.json
        into the CT2 output — without it faster-whisper falls back to the
        legacy 80 mel-bin feature extractor. Whisper large-v3 and its
        fine-tunes (this is how EraX-WoW-Turbo-V1.1 was built) use 128 mel
        bins, so skipping this produces a silent shape-mismatch crash at
        transcribe() time rather than at load() time. Best-effort: some
        checkpoints don't publish this file at all, in which case
        faster-whisper's 80-bin default is presumably correct for them.
        """
        try:
            from huggingface_hub import hf_hub_download

            src = hf_hub_download(self.model_id, "preprocessor_config.json")
            (out_dir / "preprocessor_config.json").write_bytes(Path(src).read_bytes())
        except Exception as exc:  # noqa: BLE001 - best-effort, see docstring
            logger.warning(
                "Could not fetch preprocessor_config.json for %s (%s) — "
                "falling back to faster-whisper's default feature extractor.",
                self.model_id,
                exc,
            )

    def unload(self) -> None:
        self._model = None

    def transcribe(self, pcm16: bytes, language: str = "vi") -> str:
        if self._model is None:
            raise RuntimeError(f"{self.name} engine used before load()")
        audio = _pcm16_to_float32(pcm16)
        if audio.size < settings.SAMPLE_RATE * 0.1:  # <100ms, not worth decoding
            return ""
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            vad_filter=False,  # our own VAD already segmented this buffer
            beam_size=5,
        )
        return "".join(segment.text for segment in segments).strip()

"""Shared WAV -> 16kHz mono PCM16 loading for the eval scripts.

Both benchmark.py (single ad-hoc sample) and wer_benchmark.py (a whole test
set) need to turn an arbitrary-sample-rate WAV file into the same PCM16
bytes the engines/*.py transcribe() methods expect — kept in one place
instead of duplicated across both scripts.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
from app.config import settings


def resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    if orig_sr == settings.SAMPLE_RATE:
        return audio.astype(np.float32)
    duration = audio.shape[0] / orig_sr
    n_target = max(1, int(duration * settings.SAMPLE_RATE))
    x_old = np.linspace(0, duration, num=audio.shape[0], endpoint=False)
    x_new = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def to_pcm16(audio: np.ndarray) -> bytes:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def load_pcm16(path: str) -> bytes:
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = resample_to_16k(audio, sr)
    return to_pcm16(audio)

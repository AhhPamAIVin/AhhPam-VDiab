"""Lightweight energy-based speech/silence segmentation.

Deliberately not Silero/webrtcvad: those either need torch (which we want
optional, see engines/qwen_omni_engine.py) or a native extension with murky
Python 3.14 wheel support on this box's interpreter. RMS-over-frame with
hysteresis is enough to drive "when to re-decode a partial" and "when has
the patient stopped talking" — it doesn't need to be a perfect voice
detector, just consistent.

Durations are tracked in frame counts (audio-time), not wall-clock time —
silence/speech length must reflect how much audio was actually silent/
spoken, independent of how fast the caller happens to push() chunks (matters
for the live WS path under network jitter, and for offline/batch reuse like
eval/benchmark.py where chunks are fed back-to-back with no real delay).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from app.config import settings


class VADEventType(Enum):
    SPEECH_CONTINUES = auto()
    UTTERANCE_END = auto()
    NOTHING = auto()


@dataclass
class VADEvent:
    type: VADEventType


def _frame_rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float64)))))


class UtteranceSegmenter:
    """Feed it PCM16 chunks of any size; it internally re-frames to
    VAD_FRAME_MS and tracks whether we're "in an utterance" and whether
    enough trailing silence has passed (in audio-time) to consider it
    finished.
    """

    def __init__(self) -> None:
        self._frame_samples = int(settings.SAMPLE_RATE * settings.VAD_FRAME_MS / 1000)
        self._carry = np.zeros(0, dtype=np.int16)
        self.reset()

    def reset(self) -> None:
        self._carry = np.zeros(0, dtype=np.int16)
        self._speaking = False
        self._speech_frames = 0
        self._silence_frames = 0

    def push(self, pcm16: bytes) -> VADEvent:
        samples = np.frombuffer(pcm16, dtype=np.int16)
        buf = np.concatenate([self._carry, samples])

        n_frames = buf.size // self._frame_samples
        used = n_frames * self._frame_samples
        self._carry = buf[used:]

        utterance_ended = False
        for i in range(n_frames):
            frame = buf[i * self._frame_samples : (i + 1) * self._frame_samples]
            is_speech = _frame_rms(frame) >= settings.VAD_SPEECH_RMS_THRESHOLD

            if is_speech:
                self._speaking = True
                self._speech_frames += 1
                self._silence_frames = 0
                continue

            if not self._speaking:
                continue

            self._silence_frames += 1
            silence_ms = self._silence_frames * settings.VAD_FRAME_MS
            if silence_ms >= settings.VAD_SILENCE_MS:
                speech_ms = self._speech_frames * settings.VAD_FRAME_MS
                self._speaking = False
                self._speech_frames = 0
                self._silence_frames = 0
                if speech_ms >= settings.VAD_MIN_SPEECH_MS:
                    utterance_ended = True
                # else: too short to count as real speech (mic bump, cough) — drop it silently

        if utterance_ended:
            return VADEvent(VADEventType.UTTERANCE_END)
        if self._speaking:
            return VADEvent(VADEventType.SPEECH_CONTINUES)
        return VADEvent(VADEventType.NOTHING)

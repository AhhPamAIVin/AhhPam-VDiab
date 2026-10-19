"""Offline A/B comparison of the 3 ASR engines on Vietnamese audio.

Run from codebase/stt-service/:
    python -m eval.benchmark
    python -m eval.benchmark --audio ../../022878.wav some_other_sample.wav
    python -m eval.benchmark --engines erax whisper   # skip qwen_omni

No WER scoring — there's no reference transcript for the bundled sample.
Read the printed text by eye; wire in `jiwer` later if reference transcripts
show up. Engines load one at a time (MAX_RESIDENT_ENGINES defaults to 1),
so a 3-engine run reflects cold-load time for each, not just inference.
"""

from __future__ import annotations

import argparse
import time

from app.config import settings
from app.engines.registry import registry

from eval.audio_utils import load_pcm16


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ASR engines on Vietnamese audio")
    parser.add_argument("--audio", nargs="+", default=["../../022878.wav"])
    parser.add_argument("--engines", nargs="+", default=registry.available_engines())
    args = parser.parse_args()

    for audio_path in args.audio:
        pcm16 = load_pcm16(audio_path)
        duration_s = len(pcm16) / 2 / settings.SAMPLE_RATE
        print(f"\n=== {audio_path} ({duration_s:.1f}s) ===")
        for engine_name in args.engines:
            try:
                t0 = time.perf_counter()
                engine = registry.get(engine_name)
                load_s = time.perf_counter() - t0
                t1 = time.perf_counter()
                text = engine.transcribe(pcm16)
                infer_s = time.perf_counter() - t1
                print(f"[{engine_name:10s}] load={load_s:5.1f}s  infer={infer_s:5.2f}s  -> {text!r}")
            except Exception as exc:  # noqa: BLE001 - keep comparing the rest
                print(f"[{engine_name:10s}] FAILED: {exc}")


if __name__ == "__main__":
    main()

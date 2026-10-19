"""WER comparison of the 3 ASR engines against a real Vietnamese test set.

Run `python -m eval.fetch_testset` first to populate eval/testset/, then:
    python -m eval.wer_benchmark
    python -m eval.wer_benchmark --engines erax whisper
    python -m eval.wer_benchmark --limit 10 --report /tmp/wer_report.json

Uses jiwer for word-level WER — the standard FLEURS/VIVOS convention
(space-separated tokens, which for Vietnamese means syllable-level, not
whole-word). Both reference and hypothesis are NFC-normalized and
lowercased before scoring so Unicode diacritic composition differences
between the dataset and the ASR output don't inflate the error rate.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import unicodedata
from pathlib import Path

import jiwer
from app.engines.registry import registry

from eval.audio_utils import load_pcm16

TESTSET_DIR = Path(__file__).parent / "testset"
REFERENCES_PATH = TESTSET_DIR / "references.tsv"
AUDIO_DIR = TESTSET_DIR / "audio"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip().lower()


def load_samples(limit: int | None) -> list[tuple[str, str]]:
    if not REFERENCES_PATH.exists():
        raise SystemExit(
            f"{REFERENCES_PATH} not found — run `python -m eval.fetch_testset` first."
        )
    samples: list[tuple[str, str]] = []
    with REFERENCES_PATH.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)  # header
        for row in reader:
            if len(row) < 2:
                continue
            filename, transcript = row[0], row[1]
            samples.append((filename, transcript))
    return samples[:limit] if limit else samples


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WER benchmark of the 3 ASR engines on a real Vietnamese test set"
    )
    parser.add_argument("--engines", nargs="+", default=registry.available_engines())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report", default=None, help="optional path to write a JSON report")
    args = parser.parse_args()

    samples = load_samples(args.limit)
    print(f"Loaded {len(samples)} test utterances from {REFERENCES_PATH}")

    report: dict[str, dict] = {}
    for engine_name in args.engines:
        print(f"\n=== {engine_name} ===")
        try:
            engine = registry.get(engine_name)
        except Exception as exc:  # noqa: BLE001 - keep comparing the rest
            print(f"  FAILED to load: {exc}")
            report[engine_name] = {"error": str(exc)}
            continue

        references: list[str] = []
        hypotheses: list[str] = []
        per_file: list[dict] = []
        t0 = time.perf_counter()
        for filename, reference in samples:
            pcm16 = load_pcm16(str(AUDIO_DIR / filename))
            hypothesis = engine.transcribe(pcm16)
            references.append(_normalize(reference))
            hypotheses.append(_normalize(hypothesis))
            per_file.append(
                {"filename": filename, "reference": reference, "hypothesis": hypothesis}
            )
        elapsed = time.perf_counter() - t0

        wer = jiwer.wer(references, hypotheses) if references else float("nan")
        print(f"  WER: {wer:.3f}  ({len(samples)} utterances, {elapsed:.1f}s total)")
        report[engine_name] = {"wer": wer, "elapsed_s": elapsed, "samples": per_file}

    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote report to {args.report}")


if __name__ == "__main__":
    main()

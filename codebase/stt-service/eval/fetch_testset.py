"""Fetch a small Vietnamese ASR test set for the 3-engine comparison.

Source: Google FLEURS, vi_vn config, test split
(https://huggingface.co/datasets/google/fleurs, CC-BY-4.0). Chosen over
VIVOS specifically because FLEURS ships the test split as its own audio
archive + tsv — VIVOS bundles train+test into a single ~1.4GB tarball with
no way to fetch just the test portion.

The FLEURS test audio archive is still ~544MB in full, so this streams the
tar.gz and stops as soon as `--count` matching files have been extracted,
closing the connection early rather than downloading the whole thing.

Run from codebase/stt-service/:
    python -m eval.fetch_testset              # 20 utterances (default)
    python -m eval.fetch_testset --count 50
"""

from __future__ import annotations

import argparse
import csv
import tarfile
from pathlib import Path

import requests

TSV_URL = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/vi_vn/test.tsv"
AUDIO_TAR_URL = (
    "https://huggingface.co/datasets/google/fleurs/resolve/main/data/vi_vn/audio/test.tar.gz"
)

TESTSET_DIR = Path(__file__).parent / "testset"
AUDIO_DIR = TESTSET_DIR / "audio"
REFERENCES_PATH = TESTSET_DIR / "references.tsv"

# FLEURS tsv columns: id, filename, raw_transcription, transcription,
# phonemes, num_samples, gender. Column 3 (transcription) is already
# lowercased/punctuation-stripped — the right one to score WER against.
COL_FILENAME = 1
COL_TRANSCRIPTION = 3


def fetch_references() -> dict[str, str]:
    response = requests.get(TSV_URL, timeout=60)
    response.raise_for_status()
    references: dict[str, str] = {}
    for line in response.text.splitlines():
        cols = line.split("\t")
        if len(cols) <= COL_TRANSCRIPTION:
            continue
        references[cols[COL_FILENAME]] = cols[COL_TRANSCRIPTION]
    return references


def fetch_audio_subset(references: dict[str, str], count: int) -> list[tuple[str, str]]:
    """Stream the test audio tarball, keep the first `count` files that have
    a reference transcript, then stop the download.
    """
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    collected: list[tuple[str, str]] = []

    with requests.get(AUDIO_TAR_URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        with tarfile.open(fileobj=response.raw, mode="r|gz") as tar:
            for member in tar:
                if len(collected) >= count:
                    break
                if not member.isfile() or not member.name.endswith(".wav"):
                    continue
                filename = Path(member.name).name
                transcript = references.get(filename)
                if not transcript:
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                (AUDIO_DIR / filename).write_bytes(extracted.read())
                collected.append((filename, transcript))
    return collected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20, help="number of test utterances to fetch")
    args = parser.parse_args()

    print(f"Fetching FLEURS vi_vn test references from {TSV_URL} ...")
    references = fetch_references()
    print(f"  {len(references)} reference transcripts available")

    print(f"Streaming {AUDIO_TAR_URL} until {args.count} matching audio files are found ...")
    collected = fetch_audio_subset(references, args.count)
    print(f"  fetched {len(collected)} audio files into {AUDIO_DIR}")

    if len(collected) < args.count:
        print(
            f"  WARNING: only found {len(collected)}/{args.count} — the tar's internal "
            "ordering may not match the tsv, or the archive layout changed upstream."
        )

    with REFERENCES_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["filename", "transcript"])
        writer.writerows(collected)
    print(f"Wrote references to {REFERENCES_PATH}")


if __name__ == "__main__":
    main()

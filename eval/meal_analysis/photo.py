"""Meal photo recognition (Epic 1.6/1.9) on a labelled photo set.

The set is NOT in the repo yet: it must be photos the team took or holds a
licence for — never patient photos (the system does not keep them). Put the
images in eval/data/meal_photos/ beside a labels.json:

    {"cases": [
      {"id": "P01", "file": "pho_bo_01.jpg", "is_food": true,
       "expected": [{"food_id": "pho_bo", "quantity": 1, "unit": "tô"},
                    {"food_id": null, "name": "quẩy"}]},
      {"id": "N01", "file": "ban_phim.jpg", "is_food": false, "expected": []}
    ]}

Each photo goes through recognize_from_photo (real vision call, as in
production) and the recognised names through the nutrition lookup. Scored:
- name recall / precision: labelled foods (by food_id) recognised / recognised
  table foods that were labelled;
- unit validity: recognised table foods whose unit the table knows for that
  food (or a mass unit) — otherwise the default serving is assumed;
- non-food rejection: share of is_food=false photos answered is_food=false;
- latency p50/p95 of the vision call.

    python -m eval.meal_analysis.photo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from codebase.models.agents.nodes.meal.recognize_from_photo import recognize_from_photo  # noqa: E402
from codebase.models.agents.tools.nutrition_tools import (  # noqa: E402
    _MASS_UNITS,
    load_foods,
    lookup_food,
    normalize_unit,
)

PHOTO_DIR = ROOT / "eval" / "data" / "meal_photos"
RESULTS = ROOT / "eval" / "results"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def score_case(case: dict, recognised: list[dict]) -> dict:
    foods = {food.food_id: food for food in load_foods()}
    matched = []
    for item in recognised:
        match = lookup_food(item["name"])
        matched.append((item, match.food.food_id if match else None))
    wanted = [e["food_id"] for e in case["expected"] if e.get("food_id")]
    got_ids = [food_id for _, food_id in matched if food_id]
    found = sum(1 for food_id in wanted if food_id in got_ids)
    units_ok = sum(
        1
        for item, food_id in matched
        if food_id
        and (
            item.get("unit") is None
            or normalize_unit(item["unit"]) in foods[food_id].units
            or normalize_unit(item["unit"]) in _MASS_UNITS
        )
    )
    return {
        "expected_foods": len(wanted),
        "found": found,
        "recognised_table_foods": len(got_ids),
        "recognised_expected": sum(1 for food_id in got_ids if food_id in wanted),
        "units_ok": units_ok,
    }


async def run(only: set[str] | None) -> dict:
    labels = PHOTO_DIR / "labels.json"
    if not labels.exists():
        raise SystemExit(
            f"No photo set: {labels} is missing. See this module's docstring for the layout "
            "(licensed/own photos only, never patient photos)."
        )
    cases = json.loads(labels.read_text(encoding="utf-8"))["cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]
    rows = []
    for case in cases:
        path = PHOTO_DIR / case["file"]
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        started = time.monotonic()
        try:
            recognition = await recognize_from_photo(path.read_bytes(), media_type)
            is_food, items, error = recognition.is_food, [i.model_dump() for i in recognition.items], None
        except Exception as exc:  # one bad photo must not end the run
            is_food, items, error = None, [], type(exc).__name__
        latency = round(time.monotonic() - started, 2)
        rows.append(
            {
                **case,
                "is_food_got": is_food,
                "items": items,
                "error": error,
                "latency_s": latency,
                "score": score_case(case, items),
            }
        )
        print(f"{case['id']:5} {latency:>5}s is_food={is_food} items={[i['name'] for i in items]}")

    food_rows = [r for r in rows if r["is_food"]]
    non_food = [r for r in rows if not r["is_food"]]
    expected = sum(r["score"]["expected_foods"] for r in food_rows)
    recognised = sum(r["score"]["recognised_table_foods"] for r in food_rows)
    latencies = [r["latency_s"] for r in rows if r["error"] is None]
    summary = {
        "photos": len(rows),
        "food_photos": len(food_rows),
        "non_food_photos": len(non_food),
        "name_recall": round(sum(r["score"]["found"] for r in food_rows) / expected, 3) if expected else None,
        "name_precision": round(sum(r["score"]["recognised_expected"] for r in food_rows) / recognised, 3)
        if recognised
        else None,
        "unit_validity": round(sum(r["score"]["units_ok"] for r in food_rows) / recognised, 3) if recognised else None,
        "non_food_rejection": round(sum(r["is_food_got"] is False for r in non_food) / len(non_food), 3)
        if non_food
        else None,
        "errors": sum(r["error"] is not None for r in rows),
        "latency_p50_s": _percentile(latencies, 0.5),
        "latency_p95_s": _percentile(latencies, 0.95),
    }
    return {"run_at": datetime.now(UTC).isoformat(), "summary": summary, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    report = asyncio.run(run(set(args.only.split(",")) if args.only else None))
    out = args.report or RESULTS / f"meal_photo-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {out}")


if __name__ == "__main__":
    main()

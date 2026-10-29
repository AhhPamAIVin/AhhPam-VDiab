"""Meal text analysis (Epic 1.9) on eval/data/meal_analysis_cases.json.

Runs the production steps of the meal analysis graph — parse_meal_items
(rule split first, LLM only for what the table doesn't know) ->
lookup_nutrition -> aggregate — on what a patient types in the meal form, and
scores them against hand labels (food + amount the patient said).

Expected carbs come from those labels through the SAME nutrition table, so a
carb error here is a splitting/matching/portion error, not an error in the
table's numbers — those are checked separately against Bảng thành phần thực
phẩm Việt Nam (NUTRITION_DB_VERSION says whether that has happened).

Metrics, per mode:
- item recall / precision: expected foods found (by food_id) / matched foods
  that were expected;
- portion accuracy: found foods whose grams are within 5% of the label's;
- unknown-food handling: a food the table lacks must stay unmatched (carbs
  null) — "no-match correct" counts those, "false match" counts the ones
  matched to some other food instead;
- carb MAE (g/meal) and carb_load agreement, over cases whose foods are all in
  the table (otherwise the expected total is itself unknown);
- case accuracy: every food right with the right portion, nothing extra.

Two modes: `pipeline` (as in production, LLM when needed) and `rule_only`
(LLM made to fail, so parse_meal_items keeps its rule split) — the gap is
what the LLM step buys.

    python -m eval.meal_analysis.text_analysis
    python -m eval.meal_analysis.text_analysis --only M05,M13 --modes pipeline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from codebase.backend.config import get_settings  # noqa: E402
from codebase.models.agents.nodes.meal import parse_meal_items as parse_module  # noqa: E402
from codebase.models.agents.nodes.meal.analysis import aggregate, lookup_nutrition  # noqa: E402
from codebase.models.agents.tools.nutrition_tools import (  # noqa: E402
    NUTRITION_DB_VERSION,
    load_foods,
    portion_grams,
)

CASES = ROOT / "eval" / "data" / "meal_analysis_cases.json"
RESULTS = ROOT / "eval" / "results"
GRAMS_TOLERANCE = 0.05
MODES = ("pipeline", "rule_only")


def _foods() -> dict:
    return {food.food_id: food for food in load_foods()}


def expected_items(case: dict) -> list[dict]:
    """Labels with grams/carbs filled from the table (None for unknown foods)."""
    foods = _foods()
    out = []
    for label in case["expected"]:
        food_id = label["food_id"]
        if food_id is None:
            out.append({**label, "grams": None, "carbs_g": None})
            continue
        if food_id not in foods:
            raise ValueError(f"{case['id']}: food_id {food_id!r} is not in vn_foods.csv")
        grams, _ = portion_grams(foods[food_id], label.get("quantity"), label.get("unit"))
        out.append({**label, "grams": grams, "carbs_g": grams * foods[food_id].carbs_per_100g / 100})
    return out


def _grams_ok(expected: float, got: float | None) -> bool:
    return got is not None and abs(got - expected) <= GRAMS_TOLERANCE * expected


def score_case(expected: list[dict], items: list[dict], load_thresholds: tuple[float, float]) -> dict:
    remaining = list(items)
    found = portion_right = 0
    for want in (e for e in expected if e["food_id"] is not None):
        hit = next((i for i in remaining if i.get("matched_food_id") == want["food_id"]), None)
        if hit is None:
            continue
        remaining.remove(hit)
        found += 1
        portion_right += _grams_ok(want["grams"], hit.get("grams"))
    expected_known = sum(e["food_id"] is not None for e in expected)
    expected_unknown = len(expected) - expected_known
    # Leftovers: unmatched items should be the unknown foods; matched ones are wrong foods.
    left_unmatched = sum(i.get("matched_food_id") is None for i in remaining)
    left_matched = len(remaining) - left_unmatched
    no_match_correct = min(expected_unknown, left_unmatched)
    matched_total = sum(i.get("matched_food_id") is not None for i in items)

    row = {
        "expected_known": expected_known,
        "expected_unknown": expected_unknown,
        "found": found,
        "portion_right": portion_right,
        "matched_total": matched_total,
        "wrong_foods": left_matched,
        "no_match_correct": no_match_correct,
        "false_match": min(expected_unknown - no_match_correct, left_matched),
        "exact": (
            found == expected_known
            and portion_right == found
            and left_matched == 0
            and left_unmatched == expected_unknown
        ),
    }
    if expected_unknown == 0:
        want_total = sum(e["carbs_g"] for e in expected)
        got_total, _, got_load = aggregate(items, low_max_g=load_thresholds[0], high_min_g=load_thresholds[1])
        _, _, want_load = aggregate(
            [{"carbs_g": e["carbs_g"]} for e in expected], low_max_g=load_thresholds[0], high_min_g=load_thresholds[1]
        )
        row.update(
            expected_carbs_g=round(want_total, 1),
            carbs_g=got_total,
            # A meal whose carbs came out unknown is off by the whole expected total.
            carb_abs_error=round(abs((got_total or 0.0) - want_total), 1),
            carb_load_ok=got_load == want_load,
        )
    return row


class _FailingLLM:
    model_name = "disabled"

    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError("LLM disabled for the rule_only mode")


async def analyse(description: str, portion: str | None, mode: str) -> tuple[list[dict], str]:
    if mode == "rule_only":
        with patch.object(parse_module, "get_llm", lambda *_a, **_k: _FailingLLM()):
            drafts, model_version = await parse_module.parse_meal_items(description, portion)
    else:
        drafts, model_version = await parse_module.parse_meal_items(description, portion)
    return lookup_nutrition(drafts), model_version


def summarize(rows: list[dict]) -> dict:
    scores = [r["score"] for r in rows]
    known = sum(s["expected_known"] for s in scores)
    found = sum(s["found"] for s in scores)
    matched = sum(s["matched_total"] for s in scores)
    unknown = sum(s["expected_unknown"] for s in scores)
    carb_rows = [s for s in scores if "carb_abs_error" in s]
    latencies = sorted(r["latency_s"] for r in rows)
    return {
        "case_accuracy": round(sum(s["exact"] for s in scores) / len(scores), 3),
        "item_recall": round(found / known, 3) if known else None,
        "item_precision": round(found / matched, 3) if matched else None,
        "portion_accuracy": round(sum(s["portion_right"] for s in scores) / found, 3) if found else None,
        "unknown_foods": unknown,
        "no_match_correct_rate": round(sum(s["no_match_correct"] for s in scores) / unknown, 3) if unknown else None,
        "false_match_rate": round(sum(s["false_match"] for s in scores) / unknown, 3) if unknown else None,
        "carb_cases": len(carb_rows),
        "carb_mae_g": round(sum(s["carb_abs_error"] for s in carb_rows) / len(carb_rows), 1) if carb_rows else None,
        "carb_max_error_g": max((s["carb_abs_error"] for s in carb_rows), default=None),
        "carb_load_agreement": round(sum(s["carb_load_ok"] for s in carb_rows) / len(carb_rows), 3)
        if carb_rows
        else None,
        "llm_used_rate": round(sum(r["model_version"] != parse_module.RULE_BASED_VERSION for r in rows) / len(rows), 3),
        "latency_p50_s": latencies[len(latencies) // 2],
        "latency_p95_s": latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))],
        "by_tag": {
            tag: f"{sum(r['score']['exact'] for r in rows if tag in r['tags'])}/{count}"
            for tag, count in sorted(Counter(t for r in rows for t in r["tags"]).items())
        },
    }


async def run(only: set[str] | None, modes: list[str]) -> dict:
    settings = get_settings()
    thresholds = (settings.meal_carb_low_max_g, settings.meal_carb_high_min_g)
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]
    report: dict = {
        "run_at": datetime.now(UTC).isoformat(),
        "cases": len(cases),
        "nutrition_db_version": NUTRITION_DB_VERSION,
        "model": settings.model_name if hasattr(settings, "model_name") else None,
        "carb_load_thresholds_g": thresholds,
    }
    for mode in modes:
        rows = []
        for case in cases:
            expected = expected_items(case)
            started = time.monotonic()
            try:
                items, model_version = await analyse(case["description"], case.get("portion"), mode)
                error = None
            except Exception as exc:  # one bad case must not end the run
                items, model_version, error = [], "error", type(exc).__name__
            latency = round(time.monotonic() - started, 2)
            score = score_case(expected, items, thresholds)
            rows.append(
                {
                    "id": case["id"],
                    "tags": case["tags"],
                    "description": case["description"],
                    "portion": case.get("portion"),
                    "expected": expected,
                    "items": items,
                    "model_version": model_version,
                    "error": error,
                    "latency_s": latency,
                    "score": score,
                }
            )
            flag = "OK " if score["exact"] else "XX "
            carb = f" carb {score.get('carbs_g')}/{score.get('expected_carbs_g')}" if "carb_abs_error" in score else ""
            print(f"[{mode}] {flag}{case['id']} {latency:>5}s {model_version:>10}{carb}  {case['description']}")
        report[mode] = {"summary": summarize(rows), "rows": rows}
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--modes", default=",".join(MODES), help=f"comma-separated, from {MODES}")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    modes = [m for m in args.modes.split(",") if m]
    if unknown := set(modes) - set(MODES):
        parser.error(f"unknown modes: {sorted(unknown)}")
    report = asyncio.run(run(set(args.only.split(",")) if args.only else None, modes))
    out = args.report or RESULTS / f"meal_analysis_text-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({m: report[m]["summary"] for m in modes}, ensure_ascii=False, indent=2))
    print(f"report: {out}")


if __name__ == "__main__":
    main()

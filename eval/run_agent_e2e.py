#!/usr/bin/env python3
"""Run golden AI cases against a real deployed `/api/v1/agent/chat` endpoint.

Environment variables:
  EVAL_BASE_URL, EVAL_PATIENT_TOKEN, and optionally EVAL_DOCTOR_TOKEN.
The target environment must already contain the intended seeded patient data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from eval.harness.datasets import load_cases  # noqa: E402
from eval.harness.retrieval import _title_matches  # noqa: E402


async def run(base_url: str, patient_token: str, doctor_token: str | None, patient_id: str | None) -> dict:
    details = []
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        for case in load_cases("ai_scenarios_30.json"):
            token = doctor_token if case.get("user_role") == "DOCTOR" else patient_token
            if not token:
                details.append({"id": case["id"], "status": "SKIPPED", "reason": "missing role token"})
                continue
            is_doctor = case.get("user_role") == "DOCTOR"
            if is_doctor and not patient_id:
                details.append({"id": case["id"], "status": "SKIPPED", "reason": "missing EVAL_PATIENT_ID"})
                continue
            reset_params = {"patient_id": patient_id} if is_doctor else None
            await client.delete(
                "/api/v1/agent/chat/thread",
                params=reset_params,
                headers={"Authorization": f"Bearer {token}"},
            )
            started = time.perf_counter()
            request_body = {"message": case["prompt"]}
            if is_doctor:
                request_body["patient_id"] = patient_id
            response = await client.post(
                "/api/v1/agent/chat",
                json=request_body,
                headers={"Authorization": f"Bearer {token}"},
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            expected = case["expected_behavior"]
            answer = str(body.get("response") or "")
            source_titles = [str(source.get("title") or "") for source in body.get("sources", [])]
            forbidden_hits = [text for text in expected.get("forbidden_content", []) if text.lower() in answer.lower()]
            required_sources = expected.get("required_source_titles", [])
            source_hits = [
                title for title in required_sources if any(_title_matches(title, actual) for actual in source_titles)
            ]
            # No "intent" assertion here: AgentChatResponse (schemas.py) never
            # exposes routing intent over HTTP by design (it's internal graph
            # state) — comparing body.get("intent") against expected["intent"]
            # compared None against a string on every single case, silently
            # failing 38/38 regardless of actual correctness. Found running
            # this script for the first time (2026-08-27); the golden-scenario
            # runner (eval/respond_agent/golden_scenarios.py) is the one that
            # checks intent, since it drives the graph in-process and can read
            # state["intent"] directly rather than the public response body.
            assertions = {
                "http_200": response.status_code == 200,
                "forbidden_content": not forbidden_hits,
                "sources_present": not expected.get("must_cite_sources") or bool(source_titles),
                "required_sources": not required_sources or len(source_hits) == len(required_sources),
            }
            details.append(
                {
                    "id": case["id"],
                    "category": case["category"],
                    "status": "EXECUTED",
                    "passed": all(assertions.values()),
                    "latency_ms": latency_ms,
                    "assertions": assertions,
                    "prompt": case["prompt"],
                    "response": answer,
                    "sources": body.get("sources", []),
                    "forbidden_hits": forbidden_hits,
                    "raw": body,
                }
            )
    executed = [item for item in details if item["status"] == "EXECUTED"]
    latencies = sorted(item["latency_ms"] for item in executed)
    return {
        "schema_version": "1.0",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "model": os.environ.get("EVAL_MODEL_NAME", "deployment default (not reported by API)"),
        "executed": len(executed),
        "skipped": len(details) - len(executed),
        "passed": sum(item["passed"] for item in executed),
        "pass_rate": sum(item["passed"] for item in executed) / len(executed) if executed else 0,
        "latency_ms": {
            "avg": sum(latencies) / len(latencies) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "details": details,
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return values[min(round((len(values) - 1) * quantile), len(values) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("eval/results/agent-e2e.json"))
    args = parser.parse_args()
    base_url = os.environ.get("EVAL_BASE_URL", "http://localhost:8100")
    patient_token = os.environ.get("EVAL_PATIENT_TOKEN")
    if not patient_token:
        parser.error("EVAL_PATIENT_TOKEN is required")
    report = asyncio.run(
        run(base_url, patient_token, os.environ.get("EVAL_DOCTOR_TOKEN"), os.environ.get("EVAL_PATIENT_ID"))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["executed"] and report["passed"] == report["executed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

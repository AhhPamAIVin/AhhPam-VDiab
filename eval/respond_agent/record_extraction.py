"""Recording from chat (Epic 8): the patient agent's draft_records vs the old
regex parser, on eval/data/record_extraction_cases.json.

The agent runs for real (agent_plan with the configured LLM); only the
knowledge search is stubbed (it does not affect what is recorded, and would
load the embedding model). What is scored is what would reach the patient as
a confirmation card: the drafts AFTER code validation (draft_from_agent_proposal),
so a value the model invented but the message doesn't contain counts as not
drafted, exactly as in production.

Metrics, per extractor:
- case accuracy: every expected record drafted with every checked field right,
  and nothing else;
- false-record rate: share of negative cases (questions, hypotheticals, someone
  else's numbers, plans) where anything was drafted — the costly error;
- recall: share of expected records drafted (type match);
- field accuracy: share of checked fields right, over records drafted.

    python -m eval.respond_agent.record_extraction            # agent + regex parser
    python -m eval.respond_agent.record_extraction --only B1,J3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session, SQLModel, StaticPool, create_engine

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "eval" / "data" / "record_extraction_cases.json"
RESULTS = ROOT / "eval" / "results"


def _check(expected: dict, got: dict) -> tuple[int, int]:
    """(fields right, fields checked) for one matched record."""
    right = checked = 0
    for key, want in expected.items():
        if key == "record_type":
            continue
        checked += 1
        if key in ("description_contains", "medication_contains"):
            # Accent-insensitive: "com tam" typed, "cơm tấm" drafted is right.
            from codebase.backend.agent.record_actions import _without_accents

            field = "description" if key == "description_contains" else "medication_name"
            ok = _without_accents(want) in _without_accents(str(got.get(field) or ""))
        elif key == "has_time":
            time_value = next(
                (got.get(f) for f in ("measured_at", "meal_time", "activity_time") if f in got), None
            )
            ok = (time_value is not None) == want
        elif isinstance(want, (int, float)) and not isinstance(want, bool):
            try:
                ok = abs(float(got.get(key)) - want) < 0.01
            except (TypeError, ValueError):
                ok = False
        else:
            ok = got.get(key) == want
        right += ok
    return right, checked


def score_case(expected: list[dict], drafts: list[dict]) -> dict:
    remaining = list(drafts)
    fields_right = fields_checked = matched = 0
    exact = len(expected) == len(drafts)
    for want in expected:
        candidates = [d for d in remaining if d.get("record_type") == want["record_type"]]
        if not candidates:
            exact = False
            continue
        best = max(candidates, key=lambda d: _check(want, d)[0])
        remaining.remove(best)
        matched += 1
        right, checked = _check(want, best)
        fields_right += right
        fields_checked += checked
        exact = exact and right == checked
    return {
        "exact": exact and not remaining,
        "matched": matched,
        "extra": len(remaining),
        "fields_right": fields_right,
        "fields_checked": fields_checked,
    }


def _session_with_patient() -> tuple[Session, object]:
    import codebase.backend.main  # noqa: F401 — every table on the metadata
    from codebase.backend.auth.models import User, UserRole
    from codebase.backend.medications.models import Medication

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    patient = User(email="eval@example.com", full_name="Eval", role=UserRole.PATIENT, hashed_password="x")
    session.add(patient)
    session.commit()
    session.add(
        Medication(
            patient_id=patient.id, drug_name="Metformin", dose="500mg", frequency="2 lần/ngày",
            start_date=datetime.utcnow() - timedelta(days=60), created_by=patient.id,
        )
    )
    session.commit()
    return session, patient


async def _agent_drafts(session: Session, patient, message: str) -> tuple[list[dict], list[str], list[dict]]:
    """(validated drafts, tool calls, the model's raw proposals) — the raw
    proposals show whether a miss is the model's or the validator's."""
    from codebase.backend.agent import record_actions
    from codebase.models.agents.nodes.agent_plan import agent_plan

    proposals: list[dict] = []
    validate = record_actions.draft_from_agent_proposal

    def _recording(message_text, proposal, **kwargs):
        proposals.append(proposal)
        return validate(message_text, proposal, **kwargs)

    state = {"message": message, "patient_id": patient.id, "user_role": "patient", "messages": []}
    with (
        patch("codebase.models.agents.tools.agent_tools.search_approved_knowledge", return_value=[]),
        patch.object(record_actions, "draft_from_agent_proposal", _recording),
    ):
        result = await agent_plan(state, {"configurable": {"session": session}})
    return list(result.get("agent_record_drafts") or []), list(result.get("agent_tool_calls") or []), proposals


def _parser_drafts(message: str) -> list[dict]:
    from codebase.backend.agent.record_actions import analyze_message_drafts

    drafts, _ = analyze_message_drafts(message, now=datetime.now(UTC))
    return drafts


def _summarize(rows: list[dict], key: str) -> dict:
    positives = [r for r in rows if r["expected"]]
    negatives = [r for r in rows if not r["expected"]]
    scores = [r[key] for r in rows]
    expected_records = sum(len(r["expected"]) for r in positives)
    fields_checked = sum(s["fields_checked"] for s in scores)
    return {
        "case_accuracy": round(sum(s["exact"] for s in scores) / len(scores), 3),
        "false_record_rate": round(
            sum(bool(r[key]["extra"]) for r in negatives) / len(negatives), 3
        ) if negatives else None,
        "recall": round(sum(r[key]["matched"] for r in positives) / expected_records, 3) if expected_records else None,
        "field_accuracy": round(sum(s["fields_right"] for s in scores) / fields_checked, 3) if fields_checked else None,
        "by_group": {
            group: f"{sum(r[key]['exact'] for r in rows if r['group'] == group)}/{count}"
            for group, count in Counter(r["group"] for r in rows).items()
        },
    }


async def run(only: set[str] | None) -> dict:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]
    session, patient = _session_with_patient()
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            drafts, calls, proposals = await _agent_drafts(session, patient, case["message"])
            error = None
        except Exception as exc:  # one bad case must not end the run
            drafts, calls, proposals, error = [], [], [], type(exc).__name__
        latency = round(time.monotonic() - started, 2)
        parser = _parser_drafts(case["message"])
        row = {
            **case,
            "agent_proposals": [{k: v for k, v in p.items() if v is not None} for p in proposals],
            "agent_drafts": drafts,
            "agent_tool_calls": calls,
            "agent_error": error,
            "agent_latency_s": latency,
            "agent": score_case(case["expected"], drafts),
            "parser_drafts": parser,
            "parser": score_case(case["expected"], parser),
        }
        rows.append(row)
        flag = "OK " if row["agent"]["exact"] else "XX "
        print(f"{flag}{case['id']:4} agent={len(drafts)} parser={len(parser)} {latency:>5}s  {case['message']}")
    latencies = sorted(r["agent_latency_s"] for r in rows)
    return {
        "run_at": datetime.now(UTC).isoformat(),
        "cases": len(rows),
        "agent": {**_summarize(rows, "agent"), "latency_p50_s": latencies[len(latencies) // 2]},
        "regex_parser": _summarize(rows, "parser"),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    report = asyncio.run(run(set(args.only.split(",")) if args.only else None))
    out = args.report or RESULTS / f"record_extraction-{datetime.now(UTC):%Y%m%d-%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("cases", "agent", "regex_parser")}, ensure_ascii=False, indent=2))
    print(f"report: {out}")


if __name__ == "__main__":
    main()

"""Personal-data QA faithfulness — does the LLM correctly REPORT the patient's own DB
data it was handed, not just correctly FETCH it?

Tool-calling in this codebase is not LLM-driven function-calling: fetch_data.py
deterministically calls all 4 DB tools every time (see docs/AI_Eval_Metrics_Plan.md §2.3,
tests/test_backend/test_tool_data_fetch_correctness.py), so "the LLM picks the wrong tool"
is not a failure mode that can happen here. What CAN go wrong, and had zero eval coverage
before this script: synthesize_qa.py hands the LLM `tool_results` essentially verbatim
(`_format_patient_data_block`, a `str(tool_results)`-style dump) and the LLM has to
correctly read facts back out of it — a real hallucination/mis-reporting risk distinct from
DB-fetch correctness. eval/respond_agent/golden_scenarios.py can't catch this either: every one of its
cases uses a fresh, empty patient with no real health data to be faithful *to*.

Each case here seeds a patient with KNOWN, exactly-computable data (specific glucose
readings, a specific medication, a specific assigned doctor), asks the REAL agent a
personal-data question about it (real OpenAI + real fetch_data/DB calls — no mocking
anywhere), and checks the response states the correct value: exact substring match for
names (medication, doctor), numeric-with-tolerance match for computed values (average
glucose, reading count) since an LLM may phrase/round a number slightly differently.

Logs a real experiment to LangSmith (dataset "personal-data-faithfulness-v1") and writes a
local JSON report under eval/results/.

Run (from repo root):
    python -m eval.respond_agent.personal_data_faithfulness
    python -m eval.respond_agent.personal_data_faithfulness --report /tmp/personal_data_faithfulness.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()  # must run before langsmith/langchain read LANGCHAIN_*/OPENAI_API_KEY from os.environ

from langchain_core.messages import HumanMessage  # noqa: E402
from langsmith import Client  # noqa: E402
from sqlmodel import Session, SQLModel, StaticPool, create_engine  # noqa: E402

from codebase.backend.auth.models import User, UserRole  # noqa: E402
from codebase.backend.auth.security import hash_password  # noqa: E402
from codebase.backend.health.service import create_glucose_record  # noqa: E402
from codebase.backend.medications.service import create_medication  # noqa: E402
from codebase.backend.patients.models import PatientAssignment  # noqa: E402
from codebase.models.agents.graph import agent  # noqa: E402
from codebase.models.rag.ingest import REPO_ROOT  # noqa: E402

REPORT_DIR = REPO_ROOT / "eval" / "results"
DATASET_NAME = "personal-data-faithfulness-v1"

NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
GLUCOSE_READINGS = [100.0, 110.0, 120.0]  # avg = 110.0 exactly, see metrics_tools._bucket_stats
DOCTOR_NAME = "BS. Nguyễn Văn Khang"
MEDICATION_NAME = "Metformin"

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Case:
    case_id: str
    message: str
    check: str  # "substring" | "numeric"
    expected_text: str | None = None
    expected_number: float | None = None
    tolerance: float = 0.0


_CASES = [
    Case("own_medication_name", "Tôi đang dùng loại thuốc nào?", "substring", expected_text=MEDICATION_NAME),
    Case(
        "own_average_glucose",
        "Đường huyết trung bình của tôi trong 7 ngày qua là bao nhiêu?",
        "numeric",
        expected_number=sum(GLUCOSE_READINGS) / len(GLUCOSE_READINGS),
        tolerance=1.0,
    ),
    Case("own_assigned_doctor", "Bác sĩ điều trị của tôi là ai?", "substring", expected_text="Khang"),
    Case(
        "own_glucose_reading_count",
        "Tôi đã đo đường huyết bao nhiêu lần trong 7 ngày qua?",
        "numeric",
        expected_number=float(len(GLUCOSE_READINGS)),
        tolerance=0.0,
    ),
]
_CASES_BY_ID = {c.case_id: c for c in _CASES}


def _make_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_patient_with_known_data(session: Session) -> User:
    patient = User(
        email=f"eval-faithfulness-{uuid4().hex[:8]}@diab.example.com",
        full_name="Eval Faithfulness Patient",
        role=UserRole.PATIENT,
        hashed_password=hash_password("EvalPass123"),
    )
    doctor = User(
        email=f"eval-faithfulness-doctor-{uuid4().hex[:8]}@diab.example.com",
        full_name=DOCTOR_NAME,
        role=UserRole.DOCTOR,
        hashed_password=hash_password("EvalPass123"),
    )
    session.add(patient)
    session.add(doctor)
    session.commit()
    session.refresh(patient)
    session.refresh(doctor)

    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    for day_offset, value in enumerate(GLUCOSE_READINGS):
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=value,
            unit="mg/dL",
            measured_at=NOW - timedelta(days=day_offset + 1, hours=1),
            note=None,
            created_by=patient.id,
        )
    create_medication(
        session,
        patient_id=patient.id,
        drug_name=MEDICATION_NAME,
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=90),
        note=None,
        created_by=patient.id,
    )
    session.commit()
    return patient


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for match in _NUMBER_RE.findall(text):
        try:
            numbers.append(float(match.replace(",", ".")))
        except ValueError:
            continue
    return numbers


async def run_case(case: Case) -> dict:
    session = _make_session()
    patient = _seed_patient_with_known_data(session)
    state = {
        "message": case.message,
        "request_kind": "chat",
        "messages": [HumanMessage(content=case.message)],
        "user_id": patient.id,
        "user_role": "patient",
        "patient_id": None,
        "retry_count": 0,
    }
    config = {"configurable": {"thread_id": f"eval-faithfulness-{uuid4()}", "session": session}}

    try:
        final_state = await asyncio.wait_for(agent.ainvoke(state, config=config), timeout=90)
    except Exception:
        try:
            final_state = await asyncio.wait_for(agent.ainvoke(state, config=config), timeout=90)
        except Exception as exc:
            session.close()
            return {"response": "", "error": str(exc)}
    session.close()

    return {"response": final_state.get("response", "") or ""}


def _target(inputs: dict) -> dict:
    case = _CASES_BY_ID[inputs["case_id"]]
    return asyncio.run(run_case(case))


def _response_is_faithful(run, example) -> dict:
    outputs_ref = example.outputs or {}
    response_text = (run.outputs or {}).get("response") or ""
    check = outputs_ref.get("check")

    if check == "substring":
        expected = outputs_ref.get("expected_text") or ""
        score = int(expected.lower() in response_text.lower())
        return {"key": "response_is_faithful", "score": score, "comment": f"expected substring: {expected!r}"}

    expected_number = outputs_ref.get("expected_number")
    tolerance = outputs_ref.get("tolerance", 0.0)
    found_numbers = _extract_numbers(response_text)
    match = any(abs(n - expected_number) <= tolerance for n in found_numbers)
    return {
        "key": "response_is_faithful",
        "score": int(match),
        "comment": f"expected {expected_number} (+/-{tolerance}), found numbers: {found_numbers}",
    }


def ensure_dataset(client: Client) -> None:
    if client.has_dataset(dataset_name=DATASET_NAME):
        print(f"LangSmith dataset {DATASET_NAME!r} already exists, reusing (not re-populating).")
        return
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Personal-data QA faithfulness: does the LLM correctly report facts from a "
            "patient's real seeded DB data (tool_results), not just fetch it correctly? "
            "See docs/AI_Eval_Metrics_Plan.md."
        ),
    )
    examples = [
        {
            "inputs": {"case_id": c.case_id, "message": c.message},
            "outputs": {
                "check": c.check,
                "expected_text": c.expected_text,
                "expected_number": c.expected_number,
                "tolerance": c.tolerance,
            },
        }
        for c in _CASES
    ]
    client.create_examples(dataset_id=dataset.id, examples=examples)
    print(f"Created LangSmith dataset {DATASET_NAME!r} with {len(examples)} examples.")


def _row_to_report_entry(row: dict) -> dict:
    scores = {r.key: r.score for r in row["evaluation_results"]["results"]}
    comments = {r.key: r.comment for r in row["evaluation_results"]["results"]}
    outputs = row["run"].outputs or {}
    return {
        "case_id": row["example"].inputs.get("case_id"),
        "message": row["example"].inputs.get("message"),
        "response": outputs.get("response"),
        "response_is_faithful": scores.get("response_is_faithful"),
        "detail": comments.get("response_is_faithful"),
    }


def _write_report(report_path: Path, per_case: list[dict]) -> None:
    scored = [row["response_is_faithful"] for row in per_case if row["response_is_faithful"] is not None]
    faithfulness_rate = sum(scored) / len(scored) if scored else None
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "num_cases": len(per_case),
        "faithfulness_rate": faithfulness_rate,
        "cases": per_case,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\nFaithfulness rate: {faithfulness_rate:.3f}" if faithfulness_rate is not None else "\nFaithfulness rate: n/a"
    )
    for row in per_case:
        status = "OK" if row["response_is_faithful"] else "FAIL"
        print(f"  {status} {row['case_id']}: {row['detail']}")
    print(f"Wrote report to {report_path}")


async def main_async(args: argparse.Namespace) -> None:
    client = Client()
    ensure_dataset(client)

    results = client.evaluate(
        _target,
        data=DATASET_NAME,
        evaluators=[_response_is_faithful],
        experiment_prefix="personal-data-faithfulness",
        max_concurrency=2,
    )

    per_case = [_row_to_report_entry(row) for row in results]
    report_path = (
        Path(args.report)
        if args.report
        else REPORT_DIR / f"personal_data_faithfulness_{int(datetime.now(UTC).timestamp())}.json"
    )
    _write_report(report_path, per_case)


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal-data QA faithfulness eval (LangSmith)")
    parser.add_argument("--report", default=None, help="optional path to write a JSON report")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

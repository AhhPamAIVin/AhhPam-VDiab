"""Interactive terminal chat for the respond-agent (AI Summary + AI
Consultation), run against the real seeded dev database.

Picks a doctor or patient from an indexed list built from whoever
`make migrate-nhanes-data` already loaded (patients/doctors log in with
password "123456" in the real app — irrelevant here since this script calls
the agent graph directly, no login/token involved), then calls the
respond-agent graph in-process (codebase/models/agents/graph.py) — the same
call codebase/backend/agent/router.py makes for the real /api/v1/agent/chat
and /agent/summary endpoints, just skipping the HTTP/auth layer so manual
testing doesn't need a token dance.

Needs: `make db-reset-migrate` (or `seed-admin` + `migrate-nhanes-data`)
already run against Postgres, and OPENAI_API_KEY set in .env. The backend
server does NOT need to be running — this talks to Postgres directly via
codebase.backend.db.engine, same as every other script/ here.

Usage:
    make ai-chat
    # or: python -m scripts.ai_chat_cli
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlmodel import Session, select

import codebase.backend.appointments.models as _appointments_models  # noqa: F401  (registers Appointment into SQLModel.metadata — ClinicalNote.appointment_id FKs into it)
from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.db import create_db_and_tables, engine
from codebase.backend.patients.models import PatientAssignment

# Local-only debug trace of what the agent actually did each turn (intent,
# retrieval, citations, guardrail outcome). Already covered by .gitignore's
# `logs/` rule.
_LOG_PATH = Path("logs/ai_chat.jsonl")

# Only the fields useful for eyeballing what happened — retrieved_chunks is
# trimmed to id/title/score (no full chunk text) so one line stays readable
# instead of dumping whole guideline paragraphs into the log.
_LOG_RESULT_KEYS = (
    "intent",
    "skip_knowledge_retrieval",
    "response",
    "sources",
    "data_used",
    "limitations",
    "handoff",
    "error",
    "guardrail_failures",
    "personal_data_grounded",
    "retry_count",
    "trends",
    "attention_points",
    "suggested_doctor_questions",
    "date_range",
)


def _tools_used(result: dict) -> dict:
    """Which tool actually ran and what it returned, keyed by the real tool
    function name (codebase/models/agents/tools/*.py) — this graph is a
    fixed pipeline, not a ReAct loop where the LLM picks tools, so "which
    tool ran" is just "did fetch_data / retrieve_knowledge run for this
    intent" (see graph.py: fetch_data runs for SUMMARY/QA, retrieve_knowledge
    for QA only; neither runs on a JAILBREAK/HIGH_RISK/OUT_OF_SCOPE
    short-circuit). A tool key is None when it didn't run at all, distinct
    from an empty result (it ran and genuinely found nothing)."""
    tool_results = result.get("tool_results")
    tools: dict = {
        "get_patient_profile": (tool_results or {}).get("profile") if tool_results is not None else None,
        "get_health_records": (tool_results or {}).get("records") if tool_results is not None else None,
        "get_medication_summary": (tool_results or {}).get("medications") if tool_results is not None else None,
        "get_assigned_doctor": (tool_results or {}).get("assigned_doctor") if tool_results is not None else None,
        "calculate_health_metrics": (tool_results or {}).get("metrics") if tool_results is not None else None,
    }
    if "retrieved_chunks" in result:
        tools["search_approved_knowledge"] = [
            {
                "chunk_id": c.get("chunk_id"),
                "title": c.get("title"),
                "score": c.get("score"),
                "preview": (c.get("text") or "")[:200],
            }
            for c in (result.get("retrieved_chunks") or [])
        ]
    else:
        tools["search_approved_knowledge"] = None
    return tools


def _log_turn(*, role: str, user_full_name: str, patient_full_name: str, command: str, message: str, result: dict) -> None:
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "role": role,
        "user": user_full_name,
        "patient": patient_full_name,
        "command": command,
        "message": message,
        "tools_used": _tools_used(result),
    }
    entry.update({k: result.get(k) for k in _LOG_RESULT_KEYS})

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _load_pool(session: Session, role: UserRole) -> list[User]:
    return list(session.exec(select(User).where(User.role == role, User.is_active.is_(True))).all())


def _pick(items: list[User], label: str) -> User:
    ordered = sorted(items, key=lambda u: u.full_name)
    for i, u in enumerate(ordered, start=1):
        print(f"  [{i}] {u.full_name}  <{u.email}>")
    while True:
        raw = input(f"Chọn {label} (số thứ tự): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(ordered):
            return ordered[int(raw) - 1]
        print("Lựa chọn không hợp lệ, thử lại.")


def _load_assigned_patients(session: Session, doctor_id, patients: list[User]) -> list[User]:
    assignments = session.exec(select(PatientAssignment).where(PatientAssignment.doctor_id == doctor_id)).all()
    assigned_ids = {a.patient_id for a in assignments}
    return [p for p in patients if p.id in assigned_ids]


# One checkpointer thread per CLI run, like one ChatSession in the real app —
# the graph's checkpointer requires a thread_id (KeyError without one).
_THREAD_ID = str(uuid4())


async def _run_turn(**agent_input) -> dict:
    from codebase.models.agents.graph import agent

    if agent_input.get("message"):
        agent_input.setdefault("messages", [HumanMessage(content=agent_input["message"])])
    with Session(engine) as session:
        return await agent.ainvoke(agent_input, config={"configurable": {"session": session, "thread_id": _THREAD_ID}})


def _print_result(result: dict) -> None:
    if result.get("error"):
        print(f"\n[Lỗi] {result.get('canned_response') or result['error']}\n")
        return

    print(f"\n{result.get('response', '')}\n")

    for label, key in (
        ("Nguồn tham khảo", None),
        ("Xu hướng", "trends"),
        ("Điểm cần lưu ý", "attention_points"),
        ("Câu hỏi gợi ý cho bác sĩ", "suggested_doctor_questions"),
        ("Giới hạn", "limitations"),
    ):
        if key is None:
            sources = result.get("sources") or []
            if not sources:
                continue
            print(f"{label}:")
            for s in sources:
                bits = [b for b in (s.get("title"), s.get("section"), s.get("page_ref")) if b]
                print("  - " + " | ".join(bits))
            continue

        items = result.get(key) or []
        if not items:
            continue
        print(f"{label}:")
        for item in items:
            print(f"  - {item}")

    if result.get("handoff"):
        print("\n⚠️  Cần bác sĩ xác nhận (handoff = True)")
    print()


_HELP_TEXT = """
Lệnh:
  <câu hỏi>         Hỏi AI Consultation (chat QA)
  /summary [days]   Yêu cầu AI Summary cho N ngày gần nhất (mặc định 7, tối đa 30)
  /help             Hiện lại hướng dẫn này
  /quit, /exit      Thoát
"""


def _ask_role() -> str:
    while True:
        choice = input("Đăng nhập với vai trò — [1] Bệnh nhân  [2] Bác sĩ: ").strip()
        if choice in ("1", "patient", "benh nhan"):
            return "patient"
        if choice in ("2", "doctor", "bac si"):
            return "doctor"
        print("Vui lòng chọn 1 hoặc 2.")


async def main() -> None:
    if not get_settings().openai_api_key:
        print(
            "Cảnh báo: không thấy OPENAI_API_KEY trong .env — "
            "các câu hỏi sẽ lỗi khi gọi LLM/embeddings.\n"
        )

    create_db_and_tables()
    with Session(engine) as session:
        doctors = _load_pool(session, UserRole.DOCTOR)
        patients = _load_pool(session, UserRole.PATIENT)

    if not doctors or not patients:
        print(
            "Chưa có đủ dữ liệu demo (bác sĩ/bệnh nhân) trong database. "
            "Chạy `make db-reset-migrate` (hoặc `make seed-admin && make migrate-nhanes-data`) trước.\n"
        )
        return

    print(f"Đã tìm thấy {len(doctors)} bác sĩ, {len(patients)} bệnh nhân trong dữ liệu demo.\n")

    role = _ask_role()
    print()
    if role == "doctor":
        doctor = _pick(doctors, "bác sĩ")
        with Session(engine) as session:
            assigned_patients = _load_assigned_patients(session, doctor.id, patients)
        if not assigned_patients:
            print(f"\nBác sĩ '{doctor.full_name}' chưa có bệnh nhân nào được gán — không thể chat.")
            return
        print()
        patient = _pick(assigned_patients, "bệnh nhân cần trao đổi")
        user_id, user_role, patient_id = doctor.id, "doctor", patient.id
        user_full_name = doctor.full_name
        print(f"\nBạn đang hỏi với vai trò BÁC SĨ '{doctor.full_name}', về bệnh nhân '{patient.full_name}'.")
    else:
        patient = _pick(patients, "bệnh nhân")
        user_id, user_role, patient_id = patient.id, "patient", None
        user_full_name = patient.full_name
        print(f"\nBạn đang hỏi với vai trò BỆNH NHÂN '{patient.full_name}'.")

    print(_HELP_TEXT)
    print(f"(Mỗi lượt hỏi/đáp cũng được ghi vào {_LOG_PATH} để xem lại.)\n")

    while True:
        try:
            message = input("Bạn> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTạm biệt.")
            return

        if not message:
            continue
        if message in ("/quit", "/exit", "exit", "quit"):
            print("Tạm biệt.")
            return
        if message == "/help":
            print(_HELP_TEXT)
            continue

        if message.startswith("/summary"):
            parts = message.split()
            days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 7
            result = await _run_turn(
                user_id=user_id,
                user_role=user_role,
                patient_id=patient_id,
                request_kind="summary",
                date_range_days=days,
                message="",
            )
            command = f"/summary {days}"
        else:
            result = await _run_turn(
                user_id=user_id,
                user_role=user_role,
                patient_id=patient_id,
                request_kind="chat",
                message=message,
            )
            command = "chat"

        _log_turn(
            role=user_role,
            user_full_name=user_full_name,
            patient_full_name=patient.full_name,
            command=command,
            message=message,
            result=result,
        )
        _print_result(result)


if __name__ == "__main__":
    asyncio.run(main())

"""Permission gate — first node in the graph. Shared scaffold, not owned by
any one workstream — see docs/AI_Summary_Respond_System_Design.md §4.2.

Resolves which patient_id this request is about (defaults to the caller
themselves for a patient) and enforces access via the same
`ensure_patient_access` health/router.py already uses — no parallel
authorization system for the agent.

DB session comes from LangGraph's `config["configurable"]["session"]`, set by
codebase/backend/agent/router.py from FastAPI's `Depends(get_session)` — NOT
opened directly against the module-level engine. This is what lets tests swap
in the in-memory SQLite fixture (tests/conftest.py's `app.dependency_overrides
[get_session]`) the same way every other endpoint's tests already do. Any
other node that needs DB access (fetch_data.py, tools/patient_tools.py) must
follow the same pattern — don't call `Session(engine)` directly in a node.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from sqlmodel import Session

from codebase.backend.auth.models import User
from codebase.backend.patients.access import ensure_patient_access
from codebase.models.agents.state import AgentState


async def resolve_context(state: AgentState, config: RunnableConfig) -> dict:
    """First node in the graph: resolves patient_id (defaults to the caller
    for a patient) and enforces access via the same ensure_patient_access
    RBAC check every REST endpoint uses, before any tool/LLM call runs."""
    session: Session = config["configurable"]["session"]

    patient_id = state.get("patient_id")
    if patient_id is None:
        if state.get("user_role") == "patient":
            patient_id = state["user_id"]
        else:
            return {
                "error": "patient_id_required",
                "canned_response": "Vui lòng chỉ định bệnh nhân cần xem (patient_id).",
            }

    current_user = session.get(User, state["user_id"])
    if current_user is None:
        return {"error": "forbidden", "canned_response": "Không xác định được người dùng."}
    try:
        ensure_patient_access(session, current_user, patient_id)
    except Exception:
        return {
            "error": "forbidden",
            "canned_response": "Bạn không có quyền truy cập dữ liệu của bệnh nhân này.",
        }

    return {"patient_id": patient_id}

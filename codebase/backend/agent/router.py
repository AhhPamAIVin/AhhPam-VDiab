"""Respond-agent endpoints.

See docs/AI_Summary_Respond_System_Design.md §10. `POST /chat` runs the live
LangGraph respond-agent (codebase/models/agents/graph.py) per call, same as
always. `POST /summary` no longer does — it now reads a cached
AgentSummarySnapshot (codebase/backend/summaries/service.py::ensure_snapshot),
generated out-of-band by health-record writes (health/router.py) and by a
historical backfill script, rather than invoking the LLM live on every page
load — see codebase/backend/summaries/service.py's module docstring for why.
Since ensure_snapshot() doesn't go through resolve_context.py (the graph's
permission-check node), `summarize()` below replicates that same
patient_id-resolution + ensure_patient_access check explicitly, with the same
error text, so this is not a permission regression relative to before.

The `session` from FastAPI's `Depends(get_session)` is passed into the graph
via `config={"configurable": {"session": session}}` rather than letting graph
nodes open their own DB connection — this is what makes tests' in-memory
SQLite override (tests/conftest.py) actually reach the graph. See
nodes/resolve_context.py's docstring for the full reasoning. Only `chat()`
below goes through the graph now; `summarize()` reuses the same session for
`ensure_snapshot()` directly.

Permission/validation failures from resolve_context.py (state["error"] ==
"forbidden" / "patient_id_required") are translated into real HTTP error
statuses here rather than shipped as a 200 with an apologetic message in the
body — this matches the convention every other patient-data endpoint in this
codebase already follows (health/router.py raises HTTPException(400) for a
missing patient_id and lets ensure_patient_access's HTTPException(403)
propagate directly). resolve_context.py itself still returns a plain error
dict rather than raising — that keeps tests/test_models/test_graph.py's
graph-level tests (which call agent.ainvoke() directly and assert on
result.get("error")) working unchanged; only this HTTP layer needs to know
about status codes. Safety short-circuits (JAILBREAK/HIGH_RISK/OUT_OF_SCOPE)
don't set state["error"] at all (see classify_intent.py) — those are normal
200 responses with a canned refusal, not authorization failures.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, select

from codebase.backend.agent.models import ChatSession
from codebase.backend.agent.record_actions import (
    analyze_message_drafts,
    apply_action,
    create_pending_action,
    drafts_from_record_candidates,
    interaction_for,
)
from codebase.backend.agent.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRecordActionRequest,
    AgentRecordActionResponse,
    AgentSummaryRequest,
    AgentSummaryResponse,
    ChatMessagePublic,
    ChatSessionMessagesResponse,
    ChatSessionPublic,
)
from codebase.backend.auth.dependencies import require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.health import meal_analysis
from codebase.backend.patients.access import ensure_patient_access
from codebase.backend.summaries.service import ensure_snapshot, refresh_patient_summaries
from codebase.models.agents.checkpoint import checkpointer
from codebase.models.agents.graph import agent
from codebase.models.agents.tools.insight_tools import get_patient_insights
from codebase.models.tracing import tracing_callbacks

router = APIRouter()
logger = logging.getLogger(__name__)

_can_use_agent = require_roles(UserRole.PATIENT, UserRole.DOCTOR)

_TITLE_MAX_LENGTH = 60


def _resolve_patient_id(current_user: User, patient_id: UUID | None) -> UUID | None:
    """An explicit patient_id wins; otherwise a patient means themselves. A
    doctor with no patient_id has none to resolve — resolve_context.py (and,
    for session creation below, this router directly) rejects that as
    "patient_id_required"."""
    return patient_id or (current_user.id if current_user.role == UserRole.PATIENT else None)


def _derive_title(message: str) -> str:
    stripped = " ".join(message.split())
    return stripped if len(stripped) <= _TITLE_MAX_LENGTH else stripped[: _TITLE_MAX_LENGTH - 1].rstrip() + "…"


def _load_or_create_session(session: Session, current_user: User, request: AgentChatRequest) -> ChatSession:
    """Continues an existing session (ownership-checked — a leaked/guessed id
    must not let someone else's thread be continued) or lazily creates a new
    one on the first message of a fresh chat, rather than requiring a
    separate "create session" call that could leave empty, abandoned rows
    behind. When `session_id` is given, `request.patient_id` is ignored in
    favor of the session's own — the session already pins it, which removes
    a whole class of mismatch bugs."""
    if request.session_id is not None:
        chat_session = session.get(ChatSession, request.session_id)
        if chat_session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
        if chat_session.counterpart_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your chat session")
        return chat_session

    resolved_patient_id = _resolve_patient_id(current_user, request.patient_id)
    if resolved_patient_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_id_required")

    chat_session = ChatSession(
        thread_id=str(uuid4()),
        patient_id=resolved_patient_id,
        counterpart_id=current_user.id,
        title=_derive_title(request.message),
    )
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


_ERROR_STATUS_CODES: dict[str, int] = {
    "forbidden": status.HTTP_403_FORBIDDEN,
    "patient_id_required": status.HTTP_400_BAD_REQUEST,
}


def _raise_for_graph_error(result: dict) -> None:
    """Turn a resolve_context.py permission/validation failure into a real
    HTTPException instead of letting it fall through to a 200 response."""
    error = result.get("error")
    if not error:
        return
    detail = result.get("response") or result.get("canned_response") or error
    raise HTTPException(status_code=_ERROR_STATUS_CODES.get(error, status.HTTP_400_BAD_REQUEST), detail=detail)


def _resolve_summary_patient_id(current_user: User, patient_id: UUID | None) -> UUID:
    """Same default-to-self-for-a-patient resolution resolve_context.py
    applies inside the graph (design doc §10: "patient_id resolved the same
    way health/router.py already does") — replicated here since the cached
    read path below no longer runs resolve_context.py at all."""
    if patient_id is not None:
        return patient_id
    if current_user.role == UserRole.PATIENT:
        return current_user.id
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Vui lòng chỉ định bệnh nhân cần xem (patient_id).",
    )


def _ensure_summary_access(session: Session, current_user: User, patient_id: UUID) -> None:
    """Same ensure_patient_access call + canned Vietnamese 403 text
    resolve_context.py's `except Exception` branch already produces — kept
    identical so this isn't a user-facing regression now that summarize()
    bypasses the graph (and so existing permission tests keep passing
    unchanged)."""
    try:
        ensure_patient_access(session, current_user, patient_id)
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập dữ liệu của bệnh nhân này.",
        ) from exc


@router.post("/summary", response_model=AgentSummaryResponse)
async def summarize(
    request: AgentSummaryRequest,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> AgentSummaryResponse:
    patient_id = _resolve_summary_patient_id(current_user, request.patient_id)
    _ensure_summary_access(session, current_user, patient_id)

    snapshot = await ensure_snapshot(
        session, patient_id=patient_id, role=current_user.role.value, period_days=request.days
    )
    return AgentSummaryResponse(
        summary=snapshot.summary,
        date_range=(snapshot.period_start, snapshot.period_end),
        data_used=snapshot.data_used,
        trends=snapshot.trends,
        attention_points=snapshot.attention_points,
        data_completeness=snapshot.data_completeness,
        limitations=snapshot.limitations,
        suggested_doctor_questions=snapshot.suggested_doctor_questions,
        relevant_insights=_relevant_insights_for_summary(session, current_user),
    )


def _relevant_insights_for_summary(session: Session, current_user: User) -> list[str]:
    """Patient's own patient-facing insight streams only — "weekly_batch"
    (structured-data pattern detection) and "chat" (mined from the patient's
    own consented chat sessions, plan Part 3). A patient never sees
    appointment-triggered insights — those stay in the doctor's workflow via
    GET /api/v1/doctor/patients/{id}/insights, which in turn never returns
    "chat" (see that router's docstring for the reverse side of this same
    isolation boundary). A doctor requesting a summary never gets anything
    here; that's not an oversight, it's the same boundary."""
    if current_user.role != UserRole.PATIENT:
        return []
    insights = get_patient_insights(session, current_user.id, source_kind=["weekly_batch", "chat"])
    return [insight["text"] for insight in insights]


def _patient_parser_drafts(current_user: User, message: str) -> list:
    """Patient-only, high-confidence extraction. Doctors retain the exact
    consultation flow and can never create a write action through chat. This
    only prepares an in-memory draft; no confirmation/write UI is created
    until the existing graph and all of its guardrails have completed."""
    if current_user.role != UserRole.PATIENT:
        return []
    parser_drafts, _ = analyze_message_drafts(message)
    return parser_drafts


def _chat_graph_call(
    session: Session, current_user: User, chat_session: ChatSession, message: str
) -> tuple[dict, dict]:
    """Graph input + config shared by /chat and /chat/stream."""
    graph_input = {
        "user_id": current_user.id,
        "user_role": current_user.role.value,
        "patient_id": chat_session.patient_id,
        "request_kind": "chat",
        "message": message,
        "messages": [HumanMessage(content=message)],
    }
    config = {
        "configurable": {
            "session": session,
            "thread_id": chat_session.thread_id,
        },
        "callbacks": tracing_callbacks(),
        "run_name": "agent_chat",
        "metadata": {
            "langfuse_session_id": str(chat_session.thread_id),
            "langfuse_user_id": str(current_user.id),
            "user_role": current_user.role.value,
        },
    }
    return graph_input, config


def _finalize_chat(
    session: Session,
    current_user: User,
    chat_session: ChatSession,
    request: AgentChatRequest,
    result: dict,
    parser_drafts: list,
) -> AgentChatResponse:
    """Everything /chat does after the graph finished: HTTP error mapping,
    session bookkeeping, post-guardrail record-confirmation cards."""
    _raise_for_graph_error(result)

    chat_session.updated_at = datetime.utcnow()
    session.add(chat_session)
    session.commit()

    interaction = None
    interactions = []
    # Confirmation is strictly post-guardrail. Refusal/out-of-scope paths never
    # get a save card, even if the permissive record parser found a draft.
    record_drafts = []
    llm_candidates = result.get("record_candidates") or []
    if current_user.role == UserRole.PATIENT and result.get("intent") == "QA":
        # Prefer deterministic extraction when it recognized the statement;
        # an LLM candidate must not overwrite absent fields with guesses such
        # as AFTER_MEAL or an inferred clock time.
        validated_candidates = drafts_from_record_candidates(request.message, llm_candidates)
        if not parser_drafts:
            record_drafts = validated_candidates
        elif len(parser_drafts) > 1 or len(llm_candidates) <= 1:
            record_drafts = parser_drafts
        else:
            # Preserve genuinely separate records from a compound message,
            # but replace the model's version of the parser-recognized record.
            parser_type = parser_drafts[0].get("record_type")
            other_drafts = []
            for candidate in llm_candidates:
                validated = drafts_from_record_candidates(request.message, [candidate])
                if validated and validated[0].get("record_type") != parser_type:
                    other_drafts.extend(validated)
            record_drafts = [*parser_drafts, *other_drafts]
    if result.get("intent") == "QA":
        for record_draft in record_drafts:
            pending = create_pending_action(
                session,
                patient_id=current_user.id,
                chat_session=chat_session,
                draft=record_draft,
            )
            interactions.append(interaction_for(session, pending))
        interaction = interactions[0] if interactions else None

    response_text = result.get("response", "")

    return AgentChatResponse(
        response=response_text,
        data_used=result.get("data_used", []),
        sources=result.get("sources", []),
        limitations=result.get("limitations", []),
        handoff=result.get("handoff", False),
        session_id=chat_session.id,
        interaction=interaction,
        interactions=interactions,
    )


@router.post("/chat", response_model=AgentChatResponse)
async def chat(
    request: AgentChatRequest,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> AgentChatResponse:
    chat_session = _load_or_create_session(session, current_user, request)
    parser_drafts = _patient_parser_drafts(current_user, request.message)
    graph_input, config = _chat_graph_call(session, current_user, chat_session, request.message)
    result = await agent.ainvoke(graph_input, config=config)
    return _finalize_chat(session, current_user, chat_session, request, result, parser_drafts)


# --- POST /chat/stream (Epic 5.3) --------------------------------------------
# Server-Sent Events. Safety constraint: guardrail_check judges the complete
# draft, so no answer token is ever streamed before it ran — only progress
# events while the graph works, then one `final` event carrying exactly the
# AgentChatResponse /chat would have returned. A draft guardrail_check blocks
# never leaves the server; only its replacement does.
_PROGRESS_START = "Đang phân tích câu hỏi…"
_PROGRESS_HEALTH_AND_DOCS = "Đang tra dữ liệu sức khỏe và tài liệu y khoa…"
_PROGRESS_HEALTH = "Đang tra dữ liệu sức khỏe…"
_PROGRESS_DOCS = "Đang tra cứu tài liệu y khoa đã được duyệt…"
_PROGRESS_DRAFT = "Đang soạn câu trả lời…"
_PROGRESS_SAFETY = "Đang kiểm tra an toàn câu trả lời…"


def _progress_after(node: str, update: dict) -> str | None:
    """Progress message for what the graph does NEXT, given the node that just finished."""
    if node == "plan_response":
        if update.get("canned_response") or update.get("answered_directly"):
            return None
        needs_health = bool(update.get("needs_health_data"))
        needs_docs = not update.get("skip_knowledge_retrieval")
        if needs_health and needs_docs:
            return _PROGRESS_HEALTH_AND_DOCS
        if needs_health:
            return _PROGRESS_HEALTH
        return _PROGRESS_DOCS if needs_docs else _PROGRESS_DRAFT
    if node == "agent_plan":
        # Per-tool progress already went out as custom events while it ran.
        if update.get("canned_response") or update.get("answered_directly"):
            return None
        return _PROGRESS_DRAFT
    if node == "check_data_quality":
        return _PROGRESS_DRAFT
    if node == "synthesize_dispatch":
        return _PROGRESS_SAFETY
    return None


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    request: AgentChatRequest,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Same contract as POST /chat, delivered as SSE: `progress` events
    ({"stage", "message"}), then exactly one `final` (an AgentChatResponse)
    or `error` ({"status", "detail"}) event. Session/permission errors that
    /chat raises before the graph runs are still plain HTTP errors here."""
    chat_session = _load_or_create_session(session, current_user, request)
    parser_drafts = _patient_parser_drafts(current_user, request.message)
    graph_input, config = _chat_graph_call(session, current_user, chat_session, request.message)

    async def events():
        yield _sse("progress", {"stage": "start", "message": _PROGRESS_START})
        final_state: dict = {}
        try:
            async for mode, chunk in agent.astream(
                graph_input, config=config, stream_mode=["updates", "values", "custom"]
            ):
                if mode == "values":
                    final_state = chunk
                    continue
                if mode == "custom":
                    # nodes/agent_plan.py, one line per tool it calls (Epic 8).
                    if isinstance(chunk, dict) and chunk.get("progress"):
                        yield _sse("progress", {"stage": "agent_tool", "message": chunk["progress"]})
                    continue
                for node, update in chunk.items():
                    message = _progress_after(node, update or {})
                    if message:
                        yield _sse("progress", {"stage": node, "message": message})
            response = _finalize_chat(session, current_user, chat_session, request, final_state, parser_drafts)
        except HTTPException as exc:
            yield _sse("error", {"status": exc.status_code, "detail": exc.detail})
            return
        except Exception:
            logger.exception("agent chat stream failed")
            yield _sse("error", {"status": 500, "detail": "Đã có lỗi khi xử lý câu hỏi. Vui lòng thử lại."})
            return
        yield _sse("final", response.model_dump(mode="json"))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/record-actions/{action_id}", response_model=AgentRecordActionResponse)
def handle_record_action(
    action_id: UUID,
    request: AgentRecordActionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_roles(UserRole.PATIENT)),
    session: Session = Depends(get_session),
) -> AgentRecordActionResponse:
    """Complete, confirm, or cancel a patient-only pending record draft."""
    response, interaction = apply_action(
        session,
        action_id=action_id,
        patient_id=current_user.id,
        request=request,
    )
    if interaction is not None and interaction.type == "record_saved":
        record_type = interaction.draft.get("record_type")
        if record_type in {"glucose", "blood_pressure", "meal", "activity"}:
            background_tasks.add_task(refresh_patient_summaries, patient_id=current_user.id)
        if record_type == "meal" and interaction.saved_record_id is not None:
            background_tasks.add_task(meal_analysis.analyze_meal_in_background, meal_id=interaction.saved_record_id)
    return AgentRecordActionResponse(response=response, interaction=interaction)


@router.get("/sessions", response_model=list[ChatSessionPublic])
def list_sessions(
    patient_id: UUID | None = None,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> list[ChatSessionPublic]:
    """Every ChatSession this user is the counterpart of — a doctor's own
    threads with their patients, or a patient's own self-chat threads.
    `counterpart_id == current_user.id` alone gives correct isolation (see
    ChatSession's docstring in agent/models.py); `patient_id` further narrows
    it, e.g. a doctor viewing just one patient's session list. Deliberately
    NOT re-checked against ensure_patient_access here — this only returns
    titles/timestamps, not content; the real access re-check happens at
    get_session_messages and at /chat's per-turn resolve_context.py check."""
    statement = select(ChatSession).where(ChatSession.counterpart_id == current_user.id)
    if patient_id is not None:
        statement = statement.where(ChatSession.patient_id == patient_id)
    statement = statement.order_by(ChatSession.updated_at.desc())  # type: ignore[attr-defined]
    rows = session.exec(statement).all()
    return [
        ChatSessionPublic(
            id=row.id,
            patient_id=row.patient_id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def _message_role(message: object) -> Literal["user", "assistant"] | None:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    return None


@router.get("/sessions/{session_id}/messages", response_model=ChatSessionMessagesResponse)
async def get_session_messages(
    session_id: UUID,
    current_user: User = Depends(_can_use_agent),
    session: Session = Depends(get_session),
) -> ChatSessionMessagesResponse:
    chat_session = session.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    if chat_session.counterpart_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your chat session")
    # Re-check patient access even for a read: an unassigned doctor
    # shouldn't retain read access to an old transcript just because the
    # session row still exists — same re-check posture resolve_context.py
    # already takes on every /chat turn (design doc's isolation section).
    ensure_patient_access(session, current_user, chat_session.patient_id)

    checkpoint_tuple = await checkpointer.aget_tuple(
        {"configurable": {"thread_id": chat_session.thread_id, "session": session}}
    )
    raw_messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", []) if checkpoint_tuple else []
    messages = [
        ChatMessagePublic(role=role, content=message.content)
        for message in raw_messages
        if (role := _message_role(message)) is not None
    ]
    return ChatSessionMessagesResponse(session_id=chat_session.id, messages=messages)

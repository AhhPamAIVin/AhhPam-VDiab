"""Epic 8: patient agent — tool registry, the agent_plan loop, and the full
/agent/chat path with the agent switched on."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, ToolMessage
from sqlmodel import Session

from codebase.backend.appointments.models import Appointment, AppointmentStatus
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.config import get_settings
from codebase.backend.health.models import GlucoseRecord, MealContext
from codebase.models.agents.nodes.agent_plan import agent_plan
from codebase.models.agents.tools.agent_tools import (
    TOOLS,
    TOOLS_BY_NAME,
    HealthRecordsArgs,
    KnowledgeArgs,
    NoArgs,
)

PASSWORD = "AdminPass123"
AGENT_LLM = "codebase.models.agents.nodes.agent_plan.get_llm"


def _user(session: Session, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


@pytest.fixture
def doctor(session: Session) -> User:
    return _user(session, "doctor@diab.example.com", UserRole.DOCTOR)


def _glucose(session: Session, patient: User, days_ago: float, value: float) -> None:
    session.add(
        GlucoseRecord(
            patient_id=patient.id,
            value=value,
            measured_at=datetime.utcnow() - timedelta(days=days_ago),
            meal_context=MealContext.FASTING,
            created_by=patient.id,
        )
    )
    session.commit()


def _appointment(session: Session, patient: User, doctor: User, days_ahead: float, status=AppointmentStatus.SCHEDULED):
    session.add(
        Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_at=datetime.utcnow() + timedelta(days=days_ahead),
            reason="Tái khám định kỳ",
            status=status,
            created_by=doctor.id,
        )
    )
    session.commit()


def _gather(patient: User) -> dict:
    return {"patient_id": patient.id, "tool_results": {}, "retrieved_chunks": []}


# --- registry -------------------------------------------------------------------


def test_no_tool_takes_a_patient_id():
    for tool in TOOLS:
        assert "patient_id" not in tool.args_model.model_json_schema().get("properties", {}), tool.name


def test_records_tool_caps_and_uses_the_authenticated_patient(session: Session, patient: User):
    other = _user(session, "other@diab.example.com", UserRole.PATIENT)
    _glucose(session, patient, 1, 110)
    _glucose(session, patient, 20, 150)
    _glucose(session, other, 1, 300)
    tool = TOOLS_BY_NAME["get_health_records"]

    # A patient_id smuggled into the args is ignored (not part of the schema).
    args = HealthRecordsArgs.model_validate({"days": 7, "patient_id": str(other.id)})
    outcome = tool.handler(_gather(patient), session, args)

    assert [r["value"] for r in outcome.state_update["tool_results"]["records"]["glucose"]] == [110]
    assert "đường huyết 1" in outcome.observation
    start, end = outcome.state_update["date_range"]
    assert end - start == timedelta(days=7)

    with pytest.raises(ValueError):
        HealthRecordsArgs(days=31)


def test_records_tool_honours_classify_intents_explicit_period(session: Session, patient: User):
    _glucose(session, patient, 20, 150)
    override = (datetime.utcnow() - timedelta(days=21), datetime.utcnow() - timedelta(days=19))
    outcome = TOOLS_BY_NAME["get_health_records"].handler(
        {**_gather(patient), "date_range_override": override}, session, HealthRecordsArgs()
    )
    assert [r["value"] for r in outcome.state_update["tool_results"]["records"]["glucose"]] == [150]


def test_appointments_tool(session: Session, patient: User, doctor: User):
    _appointment(session, patient, doctor, 3)
    _appointment(session, patient, doctor, 10, AppointmentStatus.CANCELLED)
    _appointment(session, patient, doctor, -30, AppointmentStatus.COMPLETED)
    outcome = TOOLS_BY_NAME["get_appointments"].handler(_gather(patient), session, NoArgs())
    rows = outcome.state_update["tool_results"]["appointments"]
    assert [row["upcoming"] for row in rows] == [False, True]
    assert rows[1]["status"] == "đã lên lịch"
    assert outcome.observation.startswith("Có 1 lịch khám sắp tới")


@pytest.mark.asyncio
async def test_knowledge_tool_merges_without_duplicates(session: Session, patient: User):
    chunks = [{"chunk_id": "a", "title": "ADA"}, {"chunk_id": "b", "title": "VADE"}]
    with patch("codebase.models.agents.tools.agent_tools.search_approved_knowledge", return_value=chunks):
        state = {**_gather(patient), "retrieved_chunks": [{"chunk_id": "a", "title": "ADA"}]}
        outcome = await TOOLS_BY_NAME["search_knowledge"].handler(state, session, KnowledgeArgs(query="chỉ số GI là gì"))
    assert [c["chunk_id"] for c in outcome.state_update["retrieved_chunks"]] == ["a", "b"]
    assert outcome.state_update["knowledge_query"] == "chỉ số GI là gì"


def test_previous_tool_results_are_kept_by_the_next_tool(session: Session, patient: User):
    first = TOOLS_BY_NAME["get_medications"].handler(_gather(patient), session, NoArgs())
    state = {**_gather(patient), **first.state_update}
    second = TOOLS_BY_NAME["get_my_doctor"].handler(state, session, NoArgs())
    assert {"medications", "assigned_doctor"} <= set(second.state_update["tool_results"])


# --- agent_plan loop --------------------------------------------------------------


def _call(name: str, args: dict | None = None, call_id: str | None = None) -> dict:
    return {"name": name, "args": args or {}, "id": call_id or f"call_{name}", "type": "tool_call"}


def _agent_llm(monkeypatch, *replies) -> MagicMock:
    """Scripted agent model: each reply is a list of tool calls (or an
    Exception); after the script it answers "XONG" with no call."""
    bound = MagicMock()
    script = list(replies)

    async def _ainvoke(messages):
        bound.seen.append(list(messages))
        if not script:
            return AIMessage(content="XONG")
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return AIMessage(content="", tool_calls=step)

    bound.seen = []
    bound.ainvoke = _ainvoke
    llm = MagicMock()
    llm.bind_tools.return_value = bound
    get_llm = MagicMock(return_value=llm)
    monkeypatch.setattr(AGENT_LLM, get_llm)
    return bound


def _state(patient: User, message: str) -> dict:
    return {"message": message, "patient_id": patient.id, "user_role": "patient", "messages": []}


def _config(session: Session) -> dict:
    return {"configurable": {"session": session}}


@pytest.mark.asyncio
async def test_greeting_takes_the_fast_path_without_a_model(session: Session, patient: User, monkeypatch):
    bound = _agent_llm(monkeypatch)
    result = await agent_plan(_state(patient, "xin chào"), _config(session))
    assert result["intent"] == "SOCIAL" and result["canned_response"]
    assert result["agent_gathered"] is False
    assert bound.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "intent"),
    [
        (_call("reply_social", {"kind": "thanks"}), "SOCIAL"),
        (_call("request_new_appointment"), "APPOINTMENT_SUPPORT"),
        (_call("out_of_scope"), "OUT_OF_SCOPE"),
    ],
)
async def test_terminal_tools_return_the_reviewed_replies(session: Session, patient: User, monkeypatch, call, intent):
    _agent_llm(monkeypatch, [call])
    result = await agent_plan(_state(patient, "cảm ơn trợ lý nha, hôm nay vậy là đủ"), _config(session))
    assert result["intent"] == intent
    assert result["canned_response"]
    assert result["agent_tool_calls"] == [call["name"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "message"),
    [
        # Golden eval 2026-10-29: each of these ended a health question with a canned/self-written refusal.
        (_call("reply_social", {"kind": "ack"}), "Hôm nay tôi bị sốt, tôi có được tự ý ngừng uống thuốc tiểu đường không?"),
        (_call("out_of_scope"), "Hàng xóm bảo uống nước lá ổi thì khỏi hẳn tiểu đường, đúng không?"),
        (_call("answer_directly", {"answer": "Tôi không thể tư vấn về tập luyện."}), "mot tuan nen tap the duc bao nhieu phut"),
    ],
)
async def test_health_messages_cannot_end_on_a_terminal_reply(session: Session, patient: User, monkeypatch, call, message):
    _agent_llm(monkeypatch, [call])
    result = await agent_plan(_state(patient, message), _config(session))
    assert result["intent"] == "QA"
    assert result["canned_response"] is None and result.get("answered_directly") is False
    assert result["needs_health_data"] is True and result["skip_knowledge_retrieval"] is False


@pytest.mark.asyncio
async def test_direct_answer_goes_through_the_guarded_direct_path(session: Session, patient: User, monkeypatch):
    _agent_llm(monkeypatch, [_call("answer_directly", {"answer": "Mình giúp bạn xem lại chỉ số và bữa ăn."})])
    result = await agent_plan(_state(patient, "bạn làm được những gì?"), _config(session))
    assert result["answered_directly"] is True
    assert result["response"].startswith("Mình giúp")


@pytest.mark.asyncio
async def test_one_llm_call_when_the_first_round_succeeds(session: Session, patient: User, monkeypatch):
    bound = _agent_llm(monkeypatch, [_call("get_medications"), _call("get_my_doctor")], [_call("get_appointments")])
    result = await agent_plan(_state(patient, "tôi dùng thuốc gì, bác sĩ là ai"), _config(session))
    assert len(bound.seen) == 1
    assert result["agent_tool_calls"] == ["get_medications", "get_my_doctor"]


@pytest.mark.asyncio
async def test_reflect_gathers_over_two_rounds_then_stops(session: Session, patient: User, doctor: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_reflect", True)
    _glucose(session, patient, 1, 110)
    _appointment(session, patient, doctor, 3)
    chunks = [{"chunk_id": "k1", "title": "ADA"}]
    bound = _agent_llm(
        monkeypatch,
        [_call("get_health_records", {"days": 7}), _call("search_knowledge", {"query": "mục tiêu đường huyết lúc đói"})],
        [_call("get_appointments")],
    )
    with patch("codebase.models.agents.tools.agent_tools.search_approved_knowledge", return_value=chunks):
        result = await agent_plan(_state(patient, "đường huyết tuần này ổn không, khi nào tôi tái khám?"), _config(session))

    assert result["agent_gathered"] is True
    assert result["agent_tool_calls"] == ["get_health_records", "search_knowledge", "get_appointments"]
    assert {"records", "profile", "appointments"} <= set(result["tool_results"])
    assert [c["chunk_id"] for c in result["retrieved_chunks"]] == ["k1"]
    assert result["skip_knowledge_retrieval"] is False
    assert result["needs_health_data"] is True
    assert result["response"] is None and result["canned_response"] is None
    # The second round saw an observation for each first-round call.
    second_round = bound.seen[1]
    observations = [m for m in second_round if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in observations] == ["call_get_health_records", "call_search_knowledge"]


@pytest.mark.asyncio
async def test_bad_args_and_unknown_tools_become_observations(session: Session, patient: User, monkeypatch):
    bound = _agent_llm(
        monkeypatch,
        [_call("get_health_records", {"days": 99}), _call("delete_everything")],
        [_call("get_medications")],
    )
    result = await agent_plan(_state(patient, "thuốc của tôi"), _config(session))
    texts = [m.content for m in bound.seen[1] if isinstance(m, ToolMessage)]
    assert texts[0].startswith("Tham số không hợp lệ")
    assert texts[1].startswith("Không có tool này")
    assert result["agent_tool_calls"] == ["get_medications"]


@pytest.mark.asyncio
async def test_round_limit(session: Session, patient: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_max_rounds", 2)
    monkeypatch.setattr(get_settings(), "patient_agent_reflect", True)
    bound = _agent_llm(monkeypatch, *([[_call("get_my_doctor")]] * 5))
    result = await agent_plan(_state(patient, "bác sĩ của tôi"), _config(session))
    assert len(bound.seen) == 2
    assert result["agent_tool_calls"] == ["get_my_doctor", "get_my_doctor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [RuntimeError("timeout"), None])
async def test_nothing_gathered_falls_back_to_gather_everything(session: Session, patient: User, monkeypatch, first):
    # A failing model, or one that answers without calling any tool.
    _agent_llm(monkeypatch, *([first] if first else []))
    result = await agent_plan(_state(patient, "đường huyết của tôi thế nào"), _config(session))
    assert result["agent_gathered"] is False
    assert result["needs_health_data"] is True
    assert result["skip_knowledge_retrieval"] is False


@pytest.mark.asyncio
async def test_failure_after_gathering_keeps_what_was_found(session: Session, patient: User, monkeypatch):
    _agent_llm(monkeypatch, [_call("get_medications")], RuntimeError("timeout"))
    result = await agent_plan(_state(patient, "thuốc của tôi"), _config(session))
    assert result["agent_gathered"] is True
    assert "medications" in result["tool_results"]


# --- full /agent/chat ----------------------------------------------------------------


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _synth_llm(answer: str) -> MagicMock:
    llm = AsyncMock()
    response = MagicMock()
    response.content = json.dumps({"answer": answer, "used_chunk_ids": [], "record_candidates": []})
    llm.ainvoke.return_value = response
    return MagicMock(return_value=llm)


@pytest.mark.asyncio
async def test_patient_chat_runs_the_agent_end_to_end(
    client: AsyncClient, session: Session, patient: User, doctor: User, monkeypatch
):
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    _appointment(session, patient, doctor, 3)
    _agent_llm(monkeypatch, [_call("get_appointments")])
    synth = _synth_llm("Bạn có một lịch tái khám sắp tới, lý do: tái khám định kỳ.")
    with (
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", synth),
        patch("codebase.models.agents.nodes.plan_response.get_llm") as planner,
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge") as search,
    ):
        response = await client.post(
            "/api/v1/agent/chat", json={"message": "Khi nào tôi tái khám?"}, headers=await _headers(client, patient)
        )

    assert response.status_code == 200, response.text
    assert "tái khám" in response.json()["response"]
    planner.assert_not_called()  # the agent replaced the planner
    search.assert_not_called()  # retrieve_knowledge is a no-op after the agent
    last = synth.return_value.ainvoke.call_args.args[0][-1]
    prompt = last["content"] if isinstance(last, dict) else last.content
    assert "Lịch khám" in prompt and "Tái khám định kỳ" in prompt


@pytest.mark.asyncio
async def test_doctor_chat_keeps_the_planner(client: AsyncClient, session: Session, patient: User, doctor: User, monkeypatch):
    from codebase.backend.patients.models import PatientAssignment

    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    bound = _agent_llm(monkeypatch)
    planner_llm = AsyncMock()
    planner_llm.ainvoke.return_value = MagicMock(
        content=json.dumps({"intent": "SOCIAL", "social_subtype": "greeting", "needs_health_data": False,
                            "needs_knowledge_retrieval": False, "knowledge_query": None, "direct_answer": None})
    )
    with patch("codebase.models.agents.nodes.plan_response.get_llm", return_value=planner_llm):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "chào bạn nhé, hôm nay thế nào", "patient_id": str(patient.id)},
            headers=await _headers(client, doctor),
        )
    assert response.status_code == 200, response.text
    assert bound.seen == []
    planner_llm.ainvoke.assert_awaited()


@pytest.mark.asyncio
async def test_flag_off_keeps_the_planner_for_patients(client: AsyncClient, patient: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", False)
    bound = _agent_llm(monkeypatch)
    planner_llm = AsyncMock()
    planner_llm.ainvoke.return_value = MagicMock(
        content=json.dumps({"intent": "OUT_OF_SCOPE", "social_subtype": None, "needs_health_data": False,
                            "needs_knowledge_retrieval": False, "knowledge_query": None, "direct_answer": None})
    )
    with patch("codebase.models.agents.nodes.plan_response.get_llm", return_value=planner_llm):
        response = await client.post(
            "/api/v1/agent/chat", json={"message": "viết giúp tôi code python"}, headers=await _headers(client, patient)
        )
    assert response.status_code == 200
    assert bound.seen == []

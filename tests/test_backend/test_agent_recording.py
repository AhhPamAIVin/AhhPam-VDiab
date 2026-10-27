"""Epic 8 — the patient agent records data through draft_records: the model
reads the message, code checks every value (and the time) against it, the
patient confirms in a card. Dangerous values get the fixed referral."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.agent.models import PendingRecordAction
from codebase.backend.agent.record_actions import draft_from_agent_proposal
from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.medications.models import Medication
from codebase.models.agents.nodes.agent_plan import agent_plan
from codebase.models.agents.tools.agent_tools import TOOLS_BY_NAME, DraftRecordsArgs
from tests.test_backend.test_patient_agent import _agent_llm, _call, _headers, _synth_llm, _user

VN = ZoneInfo("Asia/Ho_Chi_Minh")
# 10:30 in Vietnam.
NOW = datetime(2026, 10, 27, 3, 30, tzinfo=UTC)


def _vn(hour: int, minute: int = 0, *, days_ago: int = 0) -> str:
    local = NOW.astimezone(VN).replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return local.isoformat()


def _glucose(message: str, **fields) -> dict:
    return {"record_type": "glucose", "evidence": message, **fields}


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


# --- validation of a proposal ------------------------------------------------------


def test_unaccented_abbreviated_message_is_accepted_when_the_number_is_there():
    message = "dg sau an 180"
    draft = draft_from_agent_proposal(message, _glucose(message, value=180), now=NOW)
    assert draft is not None and draft["value"] == 180 and draft["unit"] == "mg/dL"


def test_a_value_not_in_the_message_is_rejected():
    message = "đường huyết sau ăn của tôi 180"
    assert draft_from_agent_proposal(message, _glucose(message, value=108), now=NOW) is None


def test_evidence_must_be_quoted_verbatim():
    message = "đường huyết sau ăn của tôi 180"
    assert draft_from_agent_proposal(message, _glucose("sau ăn của tôi 180", value=180), now=NOW) is not None
    # Not a contiguous span of the message (words dropped / reworded).
    assert draft_from_agent_proposal(message, _glucose("đường huyết 180", value=180), now=NOW) is None
    assert draft_from_agent_proposal(message, _glucose("glucose 180 sau ăn", value=180), now=NOW) is None


def test_meal_context_is_kept_only_when_stated():
    stated = "sáng nay lúc đói đường huyết 6.5"
    assert draft_from_agent_proposal(stated, _glucose(stated, value=6.5, meal_context="FASTING"), now=NOW)[
        "meal_context"
    ] == "FASTING"
    unstated = "đường huyết 6.5"
    draft = draft_from_agent_proposal(unstated, _glucose(unstated, value=6.5, meal_context="AFTER_MEAL"), now=NOW)
    assert draft["meal_context"] is None  # asked in the card, not guessed


@pytest.mark.parametrize(
    ("message", "value", "time", "time_evidence", "expected_local"),
    [
        ("đường huyết 7h sáng nay 6.5", 6.5, _vn(7), "7h sáng nay", (7, 0)),
        ("tối qua 9 giờ rưỡi đo 8.1", 8.1, _vn(21, 30, days_ago=1), "tối qua 9 giờ rưỡi", (21, 30)),
        ("đo được 160 cách đây 30 phút trước", 160, (NOW - timedelta(minutes=30)).isoformat(), "30 phút trước", (10, 0)),
        ("vừa đo xong 150", 150, NOW.isoformat(), "vừa đo", (10, 30)),
    ],
)
def test_time_is_accepted_when_its_phrase_supports_it(message, value, time, time_evidence, expected_local):
    draft = draft_from_agent_proposal(
        message, _glucose(message, value=value, time=time, time_evidence=time_evidence), now=NOW
    )
    assert draft is not None and draft["measured_at"] is not None
    local = datetime.fromisoformat(draft["measured_at"]).astimezone(VN)
    assert (local.hour, local.minute) == expected_local


@pytest.mark.parametrize(
    ("message", "time", "time_evidence"),
    [
        # "sáng nay" has no clock time: the model must not pick one.
        ("sáng nay đường huyết 6.5", _vn(7), "sáng nay"),
        # The quoted phrase says 7h, the model resolved 8h.
        ("7h sáng đường huyết 6.5", _vn(8), "7h sáng"),
        # "hôm qua" but resolved to today.
        ("hôm qua 7h đo 6.5", _vn(7), "hôm qua 7h"),
        # Future.
        ("đo lúc 15h được 6.5", _vn(15), "lúc 15h"),
        # Phrase not in the message.
        ("đường huyết 6.5", _vn(7), "7h"),
    ],
)
def test_unsupported_time_is_dropped_and_asked(message, time, time_evidence):
    draft = draft_from_agent_proposal(
        message, _glucose(message, value=6.5, time=time, time_evidence=time_evidence), now=NOW
    )
    assert draft is not None and draft["measured_at"] is None


def test_blood_pressure_said_as_tren():
    message = "huyết áp 130 trên 85 lúc 7h"
    draft = draft_from_agent_proposal(
        message,
        {"record_type": "blood_pressure", "evidence": message, "systolic": 130, "diastolic": 85,
         "time": _vn(7), "time_evidence": "lúc 7h"},
        now=NOW,
    )
    assert draft["systolic"] == 130 and draft["diastolic"] == 85 and draft["measured_at"] is not None


# --- the tool ---------------------------------------------------------------------


def test_draft_records_tool_keeps_valid_drafts_and_matches_the_medication(session: Session, patient: User):
    session.add(
        Medication(
            patient_id=patient.id, drug_name="Metformin", dose="500mg", frequency="2 lần/ngày",
            start_date=datetime.utcnow() - timedelta(days=30), created_by=patient.id,
        )
    )
    session.commit()
    message = "trưa nay ăn 2 bát phở, quên uống metformin, đường huyết 180"
    args = DraftRecordsArgs.model_validate(
        {
            "records": [
                {"record_type": "meal", "evidence": "trưa nay ăn 2 bát phở", "description": "2 bát phở",
                 "meal_type": "lunch"},
                {"record_type": "medication_note", "evidence": "quên uống metformin",
                 "medication_name": "metformin", "note_type": "missed_dose"},
                {"record_type": "glucose", "evidence": "đường huyết 180", "value": 999},  # not in the message
            ]
        }
    )
    outcome = TOOLS_BY_NAME["draft_records"].handler(
        {"patient_id": patient.id, "message": message, "tool_results": {}}, session, args
    )
    drafts = outcome.state_update["record_drafts"]
    assert [d["record_type"] for d in drafts] == ["meal", "medication_note"]
    assert drafts[1]["medication_id"] is not None  # matched the active Metformin
    assert "1 đề xuất bị loại" in outcome.observation
    assert outcome.state_update["tool_results"]["pending_record_drafts"]


@pytest.mark.asyncio
async def test_agent_plan_returns_the_validated_drafts(session: Session, patient: User, monkeypatch):
    message = "dg sau an 180"
    _agent_llm(
        monkeypatch,
        [_call("draft_records", {"records": [{"record_type": "glucose", "evidence": message, "value": 180,
                                               "meal_context": "AFTER_MEAL"}]})],
    )
    result = await agent_plan(
        {"message": message, "patient_id": patient.id, "user_role": "patient", "messages": []},
        {"configurable": {"session": session}},
    )
    assert result["agent_gathered"] is True
    draft = result["agent_record_drafts"][0]
    # Unaccented "sau an" still counts as a stated context.
    assert (draft["value"], draft["unit"], draft["meal_context"]) == (180, "mg/dL", "AFTER_MEAL")


# --- end to end --------------------------------------------------------------------


async def _chat(client: AsyncClient, patient: User, message: str, agent_calls: list, answer: str) -> dict:
    with (
        patch("codebase.models.agents.nodes.synthesize_qa.get_llm", _synth_llm(answer)),
        patch("codebase.models.agents.nodes.retrieve_knowledge.search_approved_knowledge"),
    ):
        response = await client.post(
            "/api/v1/agent/chat", json={"message": message}, headers=await _headers(client, patient)
        )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_reported_value_becomes_a_confirmation_card(client: AsyncClient, session: Session, patient: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    message = "đường huyết sau ăn của tôi 165"
    _agent_llm(
        monkeypatch,
        [_call("draft_records", {"records": [{"record_type": "glucose", "evidence": message, "value": 165,
                                               "meal_context": "AFTER_MEAL"}]})],
    )
    body = await _chat(client, patient, message, [], "Mình đã chuẩn bị bản ghi, bạn xác nhận giúp nhé.")

    assert body["interactions"] and body["interactions"][0]["draft"]["value"] == 165
    assert body["handoff"] is False
    assert session.exec(select(PendingRecordAction)).one().record_type == "glucose"


@pytest.mark.asyncio
async def test_agent_saying_nothing_to_record_overrides_the_regex_parser(
    client: AsyncClient, patient: User, monkeypatch
):
    # The regex parser would draft this; the agent judged it a question only.
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    _agent_llm(monkeypatch, [_call("get_health_records", {"days": 7})])
    body = await _chat(client, patient, "đường huyết hiện tại của tôi là 115 có ổn không", [], "Bạn chưa có bản ghi.")
    assert body["interactions"] == []


@pytest.mark.asyncio
async def test_dangerous_value_gets_the_fixed_referral(client: AsyncClient, patient: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    message = "vừa đo đường huyết 45"
    _agent_llm(
        monkeypatch,
        [_call("draft_records", {"records": [{"record_type": "glucose", "evidence": message, "value": 45}]})],
    )
    body = await _chat(client, patient, message, [], "Bạn nên ăn ngay 15g đường.")

    assert "115" in body["response"] and "15g" not in body["response"]
    assert body["handoff"] is True
    assert body["interactions"][0]["draft"]["value"] == 45  # still recordable


@pytest.mark.asyncio
async def test_dangerous_value_referral_is_what_the_history_keeps(client: AsyncClient, patient: User, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    message = "vừa đo đường huyết 320"
    _agent_llm(
        monkeypatch,
        [_call("draft_records", {"records": [{"record_type": "glucose", "evidence": message, "value": 320}]})],
    )
    synth = _synth_llm("Bạn nên uống thêm nước.")
    headers = await _headers(client, patient)
    with patch("codebase.models.agents.nodes.synthesize_qa.get_llm", synth):
        body = (await client.post("/api/v1/agent/chat", json={"message": message}, headers=headers)).json()

    synth.return_value.ainvoke.assert_not_called()  # no model-written text for this turn
    history = await client.get(f"/api/v1/agent/sessions/{body['session_id']}/messages", headers=headers)
    assert "115" in history.json()["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ai_assessment_is_returned_and_kept_in_history(client: AsyncClient, patient: User, monkeypatch):
    import json as _json
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(get_settings(), "patient_agent_enabled", True)
    _agent_llm(monkeypatch, [_call("get_health_records", {"days": 7})])
    llm = AsyncMock()
    reply = MagicMock()
    reply.content = _json.dumps(
        {"answer": "Bạn chưa có bản ghi đường huyết nào trong 7 ngày.", "assessment": "Chưa đủ dữ liệu để nhận định.",
         "used_chunk_ids": []},
        ensure_ascii=False,
    )
    llm.ainvoke.return_value = reply
    headers = await _headers(client, patient)
    with patch("codebase.models.agents.nodes.synthesize_qa.get_llm", MagicMock(return_value=llm)):
        body = (await client.post("/api/v1/agent/chat", json={"message": "tuần này tôi thế nào"}, headers=headers)).json()

    assert body["ai_assessment"] == "Chưa đủ dữ liệu để nhận định."
    history = await client.get(f"/api/v1/agent/sessions/{body['session_id']}/messages", headers=headers)
    assert history.json()["messages"][-1]["ai_assessment"] == "Chưa đủ dữ liệu để nhận định."


def test_context_and_time_are_read_from_the_words_when_the_model_leaves_them_empty():
    # The model tends to leave both empty on unaccented text.
    message = "dg sau an vua do 180"
    draft = draft_from_agent_proposal(message, _glucose(message, value=180), now=NOW)
    assert draft["meal_context"] == "AFTER_MEAL"
    assert datetime.fromisoformat(draft["measured_at"]) == NOW

    relative = "duong huyet 30 phut truoc 150"
    draft = draft_from_agent_proposal(relative, _glucose(relative, value=150), now=NOW)
    assert datetime.fromisoformat(draft["measured_at"]) == NOW - timedelta(minutes=30)


def test_vague_or_ambiguous_time_words_never_become_now():
    # "lúc nãy" (a while ago) folds to the same letters as "lúc này" (now).
    for message in ("lúc nãy đường huyết 150", "mỗi sáng đường huyết 150"):
        draft = draft_from_agent_proposal(message, _glucose(message, value=150), now=NOW)
        assert draft["measured_at"] is None, message


def test_model_quirks_seen_in_the_eval_are_absorbed_by_code():
    # Time resolved but no separate time quote: the record's quote is used.
    message = "Hôm qua lúc 9 giờ tối tôi đo đường huyết được 160"
    draft = draft_from_agent_proposal(message, _glucose(message, value=160, time=_vn(21, days_ago=1)), now=NOW)
    assert draft["measured_at"] is not None
    # Activity name translated to English: the Vietnamese name in the quote is kept.
    activity = "sang nay toi di bo 1 tieng"
    draft = draft_from_agent_proposal(
        activity,
        {"record_type": "activity", "evidence": activity, "activity_type": "walking", "duration_minutes": 60},
        now=NOW,
    )
    assert draft["activity_type"] == "đi bộ" and draft["duration_minutes"] == 60
    # Trailing punctuation added to the quote.
    question = "Đường huyết sau ăn của tôi 175 mà tôi thấy hơi mệt, có sao không?"
    assert draft_from_agent_proposal(question, _glucose("Đường huyết sau ăn của tôi 175.", value=175), now=NOW)
    # Unaccented spoken decimal.
    decimal = "duong mau cua toi 8 phay 1"
    assert draft_from_agent_proposal(decimal, _glucose(decimal, value=8.1), now=NOW)["unit"] == "mmol/L"


@pytest.mark.parametrize("message", ["Tối nay tôi định ăn phở", "Nếu đường huyết 250 thì sao", "Mai tôi sẽ đi bộ 30 phút"])
def test_plans_and_hypotheticals_are_never_drafted(message):
    proposal = {"record_type": "meal", "evidence": message, "description": "phở"}
    assert draft_from_agent_proposal(message, proposal, now=NOW) is None


def test_stable_is_not_intend():
    message = "đường huyết ổn định 120"
    assert draft_from_agent_proposal(message, _glucose(message, value=120), now=NOW) is not None


@pytest.mark.parametrize("message", ["Chồng tôi vừa đi bộ 30 phút", "me toi do duong huyet 200", "Con tôi ăn 2 bát phở"])
def test_someone_elses_numbers_are_never_drafted(message):
    proposal = {"record_type": "activity", "evidence": message, "activity_type": "đi bộ", "duration_minutes": 30}
    assert draft_from_agent_proposal(message, proposal, now=NOW) is None


def test_missing_drug_name_is_filled_from_the_patients_medications_in_the_quote(session: Session, patient: User):
    session.add(
        Medication(
            patient_id=patient.id, drug_name="Metformin", dose="500mg", frequency="2 lần/ngày",
            start_date=datetime.utcnow() - timedelta(days=30), created_by=patient.id,
        )
    )
    session.commit()
    message = "Tôi quên uống metformin và đường huyết của tôi bây giờ là 210"
    args = DraftRecordsArgs.model_validate(
        {"records": [{"record_type": "medication_note", "evidence": "Tôi quên uống metformin", "note_type": "missed_dose"}]}
    )
    outcome = TOOLS_BY_NAME["draft_records"].handler(
        {"patient_id": patient.id, "message": message, "tool_results": {}}, session, args
    )
    draft = outcome.state_update["record_drafts"][0]
    assert draft["medication_name"] == "Metformin" and draft["medication_id"] is not None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("hôm qua 7h sáng lúc đói đường huyết 120", (7, 0, 1)),
        ("tối qua 9 giờ rưỡi đường huyết 120", (21, 30, 1)),
        ("đường huyết lúc 21h30 hôm qua 120", (21, 30, 1)),
        ("đường huyết lúc 9h 120", None),  # morning or evening: asked
        ("đường huyết 8h tối nay 120", None),  # still in the future at 10:30
    ],
)
def test_clock_time_is_read_by_code_when_the_model_leaves_it_empty(message, expected):
    draft = draft_from_agent_proposal(message, _glucose(message, value=120), now=NOW)
    if expected is None:
        assert draft["measured_at"] is None
    else:
        local = datetime.fromisoformat(draft["measured_at"]).astimezone(VN)
        assert (local.hour, local.minute, (NOW.astimezone(VN).date() - local.date()).days) == expected


@pytest.mark.parametrize(
    ("message", "meal_type"),
    [("Trưa nay tôi ăn 2 bát phở bò", "lunch"), ("toi qua an com ca kho", "dinner"), ("Hôm nay tôi ăn bún chả", None)],
)
def test_meal_type_is_read_from_the_words(message, meal_type):
    description = message.split("ăn ")[-1] if "ăn " in message else message.split("an ")[-1]
    draft = draft_from_agent_proposal(
        message, {"record_type": "meal", "evidence": message, "description": description}, now=NOW
    )
    assert draft["meal_type"] == meal_type

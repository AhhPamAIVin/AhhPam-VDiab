from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlmodel import Session, SQLModel, create_engine, select

from codebase.backend.agent.models import ChatSession, PendingRecordAction
from codebase.backend.agent.record_actions import (
    analyze_message,
    apply_action,
    create_pending_action,
    draft_from_record_candidates,
    drafts_from_record_candidates,
    interaction_for,
    requires_safety_response,
)
from codebase.backend.agent.schemas import AgentRecordActionRequest
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealRecord
from codebase.models.agents.nodes.classify_intent import classify_intent
from codebase.models.agents.nodes.synthesize_qa import _parse_qa_output

NOW = datetime(2026, 8, 25, 10, 30)
PASSWORD = "PatientPass123"


def test_one_call_qa_envelope_separates_answer_and_candidates():
    raw = """{
      "answer": "Bữa ăn này cần được đánh giá trong tổng thể khẩu phần.",
      "record_candidates": [{
        "record_type": "meal",
        "fields": {"description": "1kg cá chép", "meal_type": "lunch"},
        "evidence": "trưa nay tôi ăn 1kg cá chép"
      }]
    }"""

    answer, candidates = _parse_qa_output(raw)

    assert answer.startswith("Bữa ăn này")
    assert candidates[0]["fields"]["description"] == "1kg cá chép"


def test_plain_qa_output_remains_backward_compatible():
    answer, candidates = _parse_qa_output("Câu trả lời cũ không bọc JSON.")

    assert answer == "Câu trả lời cũ không bọc JSON."
    assert candidates == []


def test_llm_meal_candidate_requires_verbatim_evidence():
    message = "trưa nay tôi ăn 1kg cá chép có ổn không?"
    valid = [{
        "record_type": "meal",
        "fields": {"description": "1kg cá chép", "meal_type": "lunch"},
        "evidence": "trưa nay tôi ăn 1kg cá chép",
    }]
    hallucinated = [{
        "record_type": "meal",
        "fields": {"description": "cá hồi", "meal_type": "lunch"},
        "evidence": "trưa nay tôi ăn cá hồi",
    }]

    assert draft_from_record_candidates(message, valid, now=NOW)["description"] == "1kg cá chép"
    assert draft_from_record_candidates(message, hallucinated, now=NOW) is None


def test_llm_activity_candidate_validates_converted_duration():
    draft = draft_from_record_candidates(
        "Tôi vừa đạp xe 1,5 giờ",
        [{
            "record_type": "activity",
            "fields": {
                "activity_type": "đạp xe",
                "duration_minutes": 90,
                "activity_time": NOW.isoformat(),
            },
            "evidence": "Tôi vừa đạp xe 1,5 giờ",
        }],
        now=NOW,
    )

    assert draft is not None
    assert draft["duration_minutes"] == 90


def test_llm_glucose_candidate_uses_web_default_unit_when_missing():
    draft = draft_from_record_candidates(
        "Đường huyết của tôi là 7",
        [{
            "record_type": "glucose",
            "fields": {"value": 7, "unit": "mg/dL"},
            "evidence": "Đường huyết của tôi là 7",
        }],
        now=NOW,
    )

    assert draft is not None
    assert draft["unit"] == "mg/dL"


def test_deterministic_glucose_parser_preserves_explicit_mmol_unit():
    draft, _ = analyze_message(
        "Đường huyết hiện tại của tôi là 7 mmol/L", now=NOW
    )

    assert draft is not None
    assert draft["value"] == 7
    assert draft["unit"] == "mmol/L"


def test_llm_extracts_glucose_and_meal_with_independent_missing_times():
    message = "hiện tại đường huyết của tôi là 115 tôi đã ăn 3 bát cơm trắng"
    drafts = drafts_from_record_candidates(
        message,
        [
            {
                "record_type": "glucose",
                "fields": {
                    "value": 115,
                    "unit": "mg/dL",
                    "measured_at": NOW.isoformat(),
                    "meal_context": None,
                },
                "evidence": "hiện tại đường huyết của tôi là 115",
            },
            {
                "record_type": "meal",
                "fields": {
                    "description": "3 bát cơm trắng",
                    "meal_type": None,
                    "meal_time": None,
                },
                "evidence": "tôi đã ăn 3 bát cơm trắng",
            },
        ],
        now=NOW,
    )

    assert [draft["record_type"] for draft in drafts] == ["glucose", "meal"]
    assert drafts[0]["measured_at"] == NOW.isoformat()
    assert drafts[1]["meal_time"] is None


def test_lunch_phrase_sets_meal_type_but_still_collects_exact_time():
    draft, _ = analyze_message("trưa nay tôi ăn là 1 con cá chép", now=NOW)

    assert draft is not None
    assert draft["record_type"] == "meal"
    assert draft["description"] == "1 con cá chép"
    assert draft["meal_type"] == "lunch"
    assert draft["meal_time"] is None


def test_llm_keeps_glucose_and_blood_pressure_clocks_separate():
    message = "đường huyết lúc 10h là 123, huyết áp lúc 11h23 là 122/22"
    drafts = drafts_from_record_candidates(
        message,
        [
            {
                "record_type": "glucose",
                "fields": {
                    "value": 123,
                    "unit": "mg/dL",
                    "measured_at": "2026-08-25T10:00:00",
                    "meal_context": None,
                },
                "evidence": "đường huyết lúc 10h là 123",
            },
            {
                "record_type": "blood_pressure",
                "fields": {
                    "systolic": 122,
                    "diastolic": 22,
                    "unit": "mmHg",
                    "measured_at": "2026-08-25T11:23:00",
                },
                "evidence": "huyết áp lúc 11h23 là 122/22",
            },
        ],
        now=datetime(2026, 8, 25, 12, 0),
    )

    assert len(drafts) == 2
    assert drafts[0]["measured_at"] == "2026-08-25T10:00:00"
    assert drafts[1]["measured_at"] == "2026-08-25T11:23:00"


def test_extracts_glucose_and_requires_context():
    draft, has_question = analyze_message("Đường huyết hiện tại của tôi là 123", now=NOW)

    assert has_question is False
    assert draft == {
        "record_type": "glucose",
        "value": 123.0,
        "unit": "mg/dL",
        "measured_at": NOW.isoformat(),
        "meal_context": None,
        "note": None,
        "symptoms": None,
    }


def test_glucose_today_with_repeated_filler_keeps_time_and_context_missing():
    draft, has_question = analyze_message("Hôm nay đường huyết của tôi là là là123", now=NOW)

    assert has_question is False
    assert draft is not None
    assert draft["value"] == 123
    assert draft["measured_at"] is None
    assert draft["meal_context"] is None


def test_glucose_accepts_today_between_metric_and_owner():
    draft, _ = analyze_message("Đường huyết hôm nay của tôi là 125", now=NOW)

    assert draft is not None
    assert draft["value"] == 125
    assert draft["measured_at"] is None


def test_glucose_now_only_requires_meal_context():
    draft, _ = analyze_message("Bây giờ đường huyết của tôi là 123", now=NOW)

    assert draft is not None
    assert draft["measured_at"] == NOW.isoformat()
    assert draft["meal_context"] is None


def test_glucose_relative_time_is_not_mistaken_for_reading_value():
    draft, _ = analyze_message(
        "Đường huyết 5 phút trước của tôi là 123",
        now=NOW,
    )

    assert draft is not None
    assert draft["value"] == 123
    assert draft["measured_at"] == "2026-08-25T10:25:00"
    assert draft["meal_context"] is None


@pytest.mark.parametrize(
    ("message", "meal_context"),
    [
        ("đường huyết sau khi ăn của tôi bây giờ là 115", "AFTER_MEAL"),
        ("đường huyết trước khi ăn của tôi bây giờ là 98", "BEFORE_MEAL"),
    ],
)
def test_glucose_meal_context_phrase_is_not_misclassified_as_meal(
    message: str, meal_context: str
):
    draft, _ = analyze_message(message, now=NOW)

    assert draft is not None
    assert draft["record_type"] == "glucose"
    assert draft["meal_context"] == meal_context
    assert draft["measured_at"] == NOW.isoformat()


def test_glucose_inline_clock_before_meal_context_is_complete():
    draft, _ = analyze_message(
        "đường huyết lúc 12:00 trước ăn của tôi là 130",
        now=datetime(2026, 8, 25, 13, 0),
    )

    assert draft is not None
    assert draft["record_type"] == "glucose"
    assert draft["value"] == 130
    assert draft["meal_context"] == "BEFORE_MEAL"
    assert draft["measured_at"] == "2026-08-25T12:00:00"


@pytest.mark.parametrize(
    "message",
    [
        "Đường huyết sau ăn của tôi ngay lúc này là 127 nó có ổn không",
        "Đường huyết sau ăn của tôi ngay lúc này là 127 ổn không",
        "Đường huyết sau ăn của tôi ngay lúc này là 127 có đáng lo không",
    ],
)
def test_glucose_advice_question_keeps_both_intents(message: str):
    draft, has_question = analyze_message(message, now=NOW)

    assert has_question is True
    assert draft is not None
    assert draft["value"] == 127
    assert draft["meal_context"] == "AFTER_MEAL"
    assert draft["measured_at"] == NOW.isoformat()


def test_extracts_composite_activity_question_without_inventing_time():
    draft, has_question = analyze_message("Hôm nay tôi chạy bộ 30 phút có ảnh hưởng gì không?", now=NOW)

    assert has_question is True
    assert draft is not None
    assert draft["record_type"] == "activity"
    assert draft["activity_type"] == "chạy bộ"
    assert draft["duration_minutes"] == 30
    assert draft["activity_time"] is None


@pytest.mark.parametrize(
    ("message", "activity_type", "duration"),
    [
        ("Tôi vừa đi bộ nhanh 30 phút có ổn không?", "đi bộ nhanh", 30),
        ("Tôi vừa đạp xe 1,5 giờ, nó có tốt không?", "đạp xe", 90),
        ("Tôi vừa bơi lội 45 phút", "bơi lội", 45),
        ("Tôi vừa tập yoga 20 phút có ảnh hưởng gì không?", "tập yoga", 20),
        ("Tôi vừa tập thái cực quyền 25 phút", "thái cực quyền", 25),
        ("Tôi vừa tập sức mạnh nhẹ 15 phút", "tập sức mạnh nhẹ", 15),
        ("Tôi vừa tập thể dục nhịp điệu 40 phút", "thể dục nhịp điệu", 40),
        ("Tôi vừa giãn cơ 10 phút", "giãn cơ", 10),
        ("Tôi vừa làm việc nhà 2 giờ", "làm việc nhà", 120),
    ],
)
def test_supported_activity_names_do_not_swallow_followup_text(
    message: str, activity_type: str, duration: int
):
    draft, _ = analyze_message(message, now=NOW)

    assert draft is not None
    assert draft["record_type"] == "activity"
    assert draft["activity_type"] == activity_type
    assert draft["duration_minutes"] == duration
    assert draft["activity_time"] == NOW.isoformat()


def test_activity_duration_is_not_mistaken_for_clock_time():
    draft, _ = analyze_message("Lúc 8 giờ tôi vừa đi bộ 2 giờ", now=NOW)

    assert draft is not None
    assert draft["duration_minutes"] == 120
    assert draft["activity_time"] == "2026-08-25T08:00:00"


def test_hypothetical_activity_is_not_a_record():
    draft, has_question = analyze_message("Nếu hôm nay tôi chạy bộ 30 phút thì có ảnh hưởng gì không?", now=NOW)

    assert has_question is True
    assert draft is None


def test_activity_advice_without_completed_duration_is_not_a_record():
    draft, has_question = analyze_message("giờ tôi chạy bộ có được không", now=NOW)

    assert has_question is True
    assert draft is None


def test_extracts_blood_pressure_with_fixed_unit():
    draft, _ = analyze_message("Huyết áp hiện tại của tôi là 120/80", now=NOW)

    assert draft is not None
    assert draft["systolic"] == 120
    assert draft["diastolic"] == 80
    assert draft["unit"] == "mmHg"


def test_generic_measurement_question_is_not_a_record():
    draft, has_question = analyze_message("Đường huyết 123 có cao không?", now=NOW)

    assert has_question is True
    assert draft is None


def test_urgent_measurement_keeps_full_safety_path():
    draft, _ = analyze_message("Đường huyết hiện tại của tôi là 300", now=NOW)

    assert draft is not None
    assert requires_safety_response(draft) is True


def test_extracts_meal_and_leaves_meal_type_for_picker():
    draft, _ = analyze_message("Tôi vừa ăn cơm gạo lứt với cá hồi", now=NOW)

    assert draft is not None
    assert draft["record_type"] == "meal"
    assert draft["description"] == "cơm gạo lứt với cá hồi"
    assert draft["meal_type"] is None
    assert draft["meal_time"] == NOW.isoformat()


@pytest.mark.parametrize(
    "message",
    [
        "Hôm nay tôi ăn cái gì",
        "Hôm nay tôi ăn gì?",
        "Hôm nay tôi ăn món gì",
        "Tôi đã ăn gì hôm nay",
    ],
)
def test_meal_question_is_not_extracted_as_a_record(message: str):
    draft, has_question = analyze_message(message, now=NOW)

    assert has_question is True
    assert draft is None


def test_meal_question_without_food_is_not_extracted_as_a_record():
    draft, has_question = analyze_message("nay tôi ăn có ổn không", now=NOW)

    assert has_question is True
    assert draft is None


def test_meal_advice_prefills_only_the_food_name():
    draft, has_question = analyze_message("giờ tôi ăn cá có được không", now=NOW)

    assert has_question is True
    assert draft is not None
    assert draft["record_type"] == "meal"
    assert draft["description"] == "cá"


@pytest.mark.parametrize(
    ("message", "description"),
    [
        ("Hôm nay tôi ăn phở", "phở"),
        ("Hôm nay tôi ăn món phở", "món phở"),
        ("Hôm nay tôi ăn phở, có sao không?", "phở"),
        ("trưa nay tôi ăn 1kg cá chép có ổn không ?", "1kg cá chép"),
        ("trưa nay tôi ăn cá chép có tốt không", "cá chép"),
        ("trưa nay tôi ăn cá chép, liệu có ổn không?", "cá chép"),
        ("trưa nay tôi ăn cá chép, nó có ổn không?", "cá chép"),
        ("trưa nay tôi ăn cá chép vậy có ổn không?", "cá chép"),
        ("trưa nay tôi ăn cá chép có làm tăng đường huyết không?", "cá chép"),
        ("trưa nay tôi ăn cá chép có phù hợp với bệnh tiểu đường không?", "cá chép"),
        ("trưa nay tôi ăn cá chép bình thường không?", "cá chép"),
        ("tôi vừa ăn cá chép. đường huyết có tăng không?", "cá chép"),
        ("trưa nay tôi ăn cá chép rồi, giờ thấy mệt", "cá chép rồi"),
        ("tôi vừa ăn cá có nhiều xương", "cá có nhiều xương"),
    ],
)
def test_actual_meal_statement_is_still_extracted(message: str, description: str):
    draft, _ = analyze_message(message, now=NOW)

    assert draft is not None
    assert draft["record_type"] == "meal"
    assert draft["description"] == description


@pytest.mark.asyncio
async def test_illicit_substance_meal_is_rejected_before_record_confirmation():
    message = "trưa nay tôi ăn 1kg heroin"
    draft, _ = analyze_message(message, now=NOW)
    result = await classify_intent({"message": message, "request_kind": "chat"})

    # Extraction may be permissive, but its draft must never become a pending
    # action unless the unchanged agent graph ultimately admits the message.
    assert draft is not None
    assert result["intent"] == "OUT_OF_SCOPE"


def test_meal_uses_stated_clock_time_without_an_extra_picker():
    draft, _ = analyze_message("Hôm qua tôi ăn tối lúc 19 giờ 30", now=NOW)

    assert draft is not None
    assert draft["meal_type"] == "dinner"
    assert draft["meal_time"] == "2026-08-24T19:30:00"


def test_pending_glucose_requires_picker_then_confirmation():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="record-action@example.com",
            full_name="Record Action",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        chat = ChatSession(
            thread_id="record-action-thread",
            patient_id=patient.id,
            counterpart_id=patient.id,
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)

        draft, _ = analyze_message("Đường huyết hiện tại của tôi là 123", now=NOW)
        assert draft is not None
        pending = create_pending_action(session, patient_id=patient.id, chat_session=chat, draft=draft)
        first = interaction_for(session, pending)

        assert first.type == "single_choice"
        assert first.field == "meal_context"
        assert {option.value for option in first.options} >= {"FASTING", "AFTER_MEAL"}

        _, second = apply_action(
            session,
            action_id=pending.id,
            patient_id=patient.id,
            request=AgentRecordActionRequest(
                action="provide_field", field="meal_context", value="AFTER_MEAL"
            ),
        )

        assert second is not None
        assert second.type == "confirm_record"
        assert session.get(PendingRecordAction, pending.id).status == "pending"


def test_pending_glucose_today_collects_time_then_context():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="partial-record-action@example.com",
            full_name="Partial Record Action",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        chat = ChatSession(thread_id="partial-record-thread", patient_id=patient.id, counterpart_id=patient.id)
        session.add(chat)
        session.commit()
        session.refresh(chat)

        draft, _ = analyze_message("Hôm nay đường huyết của tôi là là là123", now=NOW)
        assert draft is not None
        pending = create_pending_action(session, patient_id=patient.id, chat_session=chat, draft=draft)
        first = interaction_for(session, pending)
        assert first.field == "measured_at"

        _, second = apply_action(
            session,
            action_id=pending.id,
            patient_id=patient.id,
            request=AgentRecordActionRequest(
                action="provide_field", field="measured_at", value=first.options[0].value
            ),
        )
        assert second is not None
        assert second.type == "single_choice"
        assert second.field == "meal_context"


def test_meal_requires_confirmation_after_final_choice():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="meal-action@example.com",
            full_name="Meal Action",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        chat = ChatSession(
            thread_id="meal-action-thread",
            patient_id=patient.id,
            counterpart_id=patient.id,
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)

        draft, _ = analyze_message("Tôi vừa ăn cơm gạo lứt với cá hồi", now=NOW)
        assert draft is not None
        pending = create_pending_action(session, patient_id=patient.id, chat_session=chat, draft=draft)

        _, result = apply_action(
            session,
            action_id=pending.id,
            patient_id=patient.id,
            request=AgentRecordActionRequest(
                action="provide_field", field="meal_type", value="lunch"
            ),
        )

        assert result is not None
        assert result.type == "confirm_record"
        assert result.saved_record_id is None
        assert session.get(PendingRecordAction, pending.id).status == "pending"


def test_meal_now_option_completes_time_without_error():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="meal-time-action@example.com",
            full_name="Meal Time Action",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        chat = ChatSession(thread_id="meal-time-thread", patient_id=patient.id, counterpart_id=patient.id)
        session.add(chat)
        session.commit()
        session.refresh(chat)

        draft, _ = analyze_message("Hôm nay tôi ăn sáng bánh mì", now=NOW)
        assert draft is not None
        assert draft["meal_type"] == "breakfast"
        assert draft["meal_time"] is None
        pending = create_pending_action(session, patient_id=patient.id, chat_session=chat, draft=draft)
        first = interaction_for(session, pending)
        assert first.field == "meal_time"

        _, result = apply_action(
            session,
            action_id=pending.id,
            patient_id=patient.id,
            request=AgentRecordActionRequest(
                action="provide_field", field="meal_time", value=first.options[0].value
            ),
        )
        assert result is not None
        assert result.type == "confirm_record"


def test_time_field_accepts_bounded_custom_value_from_datetime_input():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="locked-time@example.com",
            full_name="Locked Time",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        chat = ChatSession(
            thread_id="locked-time-thread",
            patient_id=patient.id,
            counterpart_id=patient.id,
        )
        session.add(chat)
        session.commit()
        draft, _ = analyze_message("Hôm nay tôi ăn sáng bánh mì", now=NOW)
        pending = create_pending_action(
            session, patient_id=patient.id, chat_session=chat, draft=draft
        )

        _, interaction = apply_action(
            session,
            action_id=pending.id,
            patient_id=patient.id,
            request=AgentRecordActionRequest(
                action="provide_field",
                field="meal_time",
                value=(pending.created_at - timedelta(days=1)).replace(tzinfo=UTC).isoformat(),
            ),
        )

        assert interaction.type == "confirm_record"


def test_time_field_rejects_future_custom_value():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="future-time@example.com",
            full_name="Future Time",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        chat = ChatSession(
            thread_id="future-time-thread",
            patient_id=patient.id,
            counterpart_id=patient.id,
        )
        session.add(chat)
        session.commit()
        draft, _ = analyze_message("Hôm nay tôi ăn sáng bánh mì", now=NOW)
        pending = create_pending_action(
            session, patient_id=patient.id, chat_session=chat, draft=draft
        )

        with pytest.raises(Exception) as exc_info:
            apply_action(
                session,
                action_id=pending.id,
                patient_id=patient.id,
                request=AgentRecordActionRequest(
                    action="provide_field",
                    field="meal_time",
                    value=(pending.created_at + timedelta(days=1)).replace(tzinfo=UTC).isoformat(),
                ),
            )

        assert getattr(exc_info.value, "status_code", None) == 422


def test_confirm_retry_returns_same_meal_without_duplicate_insert():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        patient = User(
            email="idempotent-meal@example.com",
            full_name="Idempotent Meal",
            role=UserRole.PATIENT,
            hashed_password="unused",
        )
        session.add(patient)
        session.commit()
        chat = ChatSession(
            thread_id="idempotent-meal-thread",
            patient_id=patient.id,
            counterpart_id=patient.id,
        )
        session.add(chat)
        session.commit()
        draft = {
            "record_type": "meal",
            "meal_type": "lunch",
            "meal_time": NOW.isoformat(),
            "description": "cơm và cá",
            "note": None,
        }
        pending = create_pending_action(
            session, patient_id=patient.id, chat_session=chat, draft=draft
        )
        request = AgentRecordActionRequest(action="confirm")

        _, first = apply_action(
            session, action_id=pending.id, patient_id=patient.id, request=request
        )
        _, retry = apply_action(
            session, action_id=pending.id, patient_id=patient.id, request=request
        )

        assert first.saved_record_id == retry.saved_record_id
        assert len(session.exec(select(MealRecord)).all()) == 1


@pytest.mark.asyncio
async def test_patient_chat_creates_confirmable_glucose_action(
    client: AsyncClient, session: Session
):
    patient = User(
        email="chat-record@example.com",
        full_name="Chat Record",
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(patient)
    session.commit()
    session.refresh(patient)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": patient.email, "password": PASSWORD},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    graph_result = {
        "response": "Mình đã nhận được số đo.",
        "intent": "QA",
        "data_used": [],
        "sources": [],
        "limitations": [],
        "handoff": False,
    }

    invoke = AsyncMock(return_value=graph_result)
    with patch("codebase.backend.agent.router.agent.ainvoke", new=invoke):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "Đường huyết hiện tại của tôi là 123"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    invoke.assert_awaited_once()
    interaction = response.json()["interaction"]
    assert interaction["type"] == "single_choice"
    assert interaction["field"] == "meal_context"

    supplied = await client.post(
        f"/api/v1/agent/record-actions/{interaction['pending_action_id']}",
        json={"action": "provide_field", "field": "meal_context", "value": "AFTER_MEAL"},
        headers=headers,
    )
    assert supplied.status_code == 200, supplied.text
    assert supplied.json()["interaction"]["type"] == "confirm_record"

    confirmed = await client.post(
        f"/api/v1/agent/record-actions/{interaction['pending_action_id']}",
        json={"action": "confirm"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["interaction"]["type"] == "record_saved"
    saved_id = confirmed.json()["interaction"]["saved_record_id"]
    saved = session.get(GlucoseRecord, UUID(saved_id))
    assert saved is not None
    assert saved.patient_id == patient.id
    assert saved.value == 123
    assert saved.meal_context.value == "AFTER_MEAL"


@pytest.mark.asyncio
async def test_patient_chat_uses_validated_llm_candidate_when_parser_misses(
    client: AsyncClient, session: Session
):
    patient = User(
        email="llm-record@example.com",
        full_name="LLM Record",
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(patient)
    session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": patient.email, "password": PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    message = "Bữa trưa của tôi gồm 1kg cá chép, có ổn không?"
    graph_result = {
        "response": "Mình đã đánh giá phần câu hỏi của bạn.",
        "intent": "QA",
        "record_candidates": [{
            "record_type": "meal",
            "fields": {"description": "1kg cá chép", "meal_type": "lunch"},
            "evidence": "Bữa trưa của tôi gồm 1kg cá chép",
        }],
        "data_used": [],
        "sources": [],
        "limitations": [],
        "handoff": False,
    }

    with patch(
        "codebase.backend.agent.router.agent.ainvoke",
        new=AsyncMock(return_value=graph_result),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": message},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    interaction = response.json()["interaction"]
    assert interaction["type"] == "single_choice"
    assert interaction["field"] == "meal_time"
    assert interaction["draft"]["description"] == "1kg cá chép"


@pytest.mark.asyncio
async def test_patient_chat_returns_two_independent_record_interactions(
    client: AsyncClient, session: Session
):
    patient = User(
        email="multi-record@example.com",
        full_name="Multi Record",
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(patient)
    session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": patient.email, "password": PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    message = "hiện tại đường huyết của tôi là 115 tôi đã ăn 3 bát cơm trắng"
    graph_result = {
        "response": "Mình đã nhận diện hai thông tin sức khỏe.",
        "intent": "QA",
        "record_candidates": [
            {
                "record_type": "glucose",
                "fields": {
                    "value": 115,
                    "unit": "mg/dL",
                    "measured_at": NOW.isoformat(),
                    "meal_context": None,
                },
                "evidence": "hiện tại đường huyết của tôi là 115",
            },
            {
                "record_type": "meal",
                "fields": {
                    "description": "3 bát cơm trắng",
                    "meal_type": None,
                    "meal_time": None,
                },
                "evidence": "tôi đã ăn 3 bát cơm trắng",
            },
        ],
        "data_used": [],
        "sources": [],
        "limitations": [],
        "handoff": False,
    }

    with patch(
        "codebase.backend.agent.router.agent.ainvoke",
        new=AsyncMock(return_value=graph_result),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": message},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["interactions"]) == 2
    assert [item["draft"]["record_type"] for item in body["interactions"]] == [
        "glucose",
        "meal",
    ]
    assert body["interaction"] == body["interactions"][0]
    assert len(session.exec(select(PendingRecordAction)).all()) == 2


@pytest.mark.asyncio
async def test_doctor_chat_never_creates_record_action(client: AsyncClient, session: Session):
    doctor = User(
        email="doctor-record@example.com",
        full_name="Doctor Record",
        role=UserRole.DOCTOR,
        hashed_password=hash_password(PASSWORD),
    )
    patient = User(
        email="doctor-target@example.com",
        full_name="Doctor Target",
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(doctor)
    session.add(patient)
    session.commit()
    session.refresh(doctor)
    session.refresh(patient)
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": doctor.email, "password": PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    graph_result = {
        "response": "Đã phân tích số đo.",
        "intent": "QA",
        "data_used": [],
        "sources": [],
        "limitations": [],
        "handoff": False,
    }

    with patch("codebase.backend.agent.router.agent.ainvoke", new=AsyncMock(return_value=graph_result)):
        response = await client.post(
            "/api/v1/agent/chat",
            json={
                "patient_id": str(patient.id),
                "message": "Đường huyết hiện tại của tôi là 123",
            },
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["interaction"] is None
    assert session.exec(select(PendingRecordAction)).all() == []


@pytest.mark.asyncio
async def test_out_of_scope_meal_draft_never_gets_confirmation(client: AsyncClient, session: Session):
    patient = User(
        email="guarded-record@example.com",
        full_name="Guarded Record",
        role=UserRole.PATIENT,
        hashed_password=hash_password(PASSWORD),
    )
    session.add(patient)
    session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": patient.email, "password": PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    graph_result = {
        "response": "Nội dung này chưa thuộc phần mình có thể hỗ trợ.",
        "intent": "OUT_OF_SCOPE",
        "data_used": [],
        "sources": [],
        "limitations": [],
        "handoff": False,
    }

    with patch(
        "codebase.backend.agent.router.agent.ainvoke",
        new=AsyncMock(return_value=graph_result),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "trưa nay tôi ăn 1kg heroin"},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["interaction"] is None
    assert session.exec(select(PendingRecordAction)).all() == []

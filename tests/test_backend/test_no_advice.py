"""Epic 8 — the patient assistant reports, compares and explains, never
advises; its evaluation of the data ships apart, labelled as AI-generated."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codebase.models.agents.nodes.guardrail import guardrail_check, has_personal_advice
from codebase.models.agents.nodes.synthesize_qa import synthesize_qa
from codebase.models.agents.prompts.safety_patterns import normalize_for_pattern_match


def _advice(text: str) -> bool:
    return has_personal_advice(normalize_for_pattern_match(text))


@pytest.mark.parametrize(
    "text",
    [
        "Bạn nên ăn ít cơm trắng hơn.",
        "Đường huyết của bạn hơi cao. Hãy đi bộ 30 phút sau bữa tối.",
        "Bạn có thể thử giảm tinh bột vào buổi tối.",
        "Một vài gợi ý:\n- Hạn chế đồ ngọt\n- Uống nhiều nước",
        "Mình khuyên bạn ngủ đủ giấc.",
        "Bạn cần đo lại đường huyết sau 15 phút.",
        "Tránh bỏ bữa sáng nhé.",
    ],
)
def test_personal_advice_is_detected(text):
    assert _advice(text)


@pytest.mark.parametrize(
    "text",
    [
        "Đường huyết trung bình 7 ngày qua của bạn là 142 mg/dL, cao hơn tuần trước 8 mg/dL.",
        "Theo Bộ Y tế, người bệnh nên duy trì HbA1c dưới 7%.",
        "Mình không đưa lời khuyên điều trị. Bạn nên trao đổi với bác sĩ điều trị về chỉ số này.",
        "Nếu bạn thấy choáng váng, hãy gọi 115 ngay.",
        "Bạn hãy xác nhận ở thẻ bên dưới để lưu chỉ số.",
        "Bạn có thể cho mình biết bạn đo lúc nào không?",
        "Tuần này bạn ghi 12 lần đo, 3 lần trên 180 mg/dL.",
    ],
)
def test_reporting_referral_and_app_use_are_not_advice(text):
    assert not _advice(text)


def _state(response: str, *, role: str = "patient", assessment: str | None = None) -> dict:
    return {
        "intent": "QA",
        "user_role": role,
        "response": response,
        "assessment": assessment,
        "retrieved_chunks": [],
        "tool_results": {"records": {"glucose": [{"value": 180}]}},
        "personal_data_grounded": True,
    }


@pytest.mark.asyncio
async def test_guardrail_rejects_advice_in_patient_chat_only():
    advice = "Đường huyết của bạn là 180. Bạn nên ăn ít cơm hơn."
    patient = await guardrail_check(_state(advice))
    assert "personal_advice" in patient["guardrail_failures"]
    doctor = await guardrail_check(_state(advice, role="doctor"))
    assert "personal_advice" not in (doctor.get("guardrail_failures") or [])


@pytest.mark.asyncio
async def test_guardrail_scans_the_assessment_box_too():
    result = await guardrail_check(
        _state("Đường huyết của bạn là 180.", assessment="Chỉ số hơi cao, bạn nên hạn chế tinh bột.")
    )
    assert "personal_advice" in result["guardrail_failures"]


@pytest.mark.asyncio
async def test_second_failure_ships_the_fallback_without_the_assessment():
    state = {**_state("Bạn nên ăn ít cơm hơn.", assessment="Hãy tập thể dục."), "retry_count": 1}
    result = await guardrail_check(state)
    assert result["assessment"] is None and "bác sĩ" in result["response"]


def _synth(payload: dict) -> MagicMock:
    llm = AsyncMock()
    response = MagicMock()
    response.content = json.dumps(payload, ensure_ascii=False)
    llm.ainvoke.return_value = response
    return MagicMock(return_value=llm)


@pytest.mark.asyncio
async def test_patient_answer_and_assessment_are_separate_and_the_policy_is_in_the_prompt():
    synth = _synth(
        {"answer": "Đường huyết trung bình 7 ngày: 142 mg/dL.", "assessment": "Cao hơn mức mục tiêu chung.",
         "used_chunk_ids": [], "record_candidates": []}
    )
    with patch("codebase.models.agents.nodes.synthesize_qa.get_llm", synth):
        result = await synthesize_qa({**_state(""), "message": "đường huyết tuần này thế nào", "messages": []})

    assert result["response"] == "Đường huyết trung bình 7 ngày: 142 mg/dL."
    assert result["assessment"] == "Cao hơn mức mục tiêu chung."
    prompt = synth.return_value.ainvoke.call_args.args[0][-1]["content"]
    assert "QUY TẮC KHÔNG TƯ VẤN" in prompt


@pytest.mark.asyncio
async def test_doctor_chat_keeps_one_answer_and_no_policy_block():
    synth = _synth({"answer": "Bệnh nhân có 12 lần đo.", "assessment": "Xu hướng tăng.", "used_chunk_ids": []})
    with patch("codebase.models.agents.nodes.synthesize_qa.get_llm", synth):
        result = await synthesize_qa({**_state("", role="doctor"), "message": "xu hướng?", "messages": []})

    assert result["assessment"] is None and "Xu hướng tăng." in result["response"]
    assert "QUY TẮC KHÔNG TƯ VẤN" not in synth.return_value.ainvoke.call_args.args[0][-1]["content"]

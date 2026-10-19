"""LLM correction + human-in-the-loop clarity gate.

One call per finalized utterance: fix ASR noise/punctuation/diacritics, and
judge whether the result is clean enough to forward to the medical
consultant unattended. On anything going wrong (timeout, bad JSON, empty
key) we fail toward asking the patient to repeat rather than silently
forwarding a possibly-garbled transcript — the auto-send path only fires on
an explicit is_clear=True.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Bạn là bộ hiệu chỉnh transcript cho một trợ lý y tế tiếng Việt.
Bạn nhận một đoạn văn bản thô do hệ thống nhận dạng giọng nói (ASR) tạo ra từ
lời nói của bệnh nhân. Nhiệm vụ của bạn:

1. Sửa lỗi chính tả, dấu câu, dấu thanh tiếng Việt do ASR nghe sai — KHÔNG
   được thêm, suy diễn, hay thay đổi ý nghĩa y khoa của câu nói.
2. Đánh giá xem văn bản đã đủ rõ ràng, mạch lạc để chuyển thẳng cho bác sĩ/
   trợ lý AI tư vấn hay chưa.
3. Nếu văn bản quá vụn, mâu thuẫn, hoặc thiếu ý đến mức không thể hiểu chắc
   chắn bệnh nhân muốn nói gì, đặt is_clear=false và viết một câu hỏi ngắn,
   lịch sự bằng tiếng Việt để hỏi lại bệnh nhân (ví dụ: xin họ nói lại rõ
   hơn, hoặc xác nhận lại một chi tiết cụ thể bị mất).

Chỉ trả lời bằng JSON hợp lệ theo đúng schema, không thêm văn bản nào khác:
{"corrected_text": string, "is_clear": boolean, "clarification_question": string | null}
"""

_FALLBACK_CLARIFICATION = (
    "Xin lỗi, tôi chưa nghe rõ bạn vừa nói gì. Bạn có thể nói lại giúp tôi được không?"
)


@dataclass
class CorrectionResult:
    corrected_text: str
    is_clear: bool
    clarification_question: str | None


def _client() -> OpenAI:
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def correct_transcript(raw_text: str) -> CorrectionResult:
    raw_text = raw_text.strip()
    if not raw_text:
        return CorrectionResult("", False, _FALLBACK_CLARIFICATION)

    try:
        response = _client().chat.completions.create(
            model=settings.CORRECTION_MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )
        payload = json.loads(response.choices[0].message.content)
        corrected = str(payload.get("corrected_text") or raw_text).strip()
        is_clear = bool(payload.get("is_clear"))
        question = payload.get("clarification_question")
        if is_clear:
            return CorrectionResult(corrected, True, None)
        return CorrectionResult(corrected, False, question or _FALLBACK_CLARIFICATION)
    except Exception:  # noqa: BLE001 - any failure here must not auto-send raw ASR output
        logger.exception("Correction call failed for raw transcript: %r", raw_text)
        return CorrectionResult(raw_text, False, _FALLBACK_CLARIFICATION)

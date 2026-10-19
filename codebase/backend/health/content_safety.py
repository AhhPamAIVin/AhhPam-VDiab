"""Content safety check on patient-submitted free-text health-log fields.

A patient can type anything into the free-text meal `description`
(health/schemas.py MealRecordCreate) or activity `activity_type`
(ActivityRecordCreate) fields. Manual testing found that entering an illicit
drug name into the meal field (e.g. "300g heroin") was accepted and stored
as-is, then echoed back by the AI consultation chat as an established fact
about the patient's actual lunch — the assistant went on to give
heroin-specific "dietary" advice and confirmed a plan to repeat it the next
day, instead of recognizing the entry as not describing food at all. The same
failure mode applies just as well to the activity log (e.g. "hút heroin"
logged as a 30-minute activity). The AI's own output guardrail
(codebase/models/agents/nodes/guardrail.py) scans the drafted *response* for
unsafe clinical phrasing, but has no signal that the *input* health data it
was asked to reason about was never a real meal/activity, so it can't catch
this failure mode.

This check runs at submission time instead, so unsafe content never reaches
the AI's health-data context in the first place. Two layers, same shape as
classify_intent.py's deterministic gate feeding into plan_response.py's LLM
judgment:

  1. find_unsafe_substance_term — a tiny literal denylist, zero latency/cost,
     for the exact wording already seen in the wild. Trivially evaded by a
     misspelling, a synonym, or describing the same substance without naming
     it — a denylist alone is NOT the real defense here (feedback from a real
     test: a patient can easily avoid a fixed keyword list), it's just a fast
     pre-filter that skips the LLM call for the obvious case. Shared across
     meal/activity since the substances it lists are equally out of place in
     either field.
  2. llm_flags_unsafe_meal_description / llm_flags_unsafe_activity_type — a
     small LLM classifying whether the text plausibly describes a real
     meal/drink or a real physical activity, respectively. This is the layer
     that actually generalizes to rewordings the denylist can't enumerate.
     Fails OPEN (allows the submission) on any LLM/parse error, same
     convention as plan_response.py/verify_citations.py — an LLM outage must
     not block patients from logging a real meal or activity.
"""

from __future__ import annotations

import json
import logging

from codebase.models.agents.prompts.safety_patterns import normalize_for_pattern_match
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

# Substring match against normalize_for_pattern_match(text): lowercase,
# NFKC-normalized, invisible-character-stripped, whitespace-collapsed — same
# normalization safety_patterns.py applies against jailbreak evasion, reused
# here so a zero-width-character or full-width-character trick can't sneak an
# unsafe term past even this fast pre-filter.
_UNSAFE_SUBSTANCE_TERMS: list[str] = [
    # --- opioids ---
    "heroin",
    "hê rô in",
    "bạch phiến",
    "thuốc phiện",
    "opium",
    "fentanyl",
    "morphine",
    # --- stimulants ---
    "cocaine",
    "cocain",
    "methamphetamine",
    "ma túy đá",
    "ma tuý đá",
    "hàng đá",
    "amphetamine",
    # --- other common illicit drugs ---
    "ma túy",
    "ma tuý",
    "cần sa",
    "marijuana",
    "cannabis",
    "thuốc lắc",
    "ecstasy",
    "mdma",
    "ketamine",
    "lsd",
]


def find_unsafe_substance_term(text: str) -> str | None:
    """Fast literal pre-filter. Return the first matched term, or None."""
    normalized = normalize_for_pattern_match(text)
    for term in _UNSAFE_SUBSTANCE_TERMS:
        if term in normalized:
            return term
    return None


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if "\n" in text:
        first_line, rest = text.split("\n", 1)
        if first_line.strip().lower() in {"json", ""}:
            text = rest
    return text.strip()


def _parse_bool_field(content: str, field: str) -> bool | None:
    """None means unparseable — caller must fail open, never raise."""
    try:
        parsed = json.loads(_strip_code_fence(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(field)
    return value if isinstance(value, bool) else None


async def _llm_classifies_as(*, system_prompt: str, user_content: str, field: str, log_label: str) -> bool | None:
    """Shared LLM-call/parse plumbing. Returns the classified bool, or None
    on any LLM/parse failure (caller decides the fail-open default)."""
    try:
        llm = get_llm()
        result = await llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        )
        content = getattr(result, "content", None) or str(result) or ""
    except Exception:
        logger.warning("%s safety LLM call failed, allowing submission", log_label, exc_info=True)
        return None

    value = _parse_bool_field(content, field)
    if value is None:
        logger.warning("%s safety LLM got unparseable output, allowing submission: %r", log_label, content)
    return value


_MEAL_SYSTEM_PROMPT = (
    "Bạn là bộ kiểm tra an toàn nội dung cho mục \"mô tả bữa ăn\" trong nhật ký sức khỏe của "
    "bệnh nhân đái tháo đường. Nhiệm vụ DUY NHẤT của bạn là phân loại: đoạn mô tả này có hợp "
    "lý như một bữa ăn/thức uống có thật hay không, hay thực chất đang mô tả ma túy, chất cấm, "
    "chất độc, hoặc nội dung rõ ràng không phải thực phẩm/đồ uống — kể cả khi bệnh nhân viết "
    "lách, viết tắt, dùng tiếng lóng, hoặc mô tả chất đó mà không gọi đúng tên. Bạn KHÔNG đánh "
    "giá độ lành mạnh hay phù hợp dinh dưỡng của bữa ăn — một bữa ăn không lành mạnh vẫn là "
    "thực phẩm thật và vẫn được coi là hợp lệ. Chỉ trả lời DUY NHẤT một đối tượng JSON theo "
    'đúng định dạng sau, không kèm giải thích, không kèm markdown, không kèm dấu ```: '
    '{"is_food": true|false}'
)
_MEAL_USER_TEMPLATE = 'Mô tả bữa ăn do bệnh nhân nhập:\n"""\n{text}\n"""'

_ACTIVITY_SYSTEM_PROMPT = (
    "Bạn là bộ kiểm tra an toàn nội dung cho mục \"loại hoạt động thể chất\" trong nhật ký sức "
    "khỏe của bệnh nhân đái tháo đường. Nhiệm vụ DUY NHẤT của bạn là phân loại: đoạn mô tả này "
    "có hợp lý như một hoạt động/vận động thể chất có thật hay không (ví dụ: đi bộ, chạy bộ, "
    "đạp xe, bơi, tập gym, làm việc nhà, yoga...), hay thực chất đang mô tả ma túy, chất cấm, "
    "chất độc, hoặc nội dung rõ ràng không phải một hoạt động thể chất — kể cả khi bệnh nhân "
    "viết lách, viết tắt, dùng tiếng lóng, hoặc mô tả chất đó mà không gọi đúng tên. Bạn KHÔNG "
    "đánh giá cường độ hay mức độ phù hợp của hoạt động — một hoạt động gắng sức quá mức hoặc "
    "không phù hợp thể trạng vẫn là hoạt động thật và vẫn được coi là hợp lệ. Chỉ trả lời DUY "
    'NHẤT một đối tượng JSON theo đúng định dạng sau, không kèm giải thích, không kèm markdown, '
    'không kèm dấu ```: {"is_activity": true|false}'
)
_ACTIVITY_USER_TEMPLATE = 'Loại hoạt động do bệnh nhân nhập:\n"""\n{text}\n"""'


async def llm_flags_unsafe_meal_description(description: str) -> bool:
    """Semantic pass beyond find_unsafe_substance_term's literal denylist —
    catches misspellings, slang, or oblique descriptions that name no
    substance the denylist recognizes, but that a small model can still tell
    isn't food. Fails OPEN (returns False) on any LLM/parse error."""
    is_food = await _llm_classifies_as(
        system_prompt=_MEAL_SYSTEM_PROMPT,
        user_content=_MEAL_USER_TEMPLATE.format(text=description),
        field="is_food",
        log_label="meal description",
    )
    return is_food is not None and not is_food


async def llm_flags_unsafe_activity_type(activity_type: str) -> bool:
    """Activity-log counterpart to llm_flags_unsafe_meal_description — same
    semantic-pass/fail-open contract, judging "real physical activity" instead
    of "real food"."""
    is_activity = await _llm_classifies_as(
        system_prompt=_ACTIVITY_SYSTEM_PROMPT,
        user_content=_ACTIVITY_USER_TEMPLATE.format(text=activity_type),
        field="is_activity",
        log_label="activity type",
    )
    return is_activity is not None and not is_activity


async def is_unsafe_meal_description(description: str) -> bool:
    """True if the description should be rejected — either layer flags it."""
    if find_unsafe_substance_term(description) is not None:
        return True
    return await llm_flags_unsafe_meal_description(description)


async def is_unsafe_activity_type(activity_type: str) -> bool:
    """True if the activity type should be rejected — either layer flags it."""
    if find_unsafe_substance_term(activity_type) is not None:
        return True
    return await llm_flags_unsafe_activity_type(activity_type)

"""Patient-only structured record drafts extracted from chat messages.

The parser deliberately handles only high-confidence, already-occurred statements.
It never writes. Persistence happens only through ``apply_action`` after an explicit
structured confirmation, using the existing health/medication services.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codebase.backend.agent.models import ChatSession, PendingRecordAction
from codebase.backend.agent.schemas import AgentChoiceOption, AgentInteraction, AgentRecordActionRequest
from codebase.backend.health.models import (
    ActivityRecord,
    BloodPressureRecord,
    GlucoseRecord,
    MealContext,
    MealRecord,
    MealType,
)
from codebase.backend.health.schemas import (
    ActivityRecordCreate,
    BloodPressureRecordCreate,
    GlucoseRecordCreate,
    MealRecordCreate,
)
from codebase.backend.medications.models import MedicationNote, MedicationNoteType

_ACTION_TTL = timedelta(minutes=30)
_MAX_CUSTOM_TIME_AGE = timedelta(days=3650)
_CUSTOM_TIME_CLOCK_SKEW = timedelta(minutes=5)
_QUESTION_RE = re.compile(
    r"\?|\b(có ảnh hưởng|có sao|có cao|có thấp|có ổn(?: không)?|ổn không|"
    r"có tốt(?: không)?|tốt không|nguy hiểm không|bất thường không|đáng lo không|"
    r"bình thường|thế nào|tại sao|nên không|được không)\b",
    re.I,
)
_TRAILING_MEAL_QUESTION_RE = re.compile(
    r"\s*[,;]?\s+(?:(?:liệu|vậy|thế|nó|món này|ăn vậy)\s+)*(?:"
    r"có\s+(?:ảnh hưởng(?:\s+gì)?|sao|cao|thấp|ổn|tốt|được|nguy hiểm|bất thường|đáng lo|"
    r"phù hợp(?:\s+với[^?.!]*)?|lành mạnh|làm\s+(?:tăng|giảm)[^?.!]*)(?:\s+(?:không|ko|k))?|"
    r"(?:đường huyết|đường máu)\s+có\s+(?:tăng|giảm|cao|thấp)[^?.!]*\s+(?:không|ko|k)|"
    r"(?:ổn|tốt|nguy hiểm|bất thường|đáng lo|bình thường|nên)\s+(?:không|ko|k)|"
    r"thì sao|thế nào|được\s+(?:không|ko|k)"
    r")\s*[?.!]*$",
    re.I,
)
_FOLLOWUP_MEAL_CLAUSE_RE = re.compile(
    r"\s*[,;]\s*(?:rồi|sau đó|giờ|hiện giờ)\b.*$",
    re.I,
)
_HYPOTHETICAL_RE = re.compile(r"\b(nếu|giả sử|ví dụ|định|dự định|có nên)\b", re.I)
_MEAL_QUERY_RE = re.compile(r"\b(?:ăn|dùng bữa)\s+(?:(?:cái|món)\s+)?gì\b", re.I)
_INVALID_MEAL_DESCRIPTIONS = {"gì", "cái gì", "món gì"}
_MEAL_QUESTION_ONLY_RE = re.compile(
    r"^(?:có|không|ko|k|ổn|tốt|được|sao|thế nào|bình thường|nên|ăn|món|bữa|"
    r"có ổn|có tốt|có được|ổn không|tốt không|được không)(?:\s+(?:không|ko|k))?$",
    re.I,
)
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_INLINE_CLOCK = r"lúc\s+(?:[01]?\d|2[0-3])(?:\s*(?:giờ|h|:)\s*[0-5]?\d)?"
_RELATIVE_TIME = r"\d+(?:[.,]\d+)?\s*(?:phút|giờ|tiếng)\s*trước"
_ACTIVITY_NAME = (
    r"chạy bộ|đi bộ nhanh|đi bộ|đạp xe|bơi lội|bơi|tập yoga|yoga|thái cực quyền|"
    r"tập sức mạnh nhẹ|tập sức mạnh|thể dục nhịp điệu|aerobic|aerobics|giãn cơ|"
    r"làm việc nhà|tập thể dục"
)

_MEAL_CONTEXT_OPTIONS = [
    AgentChoiceOption(label="Lúc đói", value=MealContext.FASTING.value),
    AgentChoiceOption(label="Trước ăn", value=MealContext.BEFORE_MEAL.value),
    AgentChoiceOption(label="Sau ăn", value=MealContext.AFTER_MEAL.value),
    AgentChoiceOption(label="Trước khi ngủ", value=MealContext.BEFORE_SLEEP.value),
    AgentChoiceOption(label="Khác", value=MealContext.RANDOM.value),
]
_MEAL_TYPE_OPTIONS = [
    AgentChoiceOption(label="Bữa sáng", value=MealType.BREAKFAST.value),
    AgentChoiceOption(label="Bữa trưa", value=MealType.LUNCH.value),
    AgentChoiceOption(label="Bữa tối", value=MealType.DINNER.value),
    AgentChoiceOption(label="Bữa phụ", value=MealType.SNACK.value),
]


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _glucose_unit(text: str) -> str:
    """Return the explicitly stated unit, defaulting only when none exists."""
    if re.search(r"\bmmol\s*/\s*l\b", text, re.I):
        return "mmol/L"
    return "mg/dL"


def _meal_context(text: str) -> str | None:
    lowered = text.lower()
    if "lúc đói" in lowered or "nhịn đói" in lowered:
        return MealContext.FASTING.value
    if "trước ăn" in lowered or "trước khi ăn" in lowered or "trước bữa" in lowered:
        return MealContext.BEFORE_MEAL.value
    if "sau ăn" in lowered or "sau khi ăn" in lowered or "sau bữa" in lowered:
        return MealContext.AFTER_MEAL.value
    if "trước khi ngủ" in lowered:
        return MealContext.BEFORE_SLEEP.value
    return None


def _meal_type(text: str) -> str | None:
    lowered = text.lower()
    for needles, value in (
        (("bữa sáng", "ăn sáng"), MealType.BREAKFAST.value),
        (("bữa trưa", "ăn trưa", "trưa nay"), MealType.LUNCH.value),
        (("bữa tối", "ăn tối"), MealType.DINNER.value),
        (("bữa phụ", "ăn nhẹ"), MealType.SNACK.value),
    ):
        if any(needle in lowered for needle in needles):
            return value
    return None


def _occurred_now(text: str) -> bool:
    return bool(
        re.search(r"\b(vừa|mới|hiện tại|bây giờ|bây h|bây giừo|ngay lúc này|lúc này)\b", text, re.I)
    )


def _explicit_time(text: str, current: datetime) -> datetime | None:
    """Resolve a time only when the patient actually supplied one.

    A bare ``hôm nay`` identifies the date, not the measurement time, so it
    deliberately remains incomplete and is collected by the interaction UI.
    """
    # Production ``current`` is an aware UTC instant. Explicit clocks in a
    # Vietnamese utterance are Vietnam wall-clock times, so resolve them in
    # the configured patient timezone before converting back to UTC. Keep
    # naive injected values unchanged for deterministic unit tests.
    if current.tzinfo is not None:
        from zoneinfo import ZoneInfo

        from codebase.backend.config import get_settings

        result = current.astimezone(ZoneInfo(get_settings().reminder_timezone))
    else:
        result = current
    if re.search(r"\bhôm qua\b", text, re.I):
        result = result - timedelta(days=1)

    relative = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*(phút|giờ|tiếng)\s*trước\b", text, re.I
    )
    if relative:
        amount = _number(relative.group(1))
        delta = timedelta(minutes=amount) if relative.group(2).lower() == "phút" else timedelta(hours=amount)
        return current - delta

    clock = re.search(r"\b([01]?\d|2[0-3])(?:\s*(?:giờ|h|:))\s*([0-5]?\d)?\b", text, re.I)
    if clock:
        resolved = result.replace(
            hour=int(clock.group(1)),
            minute=int(clock.group(2) or 0),
            second=0,
            microsecond=0,
        )
        return resolved.astimezone(UTC) if resolved.tzinfo is not None else resolved
    if _occurred_now(text):
        return current
    return None


def _personal_measurement(text: str) -> bool:
    return bool(re.search(r"\b(của tôi|tôi vừa|tôi mới|tôi đo|vừa đo|đo được|hiện tại)\b", text, re.I))


def _meal_description(raw: str) -> str:
    """Keep the consumed-food span, excluding a later question/comment."""
    # A new sentence is never part of the meal description. Preserve dots
    # inside tokens (for example 1.5kg) by requiring whitespace after it.
    description = re.split(r"[.!?]\s+(?=\S)", raw, maxsplit=1)[0]
    description = _FOLLOWUP_MEAL_CLAUSE_RE.sub("", description)
    description = _TRAILING_MEAL_QUESTION_RE.sub("", description)
    description = re.sub(r"^(?:là|gồm|bao gồm)\s+", "", description.strip(), flags=re.I)
    return description.strip(" .,?!")


def _has_concrete_meal_description(description: str) -> bool:
    normalized = " ".join(description.lower().split()).strip(" .,?!")
    return bool(
        normalized
        and normalized not in _INVALID_MEAL_DESCRIPTIONS
        and not _MEAL_QUESTION_ONLY_RE.fullmatch(normalized)
    )


def analyze_message(message: str, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, bool]:
    """Return a high-confidence draft plus whether the same message asks a question."""
    text = " ".join(message.strip().split())
    lowered = text.lower()
    meal_query = bool(_MEAL_QUERY_RE.search(lowered))
    has_question = (
        bool(_QUESTION_RE.search(lowered))
        or bool(_TRAILING_MEAL_QUESTION_RE.search(lowered))
        or meal_query
    )
    if not text or _HYPOTHETICAL_RE.search(lowered):
        return None, has_question
    current = now or datetime.now(UTC)

    glucose = re.search(
        rf"(?:đường huyết|đường máu)(?:\s+(?:hôm nay|hôm qua|hiện tại|bây giờ|ngay lúc này|lúc này|"
        rf"của tôi|lúc đói|nhịn đói|trước ăn|trước khi ăn|trước bữa|"
        rf"sau ăn|sau khi ăn|sau bữa|trước khi ngủ|{_INLINE_CLOCK}|{_RELATIVE_TIME}))*"
        rf"\s*(?:(?:là|:|được)\s*)*{_NUMBER}",
        text,
        re.I,
    )
    if glucose and _personal_measurement(text):
        draft = {
            "record_type": "glucose",
            "value": _number(glucose.group(1)),
            "unit": _glucose_unit(text),
            "measured_at": (
                resolved_time.isoformat() if (resolved_time := _explicit_time(text, current)) else None
            ),
            "meal_context": _meal_context(text),
            "note": None,
            "symptoms": None,
        }
        return draft, has_question

    pressure = re.search(
        rf"huyết áp(?:\s+(?:hôm nay|hôm qua|hiện tại|bây giờ|của tôi|{_INLINE_CLOCK}))*"
        rf"\s*(?:(?:là|:|được)\s*)*{_NUMBER}\s*/\s*{_NUMBER}",
        text,
        re.I,
    )
    if pressure and _personal_measurement(text):
        return {
            "record_type": "blood_pressure",
            "systolic": _number(pressure.group(1)),
            "diastolic": _number(pressure.group(2)),
            "unit": "mmHg",
            "measured_at": (
                resolved_time.isoformat() if (resolved_time := _explicit_time(text, current)) else None
            ),
            "note": None,
        }, has_question

    activity = re.search(
        rf"(?:tôi\s+)?(?:vừa\s+|đã\s+|hôm nay\s+)?({_ACTIVITY_NAME})\s+(?:khoảng\s+)?{_NUMBER}\s*(phút|giờ)",
        text,
        re.I,
    )
    if activity and re.search(r"\b(tôi|vừa|đã|hôm nay)\b", lowered):
        duration = _number(activity.group(2))
        if activity.group(3).lower() == "giờ":
            duration *= 60
        # Do not let a duration such as "2 giờ" or "1,5 giờ" masquerade as
        # the measurement clock. Keep any real clock elsewhere in the text.
        activity_time_text = f"{text[:activity.start()]} {text[activity.end():]}"
        resolved_activity_time = _explicit_time(activity_time_text, current)
        if resolved_activity_time is None and _occurred_now(text):
            resolved_activity_time = current
        return {
            "record_type": "activity",
            "activity_type": activity.group(1).lower(),
            "duration_minutes": int(duration),
            "activity_time": (
                resolved_activity_time.isoformat() if resolved_activity_time else None
            ),
            "note": None,
        }, has_question

    meal = re.search(
        r"(?:hôm qua\s+|hôm nay\s+)?(?:tôi\s+)?(?:vừa\s+|đã\s+)?(?:ăn|dùng bữa)\s+(.+)",
        text,
        re.I,
    )
    meal_is_measurement_context = bool(
        re.search(r"(?:đường huyết|đường máu).{0,40}\b(?:trước|sau)(?: khi)? ăn\b", lowered)
    )
    if (
        meal
        and not meal_query
        and not meal_is_measurement_context
        and re.search(r"\b(tôi|vừa|đã|hôm nay|hôm qua|bữa sáng|bữa trưa|bữa tối)\b", lowered)
    ):
        description = _meal_description(meal.group(1))
        if not _has_concrete_meal_description(description):
            return None, True
        return {
            "record_type": "meal",
            "meal_type": _meal_type(text),
            "meal_time": (
                resolved_time.isoformat() if (resolved_time := _explicit_time(text, current)) else None
            ),
            "description": description,
            "note": None,
        }, has_question

    missed = re.search(r"(?:tôi\s+)?(?:đã\s+)?(?:quên|bỏ lỡ|chưa)\s+uống\s+(?:thuốc\s+)?([^,.?]+)", text, re.I)
    if missed:
        return {
            "record_type": "medication_note",
            "medication_name": missed.group(1).strip(),
            "medication_id": None,
            "note_type": MedicationNoteType.MISSED_DOSE.value,
            "text": text,
            "occurred_at": current.isoformat(),
        }, has_question

    return None, has_question


_RECORD_CLAUSE_START_RE = re.compile(
    rf"(?:\b(?:hôm nay|hôm qua|hiện tại|bây giờ)\s+)?(?:"
    rf"(?:đường huyết|đường máu)\b|"
    rf"huyết áp\b|"
    rf"(?:tôi\s+)?(?:vừa\s+|đã\s+)?(?:{_ACTIVITY_NAME})\b|"
    rf"(?:tôi\s+)?(?:vừa|đã)\s+(?:ăn|dùng bữa)\b|"
    rf"(?:bữa sáng|bữa trưa|bữa tối)\b|"
    rf"(?:tôi\s+)?(?:đã\s+)?(?:quên|bỏ lỡ|chưa)\s+uống\b"
    rf")",
    re.I,
)


def analyze_message_drafts(
    message: str, *, now: datetime | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Extract every independently stated record from a compound message.

    ``analyze_message`` intentionally remains the conservative single-record
    parser used by existing callers. This wrapper splits only at explicit
    record-introducing phrases, then applies that same validated parser to
    each clause. It therefore handles messages such as a glucose reading plus
    a meal without depending on the LLM returning two candidates.
    """
    text = " ".join(message.strip().split())
    first, has_question = analyze_message(text, now=now)
    starts = [match.start() for match in _RECORD_CLAUSE_START_RE.finditer(text)]
    if len(starts) < 2:
        return ([first] if first is not None else []), has_question

    drafts: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        clause = text[start:end].strip(" ,;.")
        draft, clause_has_question = analyze_message(clause, now=now)
        has_question = has_question or clause_has_question
        if draft is not None and not any(
            existing.get("record_type") == draft.get("record_type") for existing in drafts
        ):
            drafts.append(draft)
    return (drafts or ([first] if first is not None else [])), has_question


_LLM_FIELD_ALLOWLIST: dict[str, set[str]] = {
    "glucose": {"value", "unit", "measured_at", "meal_context", "note", "symptoms"},
    "blood_pressure": {"systolic", "diastolic", "unit", "measured_at", "note"},
    "meal": {"meal_type", "meal_time", "description", "note"},
    "activity": {"activity_type", "duration_minutes", "activity_time", "note"},
    "medication_note": {
        "medication_name", "medication_id", "note_type", "text", "occurred_at"
    },
}


def _normalized_span(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _evidence_has_number(evidence: str, value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    for token in re.findall(r"\d+(?:[.,]\d+)?", evidence):
        if abs(_number(token) - number) < 1e-9:
            return True
    return False


def _evidence_supports_duration(evidence: str, minutes: object) -> bool:
    try:
        expected = float(minutes)
    except (TypeError, ValueError):
        return False
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(phút|p|giờ|tiếng|h)\b", evidence, re.I):
        amount = _number(match.group(1))
        observed = amount * 60 if match.group(2).lower() in {"giờ", "tiếng", "h"} else amount
        if abs(observed - expected) < 1e-9:
            return True
    return False


def draft_from_record_candidates(
    message: str, candidates: object, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Validate one LLM-proposed draft against verbatim user evidence.

    This deliberately accepts no model confidence score. A candidate is
    trusted only when its evidence is a contiguous span of the message and
    every core value can be checked against that span.
    """
    if not isinstance(candidates, list) or len(candidates) != 1:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return None
    record_type = candidate.get("record_type")
    fields = candidate.get("fields")
    evidence = _normalized_span(candidate.get("evidence"))
    normalized_message = _normalized_span(message)
    allowed = _LLM_FIELD_ALLOWLIST.get(str(record_type))
    if not evidence or evidence not in normalized_message or not isinstance(fields, dict) or allowed is None:
        return None
    if any(key not in allowed for key in fields):
        return None

    draft = {"record_type": record_type, **fields}
    current = now or datetime.now(UTC)
    timestamp_fields = ("measured_at", "meal_time", "activity_time", "occurred_at")
    for field in timestamp_fields:
        raw = draft.get(field)
        if raw in (None, ""):
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            comparable_now = datetime.now(parsed.tzinfo) if parsed.tzinfo else current
            if parsed > comparable_now:
                return None
        except (TypeError, ValueError):
            return None

    if record_type == "glucose":
        if not _evidence_has_number(evidence, draft.get("value")):
            return None
        unit = draft.get("unit")
        if unit not in (None, "", "mg/dL", "mmol/L"):
            return None
        if unit and _normalized_span(unit) not in evidence:
            unit = "mg/dL"
        if not unit:
            unit = "mg/dL"
        context = draft.get("meal_context")
        if context not in (None, "", *(item.value for item in MealContext)):
            return None
        # Never accept a model-inferred measurement context. In particular,
        # "hiện tại" does not imply AFTER_MEAL merely because of the clock.
        explicit_context = _meal_context(evidence)
        if context and context != explicit_context:
            context = None
        draft = {
            "record_type": record_type,
            "value": float(draft["value"]),
            "unit": unit,
            "measured_at": draft.get("measured_at"),
            "meal_context": context or None,
            "note": draft.get("note"),
            "symptoms": draft.get("symptoms"),
        }
    elif record_type == "blood_pressure":
        if not all(_evidence_has_number(evidence, draft.get(key)) for key in ("systolic", "diastolic")):
            return None
        draft = {
            "record_type": record_type,
            "systolic": float(draft["systolic"]),
            "diastolic": float(draft["diastolic"]),
            "unit": draft.get("unit") or "mmHg",
            "measured_at": draft.get("measured_at"),
            "note": draft.get("note"),
        }
    elif record_type == "meal":
        description = _normalized_span(draft.get("description"))
        if not _has_concrete_meal_description(description) or description not in evidence:
            return None
        meal_evidence = re.search(r"(?:ăn|dùng bữa)\s+(.+)", evidence, re.I)
        if meal_evidence and not _has_concrete_meal_description(
            _meal_description(meal_evidence.group(1))
        ):
            return None
        meal_type = draft.get("meal_type")
        if meal_type not in (None, "", *(item.value for item in MealType)):
            return None
        draft = {
            "record_type": record_type,
            "meal_type": meal_type or None,
            "meal_time": draft.get("meal_time"),
            "description": str(draft["description"]).strip(),
            "note": draft.get("note"),
        }
    elif record_type == "activity":
        activity_type = _normalized_span(draft.get("activity_type"))
        if not activity_type or activity_type not in evidence:
            return None
        if not _evidence_supports_duration(evidence, draft.get("duration_minutes")):
            return None
        duration = float(draft["duration_minutes"])
        if not duration.is_integer():
            return None
        draft = {
            "record_type": record_type,
            "activity_type": str(draft["activity_type"]).strip(),
            "duration_minutes": int(duration),
            "activity_time": draft.get("activity_time"),
            "note": draft.get("note"),
        }
    else:
        medication_name = _normalized_span(draft.get("medication_name"))
        if not medication_name or medication_name not in evidence:
            return None
        note_type = draft.get("note_type")
        if note_type not in (None, "", *(item.value for item in MedicationNoteType)):
            return None
        draft = {
            "record_type": record_type,
            "medication_name": str(draft["medication_name"]).strip(),
            "medication_id": draft.get("medication_id"),
            "note_type": note_type or MedicationNoteType.MISSED_DOSE.value,
            "text": draft.get("text") or evidence,
            "occurred_at": draft.get("occurred_at"),
        }
    return draft


def drafts_from_record_candidates(
    message: str, candidates: object, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Validate multiple candidates atomically; never keep a partial subset."""
    if not isinstance(candidates, list) or not candidates:
        return []
    drafts: list[dict[str, Any]] = []
    for candidate in candidates:
        draft = draft_from_record_candidates(message, [candidate], now=now)
        if draft is None:
            return []
        drafts.append(draft)
    return drafts


def requires_safety_response(draft: dict[str, Any]) -> bool:
    """Keep potentially urgent measurements on the full safety graph path."""
    if draft.get("record_type") == "glucose":
        value = float(draft.get("value") or 0)
        return value < 54 or value > 250
    if draft.get("record_type") == "blood_pressure":
        return float(draft.get("systolic") or 0) >= 180 or float(draft.get("diastolic") or 0) >= 120
    return False


def _missing_fields(draft: dict[str, Any]) -> list[str]:
    required = {
        "glucose": ("value", "unit", "measured_at", "meal_context"),
        "blood_pressure": ("systolic", "diastolic", "unit", "measured_at"),
        "meal": ("meal_type", "meal_time", "description"),
        "activity": ("activity_type", "duration_minutes", "activity_time"),
        "medication_note": ("medication_id", "note_type", "occurred_at"),
    }.get(str(draft.get("record_type")), ())
    return [field for field in required if draft.get(field) in (None, "")]


def create_pending_action(
    session: Session, *, patient_id: UUID, chat_session: ChatSession, draft: dict[str, Any]
) -> PendingRecordAction:
    missing = _missing_fields(draft)
    action = PendingRecordAction(
        patient_id=patient_id,
        chat_session_id=chat_session.id,
        record_type=str(draft["record_type"]),
        payload_json=json.dumps(draft, ensure_ascii=False),
        missing_fields_json=json.dumps(missing),
        expires_at=datetime.utcnow() + _ACTION_TTL,
    )
    session.add(action)
    session.commit()
    session.refresh(action)
    return action


def _options_for(session: Session, action: PendingRecordAction, field: str) -> list[AgentChoiceOption]:
    if field == "meal_context":
        return _MEAL_CONTEXT_OPTIONS
    if field == "meal_type":
        return _MEAL_TYPE_OPTIONS
    if field in {"measured_at", "activity_time", "meal_time"}:
        # Build stable values from the pending action's creation date.  Using
        # datetime.utcnow() here made the allow-list differ between rendering
        # and submission by a few milliseconds.
        day = action.created_at
        if day.tzinfo is None:
            day = day.replace(tzinfo=UTC)
        return [
            AgentChoiceOption(label="Bây giờ", value=day.isoformat()),
            AgentChoiceOption(label="30 phút trước", value=(day - timedelta(minutes=30)).isoformat()),
            AgentChoiceOption(label="1 giờ trước", value=(day - timedelta(hours=1)).isoformat()),
            AgentChoiceOption(label="2 giờ trước", value=(day - timedelta(hours=2)).isoformat()),
        ]
    if field == "medication_id":
        from codebase.backend.medications import service as medication_service

        return [
            AgentChoiceOption(label=f"{med.drug_name} · {med.dose}", value=str(med.id))
            for med in medication_service.list_active_medications(session, action.patient_id)
        ]
    return []


def interaction_for(session: Session, action: PendingRecordAction, *, saved_record_id: UUID | None = None) -> AgentInteraction:
    draft = json.loads(action.payload_json)
    missing = _missing_fields(draft)
    if saved_record_id is not None:
        return AgentInteraction(
            type="record_saved", pending_action_id=action.id, draft=draft, saved_record_id=saved_record_id
        )
    if missing:
        field = missing[0]
        return AgentInteraction(
            type="single_choice",
            pending_action_id=action.id,
            field=field,
            options=_options_for(session, action, field),
            draft=draft,
        )
    return AgentInteraction(type="confirm_record", pending_action_id=action.id, draft=draft)


def prompt_for(interaction: AgentInteraction) -> str:
    labels = {
        "measured_at": "Bạn đo chỉ số này vào lúc nào?",
        "meal_context": "Bạn đo đường huyết trong hoàn cảnh nào?",
        "meal_type": "Đây là bữa ăn nào?",
        "activity_time": "Bạn thực hiện hoạt động này vào lúc nào?",
        "meal_time": "Bạn dùng bữa vào lúc nào?",
        "medication_id": "Bạn đang nói tới thuốc nào?",
    }
    if interaction.type == "single_choice":
        return labels.get(interaction.field or "", "Vui lòng bổ sung thông tin còn thiếu.")
    if interaction.type == "confirm_record":
        return "Thông tin đã đủ. Vui lòng xác nhận trước khi lưu vào hồ sơ."
    return "Đã lưu thông tin vào hồ sơ của bạn."


def _get_owned_action(
    session: Session, action_id: UUID, patient_id: UUID, *, allow_completed: bool = False
) -> PendingRecordAction:
    action = session.get(PendingRecordAction, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending action not found")
    if action.patient_id != patient_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your pending action")
    if action.status == "completed" and allow_completed and action.saved_record_id is not None:
        return action
    if action.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pending action is no longer active")
    if action.expires_at < datetime.utcnow():
        action.status = "expired"
        session.add(action)
        session.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Pending action expired")
    return action


def _persist(session: Session, action: PendingRecordAction) -> UUID:
    from codebase.backend.health import service as health_service
    from codebase.backend.medications import service as medication_service

    draft = json.loads(action.payload_json)
    record_type = action.record_type
    model_by_type = {
        "glucose": GlucoseRecord,
        "blood_pressure": BloodPressureRecord,
        "meal": MealRecord,
        "activity": ActivityRecord,
        "medication_note": MedicationNote,
    }
    model = model_by_type.get(record_type)
    if model is not None:
        existing = session.exec(
            select(model).where(model.source_pending_action_id == action.id)
        ).first()
        if existing is not None:
            return existing.id
    if _missing_fields(draft):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing required fields")
    if record_type == "glucose":
        valid = GlucoseRecordCreate(patient_id=action.patient_id, **{k: v for k, v in draft.items() if k != "record_type"})
        existing = health_service.find_duplicate_glucose_record(
            session, patient_id=action.patient_id, measured_at=valid.measured_at
        )
        if existing:
            # Confirmation may be retried by the browser or a proxy. Treat an
            # exact patient+timestamp match as the already successful write.
            return existing.id
        record = health_service.create_glucose_record(
            session, **valid.model_dump(), created_by=action.patient_id,
            source_pending_action_id=action.id,
        )
    elif record_type == "blood_pressure":
        valid = BloodPressureRecordCreate(patient_id=action.patient_id, **{k: v for k, v in draft.items() if k != "record_type"})
        if valid.diastolic >= valid.systolic:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Diastolic pressure must be lower than systolic pressure",
            )
        record = health_service.create_blood_pressure_record(
            session, **valid.model_dump(), created_by=action.patient_id,
            source_pending_action_id=action.id,
        )
    elif record_type == "meal":
        valid = MealRecordCreate(patient_id=action.patient_id, **{k: v for k, v in draft.items() if k != "record_type"})
        record = health_service.create_meal_record(
            session, **valid.model_dump(), created_by=action.patient_id,
            source_pending_action_id=action.id,
        )
    elif record_type == "activity":
        valid = ActivityRecordCreate(patient_id=action.patient_id, **{k: v for k, v in draft.items() if k != "record_type"})
        record = health_service.create_activity_record(
            session, **valid.model_dump(), created_by=action.patient_id,
            source_pending_action_id=action.id,
        )
    elif record_type == "medication_note":
        medication = medication_service.get_medication_or_404(session, UUID(str(draft["medication_id"])))
        if medication.patient_id != action.patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your medication")
        record = medication_service.create_medication_note(
            session,
            medication=medication,
            note_type=MedicationNoteType(draft["note_type"]),
            text=draft.get("text"),
            occurred_at=datetime.fromisoformat(draft["occurred_at"]),
            created_by=action.patient_id,
            source_pending_action_id=action.id,
        )
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported record type")
    return record.id


def apply_action(
    session: Session, *, action_id: UUID, patient_id: UUID, request: AgentRecordActionRequest
) -> tuple[str, AgentInteraction | None]:
    action = _get_owned_action(
        session, action_id, patient_id, allow_completed=request.action == "confirm"
    )
    if request.action == "confirm" and action.status == "completed":
        interaction = interaction_for(session, action, saved_record_id=action.saved_record_id)
        return prompt_for(interaction), interaction
    if request.action == "cancel":
        action.status = "cancelled"
        action.completed_at = datetime.utcnow()
        session.add(action)
        session.commit()
        return "Đã hủy, dữ liệu chưa được lưu.", None
    if request.action == "provide_field":
        draft = json.loads(action.payload_json)
        missing = _missing_fields(draft)
        if not request.field or request.field not in missing:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Field is not currently missing")
        if request.field in {"measured_at", "activity_time", "meal_time"}:
            try:
                # Datetime choices are serialized through JSON and may be
                # normalized by the browser. Validate the semantic ISO value
                # instead of comparing its string byte-for-byte with a newly
                # generated option list.
                parsed_value = datetime.fromisoformat(str(request.value).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid datetime value",
                ) from exc
            comparable_value = (
                parsed_value.replace(tzinfo=UTC)
                if parsed_value.tzinfo is None
                else parsed_value.astimezone(UTC)
            )
            action_time = action.created_at
            if action_time.tzinfo is None:
                action_time = action_time.replace(tzinfo=UTC)
            else:
                action_time = action_time.astimezone(UTC)
            # The web UI intentionally includes a datetime-local input in
            # addition to the quick options. Accept that explicit user choice,
            # but keep a bounded server-side window so a custom client cannot
            # submit a future or implausibly old health event.
            if not (
                action_time - _MAX_CUSTOM_TIME_AGE
                <= comparable_value
                <= action_time + _CUSTOM_TIME_CLOCK_SKEW
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Thời gian phải nằm trong 10 năm gần đây và không được ở tương lai",
                )
            value = comparable_value.isoformat()
        else:
            allowed = {option.value for option in _options_for(session, action, request.field)}
            if allowed and str(request.value) not in allowed:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid option value")
            value = request.value
        draft[request.field] = value
        action.payload_json = json.dumps(draft, ensure_ascii=False)
        action.missing_fields_json = json.dumps(_missing_fields(draft))
        session.add(action)
        session.commit()
        session.refresh(action)
        # Filling a field never persists by itself.  Every record type uses
        # the same explicit review-and-confirm step once the draft is complete.
        interaction = interaction_for(session, action)
        return prompt_for(interaction), interaction
    if request.action == "confirm":
        try:
            saved_id = _persist(session, action)
        except IntegrityError:
            # A concurrent confirmation may win the unique
            # source_pending_action_id insert. Recover that exact record.
            session.rollback()
            saved_id = _persist(session, action)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(include_url=False),
            ) from exc
        action.status = "completed"
        action.saved_record_id = saved_id
        action.completed_at = datetime.utcnow()
        session.add(action)
        session.commit()
        interaction = interaction_for(session, action, saved_record_id=saved_id)
        return prompt_for(interaction), interaction
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported action")

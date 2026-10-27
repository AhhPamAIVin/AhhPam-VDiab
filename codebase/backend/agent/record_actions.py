"""Patient-only structured record drafts extracted from chat messages.

The parser deliberately handles only high-confidence, already-occurred statements.
It never writes. Persistence happens only through ``apply_action`` after an explicit
structured confirmation, using the existing health/medication services.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
_GLUCOSE_UNIT_OPTIONS = [
    AgentChoiceOption(label="mg/dL", value="mg/dL"),
    AgentChoiceOption(label="mmol/L", value="mmol/L"),
]
_MEAL_TYPE_OPTIONS = [
    AgentChoiceOption(label="Bữa sáng", value=MealType.BREAKFAST.value),
    AgentChoiceOption(label="Bữa trưa", value=MealType.LUNCH.value),
    AgentChoiceOption(label="Bữa tối", value=MealType.DINNER.value),
    AgentChoiceOption(label="Bữa phụ", value=MealType.SNACK.value),
]


def _number(value: str) -> float:
    return float(value.replace(",", "."))


# "7 phẩy 2" -> "7.2": how a decimal is said (and typed by voice input).
_SPOKEN_DECIMAL_RE = re.compile(r"(\d+)\s*ph(?:ẩ|a)y\s*(\d+)", re.I)


def _with_spoken_decimals(text: str) -> str:
    return _SPOKEN_DECIMAL_RE.sub(r"\1.\2", text)


# A glucose value with no stated unit is read by plausibility, never by the
# web form's default: 7 mg/dL is not a reading a living patient reports, 7
# mmol/L is. 20-40 is genuinely ambiguous (30 mg/dL is a severe low, 30
# mmol/L a severe high) and is asked in the confirmation card instead.
_MMOL_ONLY_BELOW = 20.0
_MG_DL_ONLY_FROM = 40.0
MG_DL_PER_MMOL_L = 18.0


def _stated_glucose_unit(text: str) -> str | None:
    if re.search(r"\bmmol(?:\s*/\s*l)?\b", text, re.I):
        return "mmol/L"
    if re.search(r"\bmg\s*/\s*dl\b", text, re.I):
        return "mg/dL"
    return None


def glucose_unit_for(value: float, text: str) -> str | None:
    """The unit the patient stated, else the only plausible one, else None."""
    stated = _stated_glucose_unit(text)
    if stated:
        return stated
    if value < _MMOL_ONLY_BELOW:
        return "mmol/L"
    if value >= _MG_DL_ONLY_FROM:
        return "mg/dL"
    return None


def _glucose_unit(text: str) -> str:
    """Return the explicitly stated unit, defaulting only when none exists."""
    return _stated_glucose_unit(text) or "mg/dL"


def _without_accents(text: str) -> str:
    """'sau ăn' and 'sau an' alike — patients often type without diacritics."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


_MEAL_CONTEXT_CUES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("luc doi", "nhin doi", "khi doi", "bung doi", "chua an gi", "chua an sang"), MealContext.FASTING.value),
    (("truoc an", "truoc khi an", "truoc bua"), MealContext.BEFORE_MEAL.value),
    (("sau an", "sau khi an", "sau bua"), MealContext.AFTER_MEAL.value),
    (("truoc khi ngu", "truoc khi di ngu"), MealContext.BEFORE_SLEEP.value),
)


def _meal_context(text: str) -> str | None:
    folded = f" {' '.join(re.findall(r'[a-z0-9]+', _without_accents(text)))} "
    for cues, context in _MEAL_CONTEXT_CUES:
        if any(f" {cue} " in folded for cue in cues):
            return context
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
            "unit": glucose_unit_for(_number(glucose.group(1)), text),
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


normalized_span = _normalized_span


def evidence_supports_value(evidence: str, value: object) -> bool:
    return _evidence_has_number(_normalized_span(evidence), value)


def open_medication_reminders(session: Session, patient_id: UUID) -> list:
    """Today's (patient's local day) medication reminders still waiting for an answer."""
    from zoneinfo import ZoneInfo

    from codebase.backend.config import get_settings
    from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType

    today = datetime.now(ZoneInfo(get_settings().reminder_timezone)).date()
    return list(
        session.exec(
            select(Notification)
            .where(
                Notification.patient_id == patient_id,
                Notification.notification_type == NotificationType.MEDICATION_REMINDER,
                Notification.slot_date == today,
                Notification.status.in_([NotificationStatus.PENDING, NotificationStatus.SNOOZED]),
            )
            .order_by(Notification.scheduled_for)
        ).all()
    )


def _evidence_has_number(evidence: str, value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    for token in re.findall(r"\d+(?:[.,]\d+)?", _with_spoken_decimals(evidence)):
        if abs(_number(token) - number) < 1e-9:
            return True
    return False


def _activity_named_in(text: str) -> str | None:
    """A known activity (Vietnamese name) written in `text`, accented or not."""
    folded = f" {' '.join(_without_accents(text).split())} "
    for name in _ACTIVITY_NAME.split("|"):
        if f" {_without_accents(name)} " in folded:
            return name
    return None


# A plan or a hypothetical is not a record ("tối nay tôi định ăn phở", "nếu
# đường huyết 250…"). "ổn định" (stable) is not "định" (intend). Nor is
# someone else's reading ("chồng tôi vừa đi bộ", "mẹ tôi đo 200") — matched
# accented, plus the unaccented forms that cannot be another word.
_AGENT_NOT_A_RECORD_RE = re.compile(
    r"\b(?:nếu|giả sử|ví dụ|dự định|sắp|sẽ|có nên)\b|(?<!ổn )\bđịnh\b"
    r"|\b(?:chồng|vợ|mẹ|bố|ba|má|con|anh|chị|ông|bà|bạn|cháu|cô|chú|dì|cậu)\s+(?:tôi|mình|tớ)\b",
    re.I,
)
_OTHER_PERSON_FOLDED_RE = re.compile(r"\b(?:chong|vo|me|chi|ong|chau|chu|cau)\s+(?:toi|minh)\b")


def _contains_folded(evidence: str, value: str) -> bool:
    """`value` appears in the evidence, ignoring diacritics: the model may
    write "cơm tấm" for a patient's "com tam" — same words, still verbatim."""
    return " ".join(_without_accents(value).split()) in " ".join(_without_accents(evidence).split())


def _evidence_supports_duration(evidence: str, minutes: object) -> bool:
    try:
        expected = float(minutes)
    except (TypeError, ValueError):
        return False
    # Accent-folded: "30 phut", "1 tieng" are how many patients type it.
    for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(phut|p|gio|tieng|h)\b", _without_accents(evidence)):
        amount = _number(match.group(1))
        observed = amount * 60 if match.group(2) in {"gio", "tieng", "h"} else amount
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
    # Edge punctuation is not part of the quote: "…hơi mệt." quotes "…hơi mệt, có sao không?".
    evidence = _normalized_span(candidate.get("evidence")).strip(" .,;:!?")
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
        if draft.get("unit") not in (None, "", "mg/dL", "mmol/L"):
            return None
        # Only a unit written in the evidence counts; otherwise plausibility.
        unit = glucose_unit_for(float(draft["value"]), evidence)
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
        if not _has_concrete_meal_description(description) or not _contains_folded(evidence, description):
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
        if not activity_type or not _contains_folded(evidence, activity_type):
            # The model sometimes translates ("walking"); the name must come
            # from the patient's words, so take a known activity named there.
            activity_type = _activity_named_in(evidence)
            if activity_type is None:
                return None
            draft["activity_type"] = activity_type
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
        if not medication_name or not _contains_folded(evidence, medication_name):
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


# --- agent proposals (Epic 8: the patient agent's draft_records tool) -------
#
# The LLM reads the message (spelling, abbreviations, "7 phẩy 2", several
# records in one sentence, hypotheticals, someone else's reading); code then
# checks every value against the verbatim evidence exactly as for
# record_candidates above. The one thing the model resolves that the evidence
# check can't, the time, is accepted only when its own quoted time phrase
# supports it: a stated clock time, "N phút/giờ trước", or "vừa/bây giờ".
# Anything vaguer ("sáng nay") is left empty and asked in the card.

_TIME_FIELD = {
    "glucose": "measured_at",
    "blood_pressure": "measured_at",
    "meal": "meal_time",
    "activity": "activity_time",
    "medication_note": "occurred_at",
}
# Matched on accent-folded text ("30 phut truoc", "9h toi qua"), except the
# "now" cues: folded, "lúc này" (now) and "lúc nãy" (a while ago) collide, as
# do "mới" (just) and "mỗi" (each) — so those match accented, plus the few
# unaccented spellings that are unambiguous.
_NOW_CUE_RE = re.compile(r"\b(vừa|mới|bây giờ|bây h|hiện tại|ngay lúc này|lúc này)\b", re.I)
_NOW_CUE_FOLDED_RE = re.compile(r"\b(vua|bay gio|bay h|hien tai)\b")
_RELATIVE_TIME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(phut|p|gio|tieng|h)\s*(?:truoc|nay)\b")
_CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3])\s*(?:gio|h|g|:)\s*(ruoi|[0-5]?\d)?")
_EXPLICIT_DATE_RE = re.compile(r"\b(?:ngay\s*)?([0-3]?\d)\s*/\s*([01]?\d)\b")
_NOW_TOLERANCE = timedelta(minutes=30)
_RELATIVE_TOLERANCE = timedelta(minutes=10)
_MAX_AGENT_TIME_AGE = timedelta(days=7)


def _verified_agent_time(
    message: str, evidence: str, proposed: object, time_evidence: object, now: datetime
) -> datetime | None:
    """The model's resolved time (UTC), or None when its quoted time phrase
    does not support it. `now` is an aware UTC instant."""
    from zoneinfo import ZoneInfo

    from codebase.backend.config import get_settings

    if proposed in (None, "") or not time_evidence:
        return None
    phrase = _normalized_span(time_evidence)
    if not phrase or phrase not in _normalized_span(message):
        return None
    try:
        parsed = datetime.fromisoformat(str(proposed).replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = ZoneInfo(get_settings().reminder_timezone)
    local = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    instant = local.astimezone(UTC)
    if instant > now + _CUSTOM_TIME_CLOCK_SKEW or instant < now - _MAX_AGENT_TIME_AGE:
        return None

    folded = _without_accents(phrase)
    stated = _stated_relative_or_now(phrase, now)
    if stated is not None:
        tolerance = _RELATIVE_TOLERANCE if _RELATIVE_TIME_RE.search(folded) else _NOW_TOLERANCE
        return instant if abs(instant - stated) <= tolerance else None

    clock = _CLOCK_RE.search(folded)
    if clock:
        hour = int(clock.group(1))
        minute_text = clock.group(2) or "0"
        minute = 30 if minute_text == "ruoi" else int(minute_text)
        if local.hour not in {hour, (hour + 12) % 24} or local.minute != minute:
            return None
        today = now.astimezone(tz).date()
        context = f"{folded} {_without_accents(evidence)}"
        explicit_date = _EXPLICIT_DATE_RE.search(context)
        if explicit_date:
            day, month = int(explicit_date.group(1)), int(explicit_date.group(2))
            return instant if (local.day, local.month) == (day, month) else None
        if "hom kia" in context:
            expected_day = today - timedelta(days=2)
        elif any(cue in context for cue in ("hom qua", "toi qua", "dem qua", "sang qua", "chieu qua", "trua qua")):
            expected_day = today - timedelta(days=1)
        else:
            expected_day = today
        return instant if local.date() == expected_day else None
    return None


_PM_CUES = ("chieu", "toi")
_YESTERDAY_CUES = ("hom qua", "toi qua", "dem qua", "sang qua", "chieu qua", "trua qua")


def _stated_clock(text: str, now: datetime) -> datetime | None:
    """A clock time read by code ("7h sáng hôm qua", "9 giờ tối", "21h30"),
    only when morning/evening is unambiguous: "lúc 9h" alone is left empty."""
    from zoneinfo import ZoneInfo

    from codebase.backend.config import get_settings

    folded = " ".join(_without_accents(text).split())
    clock = _CLOCK_RE.search(folded)
    if not clock:
        return None
    hour = int(clock.group(1))
    minute = 30 if clock.group(2) == "ruoi" else int(clock.group(2) or 0)
    after = folded[clock.end() : clock.end() + 12]
    if hour < 12 and any(cue in after or f"{cue} " in folded for cue in _PM_CUES):
        hour += 12
    elif hour <= 12 and "sang" not in folded and "trua" not in folded and hour != 12:
        return None  # morning or evening? ask
    tz = ZoneInfo(get_settings().reminder_timezone)
    today = now.astimezone(tz).date()
    day = today - timedelta(days=2 if "hom kia" in folded else 1 if any(c in folded for c in _YESTERDAY_CUES) else 0)
    instant = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(UTC)
    return instant if instant <= now + _CUSTOM_TIME_CLOCK_SKEW else None


_MEAL_TYPE_FOLDED_CUES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bua sang", "an sang", "sang nay", "sang qua"), MealType.BREAKFAST.value),
    (("bua trua", "an trua", "trua nay", "trua qua"), MealType.LUNCH.value),
    (("bua toi", "an toi", "toi nay", "toi qua"), MealType.DINNER.value),
    (("bua phu", "an nhe", "an vat"), MealType.SNACK.value),
)


def _stated_meal_type(text: str) -> str | None:
    folded = f" {' '.join(_without_accents(text).split())} "
    found = {value for cues, value in _MEAL_TYPE_FOLDED_CUES if any(f" {cue} " in folded for cue in cues)}
    return found.pop() if len(found) == 1 else None


def _stated_relative_or_now(text: str, now: datetime) -> datetime | None:
    """The time "N phút/giờ trước" or "vừa/bây giờ" states, read by code —
    no clock arithmetic for the model to get wrong, so it also fills a time
    the model left empty."""
    folded = _without_accents(text)
    relative = _RELATIVE_TIME_RE.search(folded)
    if relative:
        amount = _number(relative.group(1))
        return now - (timedelta(minutes=amount) if relative.group(2) in {"phut", "p"} else timedelta(hours=amount))
    if _NOW_CUE_RE.search(text) or _NOW_CUE_FOLDED_RE.search(folded):
        return now
    return None


def draft_from_agent_proposal(
    message: str, proposal: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Validate one draft_records proposal: the same evidence checks as an
    LLM record_candidate, plus the time check above. None = rejected."""
    record_type = str(proposal.get("record_type"))
    allowed = _LLM_FIELD_ALLOWLIST.get(record_type)
    if allowed is None:
        return None
    current = now or datetime.now(UTC)
    time_field = _TIME_FIELD[record_type]
    fields = {
        key: value
        for key, value in proposal.items()
        if key in allowed and key != time_field and value not in (None, "")
    }
    evidence = str(proposal.get("evidence") or "")
    if _AGENT_NOT_A_RECORD_RE.search(evidence) or _OTHER_PERSON_FOLDED_RE.search(_without_accents(evidence)):
        return None
    draft = draft_from_record_candidates(
        message, [{"record_type": record_type, "fields": fields, "evidence": evidence}], now=current
    )
    if draft is None:
        return None
    if record_type == "glucose":
        # Read from the patient's own words, accented or not — never left to
        # the model, which tends to drop it on unaccented text ("dg sau an").
        draft["meal_context"] = _meal_context(evidence)
    # The record's own quote stands in for a missing time quote: it is
    # verbatim too, and the time check reads the clock/relative phrase in it.
    time_quote = proposal.get("time_evidence") or evidence
    verified = _verified_agent_time(message, evidence, proposal.get("time"), time_quote, current)
    if verified is None and proposal.get("time") in (None, ""):
        time_text = f"{proposal.get('time_evidence') or ''} {evidence}"
        verified = _stated_relative_or_now(time_text, current) or _stated_clock(time_text, current)
    if record_type == "meal" and not draft.get("meal_type"):
        draft["meal_type"] = _stated_meal_type(evidence)
    if verified is not None:
        draft[time_field] = verified.isoformat()
    elif record_type == "medication_note":
        draft[time_field] = current.isoformat()  # same as the parser: a missed dose noted now
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


GLUCOSE_DANGER_LOW_MG_DL = 54.0
GLUCOSE_DANGER_HIGH_MG_DL = 250.0


def requires_safety_response(draft: dict[str, Any]) -> bool:
    """A reported value outside the safe range: the chat answers it with the
    fixed referral (agent/router.py), never with model-written text."""
    if draft.get("record_type") == "glucose":
        value = float(draft.get("value") or 0)
        if draft.get("unit") == "mmol/L":
            value *= MG_DL_PER_MMOL_L
        return value < GLUCOSE_DANGER_LOW_MG_DL or value > GLUCOSE_DANGER_HIGH_MG_DL
    if draft.get("record_type") == "blood_pressure":
        return float(draft.get("systolic") or 0) >= 180 or float(draft.get("diastolic") or 0) >= 120
    return False


def urgent_value_response(draft: dict[str, Any]) -> str:
    """The fixed referral for a value requires_safety_response flagged: what
    was reported, where to turn — no advice on what to do about the value."""
    if draft.get("record_type") == "glucose":
        value = f"Chỉ số đường huyết {float(draft['value']):g} {draft.get('unit') or ''}".rstrip()
    else:
        value = f"Chỉ số huyết áp {float(draft['systolic']):g}/{float(draft['diastolic']):g} mmHg"
    return (
        f"{value} bạn vừa báo nằm ngoài ngưỡng an toàn. Nếu bạn đang thấy không khỏe, hãy gọi ngay 115 "
        "hoặc đến cơ sở y tế gần nhất. Nếu không, hãy báo cho bác sĩ điều trị sớm nhất có thể. "
        "Bạn có thể xác nhận để lưu chỉ số này vào hồ sơ ở thẻ bên dưới."
    )


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
    if field == "unit":
        return _GLUCOSE_UNIT_OPTIONS
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
        "unit": "Chỉ số này tính theo đơn vị nào?",
        "meal_context": "Bạn đo đường huyết trong hoàn cảnh nào?",
        "meal_type": "Đây là bữa ăn nào?",
        "activity_time": "Bạn thực hiện hoạt động này vào lúc nào?",
        "meal_time": "Bạn dùng bữa vào lúc nào?",
        "medication_id": "Bạn đang nói tới thuốc nào?",
    }
    if interaction.type == "single_choice":
        return labels.get(interaction.field or "", "Vui lòng bổ sung thông tin còn thiếu.")
    record_type = interaction.draft.get("record_type")
    if interaction.type == "confirm_record":
        if record_type == "delete_record":
            return "Vui lòng xác nhận xóa bản ghi này khỏi hồ sơ."
        return "Thông tin đã đủ. Vui lòng xác nhận trước khi lưu vào hồ sơ."
    if record_type == "delete_record":
        return "Đã xóa bản ghi khỏi hồ sơ của bạn."
    if record_type == "medication_taken":
        return "Đã ghi nhận bạn uống thuốc theo lời nhắc hôm nay."
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


def _complete_medication_reminders(session: Session, patient_id: UUID, draft: dict[str, Any]) -> UUID:
    """Mark the reminders the card listed as taken — only the patient's own,
    and only ones still open (one answered meanwhile is left alone)."""
    from codebase.backend.notifications.models import Notification, NotificationStatus
    from codebase.backend.notifications.service import update_notification_status

    completed: list[UUID] = []
    for raw_id in draft.get("notification_ids") or []:
        notification = session.get(Notification, UUID(str(raw_id)))
        if notification is None or notification.patient_id != patient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your reminder")
        if notification.status in (NotificationStatus.PENDING, NotificationStatus.SNOOZED):
            update_notification_status(
                session, notification=notification, new_status=NotificationStatus.COMPLETED, snooze_minutes=None
            )
        completed.append(notification.id)
    if not completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Không còn lời nhắc nào để xác nhận.")
    return completed[0]


def _persist(session: Session, action: PendingRecordAction) -> UUID:
    from codebase.backend.health import service as health_service
    from codebase.backend.medications import service as medication_service

    draft = json.loads(action.payload_json)
    record_type = action.record_type
    if record_type == "delete_record":
        target_id = UUID(str(draft["target_id"]))
        health_service.delete_own_record(
            session, patient_id=action.patient_id, record_type=str(draft["target_type"]), record_id=target_id
        )
        return target_id
    if record_type == "medication_taken":
        return _complete_medication_reminders(session, action.patient_id, draft)
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

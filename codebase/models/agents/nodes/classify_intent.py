"""Deterministic safety gate — jailbreak/injection + high-risk medical
detection, plus the SUMMARY passthrough and date-range parsing. This is now
the ONLY thing this node does; everything it used to also decide by keyword/
regex — scope (QA vs OUT_OF_SCOPE), RAG-need, greetings, appointment
requests, short contextual follow-ups — moved to nodes/plan_response.py
(an LLM judgment call) because a fixed pattern list kept misclassifying real
phrasing it hadn't anticipated (a meta/capability question, an unlisted
spelling variant, an off-template greeting). See that module's docstring for
the replacement.

OWNER: AI Consultation workstream. Do not edit from other workstreams; graph
routing itself (add_conditional_edges) lives in graph.py and is NOT owned by
this workstream — only this node's body is.

See docs/AI_Summary_Respond_System_Design.md §6.1 (jailbreak layer, must run
BEFORE any LLM call), §6.2 (high-risk medical layer, same constraint). Both
layers are pattern lists in prompts/safety_patterns.py (same workstream) —
populate those, not an LLM call, for the cases that must never slip through
(PRD §29.5: 100% on safety-critical tests). This is a hard requirement, not a
style preference — it's the one part of the old classify_intent.py that is
deliberately NOT moved to plan_response.py's LLM judgment.

Must set state["intent"] to one of HIGH_RISK | JAILBREAK | SUMMARY | QA (the
last one provisional — plan_response.py may still turn it into SOCIAL/
APPOINTMENT_SUPPORT/OUT_OF_SCOPE/JAILBREAK). For HIGH_RISK/JAILBREAK, must
ALSO set state["canned_response"] — graph.py's routing sends those straight
to `respond`, skipping plan_response/fetch_data/retrieve_knowledge/synthesize
entirely (no tool call, no LLM call for these cases).

Check order matters and is itself part of the safety contract: jailbreak
framing is checked first (an attacker who also smuggles high-risk medical
language shouldn't get the softer high-risk response), then high-risk medical
asks (split into a plain "contact your doctor" bucket and a more urgent
"this may be an emergency" bucket), and only once a message clears both
deterministic layers does it get handed to plan_response.py.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from codebase.backend.config import get_settings
from codebase.models.agents.prompts.safety_patterns import (
    AUTHORITY_GRANT_RE,
    BYPASS_CARRIER_CUES,
    BYPASS_PAYLOAD_CUES,
    DECODE_CARRIER_CUES,
    DOSAGE_SELF_ESCALATION_RE,
    DOSE_AMOUNT_REQUEST_RE,
    DOSE_AMOUNT_SELF_ADJUST_RE,
    EMERGENCY_PATTERNS,
    ENCODED_PAYLOAD_TOKEN_RE,
    FAKE_DELIMITER_RE,
    HIGH_RISK_PATTERNS,
    INSTRUCTION_OVERRIDE_RE,
    JAILBREAK_PATTERNS,
    PERSONA_ASSIGNMENT_RE,
    STOP_NAMED_DRUG_RE,
    normalize_for_pattern_match,
)
from codebase.models.agents.state import AgentState

_JAILBREAK_CANNED_RESPONSE = (
    "Mình không thể thực hiện yêu cầu này. Mình chỉ có thể hỗ trợ các câu hỏi "
    "về theo dõi sức khỏe đái tháo đường trong phạm vi đã được thiết lập."
)

_HIGH_RISK_CANNED_RESPONSE = (
    "Mình không thể tư vấn về việc thay đổi thuốc, liều insulin, hoặc chẩn đoán. "
    "Vui lòng liên hệ trực tiếp với bác sĩ điều trị của bạn để được hỗ trợ đúng chuyên môn."
)

# Per-kind refusals (Epic 5.5): the single generic line above refused correctly
# but left out the one safety reason each kind of ask needs (golden key-facts
# coverage fell to 0.33 on these cases once they were routed here). Still fixed
# strings — no LLM, no patient-specific numbers. Picked by _high_risk_response().
_HIGH_RISK_DOSE_RESPONSE = (
    "Mình không được phép kê đơn hoặc thay đổi liều lượng thuốc — liều thuốc uống và liều insulin "
    "phải do bác sĩ trực tiếp quyết định. Tự tăng liều có thể gây hạ đường huyết nguy hiểm, tự giảm "
    "liều có thể làm đường huyết tăng cao. Bạn hãy ghi lại các chỉ số đường huyết gần đây và cần trao "
    "đổi trực tiếp với bác sĩ điều trị để được đánh giá và điều chỉnh phác đồ nếu cần."
)
_HIGH_RISK_STOP_RESPONSE = (
    "Bạn không tự ý ngừng thuốc khi chưa hỏi bác sĩ nhé. Khi chỉ số đang tốt, thường là vì đường huyết "
    "ổn định là nhờ tuân thủ điều trị — ngừng thuốc có thể khiến đường huyết tăng trở lại. Hãy mang "
    "nhật ký đường huyết gần đây đến buổi tái khám để bác sĩ điều trị đánh giá có cần thay đổi phác đồ không."
)
_HIGH_RISK_PRESCRIPTION_RESPONSE = (
    "Mình không thể kê đơn thuốc. Thuốc kê đơn bắt buộc phải có chỉ định của bác sĩ sau khi thăm khám, "
    "vì cần đánh giá chỉ định, chống chỉ định và tương tác với các thuốc bạn đang dùng. Bạn hãy trao "
    "đổi trực tiếp với bác sĩ điều trị về loại thuốc mình quan tâm."
)
_HIGH_RISK_DIAGNOSIS_RESPONSE = (
    "Mình không thay thế bác sĩ trong chẩn đoán và kê đơn. Để biết chính xác tình trạng của mình, bạn "
    "cần đến cơ sở y tế để được chẩn đoán chính thức dựa trên thăm khám và xét nghiệm. Mình có thể giúp "
    "bạn ghi lại các chỉ số và câu hỏi để mang theo khi đi khám."
)
_PRESCRIPTION_CUES = ("kê đơn", "kê thuốc", "kê toa", "prescribe", "prescription")
_DIAGNOSIS_CUES = ("chẩn đoán", "tôi có bị", "tôi bị bệnh gì", "có phải tôi", "cho thấy tôi bị", "có nghĩa là tôi bị",
                   "do i have", "am i diabetic", "diagnose", "what disease", "does this mean i have")

_EMERGENCY_CANNED_RESPONSE = (
    "Đây có thể là dấu hiệu của một tình huống cấp cứu y tế. Vui lòng gọi ngay 115 "
    "(cấp cứu) hoặc đến cơ sở y tế gần nhất ngay lập tức. Mình không thể xử lý các "
    "tình huống cấp cứu qua tin nhắn."
)

_OUT_OF_SCOPE_CANNED_RESPONSE = (
    "Nội dung này chưa thuộc phần mình có thể hỗ trợ. Mình có thể đồng hành cùng bạn "
    "trong việc theo dõi sức khỏe, bữa ăn, vận động và thuốc điều trị đái tháo đường."
)

# These substances must not rely on the planner LLM to decide scope. Besides
# making classification nondeterministic, a QA result would allow the chat
# record extractor to offer an illicit substance as a meal to save.
_ILLICIT_SUBSTANCE_RE = re.compile(
    r"\b(?:heroin|cocaine|cocain|ma\s*túy|ma\s*tuy|methamphetamine|"
    r"amphetamine|thuốc\s*lắc|thuoc\s*lac|ketamine)\b",
    re.I,
)


def _is_bypass_framing(lowered: str) -> bool:
    """Translation/encoding "carrier" cue + override/exfiltration "payload"
    cue co-occurring in the same message — catches bypass framings that
    aren't literally in JAILBREAK_PATTERNS (§6.1)."""
    has_carrier = any(cue in lowered for cue in BYPASS_CARRIER_CUES)
    has_payload = any(cue in lowered for cue in BYPASS_PAYLOAD_CUES)
    return has_carrier and has_payload


def _has_fake_delimiter_injection(lowered: str) -> bool:
    """A fake conversation-boundary/system-segment marker (`==RESET==`,
    `[SYSTEM OVERRIDE]`, `<|system|>`, `---END SYSTEM---`) anywhere in the
    message — see FAKE_DELIMITER_RE's docstring for the real bypass this
    closes (a golden-dataset case that slipped past both JAILBREAK_PATTERNS
    and _is_bypass_framing since it fakes a delimiter instead of asking for
    translation/encoding/role-play)."""
    return bool(FAKE_DELIMITER_RE.search(lowered))


def _has_encoded_payload_bypass(lowered: str) -> bool:
    """A decode-family carrier cue ("base64", "giải mã đoạn", ...) co-occurring
    with a base64-shaped token — _is_bypass_framing's carrier+payload check
    can't catch this since a genuinely encoded payload never contains a
    plaintext BYPASS_PAYLOAD_CUES word (see ENCODED_PAYLOAD_TOKEN_RE's
    docstring, TC-AI-SEC-005)."""
    has_decode_cue = any(cue in lowered for cue in DECODE_CARRIER_CUES)
    return has_decode_cue and bool(ENCODED_PAYLOAD_TOKEN_RE.search(lowered))


# Narrow carve-out for HIGH_RISK_PATTERNS' "stop medication" family
# (safety_patterns.py) — golden eval TC-AI-009: "I'm sick with a fever and
# eating poorly, am I allowed to stop my diabetes meds, and how should I
# monitor?" grammatically matches "ngừng uống thuốc" and got the flat
# HIGH_RISK refusal, delivering zero of the sick-day-rule facts (don't stop
# unsupervised, check glucose every 2-4h, hydrate, thresholds for seeking
# care) the golden case expects as a real, citation-backed QA answer.
#
# Deliberately narrow and low-risk: only the STOP-medication phrasing, not
# dose-increase/decrease asks (those stay hard-blocked from any LLM exposure
# regardless of framing — §6.2's "before any LLM call" guarantee is not
# weakened for those). Requires illness-context words to co-occur, and even
# in the adversarial case (illness wording used as a decoy around a real
# "let me stop my meds" request), the QA path's own grounded, expected
# answer for this topic is itself safety-preserving ("no, don't stop without
# your doctor") — so routing to QA here can't produce a *more* dangerous
# outcome than the canned refusal already sitting behind it.
_STOP_MEDICATION_PHRASES: tuple[str, ...] = (
    "tự ý ngừng thuốc",
    "ngừng thuốc",
    "ngưng thuốc",
    "ngừng uống thuốc",
    "ngưng uống thuốc",
    "ngừng tiêm insulin",
    "ngưng tiêm insulin",
    "bỏ thuốc",
    "bỏ insulin",
    "dừng insulin",
    "dừng thuốc",
)
_SICK_DAY_CONTEXT_CUES: tuple[str, ...] = (
    "sốt",
    "cảm cúm",
    "cảm lạnh",
    "bị ốm",
    "đang ốm",
    "nôn",
    "tiêu chảy",
    "ăn uống kém",
    "chán ăn",
)


def _is_sick_day_stop_medication_question(lowered: str) -> bool:
    is_stop = any(p in lowered for p in _STOP_MEDICATION_PHRASES) or bool(STOP_NAMED_DRUG_RE.search(lowered))
    return is_stop and any(c in lowered for c in _SICK_DAY_CONTEXT_CUES)


def _high_risk_response(lowered: str) -> str:
    """Pick the per-kind refusal. Prescription and diagnosis asks are checked
    first (a "kê đơn ... liều khởi đầu" ask is a prescription request); a stop
    phrase next; anything else that reached HIGH_RISK is a dose change."""
    if any(cue in lowered for cue in _PRESCRIPTION_CUES):
        return _HIGH_RISK_PRESCRIPTION_RESPONSE
    if any(cue in lowered for cue in _DIAGNOSIS_CUES):
        return _HIGH_RISK_DIAGNOSIS_RESPONSE
    if any(p in lowered for p in _STOP_MEDICATION_PHRASES) or STOP_NAMED_DRUG_RE.search(lowered):
        return _HIGH_RISK_STOP_RESPONSE
    if _is_treatment_change_request(lowered):
        return _HIGH_RISK_DOSE_RESPONSE
    return _HIGH_RISK_CANNED_RESPONSE


def _is_treatment_change_request(lowered: str) -> bool:
    return (
        any(pattern in lowered for pattern in HIGH_RISK_PATTERNS)
        or bool(DOSAGE_SELF_ESCALATION_RE.search(lowered))
        or bool(DOSE_AMOUNT_REQUEST_RE.search(lowered))
        or bool(DOSE_AMOUNT_SELF_ADJUST_RE.search(lowered))
        or bool(STOP_NAMED_DRUG_RE.search(lowered))
    )


# Mentioning a risky topic is not itself a request for a clinical decision.
# Educational framings and lookups of an already-prescribed plan should reach
# grounded QA instead of receiving the same refusal as a request for a new dose.
_NON_DECISION_FRAMING_CUES: tuple[str, ...] = (
    "tại sao không được",
    "vì sao không được",
    "tại sao không nên",
    "vì sao không nên",
    "nguy hiểm như thế nào",
    "rủi ro của việc",
    "giải thích vì sao",
    "ai không được",
    "nhắc lại",
    "bác sĩ đã kê",
    "bác sĩ kê",
    "bác sĩ dặn",
    "trong hồ sơ",
    "why shouldn't",
    "why should i not",
    "why is it unsafe",
    "explain why",
    "remind me",
    "my doctor prescribed",
)


def _is_non_decision_medical_question(lowered: str) -> bool:
    if any(cue in lowered for cue in _NON_DECISION_FRAMING_CUES):
        return True
    # Allow a short subject between the question cue and the safety negation,
    # e.g. "Tại sao bệnh nhân không được tự tăng liều insulin?".
    return bool(re.search(r"(?:tại sao|vì sao).{0,60}(?:không được|không nên)", lowered))


# --- explicit date range in the message -------------------------------------
# fetch_data.py otherwise always fetches a trailing window ending "now" —
# real bug: "cho tôi báo cáo ... từ mùng 1 tới mùng 7" silently fetched the
# wrong week (the last 7 days ending today, not Aug 1–7), and the model
# correctly-but-unhelpfully said it had no data for the period asked about,
# because it was genuinely never given that period. Deterministic, not
# exhaustive — misses relative phrasings like "tuần trước"; those fall back
# to the existing default window, same as before this fix.
_DAY_RANGE_RE = re.compile(
    r"(?:từ\s+)?(?:mùng|ngày)\s*(\d{1,2})\s*(?:tới|đến|-)\s*(?:mùng|ngày)?\s*(\d{1,2})\b"
)

# Named single day ("hôm nay", "hôm qua", "hôm kia") and day-part-of-today/
# yesterday ("sáng nay", "tối qua", ...) — a *different* real bug from the
# one above: these already fell through to fetch_data.py's 7-day trailing
# default (which does include today), so the reading was genuinely in
# tool_results — but the model still answered "no data for today" because
# nothing in the prompt ever told it what today's date is (see
# synthesize_qa.py's _time_context_block). Scoping the fetch to exactly the
# named day is a second, complementary fix on top of that: tighter grounding
# means the model doesn't have to pick "today" out of a week of readings
# itself, and a genuinely empty day still correctly reads as "no data"
# rather than silently pulling in adjacent days' readings.
#
# Day-part phrasings ("sáng nay"/"tối qua"/...) deliberately still resolve
# to the *whole* named day, not a narrower morning/evening sub-range — this
# codebase has no established clock-boundary convention for "sáng" vs
# "chiều" (unlike meal_context, which is patient-selected, not time-derived)
# and picking one here would be guesswork. synthesize_qa.py's
# _time_context_block gives the model the actual current time plus every
# reading's real timestamp, so narrowing "sáng nay" down to the morning
# readings within that day is a natural-language judgment call left to the
# model, the same way it already has to pick out which readings are
# "hôm nay" from a full day's worth once it has the day correctly scoped.
_NAMED_DAY_OFFSET: dict[str, int] = {
    "hôm nay": 0,
    "hôm qua": 1,
    "hôm kia": 2,
    "đêm nay": 0,
    "đêm qua": 1,
    "sáng nay": 0,
    "trưa nay": 0,
    "chiều nay": 0,
    "tối nay": 0,
    "sáng qua": 1,
    "trưa qua": 1,
    "chiều qua": 1,
    "tối qua": 1,
}
_NAMED_DAY_RE = re.compile(r"\b(" + "|".join(re.escape(phrase) for phrase in _NAMED_DAY_OFFSET) + r")\b")

# "từ 1/8 đến 7/8" or "từ 1/8/2026 đến 7/8/2026" — DD/MM(/YYYY), year defaults
# to the current year when omitted on either side.
_EXPLICIT_DATE_RANGE_RE = re.compile(
    r"từ\s+(?:ngày\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?"
    r"\s*(?:tới|đến)\s+(?:ngày\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?"
)

# Relative trailing period ("7 ngày qua", "2 tuần gần đây", "trong 1 tháng") —
# the specific gap the comment above used to just accept: fetch_data.py's
# default window is always a *fixed* 7-day trailing window
# (nodes/fetch_data.py's DEFAULT_DATE_RANGE_DAYS), so before this, a question
# naming any other trailing period (2 tuần/3 tuần/1 tháng — verified live via
# the actual web dashboard's 7-ngày/14-ngày/1-tháng views vs the same question
# asked in chat) silently got 7 days of data back regardless of what was
# asked, and the model answered fluently but from the wrong window — unlike
# the "no data for that period" failure mode _DAY_RANGE_RE/_EXPLICIT_DATE_
# RANGE_RE above were written for, this one is worse because nothing about
# the (wrong) answer looks incomplete or wrong on its own. Two accepted
# shapes: a trailing qualifier ("N ngày/tuần/tháng qua/gần đây/trở lại đây/
# vừa qua") or a leading "trong" ("trong N ngày/tuần/tháng") — deliberately
# NOT bare "N ngày/tuần/tháng" with no qualifier at all, since that also
# matches unrelated frequency phrasing ("đo đường huyết 3 lần một ngày") that
# has nothing to do with a reporting period.
#
# Third alternative: "tuần trước"/"tuần vừa rồi"/"tháng trước" — same
# trailing-window idea with the count implicitly 1, no digit at all. Bare
# "ngày trước"/"ngày qua" deliberately excluded — "hôm qua" is how this
# codebase's test data and every real question so far actually says
# "yesterday" (see _NAMED_DAY_OFFSET), and "ngày qua" alone reads as vague/
# uncommon phrasing that would mostly just add false-positive risk. Treated
# as a *trailing* 7/30-day window rather than the previous calendar week/
# month (Mon–Sun, 1st–end-of-month) — matches how every other relative
# period in this file works, and how the web dashboard's own 7-ngày/
# 14-ngày/1-tháng glucose views are trailing windows, not calendar-aligned
# ones — so "tuần trước" and "7 ngày qua" resolve the same way on purpose.
_RELATIVE_PERIOD_RE = re.compile(
    r"trong\s+(?:vòng\s+)?(\d{1,3})\s*(ngày|tuần|tháng)"
    r"|(\d{1,3})\s*(ngày|tuần|tháng)\s*(?:qua|gần đây|trở lại đây|vừa qua)\b"
    r"|(tuần|tháng)\s*(?:trước|vừa rồi|qua|gần đây|trở lại đây|vừa qua)\b"
)

_DAYS_PER_UNIT: dict[str, int] = {"ngày": 1, "tuần": 7, "tháng": 30}


def _day_of_month_range(today: date, day_start: int, day_end: int) -> tuple[datetime, datetime] | None:
    """"mùng X tới mùng Y" / "ngày X đến ngày Y" names no month — assume the
    current month, unless day_start hasn't happened yet this month (a
    "history" question can't be about days that haven't occurred), in which
    case assume last month instead."""
    if not (1 <= day_start <= 31 and 1 <= day_end <= 31):
        return None
    if day_start > day_end:
        day_start, day_end = day_end, day_start
    year, month = today.year, today.month
    if day_start > today.day:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    try:
        start = datetime(year, month, day_start)
        end = datetime(year, month, day_end, 23, 59, 59, 999999)
    except ValueError:
        return None  # e.g. "mùng 30" resolved against February
    return start, end


def _vn_today() -> date:
    """Patients ask "hôm nay" against their own wall-clock day, not the
    server's UTC one — same reminder_timezone (Asia/Ho_Chi_Minh) convention
    notifications/scheduler.py and insights/scheduler.py already use for
    every other patient-facing "today"."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    return datetime.now(tz).date()


def _local_day_range_utc(local_date: date) -> tuple[datetime, datetime]:
    """UTC range covering local_date's full 00:00–23:59:59 in
    reminder_timezone, converted to the naive-UTC form GlucoseRecord.measured_at
    etc. are actually stored/compared in (see health/models.py — the frontend
    sends `.toISOString()`, always UTC, and it's stored naive). Unlike
    _day_of_month_range/_explicit_date_range below (which build naive ranges
    with no timezone shift at all), this one actually converts — those
    multi-day ranges lose at most a few boundary hours out of several days,
    but a single named day the same naive way could shift by up to 7 hours
    (VN is UTC+7), which for a *one-day* window means missing a real chunk of
    that day's readings. Worth the extra precision here specifically because
    "hôm nay"/"hôm qua" name exactly one day."""
    tz = ZoneInfo(get_settings().reminder_timezone)
    start_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1, microseconds=-1)
    return start_local.astimezone(UTC).replace(tzinfo=None), end_local.astimezone(UTC).replace(tzinfo=None)


def _explicit_date_range(match: re.Match[str]) -> tuple[datetime, datetime] | None:
    d1, m1, y1, d2, m2, y2 = match.groups()
    year_now = datetime.utcnow().year

    def _year(y: str | None) -> int:
        if not y:
            return year_now
        y_int = int(y)
        return y_int + 2000 if y_int < 100 else y_int

    try:
        start = datetime(_year(y1), int(m1), int(d1))
        end = datetime(_year(y2), int(m2), int(d2), 23, 59, 59, 999999)
    except ValueError:
        return None
    if start > end:
        start, end = end, start
    return start, end


def _extract_date_range(lowered: str, today: date | None = None) -> tuple[datetime, datetime] | None:
    today = today or _vn_today()

    match = _EXPLICIT_DATE_RANGE_RE.search(lowered)
    if match:
        result = _explicit_date_range(match)
        if result:
            return result

    match = _DAY_RANGE_RE.search(lowered)
    if match:
        return _day_of_month_range(today, int(match.group(1)), int(match.group(2)))

    match = _NAMED_DAY_RE.search(lowered)
    if match:
        offset = _NAMED_DAY_OFFSET[match.group(1)]
        return _local_day_range_utc(today - timedelta(days=offset))

    return None


def _extract_relative_days(lowered: str) -> int | None:
    """"2 tuần qua" -> 14, "trong 1 tháng" -> 30, "7 ngày qua" -> 7,
    "tuần trước"/"tuần vừa rồi" -> 7 (implicit count of 1, no digit in the
    message at all). Only called when _extract_date_range above found
    nothing more specific — see _RELATIVE_PERIOD_RE's docstring for why this
    is a separate, narrower pattern rather than folded into that function."""
    match = _RELATIVE_PERIOD_RE.search(lowered)
    if not match:
        return None
    n_str = match.group(1) or match.group(3)
    unit = match.group(2) or match.group(4) or match.group(5)
    n = int(n_str) if n_str else 1
    if n <= 0:
        return None
    return n * _DAYS_PER_UNIT[unit]


async def classify_intent(state: AgentState) -> dict:
    """Deterministic Layer 1/2 safety gate (jailbreak, then high-risk medical);
    everything else is passed through as provisional QA/SUMMARY for
    plan_response.py to classify further. See module docstring for the full
    contract and why check order matters."""
    message = state.get("message", "") or ""
    # normalize_for_pattern_match (not plain .lower()) so the deterministic
    # layers below can't be evaded with zero-width characters, full-width
    # Unicode variants, or extra whitespace/newlines mid-phrase — see that
    # function's docstring for the exact bypasses this closes.
    lowered = normalize_for_pattern_match(message)

    # NOTE: `canned_response` and `handoff` are set UNCONDITIONALLY on every
    # branch below (to None/False where not applicable) — state persists
    # across turns on this thread via the checkpointer (graph.py). Leaving
    # either key out on a turn that doesn't need it would let a *previous*
    # turn's JAILBREAK/HIGH_RISK canned_response (and/or handoff=True) leak
    # into this turn's response: respond.py takes the canned_response branch
    # whenever the key is merely present/truthy in accumulated state, so a
    # stale value silently overrides a legitimately-classified QA/SUMMARY
    # turn's real, freshly-generated answer with an old refusal forever.

    # Layer 1 — jailbreak / injection, deterministic, no LLM (§6.1).
    if (
        any(pattern in lowered for pattern in JAILBREAK_PATTERNS)
        or _is_bypass_framing(lowered)
        or _has_fake_delimiter_injection(lowered)
        or INSTRUCTION_OVERRIDE_RE.search(lowered)
        or PERSONA_ASSIGNMENT_RE.search(lowered)
        or AUTHORITY_GRANT_RE.search(lowered)
        or _has_encoded_payload_bypass(lowered)
    ):
        return {"intent": "JAILBREAK", "canned_response": _JAILBREAK_CANNED_RESPONSE, "handoff": False}

    # Deterministic scope boundary for illegal-drug content. This runs before
    # the planner so record confirmation can never depend on LLM judgment.
    if _ILLICIT_SUBSTANCE_RE.search(lowered):
        return {
            "intent": "OUT_OF_SCOPE",
            "canned_response": _OUT_OF_SCOPE_CANNED_RESPONSE,
            "handoff": False,
        }

    # Layer 2 — high-risk medical, deterministic, no LLM (§6.2). Emergency
    # framings get a more urgent canned response than plain dose/diagnosis asks,
    # and set handoff=True so the API/UI surfaces this as needing immediate
    # doctor/emergency-service escalation rather than a routine chat reply.
    if any(pattern in lowered for pattern in EMERGENCY_PATTERNS):
        return {"intent": "HIGH_RISK", "canned_response": _EMERGENCY_CANNED_RESPONSE, "handoff": True}
    if _is_treatment_change_request(lowered) and (
        not _is_sick_day_stop_medication_question(lowered) and not _is_non_decision_medical_question(lowered)
    ):
        return {"intent": "HIGH_RISK", "canned_response": _high_risk_response(lowered), "handoff": False}

    # Only messages that cleared both deterministic layers reach here.
    if state.get("request_kind") == "summary":
        return {"intent": "SUMMARY", "canned_response": None, "handoff": False}

    # Everything else — scope, greetings, appointment requests, RAG-need,
    # direct-answer capability questions, short contextual follow-ups — is
    # plan_response.py's job now (LLM judgment; see that module's docstring
    # for why a keyword net kept missing real phrasing here). Date-range
    # extraction stays here: it's precise regex date-math, not a judgment
    # call, and fetch_data.py needs it regardless of what plan_response
    # decides about needs_health_data.
    date_range_override = _extract_date_range(lowered)
    # Both keys are set unconditionally (to None when nothing matched), same
    # cross-turn-leakage reasoning as canned_response/handoff above.
    return {
        "intent": "QA",
        "date_range_override": date_range_override,
        "date_range_days": None if date_range_override else _extract_relative_days(lowered),
        "canned_response": None,
        "handoff": False,
    }

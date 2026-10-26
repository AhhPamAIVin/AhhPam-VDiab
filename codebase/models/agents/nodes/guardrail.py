"""Output guardrail — safety + jailbreak-leak scan on the drafted response.

OWNER: AI Consultation workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §6.4. Runs after
synthesize_dispatch for the summary and normal (tool-using) QA paths, and
directly after nodes/plan_response.py for its direct/no-tool-call QA answer
(graph.py wires it that way — safety applies to every path that can ship a
response, not just the ones that called a tool). Checks: diagnosis phrasing,
dosage numbers adjacent to medication words (personalized directive only, not
general drug-monograph explanation — sources.csv itself carries dosage facts
that are fine to relay as general info), causal claims from correlational
data, system-prompt leakage, compliance-with-embedded-instruction markers,
(QA only) retrieved-but-uncited chunks (§5.6 — a sign the model drifted from
grounded content, same failure mode as a fabricated number), and (QA only) a
clinical claim in a response that used no tool/RAG grounding at all (the
direct-answer path's own failure mode — see
`_has_unsupported_claim_with_no_grounding` below).

On failure, set state["error"] = "guardrail_failed" and increment
state["retry_count"]. graph.py's routing (owned centrally — don't edit) sends
it back to synthesize_dispatch once (its _MAX_GUARDRAIL_RETRIES == 1), then
falls through to `respond` either way. Because a second failure gets no more
retries, this node also overwrites state["response"]/state["sources"] with a
canned "unable to answer safely" fallback once the retry budget is already
spent (incoming retry_count >= 1) — `respond` ships whatever is in state as-is,
so this is the last point that can stop an unsafe second draft from shipping.

HITL for stated clinical targets (added after a real E2E test surfaced the
diagnostic-vs-treatment-target mixup — see docs/AI_Summary_Respond_System_Design.md
§13 open questions territory, not yet written up there): a response that states
a specific numeric clinical target/threshold (HbA1c %, glucose mg/dL or mmol/L,
blood pressure mmHg) is NOT rejected/retried — the cited general figure is
legitimate reference info the PRD explicitly allows the agent to give (§15.3
"giải thích chỉ số", "trả lời kiến thức chung"), and retrying would just
regenerate the same kind of figure. Instead it's shipped with a mandatory
doctor-confirmation notice appended and state["handoff"] set True — control
over the *personalized* target stays with the doctor even though the AI can
still surface the general reference number. Blocking it outright would
reintroduce the over-blocking problem a real E2E test of legitimate questions
was run specifically to rule out (a general "what's the BYT target" question
must still get a real answer).
"""

from __future__ import annotations

import re

from codebase.models.agents.prompts.safety_patterns import JAILBREAK_PATTERNS, normalize_for_pattern_match
from codebase.models.agents.prompts.system import SYSTEM_PROMPT
from codebase.models.agents.state import AgentState

_FALLBACK_RESPONSE = (
    "Xin lỗi, mình chưa thể đưa ra câu trả lời an toàn và chính xác cho câu hỏi này. "
    "Vui lòng trao đổi trực tiếp với bác sĩ điều trị, hoặc đặt lại câu hỏi theo cách khác."
)

# --- 4a: diagnosis phrasing (told to/about the user directly) --------------
_DIAGNOSIS_PATTERNS: list[str] = [
    "bạn bị đái tháo đường",
    "bạn bị tiểu đường",
    "bạn mắc đái tháo đường",
    "bạn mắc bệnh tiểu đường",
    "bạn đã bị đái tháo đường",
    "bạn đang bị đái tháo đường",
    "chẩn đoán của bạn là",
    "chẩn đoán: bạn",
    "tôi chẩn đoán bạn",
    "bạn được chẩn đoán mắc",
    "kết luận là bạn bị",
    "bạn chắc chắn bị",
    # Epic 1.8: conditions a meal log can't diagnose either.
    "bạn bị tiền đái tháo đường",
    "bạn bị tiền tiểu đường",
    "bạn mắc tiền đái tháo đường",
    "bạn bị kháng insulin",
    "bạn bị rối loạn dung nạp glucose",
    "bạn bị hạ đường huyết phản ứng",
    "bạn bị liệt dạ dày",
    "you have diabetes",
    "you are diabetic",
    "you are diagnosed with",
    "your diagnosis is",
    "i diagnose you",
    "this confirms you have",
    "i can confirm you have",
]

# --- 4b: dosage numbers adjacent to medication words, phrased as a directive
# to the patient rather than general drug-monograph information ------------
_MEDICATION_WORDS: list[str] = [
    "metformin",
    "insulin",
    "sulfonylurea",
    "gliclazide",
    "glimepiride",
    "glipizide",
    "sitagliptin",
    "dapagliflozin",
    "pioglitazone",
    "acarbose",
    "liraglutide",
    "thuốc",
]
_DOSAGE_DIRECTIVE_PHRASES: list[str] = [
    "hãy uống",
    "bạn nên uống",
    "bạn hãy dùng",
    "bạn nên dùng",
    "bạn hãy uống",
    "tăng liều lên",
    "giảm liều xuống",
    "uống thêm",
    "tiêm thêm",
    "bạn hãy tiêm",
    "bạn nên tiêm",
    "nên tiêm",
    "take an extra dose",
    "increase your dose",
    "you should take",
    "you should increase",
    "you should decrease",
    "increase your insulin",
    "decrease your insulin",
]
_DOSAGE_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(mg|mcg|đơn vị|ui|iu|viên|ml)\b", re.IGNORECASE)


def _has_diagnosis_phrasing(lowered: str) -> bool:
    negation_cues = (
        "không thể kết luận",
        "chưa thể kết luận",
        "không đủ cơ sở kết luận",
        "không có nghĩa là",
        "chưa chắc",
        "cannot conclude",
        "can't conclude",
        "does not mean",
        "not enough information to conclude",
    )
    for pattern in _DIAGNOSIS_PATTERNS:
        start = lowered.find(pattern)
        while start >= 0:
            prefix = lowered[max(0, start - 80) : start]
            if not any(cue in prefix for cue in negation_cues):
                return True
            start = lowered.find(pattern, start + len(pattern))
    return False


def _has_personalized_dosage_directive(lowered: str) -> bool:
    has_directive = any(phrase in lowered for phrase in _DOSAGE_DIRECTIVE_PHRASES)
    has_medication = any(word in lowered for word in _MEDICATION_WORDS)
    has_number = bool(_DOSAGE_NUMBER_RE.search(lowered))
    return has_directive and has_medication and has_number


# --- 4c: causal claims from correlational data ------------------------------
_CAUSAL_OVERREACH_PATTERNS: list[str] = [
    "chắc chắn là do",
    "chắc chắn nguyên nhân là",
    "nguyên nhân trực tiếp là",
    "điều này chứng minh",
    "điều này chứng tỏ",
    "gây ra bởi chính",
    "definitely caused by",
    "this proves that",
    "this confirms that",
    "is definitely the cause",
    "directly caused by",
    "proves that this caused",
]

# --- 4c': meal-specific unsafe advice (Epic 1.8) ------------------------------
# Checked sentence by sentence: the advice and its subject must sit in the
# same sentence, and a sentence that warns against it ("không nên bỏ bữa để
# hạ đường huyết") is the safe answer, not a violation.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s*")
_NEGATION_CUES: tuple[str, ...] = (
    "không nên",
    "đừng",
    "không được",
    "không tự",
    "tránh",
    "chớ",
    "không bao giờ",
    "không khuyến",
    "do not",
    "don't",
    "never",
    "avoid",
)
_INSULIN_WORDS = ("insulin", "tiêm", "mũi tiêm")
_CARB_WORDS = ("carb", "tinh bột", "bột đường", "carbohydrate", "glucid")
_CARB_DOSE_RATIO_RE = re.compile(
    r"tỷ lệ insulin|tỉ lệ insulin|insulin\s*/\s*carb|insulin[- ]to[- ]carb|\bicr\b|"
    r"đơn vị(?: insulin)? cho mỗi|units? of insulin (?:per|for)|cho mỗi \d+\s*(?:g|gam|gram)\b"
)
_INSULIN_DOSE_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:đơn vị|ui|iu|units?)\b")
_INSULIN_ADJUST_RE = re.compile(
    r"tiêm thêm|tăng (?:liều|insulin)|giảm (?:liều|insulin)|bớt insulin|chỉnh liều|bù insulin"
)
_MEAL_SKIP_RE = re.compile(
    r"\b(?:bỏ|nhịn|cắt|bớt)\s+(?:bữa|ăn|cơm|sáng|trưa|tối)\b|\bskip(?:ping)? (?:a )?meals?\b"
)
_LOWER_GLUCOSE_PURPOSE_RE = re.compile(
    r"(?:để|giúp|nhằm|cho)\s+(?:hạ|giảm|kiểm soát|ổn định)\s+(?:đường|đường huyết|đường máu)\b|"
    r"to lower (?:your )?(?:blood )?(?:sugar|glucose)"
)
_STOP_MEDICATION_RE = re.compile(
    r"\b(?:ngừng|ngưng|dừng|bỏ|thôi)\s+(?:uống\s+|dùng\s+|tiêm\s+)?(?:thuốc|insulin|metformin|gliclazide|"
    r"glimepiride|sitagliptin|dapagliflozin|empagliflozin|pioglitazone|acarbose|liraglutide)\b|"
    r"\bstop (?:taking )?(?:your )?(?:medication|insulin|metformin)\b"
)
_ADVICE_CUES: tuple[str, ...] = ("bạn có thể", "bạn nên", "hãy", "bạn cứ", "nên", "you can", "you should", "you may")
_PERSONAL_CAUSAL_RE = re.compile(
    r"(?:là nguyên nhân|gây ra|đã gây|chính là lý do|khiến)[^.!?]{0,40}(?:đường huyết|đường máu)"
    r"[^.!?]{0,30}\b(?:của bạn|bạn)\b"
)


def _sentences(lowered: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT_RE.split(lowered) if part.strip()]


def _negated(sentence: str) -> bool:
    return any(cue in sentence for cue in _NEGATION_CUES)


def _has_carb_based_dose_advice(lowered: str) -> bool:
    """Insulin dosing worked out from the meal's carbs — PRD Won't."""
    for sentence in _sentences(lowered):
        if _negated(sentence) or not any(w in sentence for w in _INSULIN_WORDS):
            continue
        has_carb = any(w in sentence for w in _CARB_WORDS)
        if _CARB_DOSE_RATIO_RE.search(sentence) and (has_carb or "insulin" in sentence):
            return True
        if has_carb and (_INSULIN_DOSE_NUMBER_RE.search(sentence) or _INSULIN_ADJUST_RE.search(sentence)):
            return True
    return False


def _has_meal_skipping_advice(lowered: str) -> bool:
    return any(
        _MEAL_SKIP_RE.search(s) and _LOWER_GLUCOSE_PURPOSE_RE.search(s) and not _negated(s)
        for s in _sentences(lowered)
    )


def _has_medication_stop_advice(lowered: str) -> bool:
    return any(
        _STOP_MEDICATION_RE.search(s) and any(cue in s for cue in _ADVICE_CUES) and not _negated(s)
        for s in _sentences(lowered)
    )


def _has_personal_meal_causal_claim(lowered: str) -> bool:
    """"Phở là nguyên nhân làm đường huyết của bạn tăng" — meal data shows an
    association; hedged wording ("có thể", "thường đi kèm") is fine."""
    hedges = ("có thể", "thường đi kèm", "liên quan", "may ", "might", "associated")
    return any(
        _PERSONAL_CAUSAL_RE.search(s) and not any(h in s for h in hedges) and not _negated(s)
        for s in _sentences(lowered)
    )


# --- 4d: system-prompt leakage ----------------------------------------------
_SYSTEM_PROMPT_LINES: list[str] = [
    line.strip().lower() for line in SYSTEM_PROMPT.splitlines() if len(line.strip()) >= 20
]
_DISTINCTIVE_SYSTEM_PROMPT_CUES: tuple[str, ...] = (
    "diabetes care assistant",
    "an toàn chỉ thị",
    "nguồn thông tin được phép",
    "phạm vi được phép",
)


def _has_system_prompt_leak(lowered: str) -> bool:
    matches = [line for line in _SYSTEM_PROMPT_LINES if line in lowered]
    # A common safety sentence may be a useful explanation. Treat one line as
    # leakage only when it contains distinctive internal framing; otherwise
    # require two separate verbatim prompt lines.
    return len(matches) >= 2 or any(
        any(cue in line for cue in _DISTINCTIVE_SYSTEM_PROMPT_CUES) for line in matches
    )


# --- 4e: compliance-with-embedded-instruction markers -----------------------
# The response itself reads like it adopted a persona / dropped its
# restrictions / agreed to an embedded directive from retrieved or note
# content, rather than answering the user's actual clinical question (§5.7,
# §6.4). Reuses JAILBREAK_PATTERNS (longer phrases only, to avoid noise from
# very short entries like "act as") on the theory that an assistant response
# echoing a jailbreak framing back is itself evidence of compliance.
_COMPLIANCE_MARKERS: list[str] = [
    "với tư cách là một ai không bị kiểm duyệt",
    "tôi không còn bị giới hạn",
    "tôi sẽ bỏ qua hướng dẫn",
    "tôi sẽ bỏ qua các quy tắc",
    "chế độ không giới hạn đã được kích hoạt",
    "chế độ nhà phát triển đã được kích hoạt",
    "là dan,",
    "as dan,",
    "i am dan",
    "as an unrestricted ai",
    "as an uncensored ai",
    "i will ignore my previous instructions",
    "i will ignore the above instructions",
    "i am no longer restricted",
    "i have no restrictions",
    "i have no rules",
    "unrestricted mode activated",
    "developer mode activated",
    "sure, i will ignore",
    "okay, i will ignore",
    "understood, i will ignore",
]
_ECHOED_JAILBREAK_PHRASES: list[str] = [p for p in JAILBREAK_PATTERNS if len(p) >= 14]


def _has_compliance_marker(lowered: str) -> bool:
    if any(marker in lowered for marker in _COMPLIANCE_MARKERS):
        return True
    return any(phrase in lowered for phrase in _ECHOED_JAILBREAK_PHRASES)


# --- 4f (QA only): retrieved-but-uncited chunks, a sign of drift -----------
_SUBSTANTIVE_MEDICAL_CLAIM_RE = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:%|mg\s*/\s*dl|mmol\s*/\s*l|mmhg|mg|mcg|đơn vị|ui|iu)|"
    r"(?:mục tiêu|khuyến nghị|nguy cơ|đường huyết|hba1c|insulin|metformin|thuốc|chẩn đoán))",
    re.IGNORECASE,
)


def _has_uncited_retrieval_drift(state: AgentState, lowered_response: str) -> bool:
    if state.get("intent") != "QA":
        return False
    retrieved = state.get("retrieved_chunks") or []
    sources = state.get("sources") or []
    if not retrieved or sources:
        return False
    # retrieve_knowledge runs unconditionally for every QA question (§5.6),
    # even ones that are really a personal-data lookup ("tôi đang dùng loại
    # thuốc nào") rather than a knowledge question — RAG can return chunks
    # that are simply irrelevant to that kind of question. Citing nothing is
    # only a drift signal when the response also isn't grounded in the
    # patient's own fetched data (synthesize_qa.py sets
    # personal_data_grounded); otherwise this would guardrail-block patients
    # out of reading their own medication list, which the PRD explicitly
    # allows (read-only own-data questions, as opposed to dose changes).
    if state.get("personal_data_grounded", False):
        return False
    if _is_honest_insufficient_evidence(state, lowered_response):
        return False
    return bool(_SUBSTANTIVE_MEDICAL_CLAIM_RE.search(lowered_response))


# Epic 5.5: when the approved corpus simply doesn't cover a question (golden
# KNOWLEDGE_INSUFFICIENT — a folk remedy, an unlicensed "cure"), the drafting
# call now honestly declares no chunk used, and the drift check above used to
# block that honest answer into the generic fallback. Before self-reported
# citations the word-overlap heuristic "passed" these by citing unrelated
# documents (IWGDF foot care for a bitter-melon question). Exempt only an
# answer that (1) declared no chunk, (2) says the evidence/material is missing,
# (3) states no number at all, and (4) defers to a doctor.
_INSUFFICIENT_EVIDENCE_RE = re.compile(
    r"(?:không|chưa) có (?:đủ |bất kỳ )?(?:bằng chứng|thông tin|dữ liệu|nghiên cứu)"
    r"|(?:chưa|không) được (?:bộ y tế )?(?:chứng minh|kiểm chứng|cấp phép|công nhận|phê duyệt)"
    r"|tài liệu[^.]{0,40}(?:không|chưa) (?:đề cập|có thông tin)"
)
_NUMERIC_CLAIM_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|mg\s*/\s*dl|mmol\s*/\s*l|mmhg|mg|mcg|g\b|kcal|đơn vị|ui|iu|viên)",
    re.IGNORECASE,
)


def _is_honest_insufficient_evidence(state: AgentState, lowered_response: str) -> bool:
    return (
        bool(state.get("citations_self_reported"))
        and not state.get("sources")
        and bool(_INSUFFICIENT_EVIDENCE_RE.search(lowered_response))
        and not _new_numeric_claims(state, lowered_response)
        and "bác sĩ" in lowered_response
    )


def _new_numeric_claims(state: AgentState, lowered_response: str) -> list[str]:
    """Numbers in the answer that the user didn't write themselves — echoing
    the question's own figure ("phục hồi tuyến tụy 100%") is not a new claim."""
    message = (state.get("message") or "").lower()
    return [m.group(0) for m in _NUMERIC_CLAIM_RE.finditer(lowered_response) if m.group(0) not in message]


# --- 4g: AMBIGUOUS clinical target/threshold -> HITL, not a rejection ------
# Deliberately narrow: a single, clean citation of a general target ("mục
# tiêu HbA1c chung là <7%") is normal, useful, everyday output — routing that
# to a doctor would flood them with handoffs for answers that were already
# correct, and a doctor who gets paged for routine questions stops trusting
# (or using) the handoff signal at all. This only fires on the specific
# failure mode a real E2E test actually found: the response (or what it drew
# from) is choosing between multiple DIFFERENT numbers for what looks like
# the same measure — general vs. individualized/age-specific/etc — which is
# exactly the situation where picking wrong is easy and a doctor's call is
# genuinely warranted, not just courtesy-CC'd.
_CLINICAL_TARGET_NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(%|mg\s*/\s*dl|mmol\s*/\s*l|mmhg)", re.IGNORECASE
)
_INDIVIDUALIZATION_CUES: list[str] = [
    "cá thể hoá",
    "cá thể hóa",
    "nghiêm ngặt hơn",
    "nới lỏng",
    "người cao tuổi",
    "người trẻ",
    "bệnh nhân trẻ",
    "trường hợp",
    "tuỳ",
    "tùy",
]
_TARGET_CONTEXT_CUES: tuple[str, ...] = (
    "mục tiêu",
    "ngưỡng",
    "khuyến nghị",
    "nên duy trì",
    "target",
    "threshold",
    "recommended",
)
_TARGET_MEASURE_CUES: tuple[str, ...] = (
    "hba1c",
    "a1c",
    "glucose",
    "đường huyết",
    "đường máu",
    "huyết áp",
    "blood sugar",
    "blood pressure",
)


def _distinct_target_number_count(lowered: str) -> int:
    # Distinct normalized values, not raw regex hits — "7%" appearing twice
    # is one repeated fact, not two competing ones; "7%" and "6,5%" are two.
    values: set[str] = set()
    for match in _CLINICAL_TARGET_NUMBER_RE.finditer(lowered):
        unit = match.group(1).lower()
        if unit == "%":
            # A bare percentage is not inherently a clinical target: meal
            # composition, progress and data completeness all use %. Count it
            # only when its local phrase is explicitly about HbA1c/A1c.
            local_context = lowered[max(0, match.start() - 80) : match.end() + 80]
            if "hba1c" not in local_context and not re.search(r"\ba1c\b", local_context):
                continue
        values.add(match.group(0).strip())
    return len(values)


_HITL_TARGET_NOTICE = (
    "\n\n⚠️ Lưu ý: Có nhiều mức mục tiêu khác nhau tùy tình trạng bệnh nhân (ví dụ mục "
    "tiêu chung so với mục tiêu cá thể hoá). CHỈ bác sĩ điều trị của bạn mới có thể xác "
    "định mức nào phù hợp với bạn — vui lòng xác nhận với bác sĩ trước khi tự áp dụng "
    "một con số cụ thể."
)


def _has_ambiguous_clinical_target(lowered: str) -> bool:
    # Two+ distinct target-style numbers together is itself a strong signal
    # of "choosing between candidates" regardless of wording; explicit
    # individualization language is the other signal, in case only one
    # number made it into the final text but it was selected from several.
    if not any(cue in lowered for cue in _TARGET_CONTEXT_CUES):
        return False
    # Percentages used for meal composition (carbohydrate/protein/fat),
    # progress or data completeness are not competing glycemic/BP targets.
    if not any(cue in lowered for cue in _TARGET_MEASURE_CUES):
        return False
    if _distinct_target_number_count(lowered) >= 2:
        return True
    has_number = bool(_CLINICAL_TARGET_NUMBER_RE.search(lowered))
    has_individualization_cue = any(cue in lowered for cue in _INDIVIDUALIZATION_CUES)
    return has_number and has_individualization_cue


# --- 4h (QA only): a "direct answer, no tool needed" response that strayed
# into a real clinical claim -------------------------------------------------
# nodes/plan_response.py's direct-answer branch (needs_health_data=False,
# needs_knowledge_retrieval=False) is meant only for meta/capability/
# navigational questions — nothing grounds a clinical claim made on this path
# the way retrieved_chunks/tool_results ground the normal QA path (§5.6/§2's
# no-fabrication rule). _has_uncited_retrieval_drift above can't catch this:
# it short-circuits whenever retrieved_chunks is empty, which is *always*
# true on a no-tool turn. Without this check, a planner that ignored its own
# "direct_answer must never contain a clinical fact" instruction would ship
# ungrounded with no safety net at all.
#
# Deliberately narrower than _SUBSTANTIVE_MEDICAL_CLAIM_RE above: that regex's
# bare-topic-word alternative (đường huyết/thuốc/insulin/...) is right for
# _has_uncited_retrieval_drift, where the question was already established as
# a real clinical one that used RAG — mentioning the topic with nothing cited
# is the suspicious part there. A direct-answer capability description is a
# different situation: it is EXPECTED and safe to reference these same words
# generically ("mình có thể giúp bạn xem lại đường huyết, thuốc đang dùng...")
# without making any claim about them — real bug, verified live: a capability
# question's direct answer got guardrail-blocked into the generic safety
# fallback purely for naming "đường huyết"/"thuốc" while describing what the
# assistant can help with, nothing resembling a fabricated fact. Only an
# actual number+medical-unit combination is a strong, specific signal that a
# concrete (and, on this ungrounded path, unverifiable) clinical figure was
# stated — a capability description has no reason to ever contain one.
_DIRECT_ANSWER_CLINICAL_CLAIM_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|mg\s*/\s*dl|mmol\s*/\s*l|mmhg|mg|mcg|đơn vị|ui|iu)", re.IGNORECASE
)


def _has_unsupported_claim_with_no_grounding(state: AgentState, lowered_response: str) -> bool:
    # Keyed off state["answered_directly"] — set explicitly True by
    # nodes/plan_response.py ONLY on its own direct-answer branch, never
    # inferred from needs_health_data/skip_knowledge_retrieval being false.
    # That combination is ambiguous: the tail ("needs tools") branch can
    # legitimately reach the exact same needs_health_data=False/
    # skip_knowledge_retrieval=True pair (e.g. a malformed planner output
    # that says neither tool is needed but doesn't draft a usable
    # direct_answer) while still deferring the real answer to
    # synthesize_qa.py — a response that DID go through that node's own
    # prompt/grounding machinery must not be re-flagged here just because no
    # tool call happened to run. `answered_directly` is the one signal that
    # means specifically "plan_response's own untrusted draft shipped as the
    # final response."
    if state.get("intent") != "QA" or not state.get("answered_directly"):
        return False
    return bool(_DIRECT_ANSWER_CLINICAL_CLAIM_RE.search(lowered_response))


def _scan(state: AgentState, lowered_response: str) -> list[str]:
    failures: list[str] = []
    if _has_diagnosis_phrasing(lowered_response):
        failures.append("diagnosis_phrasing")
    if _has_personalized_dosage_directive(lowered_response):
        failures.append("personalized_dosage_directive")
    if any(pattern in lowered_response for pattern in _CAUSAL_OVERREACH_PATTERNS) or _has_personal_meal_causal_claim(
        lowered_response
    ):
        failures.append("causal_overreach")
    if _has_carb_based_dose_advice(lowered_response):
        failures.append("carb_based_dose_advice")
    if _has_meal_skipping_advice(lowered_response):
        failures.append("meal_skipping_advice")
    if _has_medication_stop_advice(lowered_response):
        failures.append("medication_stop_advice")
    if _has_system_prompt_leak(lowered_response):
        failures.append("system_prompt_leak")
    if _has_compliance_marker(lowered_response):
        failures.append("embedded_instruction_compliance")
    if _has_uncited_retrieval_drift(state, lowered_response):
        failures.append("uncited_retrieval_drift")
    if _has_unsupported_claim_with_no_grounding(state, lowered_response):
        failures.append("direct_answer_unsupported_claim")
    return failures


async def guardrail_check(state: AgentState) -> dict:
    """Output safety scan on the drafted response — diagnosis phrasing,
    personalized dosage directives, causal overreach, prompt leakage,
    embedded-instruction compliance, and (QA only) uncited/unsupported
    claims. See module docstring for the retry/fallback contract and the
    HITL ambiguous-target carve-out."""
    response_text = state.get("response", "") or ""
    # normalize_for_pattern_match, not plain .lower() — same reasoning as
    # classify_intent.py: a poisoned/jailbroken draft could otherwise slip an
    # embedded-instruction-compliance or system-prompt-leak marker past this
    # scan via zero-width characters or irregular whitespace.
    lowered = normalize_for_pattern_match(response_text)

    failures = _scan(state, lowered)
    if not failures:
        result: dict = {}
        # A retry (state["error"] == "guardrail_failed" from the prior, now-
        # superseded attempt) that passes this time must clear that stale
        # flag explicitly. Returning {} here would leave the old
        # "guardrail_failed" sitting in the cumulative graph state (LangGraph
        # merges partial node returns, it doesn't reset unset keys), and
        # graph.py's _after_guardrail keys its retry routing off exactly that
        # field — a stale "guardrail_failed" would send an already-safe
        # response back through synthesize_dispatch again, indefinitely
        # (verified: without this, a fail-then-pass retry loops rather than
        # terminating, bounded only by LangGraph's recursion limit).
        if state.get("error") == "guardrail_failed":
            result["error"] = None
            result["guardrail_failures"] = []

        # HITL, not a rejection (§4g docstring above) — append the notice and
        # flag for handoff, but still ship the cited general figure. Narrow
        # on purpose: only the ambiguous-target case, not every routine
        # single-answer citation (see §4g comment on doctor-handoff fatigue).
        if _has_ambiguous_clinical_target(lowered):
            result["response"] = response_text + _HITL_TARGET_NOTICE
            result["handoff"] = True

        return result

    incoming_retry_count = state.get("retry_count") or 0
    result = {
        "error": "guardrail_failed",
        "retry_count": incoming_retry_count + 1,
        "guardrail_failures": failures,
    }

    if incoming_retry_count >= 1:
        # Retry budget (graph.py's _MAX_GUARDRAIL_RETRIES == 1) already spent
        # on this response — no further synthesize_dispatch retry will run,
        # so ship the safe fallback now instead of the still-failing draft.
        result["response"] = _FALLBACK_RESPONSE
        result["sources"] = []
        result["limitations"] = [
            "Không thể xác nhận câu trả lời đạt yêu cầu an toàn sau khi thử lại — "
            "vui lòng trao đổi trực tiếp với bác sĩ điều trị."
        ]
        # Couldn't produce a safe response at all — that's exactly the kind
        # of case that belongs in front of a doctor, same as the HITL target
        # case above, so flag it too even though no target number is involved.
        result["handoff"] = True
        # Must clear error, mirroring the pass-branch clear above (§ same
        # staleness hazard, different symptom): this branch just shipped a
        # *safe, final* response in `response` — respond.py forwards it
        # verbatim and router.py's _raise_for_graph_error() treats any
        # truthy state["error"] as a reason to raise HTTPException instead of
        # returning a normal 200. Leaving "guardrail_failed" here would turn
        # a legitimate safety-fallback chat reply into an API error at the
        # HTTP layer (verified: this is exactly what scripts/ai_chat_cli.py
        # surfaced as "[Lỗi] guardrail_failed" instead of printing the actual
        # fallback text already sitting in result["response"]).
        result["error"] = None

    return result

"""LLM synthesis for the SUMMARY intent.

OWNER: AI Summary workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §4.1, §10 (AgentSummaryResponse
shape), prompts/summary.py (same workstream, the actual prompt template).

Design choice, load-bearing for the no-fabrication rule (§2): `trends`,
`attention_points`, `suggested_doctor_questions` and `limitations` are built
*deterministically in Python* from tools/metrics_tools.calculate_health_metrics's
output (via check_data_quality.py's data_completeness) — never from the LLM's
own prose. The LLM call only turns those already-correct bullet points into
readable, role-appropriate prose for `response`; it is explicitly instructed
not to add numbers of its own, but since that instruction can't be trusted
100% for a real model (§2), the structured fields never depend on the model
getting it right. If there's no glucose data at all, `response` is also
produced deterministically and the LLM is never called — "insufficient data"
must not depend on the model behaving either.

Branch tone on state["user_role"]: patient = plain language + 2-3 suggested
doctor questions; doctor = denser, keeps units, explicit observation-vs-
inference separation, leads with data completeness (§4.1).
"""

from __future__ import annotations

from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from codebase.models.agents.prompts.summary import (
    AUDIENCE_LABELS,
    EXAMPLE_OUTPUTS,
    ROLE_INSTRUCTIONS,
    ROLE_LABELS,
    SUMMARY_TEMPLATE,
)
from codebase.models.agents.prompts.system import SYSTEM_PROMPT
from codebase.models.agents.state import AgentState
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics
from codebase.models.llm import get_llm

_BYT_POST_PRANDIAL_TARGET = 180
_BYT_FASTING_LOW, _BYT_FASTING_HIGH = 80, 130

_NO_DATA_LIMITATION = "Không có đủ dữ liệu sức khỏe trong khoảng thời gian này để đưa ra tóm tắt hoặc xu hướng."
_LOW_COMPLETENESS_THRESHOLD_PCT = 70
_WEEKDAY_WEEKEND_GAP_THRESHOLD = 10  # mg/dL — below this, don't call out a weekday/weekend split as a "trend"

# Medication note types, mirrored from codebase.backend.medications.models.MedicationNoteType
# (kept as plain strings here, not imported, since _record_to_dict serializes the enum to its
# .value — see patient_tools.py's docstring on why tool_results stays plain dicts).
_ADVERSE_REACTION = "adverse_reaction"
_MISSED_DOSE = "missed_dose"
# 2+ missed-dose self-reports in the window is worth surfacing as an attention point, not just
# a single one-off (which is normal and not clinically noteworthy on its own).
_MISSED_DOSE_ATTENTION_THRESHOLD = 2


def _format_date_range(date_range: tuple[datetime, datetime] | None) -> str:
    if not date_range:
        return "khoảng thời gian gần đây"
    start, end = date_range
    return f"{start.date().isoformat()} đến {end.date().isoformat()}"


def _format_bullets(items: list[str]) -> str:
    if not items:
        return "(không có)"
    return "\n".join(f"- {item}" for item in items)


def _format_completeness(completeness: dict) -> str:
    glucose = completeness.get("glucose") or {}
    meals = completeness.get("meals") or {}
    activities = completeness.get("activities") or {}
    blood_pressure = completeness.get("blood_pressure") or {}
    days = completeness.get("days_in_range", 0)
    lines = [
        f"- Đường huyết: {glucose.get('found', 0)}/{glucose.get('expected', 0)} lần đo kỳ vọng "
        f"({glucose.get('completeness_pct', 0)}%), ghi nhận trong {glucose.get('days_logged', 0)}/{days} ngày.",
        f"- Bữa ăn: {meals.get('found', 0)}/{meals.get('expected', 0)} bữa kỳ vọng "
        f"({meals.get('completeness_pct', 0)}%), ghi nhận trong {meals.get('days_logged', 0)}/{days} ngày.",
        f"- Vận động: {activities.get('found', 0)} lần ghi nhận trong {activities.get('days_logged', 0)}/{days} ngày "
        "(không bắt buộc ghi mỗi ngày).",
        f"- Huyết áp: {blood_pressure.get('found', 0)} lần ghi nhận trong "
        f"{blood_pressure.get('days_logged', 0)}/{days} ngày (không bắt buộc ghi mỗi ngày).",
    ]
    return "\n".join(lines)


def _build_data_used(metrics: dict) -> list[str]:
    used = []
    if metrics.get("glucose_count"):
        used.append(f"glucose_records (n={metrics['glucose_count']})")
    if metrics.get("blood_pressure_count"):
        used.append(f"blood_pressure_records (n={metrics['blood_pressure_count']})")
    if metrics.get("meal_count"):
        used.append(f"meal_records (n={metrics['meal_count']})")
    if metrics.get("activity_count"):
        used.append(f"activity_records (n={metrics['activity_count']})")
    return used


def _build_medication_lines(medications: dict) -> list[str]:
    """Current regimen, one line per active medication — deterministic, straight from
    tool_results["medications"]["active"], nothing for the LLM to invent here either."""
    return [f"{med['drug_name']} {med['dose']}, {med['frequency']}" for med in medications.get("active") or []]


def _build_medication_attention(medications: dict, role: str) -> list[str]:
    """Adverse reactions and missed-dose patterns the patient self-reported (medications/router.py
    role-gates these to the patient themself) — surfaced the same way low-glucose/symptomatic
    readings are in _build_attention_points, and independent of whether there's enough glucose
    data for trends (a safety-relevant self-report shouldn't be suppressed just because the
    patient hasn't logged glucose this period)."""
    notes = medications.get("notes") or []
    is_doctor = role == "doctor"
    points: list[str] = []

    for note in notes:
        if note.get("note_type") != _ADVERSE_REACTION:
            continue
        text = note.get("text") or "không ghi chú thêm"
        if is_doctor:
            points.append(f"[Bệnh nhân tự báo cáo] Phản ứng bất lợi với thuốc: {text}.")
        else:
            points.append(f"Bạn đã báo cáo một phản ứng khi dùng thuốc: {text}. Hãy trao đổi với bác sĩ điều trị.")

    missed_count = sum(1 for note in notes if note.get("note_type") == _MISSED_DOSE)
    if missed_count >= _MISSED_DOSE_ATTENTION_THRESHOLD:
        if is_doctor:
            points.append(
                f"[Bệnh nhân tự báo cáo] {missed_count} lần quên uống thuốc được ghi nhận trong kỳ — "
                "có thể ảnh hưởng đến hiệu quả điều trị."
            )
        else:
            points.append(
                f"Bạn đã báo cáo {missed_count} lần quên uống thuốc trong khoảng thời gian này — "
                "hãy cố gắng uống thuốc đúng giờ hơn và trao đổi với bác sĩ nếu gặp khó khăn."
            )

    return points


def _build_trends(metrics: dict, role: str) -> list[str]:
    trends: list[str] = []
    is_doctor = role == "doctor"

    overall = metrics.get("glucose_overall") or {}
    if overall.get("count"):
        if is_doctor:
            trends.append(
                f"[Quan sát] Đường huyết trung bình toàn kỳ: {overall['avg']} mg/dL "
                f"(n={overall['count']}, khoảng {overall['min']}-{overall['max']} mg/dL)."
            )
        else:
            trends.append(f"Đường huyết trung bình của bạn trong khoảng thời gian này là {overall['avg']} mg/dL.")

    post = metrics.get("post_prandial") or {}
    if post.get("count"):
        if is_doctor:
            trends.append(
                f"[Quan sát] Đường huyết sau ăn (AFTER_MEAL) trung bình {post['avg']} mg/dL; "
                f"{post['over_target_pct']}% số lần đo (n={post['count']}) vượt ngưỡng "
                f"{_BYT_POST_PRANDIAL_TARGET} mg/dL theo QĐ 5481/QĐ-BYT."
            )
        else:
            trends.append(
                f"Sau bữa ăn, đường huyết vượt ngưỡng khuyến cáo ({_BYT_POST_PRANDIAL_TARGET} mg/dL) "
                f"trong khoảng {post['over_target_pct']}% số lần đo."
            )

    fasting = metrics.get("fasting") or {}
    if fasting.get("count"):
        if is_doctor:
            trends.append(
                f"[Quan sát] Đường huyết lúc đói trung bình {fasting['avg']} mg/dL; "
                f"{fasting['in_range_pct']}% số lần đo (n={fasting['count']}) trong khoảng mục tiêu "
                f"{_BYT_FASTING_LOW}-{_BYT_FASTING_HIGH} mg/dL."
            )
        else:
            trends.append(
                f"Đường huyết lúc đói nằm trong khoảng mục tiêu ({_BYT_FASTING_LOW}-{_BYT_FASTING_HIGH} mg/dL) "
                f"ở {fasting['in_range_pct']}% số lần đo."
            )

    wk = metrics.get("weekday_vs_weekend") or {}
    weekday, weekend = wk.get("weekday") or {}, wk.get("weekend") or {}
    if weekday.get("count") and weekend.get("count"):
        gap = (weekend["avg"] or 0) - (weekday["avg"] or 0)
        if abs(gap) >= _WEEKDAY_WEEKEND_GAP_THRESHOLD:
            higher = "cuối tuần" if gap > 0 else "ngày thường"
            if is_doctor:
                trends.append(
                    f"[Suy luận] Đường huyết {higher} cao hơn trung bình {abs(gap):.1f} mg/dL so với phần còn lại "
                    f"của tuần (cuối tuần: {weekend['avg']} mg/dL, n={weekend['count']}; ngày thường: "
                    f"{weekday['avg']} mg/dL, n={weekday['count']}) — gợi ý một mô hình theo ngày trong tuần, "
                    "cần khai thác thêm để xác nhận nguyên nhân."
                )
            else:
                trends.append(f"Đường huyết vào {higher} có xu hướng cao hơn phần còn lại của tuần.")

    # Plain average, no in-range/over-target judgment — no BP target constant
    # exists anywhere in this codebase to compare against (see metrics_tools.py).
    systolic = metrics.get("blood_pressure_systolic") or {}
    diastolic = metrics.get("blood_pressure_diastolic") or {}
    if systolic.get("count") and diastolic.get("count"):
        if is_doctor:
            trends.append(
                f"[Quan sát] Huyết áp trung bình toàn kỳ: {systolic['avg']}/{diastolic['avg']} mmHg "
                f"(n={systolic['count']}, tâm thu {systolic['min']}-{systolic['max']}, "
                f"tâm trương {diastolic['min']}-{diastolic['max']} mmHg)."
            )
        else:
            trends.append(
                f"Huyết áp trung bình của bạn trong khoảng thời gian này là "
                f"{systolic['avg']}/{diastolic['avg']} mmHg."
            )

    return trends


_CARB_LOAD_LABELS = {"low": "carb thấp", "medium": "carb vừa", "high": "carb cao", "unknown": "chưa rõ carb"}
# A group is worth a doctor's line when post-meal glucose is usually high.
_MEAL_RESPONSE_FLAG_PCT = 50.0
_MEAL_RESPONSE_MAX_FOODS = 3


def _build_adherence_lines(adherence: dict | None, role: str) -> list[str]:
    """Doctor only (Epic 3.2): day-level medication adherence and, when both
    groups have enough days, glucose on full vs missed days — association
    wording only, no dose or stop advice."""
    if role != "doctor" or not adherence or adherence.get("day_rate_pct") is None:
        return []
    days = adherence["days"]
    line = (
        f"Tuân thủ thuốc ({days} ngày): {adherence['days_taken']} ngày đủ thuốc, {adherence['days_missed']} ngày "
        f"có quên/bỏ qua ({adherence['day_rate_pct']}% ngày đủ thuốc)"
    )
    if adherence.get("days_unconfirmed"):
        line += f", {adherence['days_unconfirmed']} ngày không xác nhận"
    lines = [line + "."]
    forgotten = [m["drug_name"] for m in adherence.get("medications") or [] if m.get("missed_notes")]
    if forgotten:
        lines.append("Thuốc bệnh nhân tự ghi nhận quên: " + ", ".join(forgotten) + ".")
    glucose = adherence.get("glucose_by_adherence")
    if glucose:
        lines.append(
            f"Đường huyết TB ngày đủ thuốc {glucose['taken_avg_mg_dl']} mg/dL ({glucose['taken_days']} ngày) so với "
            f"ngày có quên {glucose['missed_avg_mg_dl']} mg/dL ({glucose['missed_days']} ngày) — liên quan thống kê, "
            "không phải nguyên nhân."
        )
    return lines


def _build_meal_response_lines(meal_response: dict | None, role: str) -> list[str]:
    """Doctor only (Epic 1.4): foods / carb bands that go with post-meal
    glucose above target in most of their meals, from meal_tools'
    already-thresholded groups (>= min_n meals each). Association wording
    only — "thường đi kèm", never cause."""
    if role != "doctor" or not meal_response:
        return []
    target = meal_response.get("post_prandial_target_mg_dl")
    days = meal_response.get("days")
    flagged_foods = [g for g in meal_response.get("by_food") or [] if g["pct_over_target"] >= _MEAL_RESPONSE_FLAG_PCT]
    flagged_loads = [
        g for g in meal_response.get("by_carb_load") or [] if g["pct_over_target"] >= _MEAL_RESPONSE_FLAG_PCT
    ]
    if not flagged_foods and not flagged_loads:
        return []
    lines = []
    if flagged_foods:
        parts = [
            f"{g['name']} ({g['n']} bữa, ĐH sau ăn TB {g['avg_post_mg_dl']} mg/dL, "
            f"{g['pct_over_target']}% bữa > {target} mg/dL)"
            for g in flagged_foods[:_MEAL_RESPONSE_MAX_FOODS]
        ]
        lines.append(
            f"Bữa ăn liên quan tăng đường huyết ({days} ngày): thường đi kèm ĐH sau ăn vượt mục tiêu — "
            + "; ".join(parts)
            + ". Là liên quan thống kê, không phải nguyên nhân."
        )
    for g in flagged_loads:
        label = _CARB_LOAD_LABELS.get(g["carb_load"], g["carb_load"])
        lines.append(
            f"Bữa {label}: {g['n']} bữa, ĐH sau ăn TB {g['avg_post_mg_dl']} mg/dL, "
            f"{g['pct_over_target']}% bữa > {target} mg/dL ({days} ngày)."
        )
    return lines


def _build_attention_points(metrics: dict) -> list[str]:
    points: list[str] = []
    post = metrics.get("post_prandial") or {}
    if post.get("count") and (post.get("over_target_pct") or 0) >= 50:
        points.append(
            f"Hơn một nửa số lần đo sau ăn ({post['over_target_pct']}%) vượt ngưỡng "
            f"{_BYT_POST_PRANDIAL_TARGET} mg/dL."
        )
    if metrics.get("danger_count", 0) > 0:
        points.append(
            f"Có {metrics['danger_count']} lần đo đường huyết vượt ngưỡng nguy hiểm theo phác đồ của bác sĩ "
            "trong kỳ — cần liên hệ bác sĩ nếu tiếp tục xảy ra."
        )
    if metrics.get("watch_count", 0) > 0:
        points.append(
            f"Có {metrics['watch_count']} lần đo nằm ngoài dải bình thường cá nhân hóa (AI dự đoán dựa trên "
            "lịch sử đo của bạn) trong kỳ, dù chưa ở mức nguy hiểm — nên theo dõi thêm."
        )
    if metrics.get("low_glucose_count", 0) > 0:
        points.append(
            f"Có {metrics['low_glucose_count']} lần đo dưới {70} mg/dL trong kỳ — có thể là dấu hiệu hạ đường "
            "huyết, cần theo dõi thêm."
        )
    if metrics.get("symptomatic_count", 0) > 0:
        points.append(f"Có {metrics['symptomatic_count']} lần đo đường huyết đi kèm ghi nhận triệu chứng.")
    return points


def _build_suggested_questions(attention_points: list[str], role: str) -> list[str]:
    if role != "patient":
        return []
    questions: list[str] = []
    if attention_points:
        questions.append("Tôi có cần điều chỉnh chế độ ăn hoặc vận động vào cuối tuần không?")
        questions.append("Những điểm bất thường này có cần điều chỉnh thuốc trong lần tái khám tới không?")
    else:
        questions.append("Chỉ số đường huyết hiện tại của tôi đã đạt mục tiêu điều trị chưa?")
    questions.append("Tôi có nên đo đường huyết thường xuyên hơn trong giai đoạn tới không?")
    return questions[:3]


def _build_limitations(completeness: dict) -> list[str]:
    limitations: list[str] = []
    glucose = completeness.get("glucose") or {}
    meals = completeness.get("meals") or {}
    activities = completeness.get("activities") or {}

    glucose_pct = glucose.get("completeness_pct")
    if glucose_pct is not None and glucose_pct < _LOW_COMPLETENESS_THRESHOLD_PCT:
        limitations.append(
            f"Dữ liệu đường huyết chưa đầy đủ: {glucose.get('found', 0)}/{glucose.get('expected', 0)} lần đo "
            f"kỳ vọng ({glucose_pct}%) — các xu hướng nêu trên có thể chưa phản ánh đầy đủ thực tế."
        )

    meals_pct = meals.get("completeness_pct")
    if meals_pct is not None and meals_pct < _LOW_COMPLETENESS_THRESHOLD_PCT:
        limitations.append(
            f"Nhật ký bữa ăn chỉ ghi nhận {meals.get('found', 0)}/{meals.get('expected', 0)} bữa kỳ vọng "
            f"({meals_pct}%), có thể thiếu bối cảnh để giải thích một số chỉ số đường huyết."
        )

    if not activities.get("found"):
        limitations.append("Không có dữ liệu vận động nào được ghi nhận trong khoảng thời gian này.")

    return limitations


def _insufficient_data_message(role: str, date_range: tuple[datetime, datetime] | None, glucose_count: int) -> str:
    range_str = _format_date_range(date_range)
    if glucose_count == 0:
        if role == "doctor":
            return (
                f"Không có dữ liệu đường huyết nào trong khoảng {range_str} — chưa thể tổng hợp xu hướng lâm sàng."
            )
        return (
            f"Mình chưa thấy dữ liệu đường huyết nào được ghi nhận trong khoảng {range_str}. "
            "Hãy ghi lại chỉ số đường huyết hằng ngày để mình có thể tổng hợp và đưa ra nhận xét chính xác hơn nhé."
        )
    if role == "doctor":
        return (
            f"Chỉ có {glucose_count} lần đo đường huyết trong khoảng {range_str} — chưa đủ để tổng hợp xu hướng "
            "lâm sàng đáng tin cậy. Đề nghị nhắc bệnh nhân ghi nhận nhật ký đầy đủ hơn trước lần tái khám tới."
        )
    return (
        f"Mình mới thấy {glucose_count} lần đo đường huyết trong khoảng {range_str}, chưa đủ để nhận xét xu hướng "
        "một cách đáng tin cậy. Hãy ghi lại chỉ số đường huyết đều đặn hơn để mình hỗ trợ chính xác hơn nhé."
    )


def _fallback_response(trends: list[str], attention_points: list[str]) -> str:
    lines = ["Tóm tắt sức khỏe (tổng hợp từ dữ liệu đã ghi nhận):"]
    lines.extend(f"- {t}" for t in trends)
    if attention_points:
        lines.append("Điểm cần lưu ý:")
        lines.extend(f"- {a}" for a in attention_points)
    return "\n".join(lines)


async def synthesize_summary(state: AgentState) -> dict:
    """Drafts the doctor/patient-facing summary prose; trends/attention
    points/limitations are always computed deterministically in Python
    first (never left to the LLM) and the LLM call has a deterministic
    fallback on failure — see module docstring for the no-fabrication
    rationale."""
    if state.get("intent") != "SUMMARY":
        return {}

    tool_results = state.get("tool_results") or {}
    records = tool_results.get("records") or {}
    profile = tool_results.get("profile") or {}
    medications = tool_results.get("medications") or {}
    completeness = state.get("data_completeness") or {}
    # check_data_quality.py already computed this and stashed it in
    # tool_results — fall back to recomputing so this node stays independently
    # callable/testable even if check_data_quality didn't run first.
    metrics = tool_results.get("metrics") or calculate_health_metrics(records)

    role = state.get("user_role") or "patient"
    date_range = state.get("date_range")
    data_used = _build_data_used(metrics)
    if medications.get("active"):
        data_used.append(f"medications_active (n={len(medications['active'])})")
    if medications.get("notes"):
        data_used.append(f"medication_notes (n={len(medications['notes'])})")

    # Independent of glucose sufficiency below — a self-reported adverse reaction or missed-dose
    # pattern is safety-relevant even in a period with no/insufficient glucose logging.
    medication_attention = _build_medication_attention(medications, role)

    # check_data_quality.py already decided whether there's enough to trend
    # (design doc: "so synthesize_summary.py can say 'insufficient data'
    # instead of inventing a trend from too few points"). Fall back to a
    # bare glucose_count check when this node is unit-tested in isolation
    # (data_completeness == {}, check_data_quality never ran).
    sufficient = state.get("data_completeness", {}).get("sufficient_for_trends")
    if sufficient is None:
        sufficient = bool(metrics.get("glucose_count"))

    if not sufficient:
        # No fabricated trend when there's nothing (or too little) to trend
        # from — deterministic, no LLM call, per §2's no-fabrication rule.
        return {
            "response": _insufficient_data_message(role, date_range, metrics.get("glucose_count", 0)),
            "trends": [],
            "attention_points": medication_attention,
            "suggested_doctor_questions": [],
            "limitations": _build_limitations(completeness) or [_NO_DATA_LIMITATION],
            "data_used": data_used,
        }

    meal_response = tool_results.get("meal_glucose_response")
    trends = (
        _build_trends(metrics, role)
        + _build_meal_response_lines(meal_response, role)
        + _build_adherence_lines(tool_results.get("medication_adherence"), role)
    )
    if role == "doctor" and meal_response and meal_response.get("meals_paired"):
        data_used.append(f"meal_glucose_pairs (n={meal_response['meals_paired']})")
    attention_points = _build_attention_points(metrics) + medication_attention
    suggested_questions = _build_suggested_questions(attention_points, role)
    limitations = _build_limitations(completeness)

    prompt = SUMMARY_TEMPLATE.format(
        audience_label=AUDIENCE_LABELS.get(role, AUDIENCE_LABELS["patient"]),
        patient_name=profile.get("full_name") or "bệnh nhân",
        date_range_str=_format_date_range(date_range),
        role_label=ROLE_LABELS.get(role, ROLE_LABELS["patient"]),
        completeness_str=_format_completeness(completeness),
        medications_str=_format_bullets(_build_medication_lines(medications)),
        trends_str=_format_bullets(trends),
        attention_str=_format_bullets(attention_points),
        limitations_str=_format_bullets(limitations),
        role_instructions=ROLE_INSTRUCTIONS.get(role, ROLE_INSTRUCTIONS["patient"]),
        example_output=EXAMPLE_OUTPUTS.get(role, EXAMPLE_OUTPUTS["patient"]),
    )

    try:
        llm = get_llm("synthesize_summary")
        result = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        response_text = (getattr(result, "content", None) or "").strip() or _fallback_response(
            trends, attention_points
        )
    except Exception:
        # An LLM outage must not take down a summary the data already supports
        # — fall back to the deterministic bullet points rather than raising.
        response_text = _fallback_response(trends, attention_points)

    return {
        "response": response_text,
        "trends": trends,
        "attention_points": attention_points,
        "suggested_doctor_questions": suggested_questions,
        "limitations": limitations,
        "data_used": data_used,
    }

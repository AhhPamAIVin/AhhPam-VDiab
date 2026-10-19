"""Generate a deterministic 60-day mock dataset for one demo Type 2 diabetes patient.

Produces daily glucose / meal / activity logs across two 30-day periods (one per
appointment) with a *deliberate, traceable* behavior pattern:

  Period A (day 1-30, before appointment 1):
    - Weekday exercise adherence ~70%, weekend ~20%.
    - Dinner is "moderate" on weekdays, "heavy" (rice-heavy / eating out) on weekends.
    - After-dinner glucose runs high (often >180 mg/dL, the BYT 5481 post-prandial
      target) specifically on heavy-dinner + no-exercise days.

  Period B (day 31-60, after appointment 1's advice):
    - Weekday adherence improves (~90% exercise, lighter dinners) — doctor's advice
      is being followed on weekdays.
    - Weekend pattern barely changes (~35% exercise, dinner still heavy) — the
      Mon-Fri improvement does not carry over to Sat-Sun.

This split exists so the AI reflection step has something real to find: one insight
from appointment 1 ("post-dinner spikes on heavy-dinner/no-exercise days") should be
*updated* at appointment 2 to narrow it to weekends specifically, and a second,
genuinely new insight (occasional light-headedness after skipped/delayed lunch) is
introduced only in period B so the reflection step has a *new* insight to create too.

Run: python3 data/mock/demo_patient/generate_demo_patient.py
Output: patient_profile.json, doctor_profile.json, glucose_records.json,
        meal_records.json, activity_records.json, and a printed stats summary
        (used to write clinical_notes.json by hand, numbers must match).
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

random.seed(20260808)
# Separate RNG stream, only for "did the patient bother to log this meal at all"
# decisions. Meal logging and glucose logging are independent user actions in
# the real app (MealRecord and GlucoseRecord are unrelated tables) — a patient
# who measures glucose on a given day doesn't necessarily log what they ate,
# and vice versa. Kept on its own Random instance so adding this doesn't shift
# the main `random` sequence and invalidate the glucose stats clinical_notes.json
# already cites by exact number.
meal_log_rng = random.Random(20260810)
_BREAKFAST_LOG_P = 0.85
_LUNCH_LOG_P = 0.88
_DINNER_LOG_P = 0.92  # logged most reliably — central to the dinner-heaviness story
_ACTIVITY_LOG_P = 0.90  # patient exercised but didn't always bother logging it either

OUT_DIR = Path(__file__).parent
START_DATE = date(2026, 6, 10)
DAYS = 60
PATIENT_ID = "DEMO-P-0001"
DOCTOR_ID = "DR-0006"

DINNER_TEMPLATES = {
    "heavy": [
        "Cơm 3 chén, thịt kho tàu, canh chua, 1 lon nước ngọt",
        "Lẩu thái, bún, rau, 1 chai bia",
        "Bún chả, nem rán, 1 lon nước ngọt",
        "Cơm 3 chén, sườn xào chua ngọt, canh rau",
    ],
    "moderate": [
        "Cơm 2 chén, cá kho, rau luộc",
        "Cơm 2 chén, thịt gà kho, canh bí đỏ",
        "Cơm 2 chén, đậu hũ sốt cà, canh rau ngót",
        "Phở bò 1 tô vừa",
    ],
    "light": [
        "Cơm 1 chén, ức gà áp chảo, salad rau",
        "Canh rau củ, đậu hũ hấp, 1/2 chén cơm gạo lứt",
        "Salad cá hồi, rau trộn dầu ô liu",
        "Cháo yến mạch, trứng luộc, rau xanh",
    ],
}
BREAKFASTS = [
    "Bánh mì trứng ốp la, 1 ly sữa không đường",
    "Cháo yến mạch, 1 quả chuối",
    "Phở gà 1 tô nhỏ",
    "Xôi đậu xanh 1 phần nhỏ",
]
LUNCHES = [
    "Cơm 2 chén, cá kho, rau muống xào",
    "Bún thịt nướng, rau sống",
    "Cơm 2 chén, thịt kho trứng, canh rau",
    "Cơm gà, dưa leo",
]
EXERCISE_TYPES_WEEKDAY = ["Đi bộ buổi tối quanh khu phố", "Đi bộ trên máy ở nhà"]
EXERCISE_TYPES_WEEKEND = ["Đạp xe công viên", "Đi bộ công viên cùng vợ/chồng"]

glucose_records: list[dict] = []
meal_records: list[dict] = []
activity_records: list[dict] = []

stats = {
    "period_a": {"weekday": {"exercised": 0, "days": 0, "dinner_glu": [], "over_180": 0, "readings": 0},
                 "weekend": {"exercised": 0, "days": 0, "dinner_glu": [], "over_180": 0, "readings": 0}},
    "period_b": {"weekday": {"exercised": 0, "days": 0, "dinner_glu": [], "over_180": 0, "readings": 0},
                 "weekend": {"exercised": 0, "days": 0, "dinner_glu": [], "over_180": 0, "readings": 0}},
}

lunch_skip_days: list[int] = []  # day indices (period B only) where lunch is skipped/delayed -> dizziness signal


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


for day_idx in range(DAYS):
    d = START_DATE + timedelta(days=day_idx)
    is_weekend = d.weekday() >= 5
    period = "period_a" if day_idx < 30 else "period_b"
    bucket = "weekend" if is_weekend else "weekday"
    day_num = day_idx + 1  # 1-indexed, human-facing

    # ---- exercise adherence probability ----
    if period == "period_a":
        p_exercise = 0.20 if is_weekend else 0.70
    else:
        p_exercise = 0.35 if is_weekend else 0.90
    exercised = random.random() < p_exercise

    # ---- dinner heaviness ----
    if period == "period_a":
        heaviness = random.choices(["heavy", "moderate"], weights=[0.75, 0.25] if is_weekend else [0.30, 0.70])[0]
    else:
        if is_weekend:
            heaviness = random.choices(["heavy", "moderate"], weights=[0.70, 0.30])[0]
        else:
            heaviness = random.choices(["light", "moderate", "heavy"], weights=[0.40, 0.50, 0.10])[0]

    stats[period][bucket]["days"] += 1
    if exercised:
        stats[period][bucket]["exercised"] += 1

    # ---- glucose: fasting (07:00) ----
    fasting_trend = 138 - (16 * day_idx / (DAYS - 1))  # 138 -> 122 across 60 days
    fasting_val = round(clamp(fasting_trend + random.gauss(0, 6), 85, 200))
    skip_fasting = random.random() < 0.03

    # ---- glucose: after lunch (14:00, ~2h post 12:00 lunch) ----
    lunch_skipped_or_delayed = period == "period_b" and not is_weekend and random.random() < 0.12
    if lunch_skipped_or_delayed:
        lunch_skip_days.append(day_num)
        after_lunch_val = round(clamp(72 + random.gauss(0, 6), 58, 90))  # mild hypo-range dip
    else:
        after_lunch_val = round(clamp(158 + random.gauss(0, 10), 110, 210))
    skip_lunch_reading = random.random() < 0.03

    # ---- glucose: after dinner (21:00, ~2h post 19:00 dinner) ----
    base_map = {
        ("heavy", False): 205, ("heavy", True): 182,
        ("moderate", False): 178, ("moderate", True): 158,
        ("light", False): 160, ("light", True): 142,
    }
    dinner_base = base_map[(heaviness, exercised)]
    after_dinner_val = round(clamp(dinner_base + random.gauss(0, 8), 110, 260))
    skip_dinner_reading = random.random() < 0.04

    stats[period][bucket]["dinner_glu"].append(after_dinner_val)
    stats[period][bucket]["readings"] += 1
    if after_dinner_val > 180:
        stats[period][bucket]["over_180"] += 1

    def add_glucose(value: int, hh: int, mm: int, meal_context: str, note: str) -> None:
        glucose_records.append({
            "patient_id": PATIENT_ID,
            "value": value,
            "unit": "mg/dL",
            "measured_at": datetime.combine(d, time(hh, mm)).isoformat(),
            "meal_context": meal_context,
            "note": note,
            "measurement_source": "MANUAL",
        })

    if not skip_fasting:
        add_glucose(fasting_val, 7, 0, "FASTING", "Đo lúc đói, trước ăn sáng")
    if not skip_lunch_reading:
        note = "2h sau bữa trưa" + (" (bữa trưa bị trễ/bỏ do công việc)" if lunch_skipped_or_delayed else "")
        add_glucose(after_lunch_val, 14, 0, "AFTER_MEAL", note)
        if lunch_skipped_or_delayed:
            glucose_records[-1]["symptoms"] = "Chóng mặt nhẹ, hơi run tay"
    if not skip_dinner_reading:
        add_glucose(after_dinner_val, 21, 0, "AFTER_MEAL", f"2h sau bữa tối ({heaviness})")

    # ---- meals ----
    # Logging a meal is a separate, independently-skippable user action from
    # measuring glucose — do not assume one implies the other (see note above
    # meal_log_rng). The lunch_skipped_or_delayed case is different: that's the
    # patient actually skipping/delaying the meal itself (part of the
    # hypoglycemia story), not forgetting to log it — always logged when it
    # happens, since it's the event the story depends on.
    #
    # random.choice() calls below run unconditionally, in the same fixed order
    # every iteration, so the meal-logging skip decisions (drawn from the
    # separate meal_log_rng stream) never change how many draws the *global*
    # `random` stream consumes per day — that would desync every later day's
    # exercise/heaviness/glucose-noise draws from the numbers already cited in
    # clinical_notes.json. Compute the content first, decide whether to keep it after.
    breakfast_desc = random.choice(BREAKFASTS)
    # Matches the original call pattern exactly: no LUNCHES draw at all on a
    # lunch_skipped_or_delayed day (that branch never called random.choice
    # here), so this must stay conditional, not unconditional, or every day
    # after the first skip/delay desyncs from the already-cited glucose stats.
    lunch_desc = None if lunch_skipped_or_delayed else random.choice(LUNCHES)
    dinner_desc = random.choice(DINNER_TEMPLATES[heaviness])

    if meal_log_rng.random() < _BREAKFAST_LOG_P:
        meal_records.append({
            "patient_id": PATIENT_ID, "meal_type": "breakfast",
            "meal_time": datetime.combine(d, time(6, 45)).isoformat(),
            "description": breakfast_desc, "note": None,
        })
    if lunch_skipped_or_delayed:
        meal_records.append({
            "patient_id": PATIENT_ID, "meal_type": "lunch",
            "meal_time": datetime.combine(d, time(14, 30)).isoformat(),
            "description": "Bỏ bữa trưa đúng giờ, ăn trễ 1 ít bánh quy lúc 14h30",
            "note": "Bận họp, ăn trễ hơn thường lệ",
        })
    elif meal_log_rng.random() < _LUNCH_LOG_P:
        meal_records.append({
            "patient_id": PATIENT_ID, "meal_type": "lunch",
            "meal_time": datetime.combine(d, time(12, 0)).isoformat(),
            "description": lunch_desc, "note": None,
        })
    if meal_log_rng.random() < _DINNER_LOG_P:
        meal_records.append({
            "patient_id": PATIENT_ID, "meal_type": "dinner",
            "meal_time": datetime.combine(d, time(19, 0)).isoformat(),
            "description": dinner_desc, "note": None,
        })

    # ---- activity ----
    # Same independence: exercising and logging that exercise are separate
    # actions. `exercised` already drives the glucose math (§ dinner_base
    # above) regardless of whether the patient then logged it in the app.
    # Same RNG-ordering care as the meals block: draw unconditionally whenever
    # exercised (as before), decide whether to log via the independent stream.
    activity_type = random.choice(EXERCISE_TYPES_WEEKEND if is_weekend else EXERCISE_TYPES_WEEKDAY) if exercised else None
    duration = random.randint(25, 45) if exercised else None
    if exercised and meal_log_rng.random() < _ACTIVITY_LOG_P:
        activity_records.append({
            "patient_id": PATIENT_ID,
            "activity_type": activity_type,
            "duration_minutes": duration,
            "activity_time": datetime.combine(d, time(20, 15)).isoformat(),
            "note": None,
        })

patient_profile = {
    "patient_id": PATIENT_ID,
    "display_name": "Trần Văn Bình",
    "age_years": 52,
    "sex": "Male",
    "height_cm": 168,
    "weight_kg": 78,
    "bmi": 27.6,
    "bmi_category": "Overweight",
    "diabetes_status": "TYPE_2",
    "diagnosed_years_ago": 2,
    "hba1c_pct_baseline": 7.4,
    "hba1c_baseline_date": (START_DATE - timedelta(days=14)).isoformat(),
    "fasting_glucose_lab_mg_dl_baseline": 148,
    "comorbidities": ["Tăng huyết áp nhẹ (kiểm soát bằng Amlodipine 5mg/ngày)"],
    "medications": [
        {"drug_name": "Metformin", "dose": "850mg", "frequency": "2 lần/ngày, uống cùng bữa sáng và bữa tối"},
        {"drug_name": "Amlodipine", "dose": "5mg", "frequency": "1 lần/ngày, buổi sáng"},
    ],
    "doctor_id": DOCTOR_ID,
    "note": "Hồ sơ demo, lấy cảm hứng từ schema data/NHANES/deployment_dataset/patient_profiles.csv; "
            "các chỉ số lâm sàng nền và nhân khẩu học là dàn dựng hợp lý, không phải một bản ghi NHANES thật.",
}

doctor_profile = {
    "doctor_id": DOCTOR_ID,
    "doctor_display_name": "Võ Ngọc Long",
    "specialty": "ENDOCRINOLOGIST_DIABETOLOGIST",
    "note": "Lấy nguyên từ data/NHANES/deployment_dataset/doctor_profiles.csv (danh tính demo tổng hợp).",
}

for name, records in [("patient_profile", [patient_profile]), ("doctor_profile", [doctor_profile]),
                       ("glucose_records", glucose_records), ("meal_records", meal_records),
                       ("activity_records", activity_records)]:
    data = records[0] if name in ("patient_profile", "doctor_profile") else records
    with open(OUT_DIR / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

print(f"glucose_records: {len(glucose_records)}, meal_records: {len(meal_records)}, "
      f"activity_records: {len(activity_records)}")
print(f"lunch skip/delay days (period B weekdays): {lunch_skip_days}")
print()
for period in ("period_a", "period_b"):
    for bucket in ("weekday", "weekend"):
        s = stats[period][bucket]
        avg_dinner = sum(s["dinner_glu"]) / len(s["dinner_glu"])
        pct_ex = 100 * s["exercised"] / s["days"]
        pct_over = 100 * s["over_180"] / s["readings"]
        print(f"{period:10s} {bucket:8s} days={s['days']:2d}  exercise_adherence={pct_ex:5.1f}%  "
              f"avg_after_dinner_glucose={avg_dinner:6.1f} mg/dL  pct_readings_over_180={pct_over:5.1f}%")

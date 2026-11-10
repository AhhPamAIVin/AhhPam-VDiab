"""Bring the demo database up to date and give the demo doctor's panel a story.

Two parts, both safe to re-run (run it again before every demo):

1. Every patient. migrate_nhanes_demo_data.py's records go stale a day at a
   time, and the charts only look back 24 h / 7 days, so a few weeks after
   seeding every chart is empty. Each patient's glucose, meal, blood
   pressure, activity and medication-note rows move forward by whole days
   (time of day and every gap between records are kept) so the latest one
   lands within the last day. Older NHANES seeds also wrote local wall-clock
   times as if they were UTC — a fasting reading at 06:30 showed as 13:30 —
   which is detected per patient (most FASTING readings at a UTC hour that
   is daytime in Vietnam) and corrected once.

2. The demo doctor's five patients. The NHANES simulator series does not
   suit a demo: it repeats the same day's values every few days, and its
   level (about 200 mg/dL) has nothing to do with the visit and the HbA1c the
   panel shows. For these patients every glucose, meal, blood pressure,
   activity and missed-dose record is replaced by 90 days written from a
   scenario that fits their record (age, drugs, baseline HbA1c):
   - a completed visit 30 days ago with its clinical note and HbA1c; the 60
     days before it average the glucose that HbA1c implies (ADAG: mean
     glucose mg/dL = 28.7 x HbA1c - 46.7, Nathan et al., Diabetes Care 2008);
   - what changed after the visit, e.g. basal insulin being titrated
     (fasting glucose falls over a few weeks) or doses being missed;
   - a fixed measuring routine (fasting every morning, after dinner most
     evenings, after lunch every other day), day-to-day fasting variation
     of about 5-8 % (Lilly basal insulin trials report a between-day fasting
     SD of 14-19 mg/dL) and 2-hour post-meal rises of 35-55 mg/dL;
   - an upcoming appointment, three of them inside the 48 h brief window.
   Each reading goes through threshold_service like one entered in the app.
   Then, unless --skip-llm, the app's own generators run: pre-visit briefs,
   insights from the visit notes, the demo patient's weekly report and
   patient summary. These call the configured LLM.

All of this is synthetic demo data. Refuses to run with APP_ENV=production.

Usage:
    python -m scripts.refresh_demo_data [--dry-run] [--skip-llm]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, col, select

import codebase.backend.main  # noqa: F401  (registers every model on SQLModel.metadata)
from codebase.backend.appointments.briefs import generate_brief
from codebase.backend.appointments.models import Appointment, AppointmentStatus, PrevisitBrief
from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.db import create_db_and_tables, engine
from codebase.backend.health.models import (
    ActivityRecord,
    BloodPressureRecord,
    GlucoseRecord,
    MealContext,
    MealInputSource,
    MealRecord,
    MealType,
    MeasurementSource,
)
from codebase.backend.health.threshold_service import evaluate_glucose_reading
from codebase.backend.insights.appointment_reflection import reflect_on_clinical_note
from codebase.backend.medications.models import Medication, MedicationNote, MedicationNoteType, MedicationStatus
from codebase.backend.patients.models import CarePlanTarget, ClinicalNote, PatientAssignment
from codebase.backend.reports.service import generate_weekly_report, last_complete_week
from codebase.backend.summaries.service import generate_and_store_snapshot
from scripts.seed_demo_doctor_showcase import DEMO_PATIENT_EMAIL, _pick_doctor_and_patients, _reassign_patients

logger = logging.getLogger("scripts.refresh_demo_data")

# Fixed namespace so appointment / clinical note ids are the same on every
# run: a re-run updates them in place.
_ID_NAMESPACE = uuid.UUID("6f1d3c2e-9a4b-4c1e-8f5d-2b7a9e0c4d11")
HISTORY_DAYS = 90
VISIT_DAYS_AGO = 30


def _stable_id(*parts: object) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, ":".join(str(p) for p in parts))


# ---------------------------------------------------------------------------
# Part 1: every patient
# ---------------------------------------------------------------------------

# A row written by migrate_nhanes_demo_data.py: created_at equals the event
# time and the note carries the simulator id.
_NHANES_GLUCOSE = "measurement_source = 'SIMULATOR' AND created_at = measured_at"
_NHANES_MEAL = "created_at = meal_time AND note LIKE 'sim:%'"

# (table, event-time column) for every per-patient time series.
_SERIES = (
    ("glucose_records", "measured_at"),
    ("meal_records", "meal_time"),
    ("blood_pressure_records", "measured_at"),
    ("activity_records", "activity_time"),
    ("medication_notes", "occurred_at"),
)


def _fix_local_times(session: Session, utc_offset: timedelta) -> int:
    """NHANES rows stored as local wall-clock time: most FASTING readings at a
    UTC hour that is daytime locally. Shift those patients' NHANES rows back
    by the UTC offset. Idempotent: after the fix the same test is false."""
    offset_hours = utc_offset.total_seconds() / 3600
    rows = session.execute(
        text(
            f"""
            SELECT patient_id
            FROM glucose_records
            WHERE {_NHANES_GLUCOSE} AND meal_context = 'FASTING'
            GROUP BY patient_id
            HAVING count(*) >= 5
               AND avg(CASE WHEN extract(hour FROM measured_at) BETWEEN 3 AND 12 THEN 1 ELSE 0 END) > 0.5
            """
        )
    ).all()
    ids = [r[0] for r in rows]
    if not ids:
        return 0
    shift = f"interval '{offset_hours} hours'"
    session.execute(
        text(
            f"UPDATE glucose_records SET measured_at = measured_at - {shift}, created_at = created_at - {shift} "
            f"WHERE patient_id = ANY(:ids) AND {_NHANES_GLUCOSE}"
        ),
        {"ids": ids},
    )
    session.execute(
        text(
            f"UPDATE meal_records SET meal_time = meal_time - {shift}, created_at = created_at - {shift} "
            f"WHERE patient_id = ANY(:ids) AND {_NHANES_MEAL}"
        ),
        {"ids": ids},
    )
    return len(ids)


def _shift_days(session: Session, shifts: dict[uuid.UUID, int]) -> None:
    """Move rows forward by whole days per patient. created_at moves with the
    event (the pre-visit brief's "new data" check reads it) but never past now."""
    shifts = {pid: days for pid, days in shifts.items() if days}
    if not shifts:
        return
    session.execute(text("CREATE TEMP TABLE IF NOT EXISTS demo_shift (patient_id uuid PRIMARY KEY, days int)"))
    session.execute(text("TRUNCATE demo_shift"))
    session.execute(
        text("INSERT INTO demo_shift (patient_id, days) VALUES (:pid, :days)"),
        [{"pid": pid, "days": days} for pid, days in shifts.items()],
    )
    now_sql = "(now() AT TIME ZONE 'utc')"
    for table, column in _SERIES:
        session.execute(
            text(
                f"""
                UPDATE {table} t
                SET {column} = t.{column} + s.days * interval '1 day',
                    created_at = least(t.created_at + s.days * interval '1 day', {now_sql})
                FROM demo_shift s
                WHERE t.patient_id = s.patient_id
                """
            )
        )
    # The synthetic regimen (start/stop of a drug inside the seeded window)
    # moves with the records it explains. Long-standing drugs keep their date.
    session.execute(
        text(
            f"""
            UPDATE medications m
            SET start_date = m.start_date + s.days * interval '1 day',
                end_date = least(m.end_date + s.days * interval '1 day', {now_sql})
            FROM demo_shift s
            WHERE m.patient_id = s.patient_id AND m.note LIKE '[SYNTHETIC DEMO DATA]%'
            """
        )
    )


def _latest_event_per_patient(session: Session) -> dict[uuid.UUID, datetime]:
    union = " UNION ALL ".join(f"SELECT patient_id, max({c}) AS t FROM {t} GROUP BY patient_id" for t, c in _SERIES)
    rows = session.execute(text(f"SELECT patient_id, max(t) FROM ({union}) x GROUP BY patient_id")).all()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def refresh_all_patients(session: Session, *, now: datetime, skip: set[uuid.UUID], utc_offset: timedelta) -> dict:
    fixed = _fix_local_times(session, utc_offset)
    latest = _latest_event_per_patient(session)
    shifts = {pid: (now - ts).days for pid, ts in latest.items() if pid not in skip and ts < now}
    _shift_days(session, shifts)
    moved = sum(1 for d in shifts.values() if d)
    return {"timezone_fixed": fixed, "patients_moved": moved, "max_days": max(shifts.values(), default=0)}


# ---------------------------------------------------------------------------
# Part 2: the demo doctor's panel
# ---------------------------------------------------------------------------


def mean_glucose_for_a1c(a1c: float) -> float:
    """ADAG (Nathan et al., Diabetes Care 2008): estimated average glucose."""
    return 28.7 * a1c - 46.7


# With this routine the readings' mean sits about this far above fasting
# (fasting, +8 before lunch, +40-55 two hours after a meal, +25 at bedtime).
FASTING_BELOW_MEAN = 25.0
# Carb load of the meal (1 light .. 3 heavy) on the 2-hour reading.
LOAD_EFFECT = {1: -10.0, 2: 0.0, 3: 12.0}

# (description, portion, carb load 1-3)
BREAKFASTS = [
    ("Phở bò", "1 tô vừa", 3),
    ("Bánh mì trứng ốp la", "1 ổ", 2),
    ("Xôi lạc", "1 gói nhỏ", 3),
    ("Cháo yến mạch nấu sữa không đường", "1 bát", 1),
    ("Bún riêu cua", "1 tô", 2),
    ("Khoai lang luộc và 1 quả trứng luộc", "1 củ vừa", 2),
    ("Bánh cuốn chả", "1 đĩa", 2),
    ("Sữa đậu nành không đường, 2 lát bánh mì đen", "1 cốc", 1),
]
MAINS = [
    ("Cơm gạo lứt, cá thu kho, rau muống luộc", "1 bát cơm", 2),
    ("Cơm trắng, thịt gà luộc, canh bí xanh", "1 bát cơm", 2),
    ("Bún chả", "1 suất", 3),
    ("Cơm, đậu phụ sốt cà chua, cải ngọt xào", "1 bát cơm", 2),
    ("Cơm, tôm rim, canh rau ngót", "1 bát cơm", 2),
    ("Miến gà", "1 bát", 2),
    ("Cơm, thịt lợn luộc, canh chua cá", "2 bát cơm", 3),
    ("Cơm tấm sườn nướng", "1 đĩa", 3),
    ("Canh cá nấu chua, rau luộc", "nửa bát cơm", 1),
    ("Mì xào bò", "1 đĩa", 3),
    ("Cơm, cá rô phi hấp, su su luộc", "1 bát cơm", 2),
    ("Lẩu nấm rau, ít bún", "1 bát bún", 1),
]
SNACKS = [
    ("1 quả táo", "1 quả", 1),
    ("Sữa chua không đường", "1 hộp", 1),
    ("Hạt điều rang", "1 nắm nhỏ", 1),
    ("Chè đỗ đen", "1 cốc", 3),
    ("1 quả chuối", "1 quả", 2),
]


@dataclass
class Scenario:
    key: str
    final_status: str
    visit_a1c: float  # HbA1c at the visit 30 days ago; sets the 60 days before it
    fasting_after: float | None = None  # fasting level the post-visit change settles at
    settle_days: float = 12.0  # time constant of that change
    drift_from: int | None = None  # post-visit day a slow rise starts (missed doses)
    drift_per_day: float = 0.0
    fasting_sd: float = 7.0
    post_sd: float = 10.0
    lunch_rise: float = 40.0
    dinner_rise: float = 45.0
    heavy_dinner_prob: float = 0.2
    heavy_dinner_extra: float = 0.0
    # (centre, half-range): a steady patient whose readings all stay near
    # centre, instead of the fasting/post-meal model. See steady_values().
    steady_band: tuple[float, float] | None = None
    lunch_every_day: bool = False
    bp: tuple[float, float] = (128.0, 80.0)
    bp_daily: bool = False
    bp_after: tuple[float, float] | None = None
    activity: tuple[str, int, float, time] = ("Đi bộ", 30, 0.5, time(17, 30))
    missed_days: tuple[int, ...] = ()  # post-visit days (0 = visit day + 1 .. 29 = today)
    missed_before_every: int = 0  # before the visit: one missed evening dose every N days
    missed_drug: str = "METFORMIN"
    missed_text: str = "Quên uống thuốc buổi tối"
    hypo_day: int | None = None  # post-visit day of one bedtime hypoglycaemia
    danger_days: tuple[int, ...] = ()  # post-visit days with a post-dinner reading over the danger line
    # The latest reading, an extra check outside the routine; it sets the
    # panel badge. None: a little above today's fasting level.
    final_value: float | None = None
    final_note: str | None = None
    final_symptoms: str | None = None
    appt_offset_days: int = 7
    appt_time: time = time(9, 0)
    appt_reason: str = "Tái khám định kỳ"
    visit: dict = field(default_factory=dict)
    run_llm: bool = False

    @property
    def fasting_before(self) -> float:
        return mean_glucose_for_a1c(self.visit_a1c) - FASTING_BELOW_MEAN

    def fasting_level(self, t: int) -> float:
        """Underlying fasting glucose on post-visit day t (negative = before the visit)."""
        level = self.fasting_before
        if t < 0:
            return level
        if self.fasting_after is not None:
            level = self.fasting_after + (level - self.fasting_after) * math.exp(-(t + 1) / self.settle_days)
        if self.drift_from is not None and t >= self.drift_from:
            level += self.drift_per_day * (t - self.drift_from + 1)
        return level


SCENARIOS: dict[str, Scenario] = {
    # 75 M, heart failure, basal insulin (since January) + metformin. HbA1c
    # 8.5 % at the baseline survey six months ago; now steady and on target
    # (ADA 2025 older adults with complex health: A1C < 8 %, pre-meal
    # 90-150 mg/dL, bedtime 100-180 mg/dL).
    "steady": Scenario(
        key="steady",
        final_status="NORMAL",
        visit_a1c=6.9,
        steady_band=(150.0, 10.0),
        fasting_sd=5,
        post_sd=7,
        lunch_rise=36,
        dinner_rise=40,
        heavy_dinner_prob=0.1,
        bp=(128.0, 76.0),
        bp_daily=True,
        activity=("Đi bộ", 30, 0.8, time(17, 30)),
        appt_offset_days=1,
        appt_time=time(9, 0),
        appt_reason="Tái khám định kỳ, xét nghiệm lại HbA1c",
        visit={
            "reason": "Tái khám định kỳ đái tháo đường típ 2",
            "hpi": (
                "Tự đo đường huyết tại nhà đều đặn mỗi ngày: các lần đo lúc đói và sau ăn đều trong khoảng 140-160 mg/dL. "
                "Không có cơn hạ đường huyết. Uống thuốc và tiêm insulin đầy đủ, ăn uống điều độ, đi bộ mỗi "
                "chiều. Không khó thở khi đi lại, không phù chân."
            ),
            "exam": "Tỉnh, tiếp xúc tốt. Không phù chi dưới. Tim đều, phổi không ran. Bàn chân không loét.",
            "diagnosis": "Đái tháo đường típ 2 đạt mục tiêu đường huyết; suy tim mạn ổn định",
            "plan": (
                "Tiếp tục phác đồ hiện tại (insulin nền, metformin). Duy trì chế độ ăn và đi bộ 30 phút mỗi "
                "chiều. Đo đường huyết lúc đói mỗi sáng và 2 giờ sau ăn tối, ghi trên VDiab. Tái khám sau 1 tháng."
            ),
        },
        run_llm=True,
    ),
    # 67 M, BMI 21, rapid-acting insulin + empagliflozin + metformin, HbA1c 8.0 %.
    "unstable": Scenario(
        key="unstable",
        final_status="DANGER",
        visit_a1c=8.3,
        fasting_sd=16,
        post_sd=22,
        lunch_rise=50,
        dinner_rise=55,
        heavy_dinner_prob=0.4,
        heavy_dinner_extra=15,
        lunch_every_day=True,
        bp=(124.0, 76.0),
        activity=("Đi bộ", 20, 0.35, time(17, 0)),
        missed_days=(20, 21),
        missed_drug="INSULIN LISPRO",
        missed_text="Quên tiêm insulin trước bữa tối",
        hypo_day=13,
        danger_days=(23,),
        final_value=268,
        final_note="Đo thêm vì thấy mệt",
        final_symptoms="Khát nước, mệt mỏi",
        appt_offset_days=1,
        appt_time=time(10, 30),
        appt_reason="Khám sớm: đường huyết sau ăn vượt ngưỡng nguy hiểm",
        visit={
            "reason": "Tái khám định kỳ",
            "hpi": (
                "Đường huyết dao động nhiều: lúc đói 140-200 mg/dL, sau ăn có lúc trên 240 mg/dL. Có lần run tay, "
                "vã mồ hôi buổi tối vào hôm ăn ít mà vẫn tiêm insulin nhanh. Sụt 1 kg trong 2 tháng."
            ),
            "exam": "Tỉnh, thể trạng gầy. Tim đều, phổi trong. Không mất nước.",
            "diagnosis": "Đái tháo đường típ 2 kiểm soát kém, dao động đường huyết lớn, có nguy cơ hạ đường huyết",
            "plan": (
                "Hướng dẫn tiêm insulin nhanh theo bữa ăn thực tế, không tiêm khi bỏ bữa. Đo đường huyết trước và "
                "sau các bữa chính. Hướng dẫn xử trí hạ đường huyết. Báo bác sĩ ngay khi đường huyết trên 250 "
                "mg/dL. Tái khám sau 1 tháng."
            ),
        },
    ),
    # 76 F, BMI 40, metformin, HbA1c 8.1 %.
    "rising": Scenario(
        key="rising",
        final_status="WATCH",
        visit_a1c=7.9,
        fasting_after=142,
        settle_days=8,
        drift_from=19,
        drift_per_day=3.2,
        bp=(138.0, 84.0),
        activity=("Đi bộ", 20, 0.4, time(16, 30)),
        missed_days=(19, 21, 22, 24, 26, 27),
        missed_text="Quên uống metformin buổi tối",
        final_value=212,
        final_note="Đo thêm buổi chiều",
        appt_offset_days=0,
        appt_time=time(15, 0),
        appt_reason="Đường huyết lúc đói tăng dần trong 10 ngày gần đây",
        visit={
            "reason": "Tái khám định kỳ",
            "hpi": "Đường huyết lúc đói 145-170 mg/dL. Ăn nhiều tinh bột, ít vận động do đau khớp gối.",
            "exam": "Tỉnh. Béo phì (BMI 40). Tim đều, phổi không ran. Khớp gối hai bên không sưng.",
            "diagnosis": "Đái tháo đường típ 2 chưa đạt mục tiêu; béo phì; thoái hóa khớp gối",
            "plan": (
                "Tiếp tục metformin, uống đủ hai lần mỗi ngày. Giảm cơm và đồ ngọt, đi bộ nhẹ 20 phút mỗi ngày. "
                "Đo đường huyết lúc đói mỗi sáng. Tái khám sau 1 tháng."
            ),
        },
        run_llm=True,
    ),
    # 80 F, basal + rapid-acting insulin, HbA1c 5.6 % at baseline.
    "stable": Scenario(
        key="stable",
        final_status="NORMAL",
        visit_a1c=6.3,
        lunch_rise=35,
        dinner_rise=38,
        bp=(132.0, 76.0),
        activity=("Tập dưỡng sinh", 30, 0.6, time(6, 0)),
        appt_offset_days=6,
        appt_time=time(8, 30),
        visit={
            "reason": "Tái khám định kỳ",
            "hpi": (
                "Đường huyết ổn định, lúc đói 95-120 mg/dL, sau ăn dưới 170 mg/dL. Không có cơn hạ đường huyết. "
                "Con gái hỗ trợ tiêm insulin và ghi chỉ số."
            ),
            "exam": "Tỉnh, đi lại chậm. Tim đều, phổi không ran. Vị trí tiêm không loạn dưỡng mỡ.",
            "diagnosis": "Đái tháo đường típ 2 dùng insulin, kiểm soát tốt; người cao tuổi nguy cơ hạ đường huyết",
            "plan": (
                "Giữ mục tiêu đường huyết phù hợp tuổi, ưu tiên tránh hạ đường huyết. Tiếp tục phác đồ insulin "
                "hiện tại. Tái khám sau 5 tuần."
            ),
        },
    ),
    # 60 F, BMI 33.5, metformin, HbA1c 5.6 % at baseline.
    "dinner_spikes": Scenario(
        key="dinner_spikes",
        final_status="NORMAL",
        visit_a1c=6.6,
        dinner_rise=55,
        heavy_dinner_prob=0.55,
        heavy_dinner_extra=18,
        bp=(134.0, 86.0),
        activity=("Đi bộ sau ăn tối", 20, 0.45, time(19, 30)),
        appt_offset_days=9,
        appt_time=time(14, 0),
        appt_reason="Tái khám định kỳ, xem lại bữa tối",
        visit={
            "reason": "Tái khám định kỳ",
            "hpi": (
                "Đường huyết lúc đói ổn định 105-125 mg/dL. Ăn tối muộn và nhiều cơm, 2 giờ sau ăn tối thường "
                "180-210 mg/dL."
            ),
            "exam": "Tỉnh. Thừa cân. Tim đều, phổi trong. Không phù.",
            "diagnosis": "Đái tháo đường típ 2; tăng đường huyết sau ăn bữa tối",
            "plan": (
                "Tiếp tục metformin. Giảm lượng cơm bữa tối, ăn rau trước, đi bộ 15-20 phút sau ăn tối. Đo đường "
                "huyết 2 giờ sau ăn tối để theo dõi. Tái khám sau 6 tuần."
            ),
        },
    ),
}

# Which scenario fits which demo account (age, drugs, baseline HbA1c). Any
# other panel member gets the scenarios left over, in this order.
SCENARIO_BY_EMAIL = {
    DEMO_PATIENT_EMAIL: "steady",
    "khoidm_0101@gmail.com": "unstable",
    "minhnn_0053@gmail.com": "rising",
    "hungbk_0279@gmail.com": "stable",
    "nganpk_0073@gmail.com": "dinner_spikes",
}
PANEL_SIZE = len(SCENARIOS)


@dataclass
class Clock:
    now: datetime  # naive UTC
    tz: ZoneInfo

    @property
    def today(self) -> date:
        return self.now.replace(tzinfo=UTC).astimezone(self.tz).date()

    def utc(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, tzinfo=self.tz).astimezone(UTC).replace(tzinfo=None)


def _jitter(rng: random.Random, at: time, minutes: int) -> time:
    total = at.hour * 60 + at.minute + rng.randint(-minutes, minutes)
    total = max(0, min(total, 23 * 60 + 59))
    return time(total // 60, total % 60)


def select_panel(session: Session) -> tuple[User, list[tuple[User, Scenario]]]:
    """The demo doctor's current patients, demo patient first. Only when that
    panel is incomplete does seed_demo_doctor_showcase.py pick one."""
    doctor, picked = _pick_doctor_and_patients(session)
    current = list(
        session.exec(
            select(User)
            .join(PatientAssignment, col(PatientAssignment.patient_id) == col(User.id))
            .where(PatientAssignment.doctor_id == doctor.id)
        ).all()
    )
    emails = {p.email for p in current}
    if len(current) < PANEL_SIZE or DEMO_PATIENT_EMAIL not in emails:
        admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
        _reassign_patients(session, doctor, picked, admin or doctor)
        current = picked
    current.sort(key=lambda p: (p.email != DEMO_PATIENT_EMAIL, p.email not in SCENARIO_BY_EMAIL, p.email))
    current = current[:PANEL_SIZE]
    taken = {SCENARIO_BY_EMAIL[p.email] for p in current if p.email in SCENARIO_BY_EMAIL}
    spare = [key for key in SCENARIOS if key not in taken]
    panel = []
    for patient in current:
        key = SCENARIO_BY_EMAIL.get(patient.email) or spare.pop(0)
        panel.append((patient, SCENARIOS[key]))
    return doctor, panel


def _clean_panel_patient(session: Session, patient_id: uuid.UUID) -> None:
    """Everything the 90 days replace, and what was derived from it."""
    params = {"pid": patient_id}
    for table in (
        "doctor_watch_reviews",
        "meal_analyses",
        "glucose_records",
        "meal_records",
        "blood_pressure_records",
        "activity_records",
        "medication_notes",
        "patient_weekly_reports",
        "insight_feedback",
        "patient_insights",
    ):
        session.execute(text(f"DELETE FROM {table} WHERE patient_id = :pid"), params)
    session.execute(
        text("DELETE FROM notifications WHERE patient_id = :pid AND notification_type = 'WEEKLY_REPORT'"), params
    )


def _delete_insight_vectors(patient_ids: list[uuid.UUID]) -> None:
    from codebase.models.agents.tools.insight_tools import get_insights_collection

    collection = get_insights_collection()
    for patient_id in patient_ids:
        collection.delete(where={"patient_id": str(patient_id)})


def _active_care_plan(session: Session, patient_id: uuid.UUID) -> CarePlanTarget | None:
    return session.exec(
        select(CarePlanTarget)
        .where(CarePlanTarget.patient_id == patient_id, CarePlanTarget.effective_to.is_(None))
        .order_by(col(CarePlanTarget.version).desc())
    ).first()


def _evaluate(session: Session, record: GlucoseRecord) -> None:
    """Same enrichment health/service.py::create_glucose_record does."""
    evaluation = evaluate_glucose_reading(session, record)
    record.predicted_min_mg_dl = evaluation.predicted_min_mg_dl
    record.predicted_max_mg_dl = evaluation.predicted_max_mg_dl
    record.threshold_status = evaluation.status
    record.threshold_evaluation_source = "ML_QUANTILE_V2" if evaluation.ml_called else "DOCTOR_RULE_FALLBACK"
    if evaluation.status == "DANGER":
        record.threshold_evaluation_source = "DOCTOR_DANGER_RULE"
    if evaluation.status == "UNAVAILABLE":
        record.threshold_evaluation_source = "UNAVAILABLE"
    record.threshold_fallback_reason = evaluation.fallback_reason
    record.care_plan_target_version = evaluation.care_plan_target_version
    session.add(record)


def _add_glucose(
    session: Session,
    rng: random.Random,
    patient: User,
    *,
    value: float,
    at: datetime,
    context: MealContext | None,
    note: str | None = None,
    symptoms: str | None = None,
) -> GlucoseRecord:
    record = GlucoseRecord(
        patient_id=patient.id,
        value=round(max(40.0, min(value, 450.0))),
        measured_at=at,
        note=note,
        meal_context=context,
        measurement_source=MeasurementSource.GLUCOSE_METER,
        symptoms=symptoms,
        created_by=patient.id,
        created_at=at + timedelta(minutes=rng.randint(1, 6)),
    )
    session.add(record)
    session.flush()
    _evaluate(session, record)
    return record


def _pick_meal(rng: random.Random, menu: list[tuple[str, str, int]], heavy_prob: float) -> tuple[str, str, int]:
    pool = [m for m in menu if m[2] == 3] if rng.random() < heavy_prob else [m for m in menu if m[2] < 3]
    return rng.choice(pool)


def _drug_for(session: Session, patient_id: uuid.UUID, keyword: str) -> Medication | None:
    active = session.exec(
        select(Medication).where(Medication.patient_id == patient_id, Medication.status == MedicationStatus.ACTIVE)
    ).all()
    for medication in active:
        if keyword.upper() in medication.drug_name.upper():
            return medication
    return active[0] if active else None


# Steady patient: small shift by context on top of the shared level.
STEADY_CONTEXT_SHIFT = {
    MealContext.FASTING: -1.5,
    MealContext.BEFORE_MEAL: -0.8,
    MealContext.AFTER_MEAL: 1.5,
    MealContext.BEFORE_SLEEP: 0.4,
}


def steady_values(rng: random.Random, centre: float, half: float):
    """A steady patient's level: an AR(1) process around `centre`, so each
    reading stays close to the one before (phi 0.85, median step about
    2 mg/dL), drifts a little and turns irregularly; SD about 0.35 x half
    (CV ~2.5 % at 150), so it stays inside centre +- half without being
    clipped. Yields the deviation from centre."""
    phi = 0.85
    sd = half * 0.35
    innovation_sd = sd * math.sqrt(1 - phi**2)
    deviation = rng.gauss(0, sd)
    while True:
        deviation = phi * deviation + rng.gauss(0, innovation_sd)
        yield deviation


def _generate_history(session: Session, patient: User, scenario: Scenario, clock: Clock) -> dict:
    rng = random.Random(f"{patient.id}:{scenario.key}")
    counts = {"glucose": 0, "meals": 0, "bp": 0, "activity": 0, "missed": 0}
    missed_drug = _drug_for(session, patient.id, scenario.missed_drug)
    cutoff = clock.now - timedelta(minutes=10)
    first_day = clock.today - timedelta(days=HISTORY_DAYS - 1)
    first_post_day = HISTORY_DAYS - VISIT_DAYS_AGO  # day index of visit day + 1
    steady = (
        steady_values(random.Random(f"{patient.id}:steady"), *scenario.steady_band) if scenario.steady_band else None
    )

    def missed(t: int) -> bool:
        if t >= 0:
            return t in scenario.missed_days
        return scenario.missed_before_every > 0 and t % scenario.missed_before_every == 3

    for i in range(HISTORY_DAYS):
        day = first_day + timedelta(days=i)
        t = i - first_post_day
        weekday = day.weekday()  # Monday = 0
        fasting = scenario.fasting_level(t)
        # One shared offset per day: a bad day is bad all day.
        day_effect = rng.gauss(0, scenario.fasting_sd * 0.6)
        missed_yesterday, missed_today = missed(t - 1), missed(t)

        meals: dict[MealType, tuple[str, str, int]] = {}
        plan = [
            (MealType.BREAKFAST, time(6, 50), BREAKFASTS, 0.25),
            (MealType.LUNCH, time(11, 45), MAINS, 0.25),
            (MealType.DINNER, time(18, 40), MAINS, scenario.heavy_dinner_prob),
        ]
        if weekday in (1, 4, 6):
            plan.append((MealType.SNACK, time(15, 30), SNACKS, 0.15))
        for meal_type, at, menu, heavy in plan:
            when = clock.utc(day, _jitter(rng, at, 15))
            choice = _pick_meal(rng, menu, heavy)
            meals[meal_type] = choice
            if when > cutoff:
                continue
            session.add(
                MealRecord(
                    patient_id=patient.id,
                    meal_type=meal_type,
                    meal_time=when,
                    description=choice[0],
                    portion=choice[1],
                    input_source=MealInputSource.TEXT,
                    created_by=patient.id,
                    created_at=when + timedelta(minutes=rng.randint(2, 30)),
                )
            )
            counts["meals"] += 1

        def noise(sd: float) -> float:
            return day_effect + rng.gauss(0, sd * 0.8)

        lunch_load = meals[MealType.LUNCH][2]
        dinner_load = meals[MealType.DINNER][2]
        after_lunch = fasting + scenario.lunch_rise + LOAD_EFFECT[lunch_load] + noise(scenario.post_sd)
        after_dinner = fasting + scenario.dinner_rise + LOAD_EFFECT[dinner_load] + noise(scenario.post_sd)
        if dinner_load == 3:
            after_dinner += scenario.heavy_dinner_extra
        if missed_today:
            after_dinner += 15
        if t in scenario.danger_days:
            after_dinner = max(after_dinner, 258)

        # A fixed routine, as a patient keeps it: fasting every morning, after
        # dinner most evenings, after lunch every other day, the odd bedtime check.
        readings: list[tuple[time, MealContext, float, str | None, str | None]] = []
        if rng.random() > 0.04:
            note = "Tối qua quên thuốc" if missed_yesterday else None
            readings.append(
                (
                    time(6, 30),
                    MealContext.FASTING,
                    fasting + noise(scenario.fasting_sd) + (18 if missed_yesterday else 0),
                    note,
                    None,
                )
            )
        if weekday in (1, 3) and not scenario.lunch_every_day:
            readings.append(
                (time(11, 15), MealContext.BEFORE_MEAL, fasting + 8 + noise(scenario.fasting_sd), None, None)
            )
        if scenario.lunch_every_day or weekday in (0, 2, 4, 6):
            readings.append((time(13, 50), MealContext.AFTER_MEAL, after_lunch, None, None))
        if rng.random() > 0.1 or t in scenario.danger_days:
            note = "Bữa tối ăn nhiều cơm" if dinner_load == 3 else None
            readings.append((time(20, 45), MealContext.AFTER_MEAL, after_dinner, note, None))
        if scenario.hypo_day is not None and t == scenario.hypo_day:
            readings.append(
                (time(22, 5), MealContext.BEFORE_SLEEP, 62, "Đã uống 1 cốc nước đường", "Run tay, vã mồ hôi")
            )
        elif weekday in (2, 5):
            readings.append((time(22, 5), MealContext.BEFORE_SLEEP, after_dinner - 20 + rng.gauss(0, 6), None, None))

        for at, context, value, note, symptoms in readings:
            when = clock.utc(day, _jitter(rng, at, 10))
            if when <= cutoff:
                if steady is not None:
                    centre, half = scenario.steady_band
                    value = centre + next(steady) + STEADY_CONTEXT_SHIFT.get(context, 0.0)
                    value, note = max(centre - half, min(centre + half, value)), None
                _add_glucose(session, rng, patient, value=value, at=when, context=context, note=note, symptoms=symptoms)
                counts["glucose"] += 1

        bp_at = clock.utc(day, _jitter(rng, time(7, 5), 10))
        if (scenario.bp_daily or i % 2 == 0) and bp_at <= cutoff:
            systolic_base, diastolic_base = scenario.bp
            if t >= 0 and scenario.bp_after is not None:
                weight = min(1.0, (t + 1) / 20)
                systolic_base += (scenario.bp_after[0] - systolic_base) * weight
                diastolic_base += (scenario.bp_after[1] - diastolic_base) * weight
            systolic = round(systolic_base + rng.gauss(0, 4))
            diastolic = round(min(diastolic_base + rng.gauss(0, 3), systolic - 30))
            session.add(
                BloodPressureRecord(
                    patient_id=patient.id,
                    systolic=systolic,
                    diastolic=diastolic,
                    measured_at=bp_at,
                    created_by=patient.id,
                    created_at=bp_at + timedelta(minutes=rng.randint(1, 5)),
                )
            )
            counts["bp"] += 1

        activity_type, minutes, probability, at = scenario.activity
        act_at = clock.utc(day, _jitter(rng, at, 15))
        if rng.random() < probability and act_at <= cutoff:
            session.add(
                ActivityRecord(
                    patient_id=patient.id,
                    activity_type=activity_type,
                    duration_minutes=minutes + rng.choice([-5, 0, 0, 5]),
                    activity_time=act_at,
                    created_by=patient.id,
                )
            )
            counts["activity"] += 1

        missed_at = clock.utc(day, time(21, 0))
        if missed_today and missed_drug is not None and missed_at <= cutoff:
            session.add(
                MedicationNote(
                    medication_id=missed_drug.id,
                    patient_id=patient.id,
                    note_type=MedicationNoteType.MISSED_DOSE,
                    text=scenario.missed_text,
                    occurred_at=missed_at,
                    created_by=patient.id,
                    created_at=clock.utc(day + timedelta(days=1), time(7, 0)) if day < clock.today else missed_at,
                )
            )
            counts["missed"] += 1
        session.flush()

    # The panel badge reads the latest reading's status: end on the
    # scenario's status, forcing the doctor-target rule if the ML band
    # disagrees (same approach as seed_demo_doctor_showcase.py).
    latest = session.exec(
        select(GlucoseRecord)
        .where(GlucoseRecord.patient_id == patient.id)
        .order_by(col(GlucoseRecord.measured_at).desc())
    ).first()
    if scenario.final_value is None and latest is not None and latest.threshold_status == scenario.final_status:
        counts["final_status"] = latest.threshold_status
        return counts
    final_value = scenario.final_value
    if final_value is None:
        final_value = scenario.fasting_level(VISIT_DAYS_AGO - 1) + 25 + rng.gauss(0, 5)
    final = _add_glucose(
        session,
        rng,
        patient,
        value=final_value,
        at=clock.now - timedelta(minutes=12),
        context=MealContext.RANDOM,
        note=scenario.final_note or "Đo thêm",
        symptoms=scenario.final_symptoms,
    )
    if final.threshold_status != scenario.final_status:
        final.meal_context = None
        _evaluate(session, final)
    counts["glucose"] += 1
    counts["final_status"] = final.threshold_status
    return counts


def _baseline_note(session: Session, patient_id: uuid.UUID) -> ClinicalNote | None:
    return session.exec(
        select(ClinicalNote)
        .where(ClinicalNote.patient_id == patient_id, ClinicalNote.appointment_id.is_(None))
        .order_by(col(ClinicalNote.created_at))
    ).first()


def _upsert_visits(session: Session, doctor: User, patient: User, scenario: Scenario, clock: Clock) -> dict:
    """A completed visit 30 days ago with its clinical note, and the next appointment."""
    visit_day = clock.today - timedelta(days=VISIT_DAYS_AGO)
    visit_at = clock.utc(visit_day, scenario.appt_time)
    next_day = clock.today + timedelta(days=scenario.appt_offset_days)
    next_at = clock.utc(next_day, scenario.appt_time)
    if next_at <= clock.now:
        next_at = clock.utc(next_day + timedelta(days=1), scenario.appt_time)

    past_id = _stable_id(patient.id, "appointment", "past")
    past = session.get(Appointment, past_id) or Appointment(
        id=past_id, patient_id=patient.id, doctor_id=doctor.id, created_by=doctor.id, scheduled_at=visit_at
    )
    past.doctor_id = doctor.id
    past.scheduled_at = visit_at
    past.status = AppointmentStatus.COMPLETED
    past.reason = scenario.visit["reason"]
    past.created_at = visit_at - timedelta(days=30)
    past.updated_at = visit_at + timedelta(hours=1)
    session.add(past)

    next_id = _stable_id(patient.id, "appointment", "next")
    upcoming = session.get(Appointment, next_id) or Appointment(
        id=next_id, patient_id=patient.id, doctor_id=doctor.id, created_by=doctor.id, scheduled_at=next_at
    )
    upcoming.doctor_id = doctor.id
    upcoming.scheduled_at = next_at
    upcoming.status = AppointmentStatus.SCHEDULED
    upcoming.reason = scenario.appt_reason
    upcoming.created_at = visit_at + timedelta(hours=1)
    upcoming.updated_at = clock.now
    session.add(upcoming)
    session.flush()
    # The records under every brief changed; the LLM step writes a new one.
    for brief in session.exec(select(PrevisitBrief).where(PrevisitBrief.patient_id == patient.id)).all():
        session.delete(brief)

    baseline = _baseline_note(session, patient.id)
    if baseline is not None:
        # The NHANES baseline survey sits before the 90 days of records, not
        # in the middle of them.
        baseline.created_at = baseline.updated_at = clock.utc(clock.today - timedelta(days=180), time(9, 0))
        session.add(baseline)
    systolic, diastolic = scenario.bp
    note_id = _stable_id(patient.id, "clinical_note", "past")
    note = session.get(ClinicalNote, note_id) or ClinicalNote(
        id=note_id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        reason_for_visit=scenario.visit["reason"],
        history_of_present_illness=scenario.visit["hpi"],
        diagnosis=scenario.visit["diagnosis"],
        treatment_plan=scenario.visit["plan"],
    )
    note.doctor_id = doctor.id
    note.appointment_id = past.id
    note.reason_for_visit = scenario.visit["reason"]
    note.history_of_present_illness = scenario.visit["hpi"]
    note.past_medical_history = baseline.past_medical_history if baseline else None
    note.weight_kg = baseline.weight_kg if baseline else None
    note.blood_pressure_systolic = int(systolic + 4)
    note.blood_pressure_diastolic = int(diastolic + 2)
    note.clinical_exam_findings = scenario.visit["exam"]
    note.data_period_start = visit_day - timedelta(days=30)
    note.data_period_end = visit_day - timedelta(days=1)
    note.hba1c_pct = scenario.visit_a1c
    note.paraclinical_results = f"HbA1c {scenario.visit_a1c}%"
    note.diagnosis = scenario.visit["diagnosis"]
    note.treatment_plan = scenario.visit["plan"]
    note.follow_up_date = next_at.replace(tzinfo=UTC).astimezone(clock.tz).date()
    note.created_at = visit_at + timedelta(minutes=40)
    note.updated_at = visit_at + timedelta(minutes=40)
    session.add(note)
    session.flush()
    return {"visit": past, "next": upcoming, "note": note}


def refresh_panel(session: Session, clock: Clock) -> tuple[User, list[tuple[User, Scenario, dict]]]:
    doctor, panel = select_panel(session)
    results = []
    for patient, scenario in panel:
        _clean_panel_patient(session, patient.id)
        if _active_care_plan(session, patient.id) is None:
            logger.warning("%s has no active care plan; statuses will be UNAVAILABLE", patient.email)
        visits = _upsert_visits(session, doctor, patient, scenario, clock)
        counts = _generate_history(session, patient, scenario, clock)
        results.append((patient, scenario, {**counts, **visits}))
    return doctor, results


# ---------------------------------------------------------------------------
# LLM-backed artefacts, through the same code paths the app uses
# ---------------------------------------------------------------------------


async def generate_ai_artifacts(
    panel: list[tuple[uuid.UUID, str, uuid.UUID, uuid.UUID, bool]], now: datetime
) -> list[str]:
    lead = timedelta(hours=get_settings().previsit_brief_lead_hours)
    done: list[str] = []
    with Session(engine) as session:
        for patient_id, email, next_id, note_id, run_llm in panel:
            appointment = session.get(Appointment, next_id)
            if appointment is not None and appointment.scheduled_at - now <= lead:
                try:
                    await generate_brief(session, appointment, now=now)
                    done.append(f"brief {email}")
                except Exception as exc:
                    session.rollback()
                    done.append(f"brief {email} FAILED ({type(exc).__name__})")
            note = session.get(ClinicalNote, note_id)
            if run_llm and note is not None:
                try:
                    insights = await reflect_on_clinical_note(session, note)
                    done.append(f"insights {email}: {len(insights)}")
                except Exception as exc:
                    session.rollback()
                    done.append(f"insights {email} FAILED ({type(exc).__name__})")

        demo_patient_id, demo_email = panel[0][0], panel[0][1]
        patient = session.get(User, demo_patient_id)
        week_start, week_end, period_start, period_end = last_complete_week(now)
        try:
            report = await generate_weekly_report(
                session,
                patient,
                week_start=week_start,
                week_end=week_end,
                period_start=period_start,
                period_end=period_end,
                now_utc=now,
            )
            done.append(f"weekly report {demo_email} {week_start}..{week_end}: {report.status}")
        except Exception as exc:
            session.rollback()
            done.append(f"weekly report {demo_email} FAILED ({type(exc).__name__})")
        try:
            await generate_and_store_snapshot(session, patient_id=demo_patient_id, role="patient", period_days=7)
            done.append(f"patient summary {demo_email}")
        except Exception as exc:
            session.rollback()
            done.append(f"patient summary {demo_email} FAILED ({type(exc).__name__})")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="compute everything, then roll back")
    parser.add_argument("--skip-llm", action="store_true", help="no briefs, insights, weekly report or summary")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("refresh_demo_data rewrites patient records; it never runs with APP_ENV=production.")

    create_db_and_tables()
    tz = ZoneInfo(settings.reminder_timezone)
    now = datetime.utcnow()
    clock = Clock(now=now, tz=tz)
    utc_offset = datetime.now(tz).utcoffset() or timedelta(0)

    with Session(engine) as session:
        _, panel = select_panel(session)
        panel_ids = [p.id for p, _ in panel]
        summary = refresh_all_patients(session, now=now, skip=set(panel_ids), utc_offset=utc_offset)
        logger.info(
            "all patients: local-time fix for %d, moved %d (up to %d days)",
            summary["timezone_fixed"],
            summary["patients_moved"],
            summary["max_days"],
        )
        doctor, results = refresh_panel(session, clock)
        for patient, scenario, info in results:
            logger.info(
                "  %-24s %-13s glucose=%d meals=%d bp=%d activity=%d missed=%d latest=%s next visit=%s",
                patient.email,
                scenario.key,
                info["glucose"],
                info["meals"],
                info["bp"],
                info["activity"],
                info["missed"],
                info["final_status"],
                info["next"].scheduled_at.replace(tzinfo=UTC).astimezone(tz).strftime("%d/%m %H:%M"),
            )
        panel_jobs = [(p.id, p.email, info["next"].id, info["note"].id, s.run_llm) for p, s, info in results]
        if args.dry_run:
            session.rollback()
            logger.info("dry run: rolled back")
            return
        session.commit()
        logger.info("demo doctor %s: panel of %d committed", doctor.email, len(results))

    # Insights for these patients were deleted with their records above.
    try:
        _delete_insight_vectors(panel_ids)
    except Exception:
        logger.exception("could not delete the panel's insight vectors from Qdrant")
    if args.skip_llm:
        return
    for line in asyncio.run(generate_ai_artifacts(panel_jobs, now)):
        logger.info("  %s", line)


if __name__ == "__main__":
    main()

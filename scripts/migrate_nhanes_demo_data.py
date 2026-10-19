"""Migrate the synthetic NHANES/simulator demo dataset into the live database.

Loads CSVs already on disk (data/NHANES/deployment_dataset, data/simulator_60d,
data/NHANES/medication_flow_60d) and inserts rows into the existing SQLModel
tables — no new tables, no separate schema. See the plan this was built from
for the full reasoning behind each decision below; summarized here:

- Every row's id is `uuid.uuid5(NAMESPACE_URL, "nhanes:<kind>:<source id>")` —
  deterministic, so re-running this script is a no-op (INSERT ... ON CONFLICT
  DO NOTHING) rather than a duplicate-inserting one. Safe to re-run.
- Dates shift from the source's fixed 2026-01-01..2026-03-01 window to a
  rolling "2 calendar months ago -> today" window, recomputed on every run.
- Medication dose/frequency for the NHANES-matched drugs is chosen per
  patient from a two-tier lookup (their own HbA1c vs. the project's <7%
  target), not a flat value — see DRUG_DOSE_TIERS.
- Only patients/doctors/assignments, glucose, meals, medications (two
  sources), and one clinical note per patient are imported. Caregivers,
  CGMacros, reminders/administrations, and episode/run metadata are
  deliberately out of scope (no destination field/table for them).

Usage:

    python -m scripts.migrate_nhanes_demo_data [--dry-run]

Requires an Admin account to already exist (`make seed-admin`) — a synthetic
`patient_assignments` row needs a real `assigned_by`. `make db-reset-migrate`
does reset + seed-admin + this script in one command.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import logging
import re
import unicodedata
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

import codebase.backend.appointments.models as _appointments_models  # noqa: F401  (registers Appointment into SQLModel.metadata — ClinicalNote.appointment_id FKs into it)
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.db import create_db_and_tables, engine
from codebase.backend.health.models import (
    GlucoseRecord,
    MealContext,
    MealRecord,
    MealType,
    MeasurementSource,
)
from codebase.backend.medications.models import Medication, MedicationStatus
from codebase.backend.patients.models import ClinicalNote, PatientAssignment

logger = logging.getLogger("scripts.migrate_nhanes_demo_data")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DIR = REPO_ROOT / "data" / "NHANES" / "deployment_dataset"
SIM_DIR = REPO_ROOT / "data" / "simulator_60d"
MEDFLOW_DIR = REPO_ROOT / "data" / "NHANES" / "medication_flow_60d"

PATIENT_PROFILES_CSV = DEPLOY_DIR / "patient_profiles.csv"
DOCTOR_PROFILES_CSV = DEPLOY_DIR / "doctor_profiles.csv"
ASSIGNMENTS_CSV = DEPLOY_DIR / "patient_doctor_assignments.csv"
BASELINE_CLINICAL_CSV = DEPLOY_DIR / "patient_baseline_clinical.csv"
LIFESTYLE_CSV = DEPLOY_DIR / "patient_lifestyle_baseline.csv"
NHANES_MEDICATIONS_CSV = DEPLOY_DIR / "patient_medications.csv"

REGIMENS_CSV = MEDFLOW_DIR / "synthetic_medication_regimens.csv"

GLUCOSE_CSV = SIM_DIR / "synthetic_glucose_measurements.csv"
MEALS_CSV = SIM_DIR / "synthetic_meal_events.csv"

# The source dataset's own fixed simulation window (verified: uniform across
# all 978 simulation_runs.csv rows) — the anchor the date-shift is computed
# against.
OLD_WINDOW_START = date(2026, 1, 1)

# clinical_target_templates.csv: TARGET-BYT-ADULT-A1C — the project's own
# general adult HbA1c target, not an invented cutoff.
HBA1C_TARGET = 7.0

BATCH_SIZE = 2000


# --- dose lookup ------------------------------------------------------------
# Two tiers per drug (dose, frequency), chosen per patient by their own HbA1c
# vs. HBA1C_TARGET. Curated from each drug's LIỀU_DÙNG_(ĐÁI_THÁO_ĐƯỜNG_TÍP_2)
# section in data/knowledge_structured/drug_reference_sections.csv — not
# parsed at runtime (100-char column limit, and each drug's monograph text
# reads differently). Sitagliptin and insulin have no severity-based tier in
# their own monograph, so both tiers are identical for them.

DoseTier = tuple[str, str]

DRUG_DOSE_TIERS: dict[str, dict[str, DoseTier]] = {
    "METFORMIN": {
        "at_target": ("500mg", "1-2 lần/ngày, cùng bữa ăn"),
        "not_at_target": ("1500-2000mg", "chia 2-3 lần/ngày, cùng bữa ăn"),
    },
    "GLIMEPIRIDE": {
        "at_target": ("1-2mg", "1 lần/ngày, trước bữa sáng"),
        "not_at_target": ("4mg", "1 lần/ngày, trước bữa sáng (duy trì cao)"),
    },
    "PIOGLITAZONE": {
        "at_target": ("15mg", "1 lần/ngày"),
        "not_at_target": ("30-45mg", "1 lần/ngày"),
    },
    "EMPAGLIFLOZIN": {
        "at_target": ("10mg", "1 lần/ngày"),
        "not_at_target": ("25mg", "1 lần/ngày (cần eGFR≥60)"),
    },
    "DAPAGLIFLOZIN": {
        "at_target": ("5mg", "1 lần/ngày"),
        "not_at_target": ("10mg", "1 lần/ngày"),
    },
    "LIRAGLUTIDE": {
        "at_target": ("0,6mg", "Tiêm dưới da, 1 lần/ngày"),
        "not_at_target": ("1,8mg", "Tiêm dưới da, 1 lần/ngày"),
    },
    "REPAGLINIDE": {
        "at_target": ("0,5mg", "3 lần/ngày, trước ăn 15-30 phút"),
        "not_at_target": ("1-2mg", "3 lần/ngày, trước ăn 15-30 phút"),
    },
    "ACARBOSE": {
        "at_target": ("25mg", "1 lần/ngày, cùng bữa ăn"),
        "not_at_target": ("50mg", "3 lần/ngày, cùng bữa ăn"),
    },
    "SEMAGLUTIDE": {
        "at_target": ("0,25mg/tuần", "1 lần/tuần, tiêm dưới da"),
        "not_at_target": ("0,5-1mg/tuần", "1 lần/tuần, tiêm dưới da"),
    },
    "SITAGLIPTIN": {
        "at_target": ("100mg", "1 lần/ngày (đơn trị)"),
        "not_at_target": ("100mg", "1 lần/ngày (đơn trị)"),
    },
    "INSULIN": {
        "at_target": ("Cá thể hóa — không có liều cố định", "Tiêm dưới da, theo phác đồ bác sĩ"),
        "not_at_target": ("Cá thể hóa — không có liều cố định", "Tiêm dưới da, theo phác đồ bác sĩ"),
    },
}

# doctor_diagnosed_* flags carried into history_of_present_illness / diagnosis
# context — transcribed from the source's own booleans, not invented.
FLAG_LABELS: dict[str, str] = {
    "doctor_diagnosed_diabetes_flag": "đái tháo đường",
    "doctor_diagnosed_prediabetes_flag": "tiền đái tháo đường",
    "doctor_diagnosed_hypertension_flag": "tăng huyết áp",
    "doctor_diagnosed_high_cholesterol_flag": "tăng cholesterol",
}
COMORBIDITY_LABELS: dict[str, str] = {
    "congestive_heart_failure_flag": "suy tim",
    "coronary_heart_disease_flag": "bệnh mạch vành",
    "angina_flag": "đau thắt ngực",
    "heart_attack_flag": "nhồi máu cơ tim",
    "stroke_flag": "đột quỵ",
    "thyroid_problem_flag": "bệnh tuyến giáp",
    "liver_condition_flag": "bệnh gan",
}

PARACLINICAL_FIELDS: list[tuple[str, str, str]] = [
    ("fasting_glucose_mg_dl", "Glucose đói", "mg/dL"),
    ("hdl_mg_dl", "HDL", "mg/dL"),
    ("ldl_mg_dl", "LDL", "mg/dL"),
    ("total_cholesterol_mg_dl", "Cholesterol toàn phần", "mg/dL"),
    ("triglycerides_mg_dl", "Triglyceride", "mg/dL"),
    ("creatinine_mg_dl", "Creatinine", "mg/dL"),
    ("alt_u_l", "ALT", "U/L"),
    ("ast_u_l", "AST", "U/L"),
    ("ggt_u_l", "GGT", "U/L"),
    ("bun_mg_dl", "BUN", "mg/dL"),
]


@dataclass(frozen=True)
class PatientInfo:
    uuid: uuid.UUID
    display_name: str
    weight_kg: float | None
    diabetes_label: str


# --- small helpers ------------------------------------------------------------


def _months_ago(d: date, months: int) -> date:
    """`d` minus `months` calendar months, clamping the day to the target
    month's length (e.g. Mar 31 minus 1 month -> Feb 28/29)."""
    total = d.year * 12 + (d.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _compute_shift() -> timedelta:
    window_end = date.today()
    window_start = _months_ago(window_end, 2)
    return window_start - OLD_WINDOW_START


_DJ_MAP = str.maketrans({"đ": "d", "Đ": "D"})


def _strip_diacritics(s: str) -> str:
    s = s.translate(_DJ_MAP)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", s)


def _make_login_email(display_name: str, index: int, domain: str) -> str:
    parts = display_name.split()
    given = _strip_diacritics(parts[-1]) if parts else "User"
    initials = "".join(_strip_diacritics(p)[0].upper() for p in parts[:-1] if p)
    local = re.sub(r"[^A-Za-z0-9]", "", f"{given}{initials}") or "user"
    return f"{local.lower()}_{index:04d}@{domain}"


def _det_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, ":".join(("nhanes", *parts)))


def _truthy(v: object) -> bool:
    return str(v).strip().lower() == "true"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.strip())


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --- patients / doctors / assignments ----------------------------------------


def _load_patient_profiles() -> dict[str, PatientInfo]:
    info: dict[str, PatientInfo] = {}
    for row in _load_csv_rows(PATIENT_PROFILES_CSV):
        pid = row["patient_id"]
        weight = float(row["weight_kg"]) if row.get("weight_kg") else None
        info[pid] = PatientInfo(
            uuid=_det_id("patient", pid),
            display_name=row["display_name"],
            weight_kg=weight,
            diabetes_label=row["project_diabetes_label"],
        )
    return info


def _load_doctor_names() -> dict[str, str]:
    return {row["doctor_id"]: row["doctor_display_name"] for row in _load_csv_rows(DOCTOR_PROFILES_CSV)}


def _build_patient_users(patients: dict[str, PatientInfo]) -> list[dict]:
    now = datetime.utcnow()
    rows = []
    for index, (_pid, info) in enumerate(sorted(patients.items(), key=lambda kv: int(kv[0])), start=1):
        email = _make_login_email(info.display_name, index, "gmail.com")
        rows.append(
            {
                "id": info.uuid,
                "email": email,
                "hashed_password": hash_password(email),
                "full_name": info.display_name,
                "role": UserRole.PATIENT.value,
                "is_active": True,
                "must_change_password": False,
                "token_version": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


def _build_doctor_users(doctor_names: dict[str, str]) -> list[dict]:
    now = datetime.utcnow()
    rows = []
    for index, (did, name) in enumerate(sorted(doctor_names.items()), start=1):
        email = _make_login_email(name, index, "vdiab.com.vn")
        rows.append(
            {
                "id": _det_id("doctor", did),
                "email": email,
                "hashed_password": hash_password(email),
                "full_name": name,
                "role": UserRole.DOCTOR.value,
                "is_active": True,
                "must_change_password": False,
                "token_version": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


def _get_admin_id(session: Session, *, allow_missing: bool = False) -> uuid.UUID | None:
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
    if admin is not None:
        return admin.id
    if allow_missing:
        return None
    raise RuntimeError(
        "No Admin user found in the database — run `make seed-admin` first "
        "(patient_assignments.assigned_by needs an existing Admin)."
    )


def _build_assignments(
    assignment_rows: list[dict[str, str]],
    patients: dict[str, PatientInfo],
    doctor_uuid_map: dict[str, uuid.UUID],
    admin_id: uuid.UUID | None,
) -> tuple[list[dict], dict[uuid.UUID, uuid.UUID]]:
    """Returns (rows ready to insert, patient_uuid -> doctor_uuid map). The
    map is always built from the source pairing regardless of whether an
    admin exists yet — downstream steps (medications, clinical notes) need it
    even in a dry-run before `make seed-admin` has run."""
    patient_doctor_map: dict[uuid.UUID, uuid.UUID] = {}
    rows: list[dict] = []
    now = datetime.utcnow()
    for row in assignment_rows:
        pid, did = row["patient_id"], row["doctor_id"]
        if pid not in patients or did not in doctor_uuid_map:
            continue
        p_uuid = patients[pid].uuid
        d_uuid = doctor_uuid_map[did]
        patient_doctor_map[p_uuid] = d_uuid
        if admin_id is None:
            continue
        rows.append(
            {
                "id": _det_id("assignment", row["assignment_id"]),
                "doctor_id": d_uuid,
                "patient_id": p_uuid,
                "assigned_by": admin_id,
                "created_at": now,
            }
        )
    return rows, patient_doctor_map


# --- medications ---------------------------------------------------------------


def _build_regimen_medications(
    patients: dict[str, PatientInfo],
    patient_doctor_map: dict[uuid.UUID, uuid.UUID],
    shift: timedelta,
) -> list[dict]:
    rows = []
    for row in _load_csv_rows(REGIMENS_CSV):
        pid = row["patient_id"]
        if pid not in patients:
            continue
        p_uuid = patients[pid].uuid
        start_dt = datetime.combine(_parse_date(row["start_date"]), datetime.min.time()) + shift
        end_dt = (
            datetime.combine(_parse_date(row["end_date"]), datetime.min.time()) + shift
            if row.get("end_date")
            else None
        )
        today_dt = datetime.combine(date.today(), datetime.min.time())
        status = MedicationStatus.ACTIVE if (end_dt is None or end_dt >= today_dt) else MedicationStatus.DISCONTINUED
        dose = f"{row['dose_value']} {row['dose_unit']}".strip()
        frequency = f"{row['frequency_code']} ({row['timing_rule']})" if row.get("timing_rule") else row["frequency_code"]
        note = f"[SYNTHETIC DEMO DATA] regimen={row['regimen_id']}, dose_basis={row['dose_basis']}"
        rows.append(
            {
                "id": _det_id("medregimen", row["regimen_id"]),
                "patient_id": p_uuid,
                "drug_name": row["source_drug_name"][:200],
                "dose": dose[:100],
                "frequency": frequency[:100],
                "start_date": start_dt,
                "end_date": end_dt,
                "status": status.value,
                "note": note[:500],
                "discontinued_reason": None,
                "replaces_medication_id": None,
                "created_by": patient_doctor_map.get(p_uuid, p_uuid),
                "created_at": start_dt,
                "updated_at": start_dt,
            }
        )
    return rows


def _match_drug(drug_name: str) -> str | None:
    """Substring match against DRUG_DOSE_TIERS' 11 keys (10 dosed drugs +
    insulin). Returns None for no match *or* an ambiguous fixed-dose-combo
    name (e.g. "METFORMIN; SITAGLIPTIN") that hits more than one — we have no
    monograph dosing for the combination product itself, only each single
    ingredient, so those rows are skipped rather than guessing which
    ingredient's dose applies."""
    upper = drug_name.upper()
    hits = [d for d in DRUG_DOSE_TIERS if d in upper]
    return hits[0] if len(hits) == 1 else None


def _build_nhanes_medications(
    patients: dict[str, PatientInfo],
    patient_doctor_map: dict[uuid.UUID, uuid.UUID],
    baseline_by_patient: dict[str, dict[str, str]],
    window_start: date,
) -> list[dict]:
    rows = []
    window_start_dt = datetime.combine(window_start, datetime.min.time())
    for row in _load_csv_rows(NHANES_MEDICATIONS_CSV):
        pid = row["patient_id"]
        if pid not in patients:
            continue
        drug = _match_drug(row["drug_name"])
        if drug is None:
            continue

        baseline = baseline_by_patient.get(pid)
        hba1c = float(baseline["hba1c_pct"]) if baseline and baseline.get("hba1c_pct") else None
        at_target = hba1c is None or hba1c < HBA1C_TARGET
        tier_key = "at_target" if at_target else "not_at_target"
        tier_label = "KHỞI ĐẦU" if tier_key == "at_target" else "DUY TRÌ"
        dose, frequency = DRUG_DOSE_TIERS[drug][tier_key]

        p_uuid = patients[pid].uuid
        days_taken = float(row["days_taken"]) if row.get("days_taken") else 0.0
        start_dt = window_start_dt - timedelta(days=days_taken)

        hba1c_str = f"{hba1c:.1f}" if hba1c is not None else "không rõ"
        has_htn = _truthy(baseline.get("doctor_diagnosed_hypertension_flag")) if baseline else False
        htn_note = "; kèm THA" if has_htn else ""
        note = (
            f"[LIỀU THAM KHẢO TỪ DƯỢC THƯ, mức {tier_label} do HbA1c {hba1c_str}% "
            f"(đích <{HBA1C_TARGET:g}%){htn_note} — KHÔNG PHẢI CHỈ ĐỊNH THỰC TẾ] {row['medication_record_id']}"
        )

        rows.append(
            {
                "id": _det_id("medrecord", row["medication_record_id"]),
                "patient_id": p_uuid,
                "drug_name": row["drug_name"][:200],
                "dose": dose[:100],
                "frequency": frequency[:100],
                "start_date": start_dt,
                "end_date": None,
                "status": MedicationStatus.ACTIVE.value,
                "note": note[:500],
                "discontinued_reason": None,
                "replaces_medication_id": None,
                "created_by": patient_doctor_map.get(p_uuid, p_uuid),
                "created_at": start_dt,
                "updated_at": start_dt,
            }
        )
    return rows


# --- clinical notes --------------------------------------------------------------


def _flags_present(row: dict[str, str], labels: dict[str, str]) -> list[str]:
    return [label for col, label in labels.items() if _truthy(row.get(col))]


def _build_history(row: dict[str, str]) -> str:
    present = _flags_present(row, FLAG_LABELS)
    diagnosed = ", ".join(present) if present else "không ghi nhận chẩn đoán nào từ bác sĩ trong khảo sát"
    family = "có" if _truthy(row.get("family_history_diabetes_flag")) else "không"
    return f"Theo khảo sát nền NHANES: được bác sĩ chẩn đoán {diagnosed}. Tiền sử gia đình đái tháo đường: {family}."


def _build_past_history(row: dict[str, str], lifestyle: dict[str, str] | None) -> str:
    present = _flags_present(row, COMORBIDITY_LABELS)
    parts = [", ".join(present)] if present else ["không ghi nhận bệnh đồng mắc trong khảo sát nền"]
    if lifestyle is not None:
        smoked = "có" if _truthy(lifestyle.get("smoked_100_cigarettes_flag")) else "không"
        parts.append(f"hút thuốc: {smoked}")
    return "; ".join(parts) + "."


def _build_paraclinical(row: dict[str, str]) -> str | None:
    parts = [f"{label}: {row[col]} {unit}" for col, label, unit in PARACLINICAL_FIELDS if row.get(col)]
    return "; ".join(parts)[:2000] if parts else None


def _build_clinical_notes(
    patients: dict[str, PatientInfo],
    patient_doctor_map: dict[uuid.UUID, uuid.UUID],
    baseline_rows: list[dict[str, str]],
    lifestyle_by_patient: dict[str, dict[str, str]],
) -> list[dict]:
    rows = []
    now = datetime.utcnow()
    for row in baseline_rows:
        pid = row["patient_id"]
        if pid not in patients:
            continue
        info = patients[pid]
        doctor_id = patient_doctor_map.get(info.uuid)
        if doctor_id is None:
            continue  # no assigned doctor yet to attribute the note to

        hba1c = float(row["hba1c_pct"]) if row.get("hba1c_pct") else None
        sbp = round(float(row["sbp_mean"])) if row.get("sbp_mean") else None
        dbp = round(float(row["dbp_mean"])) if row.get("dbp_mean") else None
        diagnosis = f"{info.diabetes_label} (nhãn thuật toán từ NHANES — cần bác sĩ xác nhận)"

        rows.append(
            {
                "id": _det_id("clinicalnote", pid),
                "patient_id": info.uuid,
                "doctor_id": doctor_id,
                "appointment_id": None,
                "reason_for_visit": "Nhập liệu nền từ khảo sát NHANES (baseline) — không phải buổi khám thực tế.",
                "history_of_present_illness": _build_history(row)[:4000],
                "past_medical_history": _build_past_history(row, lifestyle_by_patient.get(pid))[:2000],
                "weight_kg": info.weight_kg,
                "blood_pressure_systolic": sbp,
                "blood_pressure_diastolic": dbp,
                "clinical_exam_findings": None,
                "data_period_start": None,
                "data_period_end": None,
                "hba1c_pct": hba1c,
                "paraclinical_results": _build_paraclinical(row),
                "diagnosis": diagnosis[:1000],
                "treatment_plan": (
                    "Nhập tự động từ dữ liệu nền NHANES — CẦN bác sĩ xem lại và lập kế hoạch điều trị "
                    "thực tế trước khi sử dụng cho quyết định lâm sàng."
                ),
                "follow_up_date": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


# --- glucose / meals (streamed, bulk) -------------------------------------------


def _iter_glucose_rows(patients: dict[str, PatientInfo], shift: timedelta) -> Iterator[dict]:
    with GLUCOSE_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = row["patient_id"]
            if pid not in patients:
                continue
            p_uuid = patients[pid].uuid
            measured_at = _parse_dt(row["measured_at"]) + shift
            meal_context = MealContext(row["meal_context"]).value
            yield {
                "id": _det_id("glucose", row["measurement_id"]),
                "patient_id": p_uuid,
                "value": float(row["glucose_mg_dl"]),
                "unit": row["unit"] or "mg/dL",
                "measured_at": measured_at,
                "note": f"sim:{row['measurement_id']}",
                "meal_context": meal_context,
                "measurement_source": MeasurementSource.SIMULATOR.value,
                "symptoms": None,
                "created_by": p_uuid,
                "created_at": measured_at,
            }


def _iter_meal_rows(patients: dict[str, PatientInfo], shift: timedelta) -> Iterator[dict]:
    with MEALS_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = row["patient_id"]
            if pid not in patients:
                continue
            p_uuid = patients[pid].uuid
            meal_time = _parse_dt(row["meal_started_at"]) + shift
            meal_type = MealType(row["meal_type"].lower()).value
            description = (
                f"{float(row['calories_kcal']):.0f} kcal · {float(row['carbohydrate_g']):.0f}g carb · "
                f"{float(row['protein_g']):.0f}g protein · {float(row['fat_g']):.0f}g fat"
            )
            yield {
                "id": _det_id("meal", row["meal_id"]),
                "patient_id": p_uuid,
                "meal_type": meal_type,
                "meal_time": meal_time,
                "description": description[:1000],
                "note": f"sim:{row['meal_id']}",
                "created_by": p_uuid,
                "created_at": meal_time,
            }


# --- DB write helpers -----------------------------------------------------------
# Pure SQLModel — no raw SQLAlchemy Core/dialect imports. SQLModel has no
# `ON CONFLICT DO NOTHING` of its own (that's a Postgres-dialect-specific Core
# feature with no SQLModel equivalent), so idempotency here is a plain
# check-existing-ids-then-insert-the-rest, same shape as seed_admin.py's
# check-then-insert — just batched. Building real model instances (rather than
# raw column dicts) also means every row gets Pydantic's Field validation
# (max_length, gt/le, etc.) for free before it reaches the database.


def _existing_ids(session: Session, model_cls: type, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(session.exec(select(model_cls.id).where(model_cls.id.in_(ids))).all())


def _write_batch(session: Session, model_cls: type, rows: list[dict], *, dry_run: bool) -> tuple[int, int]:
    """Returns (prepared, newly-inserted-or-would-insert)."""
    if not rows:
        return 0, 0
    existing = _existing_ids(session, model_cls, [r["id"] for r in rows])
    new_rows = [r for r in rows if r["id"] not in existing]
    if dry_run:
        return len(rows), len(new_rows)
    session.add_all(model_cls(**r) for r in new_rows)
    session.commit()
    return len(rows), len(new_rows)


def _write_bulk(
    session: Session, model_cls: type, row_iter: Iterable[dict], *, batch_size: int, dry_run: bool
) -> tuple[int, int]:
    prepared = 0
    changed = 0
    batch: list[dict] = []
    for row in row_iter:
        batch.append(row)
        if len(batch) >= batch_size:
            p, c = _write_batch(session, model_cls, batch, dry_run=dry_run)
            prepared += p
            changed += c
            batch = []
    if batch:
        p, c = _write_batch(session, model_cls, batch, dry_run=dry_run)
        prepared += p
        changed += c
    return prepared, changed


# --- orchestration ---------------------------------------------------------------


def run_migration(*, dry_run: bool = False) -> dict[str, tuple[int, int]]:
    create_db_and_tables()
    summary: dict[str, tuple[int, int]] = {}

    with Session(engine) as session:
        shift = _compute_shift()
        window_start = OLD_WINDOW_START + shift
        window_end = date.today()
        logger.info("Date window: %s -> %s (shift=%dd)", window_start.isoformat(), window_end.isoformat(), shift.days)

        patients = _load_patient_profiles()
        doctor_names = _load_doctor_names()
        doctor_uuid_map = {did: _det_id("doctor", did) for did in doctor_names}
        baseline_rows = _load_csv_rows(BASELINE_CLINICAL_CSV)
        baseline_by_patient = {r["patient_id"]: r for r in baseline_rows}
        lifestyle_by_patient = {r["patient_id"]: r for r in _load_csv_rows(LIFESTYLE_CSV)}
        assignment_rows = _load_csv_rows(ASSIGNMENTS_CSV)

        summary["users (patients)"] = _write_batch(
            session, User, _build_patient_users(patients), dry_run=dry_run
        )
        summary["users (doctors)"] = _write_batch(
            session, User, _build_doctor_users(doctor_names), dry_run=dry_run
        )

        admin_id = _get_admin_id(session, allow_missing=dry_run)
        if admin_id is None:
            logger.warning("No Admin user found yet — assignments would fail on a real run until `make seed-admin` runs.")
        assignment_build_rows, patient_doctor_map = _build_assignments(
            assignment_rows, patients, doctor_uuid_map, admin_id
        )
        summary["patient_assignments"] = _write_batch(
            session, PatientAssignment, assignment_build_rows, dry_run=dry_run
        )

        regimen_rows = _build_regimen_medications(patients, patient_doctor_map, shift)
        nhanes_med_rows = _build_nhanes_medications(patients, patient_doctor_map, baseline_by_patient, window_start)
        summary["medications"] = _write_batch(
            session, Medication, regimen_rows + nhanes_med_rows, dry_run=dry_run
        )
        logger.info(
            "medications: %d from simulator regimens, %d from NHANES (knowledge-base matched)",
            len(regimen_rows),
            len(nhanes_med_rows),
        )

        clinical_note_rows = _build_clinical_notes(patients, patient_doctor_map, baseline_rows, lifestyle_by_patient)
        summary["clinical_notes"] = _write_batch(session, ClinicalNote, clinical_note_rows, dry_run=dry_run)

        summary["glucose_records"] = _write_bulk(
            session,
            GlucoseRecord,
            _iter_glucose_rows(patients, shift),
            batch_size=BATCH_SIZE,
            dry_run=dry_run,
        )
        summary["meal_records"] = _write_bulk(
            session, MealRecord, _iter_meal_rows(patients, shift), batch_size=BATCH_SIZE, dry_run=dry_run
        )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and report expected counts without writing to the database."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    summary = run_migration(dry_run=args.dry_run)

    label = "would insert" if args.dry_run else "inserted"
    print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'Migration complete'}:")
    for table_name, (prepared, changed) in summary.items():
        print(f"  {table_name:20s} {prepared:>8,} prepared, {changed:>8,} {label}")


if __name__ == "__main__":
    main()

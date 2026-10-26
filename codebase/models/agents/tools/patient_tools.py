"""get_patient_profile / get_health_records tools — thin wrappers over the
existing health/patients service layer, never direct DB access from the agent.

OWNER: AI Summary workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §8. Take `session: Session` as an
explicit first argument, same convention as codebase/backend/health/service.py
and patients/service.py — the caller (nodes/fetch_data.py) is responsible for
getting it from LangGraph's config, these tools should stay plain, testable
functions with no session-acquisition logic of their own.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session

from codebase.backend.auth.models import User
from codebase.backend.health.meal_analysis import analyses_by_meal
from codebase.backend.health.service import (
    list_activity_records,
    list_blood_pressure_records,
    list_glucose_records,
    list_meal_records,
)
from codebase.backend.medications.service import (
    list_active_medications,
    list_medication_notes_for_patient,
)
from codebase.backend.patients.service import get_assigned_doctor as _get_assigned_doctor

# PRD §15.7 — a summary/consultation request never pulls more than 30 days of
# health history in one shot, regardless of what the caller asked for.
MAX_DATE_RANGE_DAYS = 30


def get_patient_profile(session: Session, patient_id: UUID) -> dict:
    """Wrap codebase.backend.auth.models.User fields.

    No dedicated PatientProfile model exists yet (see design doc §1) — this
    returns what's available today (name, role), not clinical baseline data
    (no diagnosis date, HbA1c baseline, etc.). Current medications are a
    separate concern, covered by get_medication_summary below, not this
    function. Callers (fetch_data, synthesize_summary) must not assume more
    than this shape provides.
    """
    user = session.get(User, patient_id)
    if user is None:
        return {"patient_id": str(patient_id), "full_name": None, "role": None, "found": False}
    return {
        "patient_id": str(user.id),
        "full_name": user.full_name,
        "role": user.role.value,
        "found": True,
    }


def get_assigned_doctor(session: Session, patient_id: UUID) -> dict:
    """Wrap patients.service.get_assigned_doctor (the PatientAssignment link) —
    lets synthesize_qa answer "bác sĩ điều trị của tôi là ai?" from the
    patient's own data instead of the generic "tôi không có thông tin" fallback
    it fell back to while this tool didn't exist yet. No assignment is a real,
    valid state (not an error) — {"assigned": False, ...} lets the model say so
    honestly rather than fabricate a name.
    """
    doctor = _get_assigned_doctor(session, patient_id)
    if doctor is None:
        return {"assigned": False, "doctor_id": None, "full_name": None}
    return {"assigned": True, "doctor_id": str(doctor.id), "full_name": doctor.full_name}


def _record_to_dict(record) -> dict:
    """SQLModel row -> plain dict, same field names as the model. Kept as
    native Python types (UUID/datetime/enum) since this stays in-process
    (tool_results is never serialized directly onto the HTTP response) —
    downstream code (metrics_tools, synthesize_summary) works off these
    field names directly.
    """
    return record.model_dump()


def _meals_with_analysis(session: Session, meals: list) -> list[dict]:
    """Meal rows plus, once the background analysis has run (Epic 1.3), its
    table-based estimate under "carb_estimate". The carbs of an unmatched
    item stays None — the model must say "chưa ước tính được", not guess."""
    analyses = analyses_by_meal(session, [m.id for m in meals])
    rows = []
    for meal in meals:
        row = _record_to_dict(meal)
        analysis = analyses.get(meal.id)
        if analysis is not None:
            row["carb_estimate"] = {
                "total_carbs_g": analysis.total_carbs_g,
                "carb_load": analysis.carb_load.value,
                "has_unmatched_items": any(i.get("carbs_g") is None for i in analysis.items),
                "items": [{"name": i.get("matched_name") or i.get("name"), "carbs_g": i.get("carbs_g")} for i in analysis.items],
                "status": analysis.status.value,
            }
        rows.append(row)
    return rows


def get_health_records(session: Session, patient_id: UUID, start: datetime, end: datetime) -> dict:
    """Wrap codebase.backend.health.service.list_glucose_records /
    list_blood_pressure_records / list_meal_records / list_activity_records
    for the given patient + range.

    Caps range at 30 days per PRD §15.7 — enforce here, not just at the call
    site, so any future caller of this tool gets the same guarantee fetch_data
    relies on.

    Returns {"glucose": [...], "blood_pressure": [...], "meals": [...],
    "activities": [...]}, one dict per record (see _record_to_dict) — this is
    the shape metrics_tools.calculate_health_metrics expects.
    """
    if start > end:
        start, end = end, start
    if (end - start) > timedelta(days=MAX_DATE_RANGE_DAYS):
        start = end - timedelta(days=MAX_DATE_RANGE_DAYS)

    glucose = list_glucose_records(session, patient_id, start_date=start, end_date=end)
    blood_pressure = list_blood_pressure_records(session, patient_id, start_date=start, end_date=end)
    meals = list_meal_records(session, patient_id, start_date=start, end_date=end)
    activities = list_activity_records(session, patient_id, start_date=start, end_date=end)

    return {
        "glucose": [_record_to_dict(r) for r in glucose],
        "blood_pressure": [_record_to_dict(r) for r in blood_pressure],
        "meals": _meals_with_analysis(session, meals),
        "activities": [_record_to_dict(r) for r in activities],
    }


def get_medication_summary(session: Session, patient_id: UUID, start: datetime, end: datetime) -> dict:
    """Wrap codebase.backend.medications.service for the summary: the patient's current
    regimen plus any patient-reported missed-dose/adverse-reaction notes within [start, end].

    Medication notes are only ever filed by the patient themself (medications/router.py
    role-gates POST .../notes to PATIENT, and checks the medication is theirs) — never
    invented or inferred by the agent, same no-fabrication guarantee as health records.

    Returns {"active": [...], "notes": [...]}, one dict per record (see _record_to_dict) —
    synthesize_summary.py turns "notes" into attention_points deterministically, the same way
    metrics_tools.calculate_health_metrics's output does for glucose/meal data.
    """
    active = list_active_medications(session, patient_id)
    notes = [
        note for note in list_medication_notes_for_patient(session, patient_id) if start <= note.occurred_at <= end
    ]
    return {
        "active": [_record_to_dict(m) for m in active],
        "notes": [_record_to_dict(n) for n in notes],
    }

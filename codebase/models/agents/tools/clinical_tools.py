"""Clinical context for the doctor's pre-visit brief (Epic 4, open question
"brief chưa có HbA1c, mục tiêu/mức hoàn thành và trích dẫn") — no LLM.

- HbA1c: the latest values a doctor entered in clinical notes (the only place
  the system holds lab results), each with the note date it comes from.
- Glucose target: the CarePlanTarget in force at the end of the window, and
  how many of the window's readings were within target. "Within target" is
  each reading's own threshold_status (NORMAL vs WATCH/DANGER, against the
  care plan in force when it was measured — same rule as the weekly report,
  reports/stats.py); readings measured before any care plan are left out.
- The previous visit's treatment plan and follow-up date, as the doctor wrote
  them.

Every value carries where it came from (note date, care plan version) so the
brief can cite it. Datetimes are naive UTC, like the records'.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, or_, select

from codebase.backend.health.models import GlucoseRecord
from codebase.backend.patients.models import CarePlanTarget, ClinicalNote

MAX_HBA1C_VALUES = 3
_EVALUATED = ("NORMAL", "WATCH", "DANGER")


def _active_target(session: Session, patient_id: UUID, at: datetime) -> CarePlanTarget | None:
    return session.exec(
        select(CarePlanTarget)
        .where(
            CarePlanTarget.patient_id == patient_id,
            col(CarePlanTarget.effective_from) <= at,
            or_(col(CarePlanTarget.effective_to).is_(None), col(CarePlanTarget.effective_to) > at),
        )
        .order_by(col(CarePlanTarget.version).desc())
    ).first()


def get_clinical_context(session: Session, patient_id: UUID, start: datetime, end: datetime) -> dict:
    notes = session.exec(
        select(ClinicalNote)
        .where(ClinicalNote.patient_id == patient_id, col(ClinicalNote.created_at) <= end)
        .order_by(col(ClinicalNote.created_at).desc())
    ).all()
    hba1c = [
        {"value_pct": note.hba1c_pct, "date": note.created_at.date().isoformat()}
        for note in notes
        if note.hba1c_pct is not None
    ][:MAX_HBA1C_VALUES]

    target = _active_target(session, patient_id, end)
    statuses = session.exec(
        select(GlucoseRecord.threshold_status).where(
            GlucoseRecord.patient_id == patient_id,
            col(GlucoseRecord.measured_at) >= start,
            col(GlucoseRecord.measured_at) <= end,
        )
    ).all()
    evaluated = [s for s in statuses if s in _EVALUATED]
    in_target = sum(1 for s in evaluated if s == "NORMAL")

    last_note = notes[0] if notes else None
    return {
        "hba1c": hba1c,
        "care_plan": (
            {
                "version": target.version,
                "target_low_mg_dl": target.target_low_mg_dl,
                "target_high_mg_dl": target.target_high_mg_dl,
                "effective_from": target.effective_from.date().isoformat(),
            }
            if target is not None
            else None
        ),
        "target_attainment": {
            "readings": len(statuses),
            "evaluated": len(evaluated),
            "in_target": in_target,
            "in_target_pct": round(100 * in_target / len(evaluated), 1) if evaluated else None,
        },
        "last_visit": (
            {
                "date": last_note.created_at.date().isoformat(),
                "treatment_plan": last_note.treatment_plan,
                "follow_up_date": last_note.follow_up_date.isoformat() if last_note.follow_up_date else None,
            }
            if last_note is not None
            else None
        ),
    }

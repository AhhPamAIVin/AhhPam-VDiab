"""Medication adherence (Epic 3.1) and its association with glucose (3.2).

Data the system actually has: medication reminders are NOT per drug — every
enabled glucose reminder carries one MEDICATION_REMINDER listing all active
drugs (notifications/scheduler.py), so a day can hold more reminders than real
doses. Plus MedicationNote(MISSED_DOSE), which is per drug. Hence two figures:

- day level (the headline): a local day is
  * "missed"      — a medication reminder was skipped, or a missed dose was noted;
  * "taken"       — at least one medication reminder completed and nothing missed;
  * "unconfirmed" — only reminders left unanswered after the day ended
                    (counted as not-confirmed, not as forgotten);
  * "no_data"     — no reminder and no note.
  day_rate = taken / (taken + missed).
- dose level, the task's formula over reminders:
  completed / (completed + skipped + missed + expired_pending), with one dose
  never counted twice: a day's misses are max(skipped + expired, missed
  notes), capped at its opportunities.

Today's still-open reminders are left out (the day isn't over). Missed-dose
notes on an as-needed drug (frequency like "khi cần", "PRN") are left out —
skipping an as-needed dose isn't non-adherence. Days are local
(settings.reminder_timezone). Output is JSON-serializable.

The glucose comparison (3.2) is descriptive only: mean of daily glucose means
on "taken" vs "missed" days, each group needing >= MIN_DAYS_PER_GROUP days.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from codebase.backend.config import get_settings
from codebase.backend.health.models import GlucoseRecord
from codebase.backend.medications.models import Medication, MedicationNote, MedicationNoteType
from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType

MIN_DAYS_PER_GROUP = 3
_AS_NEEDED_RE = re.compile(r"khi cần|lúc cần|nếu cần|\bprn\b|khi đau|khi sốt", re.IGNORECASE)
_UTC = ZoneInfo("UTC")


def is_as_needed(medication: Medication) -> bool:
    return bool(_AS_NEEDED_RE.search(medication.frequency or ""))


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().reminder_timezone)


def _local_date(value: datetime, tz: ZoneInfo) -> date:
    return value.replace(tzinfo=_UTC).astimezone(tz).date()


def _utc_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    def to_utc(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=tz).astimezone(_UTC).replace(tzinfo=None)

    return to_utc(start), to_utc(end + timedelta(days=1))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def compute_adherence(
    session: Session, patient_id: UUID, start: date, end: date, *, today: date | None = None
) -> dict:
    """Adherence over local days [start, end] (inclusive)."""
    tz = _tz()
    today = today or datetime.now(tz).date()
    utc_start, utc_end = _utc_bounds(start, end, tz)

    reminders = session.exec(
        select(Notification).where(
            Notification.patient_id == patient_id,
            Notification.notification_type == NotificationType.MEDICATION_REMINDER,
            col(Notification.slot_date) >= start,
            col(Notification.slot_date) <= end,
        )
    ).all()
    medications = {
        m.id: m for m in session.exec(select(Medication).where(Medication.patient_id == patient_id)).all()
    }
    notes = [
        n
        for n in session.exec(
            select(MedicationNote).where(
                MedicationNote.patient_id == patient_id,
                MedicationNote.note_type == MedicationNoteType.MISSED_DOSE,
                col(MedicationNote.occurred_at) >= utc_start,
                col(MedicationNote.occurred_at) < utc_end,
            )
        ).all()
        if n.medication_id not in medications or not is_as_needed(medications[n.medication_id])
    ]

    per_day: dict[date, dict[str, int]] = defaultdict(lambda: {"completed": 0, "skipped": 0, "expired": 0, "missed_notes": 0})
    for reminder in reminders:
        day = per_day[reminder.slot_date]
        if reminder.status == NotificationStatus.COMPLETED:
            day["completed"] += 1
        elif reminder.status == NotificationStatus.SKIPPED:
            day["skipped"] += 1
        elif reminder.slot_date < today:
            day["expired"] += 1  # pending/snoozed when the day ended
    missed_by_drug: dict[UUID, int] = defaultdict(int)
    for note in notes:
        per_day[_local_date(note.occurred_at, tz)]["missed_notes"] += 1
        missed_by_drug[note.medication_id] += 1

    daily = []
    totals = {"taken": 0, "missed": 0, "unconfirmed": 0, "no_data": 0}
    doses_taken = doses_total = 0
    day = start
    while day <= end:
        counts = per_day.get(day, {"completed": 0, "skipped": 0, "expired": 0, "missed_notes": 0})
        opportunities = max(counts["completed"] + counts["skipped"] + counts["expired"], counts["missed_notes"])
        misses = min(opportunities, max(counts["skipped"] + counts["expired"], counts["missed_notes"]))
        doses_total += opportunities
        doses_taken += opportunities - misses
        if counts["skipped"] or counts["missed_notes"]:
            status = "missed"
        elif counts["completed"]:
            status = "taken"
        elif counts["expired"]:
            status = "unconfirmed"
        else:
            status = "no_data"
        totals[status] += 1
        daily.append({"date": day.isoformat(), "status": status, **counts})
        day += timedelta(days=1)

    rated_days = totals["taken"] + totals["missed"]
    in_window = [
        m for m in medications.values()
        if m.start_date < utc_end and (m.end_date is None or m.end_date > utc_start)
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
        "day_rate_pct": _round(100 * totals["taken"] / rated_days) if rated_days else None,
        "days_taken": totals["taken"],
        "days_missed": totals["missed"],
        "days_unconfirmed": totals["unconfirmed"],
        "days_no_data": totals["no_data"],
        "dose_rate_pct": _round(100 * doses_taken / doses_total) if doses_total else None,
        "doses_considered": doses_total,
        "medications": [
            {
                "medication_id": str(m.id),
                "drug_name": m.drug_name,
                "as_needed": is_as_needed(m),
                "missed_notes": missed_by_drug.get(m.id, 0),
            }
            for m in sorted(in_window, key=lambda m: m.start_date)
        ],
        "missed_dates": [d["date"] for d in daily if d["status"] == "missed"],
        "daily": daily,
        "glucose_by_adherence": _glucose_by_adherence(session, patient_id, daily, utc_start, utc_end, tz),
    }


def _glucose_by_adherence(session, patient_id, daily, utc_start, utc_end, tz) -> dict | None:
    readings = session.exec(
        select(GlucoseRecord).where(
            GlucoseRecord.patient_id == patient_id,
            col(GlucoseRecord.measured_at) >= utc_start,
            col(GlucoseRecord.measured_at) < utc_end,
        )
    ).all()
    by_day: dict[str, list[float]] = defaultdict(list)
    for reading in readings:
        by_day[_local_date(reading.measured_at, tz).isoformat()].append(reading.value)
    groups: dict[str, list[float]] = {"taken": [], "missed": []}
    for day in daily:
        values = by_day.get(day["date"])
        if values and day["status"] in groups:
            groups[day["status"]].append(sum(values) / len(values))
    if any(len(v) < MIN_DAYS_PER_GROUP for v in groups.values()):
        return None
    return {
        "taken_days": len(groups["taken"]),
        "taken_avg_mg_dl": _round(sum(groups["taken"]) / len(groups["taken"])),
        "missed_days": len(groups["missed"]),
        "missed_avg_mg_dl": _round(sum(groups["missed"]) / len(groups["missed"])),
    }


def adherence_last_days(session: Session, patient_id: UUID, days: int) -> dict:
    today = datetime.now(_tz()).date()
    return compute_adherence(session, patient_id, today - timedelta(days=days - 1), today, today=today)

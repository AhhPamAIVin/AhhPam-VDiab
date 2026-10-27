"""Weekly patient report generation and delivery (Epic 2.3-2.4).

run_weekly_report_tick() is the cron job (insights/scheduler.py, Sunday 08:00
local by default): for every active patient, the Sunday-Saturday week that
just ended gets one report —

1. stats.compute_weekly_stats (deterministic);
2. no data at all -> a fixed "chưa có số đo" text, no LLM call; otherwise the
   patient-facing AI Summary for exactly that week
   (summaries.service.generate_and_store_snapshot, already guardrailed),
   copied into the report;
3. one "việc nên làm" picked by rule from reviewed texts (stats.pick_tip);
4. unless the patient opted out (User.weekly_report_enabled), an in-app
   Notification and a short Zalo message — no health figures over Zalo.

Idempotent: (patient_id, week_start) is unique, an existing report is left
alone, a FAILED one is retried. One patient's failure is logged (no PHI) and
the batch carries on.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType
from codebase.backend.notifications.service import get_or_create_notification
from codebase.backend.notifications.zalo import ZaloAPIError, ZaloBotNotifier, ZaloConfigurationError
from codebase.backend.reports.models import PatientWeeklyReport, WeeklyReportStatus
from codebase.backend.reports.stats import compute_weekly_stats, has_any_data, pick_tip
from codebase.backend.summaries.service import generate_and_store_snapshot

logger = logging.getLogger(__name__)

NO_DATA_SUMMARY = (
    "Tuần này bạn chưa ghi số đo đường huyết, huyết áp, bữa ăn hay vận động nào, nên mình chưa có gì để "
    "tổng kết. Mỗi lần đo và ghi lại đều giúp bạn và bác sĩ hiểu rõ hơn tình hình của mình."
)
# Section keys the page renders; "medications" joins when Epic 3.1 lands.
SECTIONS = ["glucose", "logging", "meals", "blood_pressure"]


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().reminder_timezone)


def _to_utc_naive(local_day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(local_day, time.min, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def last_complete_week(now_utc: datetime) -> tuple[date, date, datetime, datetime]:
    """(week_start, week_end, period_start, period_end) of the last
    Sunday-Saturday week that has fully ended by `now_utc` (naive UTC)."""
    tz = _tz()
    today = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    # Python: Monday=0 ... Sunday=6. Last Saturday strictly before today.
    week_end = today - timedelta(days=(today.weekday() + 2) % 7 or 7)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end, _to_utc_naive(week_start, tz), _to_utc_naive(week_end + timedelta(days=1), tz)


def get_report(session: Session, patient_id: UUID, week_start: date) -> PatientWeeklyReport | None:
    return session.exec(
        select(PatientWeeklyReport).where(
            PatientWeeklyReport.patient_id == patient_id, PatientWeeklyReport.week_start == week_start
        )
    ).first()


def list_reports(session: Session, patient_id: UUID) -> list[PatientWeeklyReport]:
    statement = (
        select(PatientWeeklyReport)
        .where(PatientWeeklyReport.patient_id == patient_id, PatientWeeklyReport.status != WeeklyReportStatus.FAILED)
        .order_by(col(PatientWeeklyReport.week_start).desc())
    )
    return list(session.exec(statement).all())


def _slot_key(week_start: date) -> str:
    return f"weekly_report:{week_start.isoformat()}"


def _title(report: PatientWeeklyReport) -> str:
    return f"Báo cáo sức khỏe tuần {report.week_start:%d/%m}–{report.week_end:%d/%m}"


async def _deliver(session: Session, patient: User, report: PatientWeeklyReport, now_utc: datetime) -> None:
    title = _title(report)
    get_or_create_notification(
        session,
        patient_id=patient.id,
        notification_type=NotificationType.WEEKLY_REPORT,
        slot_key=_slot_key(report.week_start),
        slot_date=now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(_tz()).date(),
        scheduled_for=now_utc,
        title=title,
        message="Báo cáo tuần của bạn đã sẵn sàng. Mở mục Báo cáo tuần trong ứng dụng để xem.",
    )
    report.status = WeeklyReportStatus.DELIVERED
    report.delivered_at = now_utc
    session.add(report)
    session.commit()
    if patient.zalo_chat_id and get_settings().zalo_bot_enabled:
        # A pointer only: health figures stay in the app (no separate consent
        # to send them over Zalo).
        base_url = get_settings().frontend_public_url.rstrip("/")
        where = f"Xem chi tiết: {base_url}/reports" if base_url else "Mở ứng dụng VDiab, mục Báo cáo tuần, để xem chi tiết."
        try:
            await ZaloBotNotifier().send_message(patient.zalo_chat_id, f"{title}\n{where}")
        except (ZaloConfigurationError, ZaloAPIError):
            logger.warning("weekly report zalo push failed")


async def generate_weekly_report(
    session: Session, patient: User, *, week_start: date, week_end: date, period_start: datetime,
    period_end: datetime, now_utc: datetime,
) -> PatientWeeklyReport:
    existing = get_report(session, patient.id, week_start)
    if existing is not None and existing.status != WeeklyReportStatus.FAILED:
        return existing

    stats = compute_weekly_stats(session, patient.id, period_start, period_end)
    report = existing or PatientWeeklyReport(
        patient_id=patient.id,
        week_start=week_start,
        week_end=week_end,
        period_start=period_start,
        period_end=period_end,
        status=WeeklyReportStatus.GENERATED,
    )
    try:
        if has_any_data(stats):
            snapshot = await generate_and_store_snapshot(
                session,
                patient_id=patient.id,
                role="patient",
                period_days=7,
                period_start=period_start,
                period_end=period_end,
            )
            report.snapshot_id = snapshot.id
            report.summary = snapshot.summary
        else:
            report.snapshot_id = None
            report.summary = NO_DATA_SUMMARY
    except Exception:
        session.rollback()
        report.status = WeeklyReportStatus.FAILED
        report.stats = stats
        session.add(report)
        session.commit()
        raise
    report.stats = stats
    report.tip = pick_tip(stats)
    report.sections_enabled = SECTIONS if has_any_data(stats) else []
    report.status = WeeklyReportStatus.GENERATED
    session.add(report)
    session.commit()
    session.refresh(report)
    if patient.weekly_report_enabled:
        await _deliver(session, patient, report, now_utc)
    return report


async def run_weekly_report_tick(session: Session, *, now_utc: datetime) -> dict:
    """Generate (and deliver) last week's report for every active patient.
    Returns counts only."""
    week_start, week_end, period_start, period_end = last_complete_week(now_utc)
    patients = session.exec(
        select(User).where(User.role == UserRole.PATIENT, col(User.is_active).is_(True))
    ).all()
    counts = {"generated": 0, "already_existed": 0, "failed": 0}
    for patient in patients:
        existing = get_report(session, patient.id, week_start)
        if existing is not None and existing.status != WeeklyReportStatus.FAILED:
            counts["already_existed"] += 1
            continue
        try:
            await generate_weekly_report(
                session, patient, week_start=week_start, week_end=week_end,
                period_start=period_start, period_end=period_end, now_utc=now_utc,
            )
            counts["generated"] += 1
        except Exception:
            session.rollback()
            counts["failed"] += 1
            logger.exception("weekly report failed for patient_id=%s", patient.id)
    logger.info("weekly report tick week_start=%s %s", week_start, counts)
    return counts


def mark_read(session: Session, report: PatientWeeklyReport, now_utc: datetime) -> PatientWeeklyReport:
    if report.read_at is None:
        report.read_at = now_utc
        session.add(report)
        notification = session.exec(
            select(Notification).where(
                Notification.patient_id == report.patient_id, Notification.slot_key == _slot_key(report.week_start)
            )
        ).first()
        if notification is not None and notification.status == NotificationStatus.PENDING:
            notification.status = NotificationStatus.COMPLETED
            notification.responded_at = now_utc
            session.add(notification)
        session.commit()
        session.refresh(report)
    return report

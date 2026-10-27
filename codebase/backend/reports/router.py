"""Weekly report API (Epic 2.5). Patient-only: the report is written for the
patient and, for now, not shared with the doctor (open question in
docs/tasks/02-patient-weekly-report.md); a report of someone else is a 404."""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, SQLModel

from codebase.backend.auth.dependencies import require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.reports import service
from codebase.backend.reports.models import PatientWeeklyReport, WeeklyReportStatus
from codebase.backend.security.rate_limit import limit_agent
from codebase.backend.time_utils import UtcDatetime

router = APIRouter()

_patient_only = require_roles(UserRole.PATIENT)


class WeeklyReportListItem(SQLModel):
    id: UUID
    week_start: date
    week_end: date
    status: WeeklyReportStatus
    tip: str | None
    read_at: UtcDatetime | None
    created_at: UtcDatetime


class WeeklyReportPublic(WeeklyReportListItem):
    summary: str
    stats: dict
    sections_enabled: list[str]
    delivered_at: UtcDatetime | None


def _owned(session: Session, current_user: User, report_id: UUID) -> PatientWeeklyReport:
    report = session.get(PatientWeeklyReport, report_id)
    if report is None or report.patient_id != current_user.id or report.status == WeeklyReportStatus.FAILED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy báo cáo.")
    return report


@router.get("/weekly", response_model=list[WeeklyReportListItem])
def list_weekly_reports(
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> list[PatientWeeklyReport]:
    return service.list_reports(session, current_user.id)


@router.post("/weekly/generate", response_model=WeeklyReportPublic, dependencies=[Depends(limit_agent)])
async def generate_last_week(
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> PatientWeeklyReport:
    """Last completed week's report, generated now if the Sunday job hasn't
    made it yet (first use, or the job was down). Idempotent."""
    now = datetime.utcnow()
    week_start, week_end, period_start, period_end = service.last_complete_week(now)
    try:
        return await service.generate_weekly_report(
            session, current_user, week_start=week_start, week_end=week_end,
            period_start=period_start, period_end=period_end, now_utc=now,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Chưa tạo được báo cáo lúc này, vui lòng thử lại sau."
        ) from None


@router.get("/weekly/{report_id}", response_model=WeeklyReportPublic)
def get_weekly_report(
    report_id: UUID,
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> PatientWeeklyReport:
    return _owned(session, current_user, report_id)


@router.post("/weekly/{report_id}/read", response_model=WeeklyReportPublic)
def mark_weekly_report_read(
    report_id: UUID,
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> PatientWeeklyReport:
    return service.mark_read(session, _owned(session, current_user, report_id), datetime.utcnow())

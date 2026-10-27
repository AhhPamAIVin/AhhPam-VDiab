from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint
from sqlmodel import Enum as SAEnum


class WeeklyReportStatus(StrEnum):
    GENERATED = "generated"  # stored; no notification (patient opted out)
    DELIVERED = "delivered"  # in-app notification created
    FAILED = "failed"  # generation failed; the next run retries it


class PatientWeeklyReport(SQLModel, table=True):
    """One weekly report per patient and week (Epic 2). The week runs Sunday
    to Saturday in settings.reminder_timezone; period_start/period_end are
    the same bounds in naive UTC, like every record timestamp.

    `summary` is copied from the AI Summary snapshot it was written from
    (snapshot_id): snapshots are upserted per day, so a later refresh must not
    change a report the patient has already received. `stats` holds the
    deterministic figures (reports/stats.py) the summary and the page show."""

    __tablename__ = "patient_weekly_reports"
    __table_args__ = (UniqueConstraint("patient_id", "week_start", name="uq_weekly_report_patient_week"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    week_start: date = Field(index=True)
    week_end: date  # inclusive
    period_start: datetime
    period_end: datetime
    snapshot_id: UUID | None = Field(default=None, foreign_key="agent_summary_snapshots.id")
    summary: str = Field(default="")
    tip: str | None = Field(default=None, max_length=500)
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    sections_enabled: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: WeeklyReportStatus = Field(
        sa_column=Column(SAEnum(WeeklyReportStatus, name="weekly_report_status"), nullable=False)
    )
    delivered_at: datetime | None = Field(default=None)
    read_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)

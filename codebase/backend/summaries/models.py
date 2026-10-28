from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class SummaryRole(StrEnum):
    """Which audience this cached snapshot's prose was written for — see
    codebase/models/agents/nodes/synthesize_summary.py's is_doctor branching:
    the `response`/`trends`/`attention_points` text genuinely differs by
    role, so this is part of the cache key, not a display-only filter."""

    PATIENT = "patient"
    DOCTOR = "doctor"


class AgentSummarySnapshot(SQLModel, table=True):
    """A cached AI Summary — mirrors codebase/backend/insights/models.py's
    PatientInsight shape/reasoning, applied to AI Summary instead of
    long-term insights. Fixes the "regenerates on every page reset" problem:
    POST /api/v1/agent/summary reads the latest row here instead of
    invoking the LLM live on every call (see agent/router.py::summarize).

    One row per (patient_id, role, period_days, period_end) where
    period_end is normalized to midnight-of-day (see
    summaries/service.py::_normalize_period_end) — so a chatty day of
    multiple health-record writes upserts the same row (one LLM-authored
    summary per day, not one per write), while a new day inserts a new row.
    That per-day grain is what gives "keep a summary trail since the
    beginning" (the historical-backfill requirement) its actual rows.
    """

    __tablename__ = "agent_summary_snapshots"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    role: SummaryRole = Field(sa_column=Column(SAEnum(SummaryRole, name="summary_role"), nullable=False, index=True))
    period_days: int = Field(index=True)
    period_start: datetime
    period_end: datetime = Field(index=True)

    summary: str
    trends: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    attention_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    suggested_doctor_questions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    limitations: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    data_used: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    data_completeness: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Doctor snapshots only (Epic 4.2): {"changes"|"clinical"|"meals"|"medication_adherence": [lines]}.
    sections: dict = Field(default_factory=dict, sa_column=Column(JSON))

    generated_at: datetime = Field(default_factory=datetime.utcnow)

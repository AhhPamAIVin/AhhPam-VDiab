from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel
from sqlmodel import Enum as SAEnum


class InsightStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class InsightConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InsightSourceKind(StrEnum):
    """Which pipeline produced this insight — see
    docs/AI_Summary_Respond_System_Design.md §7 and
    /Users/phamminh/.claude/plans/warm-crafting-muffin.md §B. Not just
    descriptive metadata: it's the audience/trust-level split. `APPOINTMENT`
    insights are grounded in a doctor's ClinicalNote and are doctor-facing;
    `WEEKLY_BATCH` insights come from an unreviewed weekly AI scan of raw
    records and are patient-facing (plain language, per the plan's two
    propose-prompt variants). It also partitions the create-vs-update
    similarity match in upsert_insight.py — a candidate from one source_kind
    must never match/update an insight from the other, for the same patient
    or a different one (plan §B/§C's "hard invariant, not optional").
    """

    APPOINTMENT = "appointment"
    WEEKLY_BATCH = "weekly_batch"
    # Mined from the patient's own self-chat sessions (ChatSession, agent/
    # models.py), never a doctor's working thread about the patient. Patient-
    # facing only, gated behind User.ai_memory_consent — insights/router.py's
    # doctor-facing endpoint explicitly excludes this source_kind. See
    # /Users/phamminh/.claude/plans/memoized-churning-patterson.md Part 3.
    CHAT = "chat"


class PatientInsight(SQLModel, table=True):
    """A small, structured, auditable long-term-memory row for one patient —
    NOT a chat transcript, NOT a confirmed clinical fact (design doc §7,
    §7.4: "additional context [the respond-agent] may mention, sourced and
    dated, never presented as more certain than the note/data it came
    from"). Built by codebase/models/agents/nodes/reflect.py +
    upsert_insight.py's gather -> propose -> match -> create-or-update
    algorithm (design doc §7.3).

    Schema is design doc §7.1 as originally specified, with the two
    plan-§B modifications applied on top:
      - `source_note_id` is now optional (UUID | None) — the weekly-batch
        leg has no ClinicalNote to point to (it's a periodic scan of raw
        records, not tied to any one appointment).
      - `source_kind` is new (InsightSourceKind) — see that enum's
        docstring for why this is a hard partitioning axis, not just a
        label.

    Versioning is in-place, not a new row per revision: when
    upsert_insight.py finds a semantic match above SIMILARITY_THRESHOLD, it
    mutates THIS SAME row (same `id`, `version` incremented,
    `supersedes_version` recorded, `text`/`evidence`/`confidence` replaced
    with the LLM-merged result, the vector-store embedding for this id replaced in
    place) rather than writing a second row — confirmed against
    data/mock/demo_patient/expected_reflections.json's EVAL-REFLECT-002,
    where INS-DEMO-P-0001-01 goes version 1 -> 2 under the same insight_id,
    not a new id. `status`/`InsightStatus.SUPERSEDED` is defined for a
    future manual-review workflow (a doctor explicitly retiring an insight)
    — the reflect-agent's own algorithm never sets it; every row it writes
    stays ACTIVE.
    """

    __tablename__ = "patient_insights"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(foreign_key="users.id", index=True)
    version: int = Field(default=1)
    category: str = Field(max_length=100)  # e.g. "glucose_pattern", "hypoglycemia_risk" — open, not a closed enum
    text: str = Field(max_length=2000)
    evidence: dict = Field(sa_column=Column(JSON))  # source_note_id (label), date_range, supporting_stats
    confidence: InsightConfidence = Field(
        sa_column=Column(SAEnum(InsightConfidence, name="insight_confidence"), nullable=False)
    )
    status: InsightStatus = Field(
        default=InsightStatus.ACTIVE,
        sa_column=Column(SAEnum(InsightStatus, name="insight_status"), nullable=False, server_default="ACTIVE"),
    )
    supersedes_version: int | None = Field(default=None)
    # Optional per plan §B: only the appointment leg has a ClinicalNote to
    # point to. FK not enforced with nullable=False since weekly_batch rows
    # legitimately have none.
    source_note_id: UUID | None = Field(default=None, foreign_key="clinical_notes.id")
    source_kind: InsightSourceKind = Field(
        sa_column=Column(SAEnum(InsightSourceKind, name="insight_source_kind"), nullable=False)
    )
    first_observed_at: datetime
    last_confirmed_at: datetime

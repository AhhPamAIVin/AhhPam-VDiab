from uuid import UUID

from sqlmodel import SQLModel

from codebase.backend.time_utils import UtcDatetime


class PatientInsightPublic(SQLModel):
    """Doctor-facing view of one PatientInsight row — both source_kind
    streams (see router.py), labeled via `reviewed` so the UI can
    distinguish a doctor-confirmed appointment insight from an unreviewed
    weekly-batch one, per the plan's "doctors see both, clearly labeled"
    decision."""

    id: UUID
    category: str
    text: str
    confidence: str
    version: int
    source_kind: str
    # True for source_kind="appointment" (grounded in a doctor's own
    # ClinicalNote), False for "weekly_batch" (raw AI pattern-detection over
    # records, same content a patient sees in patient-appropriate language,
    # never doctor-reviewed) — see InsightSourceKind's docstring (models.py)
    # for the full audience/trust-level reasoning this labels.
    reviewed: bool
    first_observed_at: UtcDatetime
    last_confirmed_at: UtcDatetime

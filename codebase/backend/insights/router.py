"""Doctor-facing PatientInsight endpoint (design doc §7.4, plan §B/§C:
read-only, both source_kind streams, gated by ensure_patient_access exactly
like every other patient-data endpoint — an unassigned doctor gets 403, same
as codebase/backend/patients/router.py's clinical-note endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user
from codebase.backend.auth.models import User
from codebase.backend.db import get_session
from codebase.backend.insights.schemas import PatientInsightPublic
from codebase.backend.patients.access import ensure_patient_access
from codebase.models.agents.tools.insight_tools import get_patient_insights

router = APIRouter()


@router.get("/patients/{patient_id}/insights", response_model=list[PatientInsightPublic])
def list_patient_insights(
    patient_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[PatientInsightPublic]:
    """The two doctor-facing PatientInsight streams for a patient:
    source_kind="appointment" (doctor-authored-and-grounded) and
    "weekly_batch" (unreviewed AI pattern-detection, patient-facing plain
    language) — `reviewed` labels which is which, per the plan's "doctors
    see both, clearly labeled" decision. Patient-facing surfacing of their
    own weekly_batch insights happens separately, via
    AgentSummaryResponse.relevant_insights (codebase/backend/agent/router.py)
    — this endpoint is doctor/admin-only in effect, since ensure_patient_access
    lets a patient view their own record too but the frontend doctor view is
    this endpoint's only caller in this phase.

    source_kind="chat" is deliberately excluded — mined from a patient's own
    chat sessions under their explicit consent (plan Part 3), it's
    patient-facing only and must never reach a doctor's view, even an
    assigned one. Leaving get_patient_insights unfiltered here would
    silently include it the moment any chat insight exists, so this is an
    explicit allowlist, not an oversight."""
    ensure_patient_access(session, current_user, patient_id)
    insights = get_patient_insights(session, patient_id, source_kind=["appointment", "weekly_batch"])
    return [
        PatientInsightPublic(
            id=insight["id"],
            category=insight["category"],
            text=insight["text"],
            confidence=str(insight["confidence"]),
            version=insight["version"],
            source_kind=str(insight["source_kind"]),
            reviewed=str(insight["source_kind"]) == "appointment",
            first_observed_at=insight["first_observed_at"],
            last_confirmed_at=insight["last_confirmed_at"],
        )
        for insight in insights
    ]

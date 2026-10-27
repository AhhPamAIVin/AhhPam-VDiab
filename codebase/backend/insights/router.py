"""Doctor-facing PatientInsight endpoint (design doc §7.4, plan §B/§C:
read-only, both source_kind streams, gated by ensure_patient_access exactly
like every other patient-data endpoint — an unassigned doctor gets 403, same
as codebase/backend/patients/router.py's clinical-note endpoints), plus the
doctor's confirm/reject feedback on an insight (Epic 4.4)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.insights import feedback
from codebase.backend.insights.models import InsightFeedback, InsightSourceKind, PatientInsight
from codebase.backend.insights.schemas import InsightFeedbackRequest, PatientInsightPublic
from codebase.backend.patients.access import ensure_patient_access
from codebase.models.agents.tools.insight_tools import get_patient_insights

router = APIRouter()

_doctor_only = require_roles(UserRole.DOCTOR)


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
    — ensure_patient_access lets a patient read their own record here too
    (the patient's health page shares this panel), so a patient gets neither
    the insights a doctor rejected nor any verdict.

    source_kind="chat" is deliberately excluded — mined from a patient's own
    chat sessions under their explicit consent (plan Part 3), it's
    patient-facing only and must never reach a doctor's view, even an
    assigned one. Leaving get_patient_insights unfiltered here would
    silently include it the moment any chat insight exists, so this is an
    explicit allowlist, not an oversight."""
    ensure_patient_access(session, current_user, patient_id)
    insights = get_patient_insights(session, patient_id, source_kind=["appointment", "weekly_batch"])
    is_patient = current_user.role == UserRole.PATIENT
    if is_patient:
        rejected = feedback.rejected_insight_ids(session, patient_id)
        insights = [insight for insight in insights if insight["id"] not in rejected]
    verdicts = {} if is_patient else feedback.latest_feedback(session, [insight["id"] for insight in insights])
    return [_public(insight, verdicts.get(insight["id"])) for insight in insights]


def _public(insight: dict, verdict: InsightFeedback | None) -> PatientInsightPublic:
    return PatientInsightPublic(
        id=insight["id"],
        category=insight["category"],
        text=insight["text"],
        confidence=str(insight["confidence"]),
        version=insight["version"],
        source_kind=str(insight["source_kind"]),
        reviewed=str(insight["source_kind"]) == "appointment",
        first_observed_at=insight["first_observed_at"],
        last_confirmed_at=insight["last_confirmed_at"],
        verdict=verdict.verdict if verdict else None,
        verdict_comment=verdict.comment if verdict else None,
        verdict_version=verdict.insight_version if verdict else None,
        verdict_at=verdict.updated_at if verdict else None,
    )


@router.put("/patients/{patient_id}/insights/{insight_id}/feedback", response_model=PatientInsightPublic)
def set_insight_feedback(
    patient_id: UUID,
    insight_id: UUID,
    request: InsightFeedbackRequest,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> PatientInsightPublic:
    """Confirm or reject one insight — the patient's assigned doctor only
    (role check + ensure_patient_access). Sending again changes the verdict."""
    ensure_patient_access(session, current_user, patient_id)
    insight = session.get(PatientInsight, insight_id)
    if insight is None or insight.patient_id != patient_id or insight.source_kind == InsightSourceKind.CHAT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy nhận định.")
    insight_data = insight.model_dump()  # before the commit below expires it
    saved = feedback.set_feedback(
        session, insight=insight, doctor_id=current_user.id, verdict=request.verdict, comment=request.comment
    )
    return _public(insight_data, saved)

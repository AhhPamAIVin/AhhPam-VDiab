from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.schemas import UserPublic
from codebase.backend.db import get_session
from codebase.backend.patients import service
from codebase.backend.patients.access import ensure_patient_access
from codebase.backend.patients.models import CarePlanTarget, ClinicalNote
from codebase.backend.patients.schemas import (
    CarePlanTargetVersionCreate,
    CarePlanTargetVersionPublic,
    ClinicalNoteCreate,
    ClinicalNotePublic,
    ClinicalNoteUpdate,
)

router = APIRouter()

_doctor_only = require_roles(UserRole.DOCTOR)


@router.get("/patients", response_model=list[UserPublic])
def list_my_patients(
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> list[User]:
    """List patients assigned to the current doctor."""
    return service.list_patients_for_doctor(session, current_user.id)


@router.post("/patients/{patient_id}/notes", response_model=ClinicalNotePublic, status_code=status.HTTP_201_CREATED)
def create_clinical_note(
    patient_id: UUID,
    request: ClinicalNoteCreate,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> ClinicalNote:
    """Add a clinical file (bệnh án) entry for a patient assigned to the current doctor."""
    ensure_patient_access(session, current_user, patient_id)
    return service.create_clinical_note(
        session,
        patient_id=patient_id,
        doctor_id=current_user.id,
        appointment_id=request.appointment_id,
        reason_for_visit=request.reason_for_visit,
        history_of_present_illness=request.history_of_present_illness,
        past_medical_history=request.past_medical_history,
        weight_kg=request.weight_kg,
        blood_pressure_systolic=request.blood_pressure_systolic,
        blood_pressure_diastolic=request.blood_pressure_diastolic,
        clinical_exam_findings=request.clinical_exam_findings,
        data_period_start=request.data_period_start,
        data_period_end=request.data_period_end,
        hba1c_pct=request.hba1c_pct,
        paraclinical_results=request.paraclinical_results,
        diagnosis=request.diagnosis,
        treatment_plan=request.treatment_plan,
        follow_up_date=request.follow_up_date,
    )


@router.get("/patients/{patient_id}/notes", response_model=list[ClinicalNotePublic])
def list_clinical_notes(
    patient_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ClinicalNote]:
    """List clinical (bệnh án) entries for a patient: their assigned doctor, an
    admin, or the patient themself viewing their own record."""
    ensure_patient_access(session, current_user, patient_id)
    return service.list_clinical_notes(session, patient_id)


@router.patch("/patients/{patient_id}/notes/{note_id}", response_model=ClinicalNotePublic)
def update_clinical_note(
    patient_id: UUID,
    note_id: UUID,
    request: ClinicalNoteUpdate,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> ClinicalNote:
    """Edit a clinical note (only the authoring doctor may update it). Only the
    fields present in the request body are changed — this is a partial update."""
    ensure_patient_access(session, current_user, patient_id)
    note = service.get_clinical_note_or_404(session, patient_id=patient_id, note_id=note_id)
    if note.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your note")
    return service.update_clinical_note(session, note=note, fields=request.model_dump(exclude_unset=True))


@router.delete("/patients/{patient_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_clinical_note(
    patient_id: UUID,
    note_id: UUID,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> None:
    """Delete a clinical note (only the authoring doctor may delete it)."""
    ensure_patient_access(session, current_user, patient_id)
    note = service.get_clinical_note_or_404(session, patient_id=patient_id, note_id=note_id)
    if note.doctor_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your note")
    service.delete_clinical_note(session, note)


@router.post(
    "/patients/{patient_id}/care-plan-targets",
    response_model=CarePlanTargetVersionPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_care_plan_target_version(
    patient_id: UUID,
    request: CarePlanTargetVersionCreate,
    current_user: User = Depends(_doctor_only),
    session: Session = Depends(get_session),
) -> CarePlanTarget:
    ensure_patient_access(session, current_user, patient_id)
    return service.create_care_plan_target_version(
        session,
        patient_id=patient_id,
        doctor_id=current_user.id,
        **request.model_dump(),
    )


@router.get("/patients/{patient_id}/care-plan-targets", response_model=list[CarePlanTargetVersionPublic])
def list_care_plan_target_versions(
    patient_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[CarePlanTarget]:
    ensure_patient_access(session, current_user, patient_id)
    return service.list_care_plan_target_versions(session, patient_id)

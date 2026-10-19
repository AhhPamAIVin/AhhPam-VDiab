from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.medications import service
from codebase.backend.medications.models import Medication, MedicationNote
from codebase.backend.medications.schemas import (
    MedicationChange,
    MedicationCreate,
    MedicationDiscontinue,
    MedicationNoteCreate,
    MedicationNotePublic,
    MedicationPublic,
)
from codebase.backend.patients.access import ensure_patient_access

router = APIRouter()

_can_write = require_roles(UserRole.DOCTOR)
_patient_only = require_roles(UserRole.PATIENT)


def _resolve_patient_id(current_user: User, patient_id: UUID | None) -> UUID:
    """Patients default to themselves; Doctor/Admin must specify which patient."""
    if patient_id is not None:
        return patient_id
    if current_user.role == UserRole.PATIENT:
        return current_user.id
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_id is required")


@router.post("", response_model=MedicationPublic, status_code=status.HTTP_201_CREATED)
def create_medication(
    request: MedicationCreate,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> Medication:
    """Doctor prescribes a new medication for a patient currently assigned to them."""
    ensure_patient_access(session, current_user, request.patient_id)
    return service.create_medication(
        session,
        patient_id=request.patient_id,
        drug_name=request.drug_name,
        dose=request.dose,
        frequency=request.frequency,
        start_date=request.start_date,
        note=request.note,
        created_by=current_user.id,
    )


@router.post("/{medication_id}/change", response_model=MedicationPublic, status_code=status.HTTP_201_CREATED)
def change_medication(
    medication_id: UUID,
    request: MedicationChange,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> Medication:
    """Change a medication (new dosage or drug swap): atomically discontinues the current entry
    and creates the replacement. The old entry's drug/dose/frequency are never overwritten — it
    stays in history as a discontinued row with its original values intact."""
    medication = service.get_medication_or_404(session, medication_id)
    ensure_patient_access(session, current_user, medication.patient_id)
    return service.change_medication(
        session,
        current=medication,
        drug_name=request.drug_name,
        dose=request.dose,
        frequency=request.frequency,
        start_date=request.start_date,
        note=request.note,
        changed_by=current_user.id,
    )


@router.post("/{medication_id}/discontinue", response_model=MedicationPublic)
def discontinue_medication(
    medication_id: UUID,
    request: MedicationDiscontinue,
    current_user: User = Depends(_can_write),
    session: Session = Depends(get_session),
) -> Medication:
    """Stop a medication with no replacement drug (e.g. course of treatment finished)."""
    medication = service.get_medication_or_404(session, medication_id)
    ensure_patient_access(session, current_user, medication.patient_id)
    return service.discontinue_medication(
        session,
        medication=medication,
        end_date=request.end_date,
        note=request.note,
    )


@router.get("/active", response_model=list[MedicationPublic])
def list_active(
    patient_id: UUID | None = Query(default=None),
    as_of: datetime | None = Query(default=None, description="View the regimen as it stood at this point in time instead of now (e.g. what was this patient on as of 2026-06-15)."),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Medication]:
    """Medications the patient was on at `as_of` (defaults to right now)."""
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    if as_of is not None:
        return service.list_medications_as_of(session, target_patient_id, as_of)
    return service.list_active_medications(session, target_patient_id)


@router.get("/history", response_model=list[MedicationPublic])
def list_history(
    patient_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Medication]:
    """Full medication timeline for a patient, chronological, including discontinued entries."""
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_medication_history(session, target_patient_id)


@router.post("/{medication_id}/notes", response_model=MedicationNotePublic, status_code=status.HTTP_201_CREATED)
def create_medication_note(
    medication_id: UUID,
    request: MedicationNoteCreate,
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> MedicationNote:
    """Patient self-reports a missed dose or adverse reaction against one of their own
    medications. Doctors document clinically via ClinicalNote, not here."""
    medication = service.get_medication_or_404(session, medication_id)
    if medication.patient_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your medication")
    return service.create_medication_note(
        session,
        medication=medication,
        note_type=request.note_type,
        text=request.text,
        occurred_at=request.occurred_at,
        created_by=current_user.id,
    )


@router.get("/{medication_id}/notes", response_model=list[MedicationNotePublic])
def list_notes_for_medication(
    medication_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[MedicationNote]:
    """Notes filed against one medication entry: its patient, their assigned doctor, or admin."""
    medication = service.get_medication_or_404(session, medication_id)
    ensure_patient_access(session, current_user, medication.patient_id)
    return service.list_medication_notes(session, medication_id)


@router.get("/notes", response_model=list[MedicationNotePublic])
def list_notes_for_patient(
    patient_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[MedicationNote]:
    """Every medication note for a patient across their whole regimen — lets a doctor review
    missed doses/adverse reactions without checking each medication one by one."""
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_medication_notes_for_patient(session, target_patient_id)

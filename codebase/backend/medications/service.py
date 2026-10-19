from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, or_, select

from codebase.backend.medications.models import Medication, MedicationNote, MedicationNoteType, MedicationStatus


def create_medication(
    session: Session,
    *,
    patient_id: UUID,
    drug_name: str,
    dose: str,
    frequency: str,
    start_date: datetime,
    note: str | None,
    created_by: UUID,
) -> Medication:
    medication = Medication(
        patient_id=patient_id,
        drug_name=drug_name,
        dose=dose,
        frequency=frequency,
        start_date=start_date,
        status=MedicationStatus.ACTIVE,
        note=note,
        created_by=created_by,
    )
    session.add(medication)
    session.commit()
    session.refresh(medication)
    return medication


def get_medication_or_404(session: Session, medication_id: UUID) -> Medication:
    medication = session.get(Medication, medication_id)
    if medication is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medication not found")
    return medication


def change_medication(
    session: Session,
    *,
    current: Medication,
    drug_name: str,
    dose: str,
    frequency: str,
    start_date: datetime | None,
    note: str | None,
    changed_by: UUID,
) -> Medication:
    """Atomically discontinue `current` and create its replacement.

    This is the history-preserving core of the module: `current`'s drug_name/dose/frequency are
    never mutated — only its status/end_date/updated_at change — so the exact regimen it
    represented stays queryable forever via the history endpoint. Both writes are flushed in a
    single commit so a caller never observes an old entry discontinued without its replacement
    (or vice versa).
    """
    if current.status != MedicationStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Medication is not active")

    effective_start = start_date or datetime.utcnow()

    current.status = MedicationStatus.DISCONTINUED
    current.end_date = effective_start
    current.discontinued_reason = note
    current.updated_at = datetime.utcnow()
    session.add(current)

    replacement = Medication(
        patient_id=current.patient_id,
        drug_name=drug_name,
        dose=dose,
        frequency=frequency,
        start_date=effective_start,
        status=MedicationStatus.ACTIVE,
        note=note,
        replaces_medication_id=current.id,
        created_by=changed_by,
    )
    session.add(replacement)

    session.commit()
    session.refresh(current)
    session.refresh(replacement)
    return replacement


def discontinue_medication(
    session: Session,
    *,
    medication: Medication,
    end_date: datetime | None,
    note: str | None,
) -> Medication:
    """Stop a medication with no replacement (e.g. course of treatment finished)."""
    if medication.status != MedicationStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Medication is not active")

    medication.status = MedicationStatus.DISCONTINUED
    medication.end_date = end_date or datetime.utcnow()
    medication.discontinued_reason = note
    medication.updated_at = datetime.utcnow()
    session.add(medication)
    session.commit()
    session.refresh(medication)
    return medication


def list_active_medications(session: Session, patient_id: UUID) -> list[Medication]:
    """What the patient is currently on."""
    statement = select(Medication).where(
        Medication.patient_id == patient_id,
        Medication.status == MedicationStatus.ACTIVE,
    )
    return list(session.exec(statement.order_by(col(Medication.start_date))).all())


def list_medications_as_of(session: Session, patient_id: UUID, as_of: datetime) -> list[Medication]:
    """What the patient was on at a specific point in time in the past (or now).

    Unlike list_active_medications (which trusts the `status` column), this walks the
    start_date/end_date boundaries directly so it also answers historical questions like
    "what was this patient taking on 2026-06-15" — a date that may fall entirely within an
    entry that has since been discontinued/replaced. A medication counts as in effect at
    `as_of` if it had already started (start_date <= as_of) and either never ended or ended
    strictly after `as_of`. change_medication() sets the old entry's end_date equal to the
    new entry's start_date, so at that exact instant this boundary picks the new entry, never
    both — no double-counting at a change point.
    """
    statement = select(Medication).where(
        Medication.patient_id == patient_id,
        col(Medication.start_date) <= as_of,
        or_(col(Medication.end_date).is_(None), col(Medication.end_date) > as_of),
    )
    return list(session.exec(statement.order_by(col(Medication.start_date))).all())


def list_medication_history(session: Session, patient_id: UUID) -> list[Medication]:
    """Every medication entry ever recorded for this patient, oldest first — the full timeline,
    including discontinued/replaced entries, so how the regimen evolved is reconstructable."""
    statement = select(Medication).where(Medication.patient_id == patient_id)
    return list(session.exec(statement.order_by(col(Medication.start_date), col(Medication.created_at))).all())


def create_medication_note(
    session: Session,
    *,
    medication: Medication,
    note_type: MedicationNoteType,
    text: str | None,
    occurred_at: datetime | None,
    created_by: UUID,
    source_pending_action_id: UUID | None = None,
) -> MedicationNote:
    """A patient self-report against a specific medication (missed dose / adverse reaction).
    `patient_id` is copied from the medication, not taken from the caller, so a note can never
    be filed against someone else's medication regardless of what the request claims."""
    note = MedicationNote(
        medication_id=medication.id,
        patient_id=medication.patient_id,
        note_type=note_type,
        text=text,
        occurred_at=occurred_at or datetime.utcnow(),
        created_by=created_by,
        source_pending_action_id=source_pending_action_id,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def list_medication_notes(session: Session, medication_id: UUID) -> list[MedicationNote]:
    """Every note filed against a single medication entry, oldest first."""
    statement = select(MedicationNote).where(MedicationNote.medication_id == medication_id)
    return list(session.exec(statement.order_by(col(MedicationNote.occurred_at))).all())


def list_medication_notes_for_patient(session: Session, patient_id: UUID) -> list[MedicationNote]:
    """Every medication note for a patient across their whole regimen, oldest first — lets a
    doctor review missed doses/adverse reactions without checking each medication one by one."""
    statement = select(MedicationNote).where(MedicationNote.patient_id == patient_id)
    return list(session.exec(statement.order_by(col(MedicationNote.occurred_at))).all())

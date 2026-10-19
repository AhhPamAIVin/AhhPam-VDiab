from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from codebase.backend.appointments.models import Appointment
from codebase.backend.auth.models import User, UserRole
from codebase.backend.patients.models import (
    CarePlanTarget,
    CarePlanTargetStatus,
    ClinicalNote,
    PatientAssignment,
    RiskGroup,
)


def _get_user_with_role(session: Session, user_id: UUID, role: UserRole, label: str) -> User:
    user = session.get(User, user_id)
    if user is None or user.role != role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return user


def is_doctor_assigned(session: Session, *, doctor_id: UUID, patient_id: UUID) -> bool:
    statement = select(PatientAssignment).where(
        PatientAssignment.doctor_id == doctor_id,
        PatientAssignment.patient_id == patient_id,
    )
    return session.exec(statement).first() is not None


def assign_patient(session: Session, *, doctor_id: UUID, patient_id: UUID, assigned_by: UUID) -> PatientAssignment:
    """Assign a patient to a doctor. Raises 404 for a bad role, 409 if the patient already has a doctor.

    A patient may only have one doctor at a time (1 doctor : N patients). The admin
    must unassign the existing doctor before assigning a different one.
    """
    _get_user_with_role(session, doctor_id, UserRole.DOCTOR, "Doctor")
    _get_user_with_role(session, patient_id, UserRole.PATIENT, "Patient")
    existing = session.exec(
        select(PatientAssignment).where(PatientAssignment.patient_id == patient_id)
    ).first()
    if existing is not None:
        detail = (
            "Patient already assigned to this doctor"
            if existing.doctor_id == doctor_id
            else "Patient already assigned to another doctor"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    assignment = PatientAssignment(doctor_id=doctor_id, patient_id=patient_id, assigned_by=assigned_by)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return assignment


def unassign_patient(session: Session, assignment_id: UUID) -> None:
    assignment = session.get(PatientAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    session.delete(assignment)
    session.commit()


def list_assignments(session: Session, *, doctor_id: UUID | None = None) -> list[PatientAssignment]:
    statement = select(PatientAssignment)
    if doctor_id is not None:
        statement = statement.where(PatientAssignment.doctor_id == doctor_id)
    return list(session.exec(statement.order_by(col(PatientAssignment.created_at))).all())


def list_patients_for_doctor(session: Session, doctor_id: UUID) -> list[User]:
    statement = (
        select(User)
        .join(PatientAssignment, col(PatientAssignment.patient_id) == col(User.id))
        .where(PatientAssignment.doctor_id == doctor_id)
    )
    return list(session.exec(statement).all())


def get_assigned_doctor(session: Session, patient_id: UUID) -> User | None:
    """Inverse of list_patients_for_doctor: the one doctor (if any) currently
    assigned to this patient, per PatientAssignment's 1-doctor-per-patient
    constraint. None is a valid, expected result (not yet assigned)."""
    statement = (
        select(User)
        .join(PatientAssignment, col(PatientAssignment.doctor_id) == col(User.id))
        .where(PatientAssignment.patient_id == patient_id)
    )
    return session.exec(statement).first()


def _ensure_appointment_belongs_to_patient(session: Session, *, appointment_id: UUID, patient_id: UUID) -> None:
    """Same scoping pattern as get_clinical_note_or_404: 404 (not 403) so a doctor
    can't probe for another patient's appointment ids via this field."""
    appointment = session.get(Appointment, appointment_id)
    if appointment is None or appointment.patient_id != patient_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found for this patient")


def _validate_note_consistency(note: ClinicalNote) -> None:
    """Checks invariants on the fully assembled note (works for both create and
    partial update, since it looks at the merged end-state rather than the diff)."""
    if note.data_period_start is not None and note.data_period_end is not None:
        if note.data_period_start > note.data_period_end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="data_period_start must be on or before data_period_end",
            )
    has_systolic = note.blood_pressure_systolic is not None
    has_diastolic = note.blood_pressure_diastolic is not None
    if has_systolic != has_diastolic:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="blood_pressure_systolic and blood_pressure_diastolic must be provided together",
        )
    if (
        has_systolic
        and has_diastolic
        and note.blood_pressure_diastolic >= note.blood_pressure_systolic  # type: ignore[operator]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="blood_pressure_diastolic (huyết áp tâm trương) must be lower than blood_pressure_systolic (huyết áp tâm thu)",
        )


def create_clinical_note(
    session: Session,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    appointment_id: UUID | None,
    reason_for_visit: str,
    history_of_present_illness: str,
    past_medical_history: str | None,
    weight_kg: float | None,
    blood_pressure_systolic: int | None,
    blood_pressure_diastolic: int | None,
    clinical_exam_findings: str | None,
    data_period_start: date | None,
    data_period_end: date | None,
    hba1c_pct: float | None,
    paraclinical_results: str | None,
    diagnosis: str,
    treatment_plan: str,
    follow_up_date: date | None,
) -> ClinicalNote:
    if appointment_id is not None:
        _ensure_appointment_belongs_to_patient(session, appointment_id=appointment_id, patient_id=patient_id)
    note = ClinicalNote(
        patient_id=patient_id,
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        reason_for_visit=reason_for_visit,
        history_of_present_illness=history_of_present_illness,
        past_medical_history=past_medical_history,
        weight_kg=weight_kg,
        blood_pressure_systolic=blood_pressure_systolic,
        blood_pressure_diastolic=blood_pressure_diastolic,
        clinical_exam_findings=clinical_exam_findings,
        data_period_start=data_period_start,
        data_period_end=data_period_end,
        hba1c_pct=hba1c_pct,
        paraclinical_results=paraclinical_results,
        diagnosis=diagnosis,
        treatment_plan=treatment_plan,
        follow_up_date=follow_up_date,
    )
    _validate_note_consistency(note)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def list_clinical_notes(session: Session, patient_id: UUID) -> list[ClinicalNote]:
    statement = select(ClinicalNote).where(ClinicalNote.patient_id == patient_id)
    return list(session.exec(statement.order_by(col(ClinicalNote.created_at).desc())).all())


def get_clinical_note_or_404(session: Session, *, patient_id: UUID, note_id: UUID) -> ClinicalNote:
    """Scoped to patient_id so a note_id from a different patient can't be reached via this path."""
    note = session.get(ClinicalNote, note_id)
    if note is None or note.patient_id != patient_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinical note not found")
    return note


def update_clinical_note(session: Session, *, note: ClinicalNote, fields: dict[str, object]) -> ClinicalNote:
    """Partial update: `fields` is the request's `model_dump(exclude_unset=True)`,
    so only keys the caller actually sent are applied — everything else on the
    note is left untouched."""
    for key, value in fields.items():
        setattr(note, key, value)
    if "appointment_id" in fields and note.appointment_id is not None:
        _ensure_appointment_belongs_to_patient(session, appointment_id=note.appointment_id, patient_id=note.patient_id)
    _validate_note_consistency(note)
    note.updated_at = datetime.utcnow()
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def delete_clinical_note(session: Session, note: ClinicalNote) -> None:
    session.delete(note)
    session.commit()


def create_care_plan_target_version(
    session: Session,
    *,
    patient_id: UUID,
    doctor_id: UUID,
    target_low_mg_dl: float,
    target_high_mg_dl: float,
    danger_low_mg_dl: float,
    danger_high_mg_dl: float,
    risk_group: RiskGroup,
    effective_from: datetime,
    source_clinical_note_id: UUID | None,
    source_appointment_id: UUID | None,
    change_reason: str,
) -> CarePlanTarget:
    if effective_from.tzinfo is not None:
        effective_from = effective_from.astimezone(UTC).replace(tzinfo=None)
    if not danger_low_mg_dl < target_low_mg_dl < target_high_mg_dl < danger_high_mg_dl:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Thresholds must satisfy danger_low < target_low < target_high < danger_high",
        )
    if source_clinical_note_id is not None:
        note = get_clinical_note_or_404(session, patient_id=patient_id, note_id=source_clinical_note_id)
        if note.doctor_id != doctor_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Clinical note belongs to another doctor")
    if source_appointment_id is not None:
        _ensure_appointment_belongs_to_patient(
            session, appointment_id=source_appointment_id, patient_id=patient_id
        )

    current = session.exec(
        select(CarePlanTarget)
        .where(
            CarePlanTarget.patient_id == patient_id,
            CarePlanTarget.status == CarePlanTargetStatus.ACTIVE,
        )
        .order_by(col(CarePlanTarget.version).desc())
    ).first()
    version = 1 if current is None else current.version + 1
    if current is not None:
        if effective_from <= current.effective_from:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="effective_from must be later than the current care-plan version",
            )
        current.status = CarePlanTargetStatus.SUPERSEDED
        current.effective_to = effective_from
        current.updated_at = datetime.utcnow()
        session.add(current)

    target = CarePlanTarget(
        patient_id=patient_id,
        approved_by_doctor_id=doctor_id,
        version=version,
        target_low_mg_dl=target_low_mg_dl,
        target_high_mg_dl=target_high_mg_dl,
        danger_low_mg_dl=danger_low_mg_dl,
        danger_high_mg_dl=danger_high_mg_dl,
        risk_group=risk_group,
        effective_from=effective_from,
        source_clinical_note_id=source_clinical_note_id,
        source_appointment_id=source_appointment_id,
        change_reason=change_reason,
    )
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def list_care_plan_target_versions(session: Session, patient_id: UUID) -> list[CarePlanTarget]:
    statement = (
        select(CarePlanTarget)
        .where(CarePlanTarget.patient_id == patient_id)
        .order_by(col(CarePlanTarget.version).desc())
    )
    return list(session.exec(statement).all())

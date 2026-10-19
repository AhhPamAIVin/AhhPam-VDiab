from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.patients.models import CarePlanTargetStatus, RiskGroup
from codebase.backend.patients.service import create_care_plan_target_version


def user(session: Session, email: str, role: UserRole) -> User:
    value = User(email=email, full_name=email, role=role, hashed_password=hash_password("AdminPass123"))
    session.add(value)
    session.commit()
    session.refresh(value)
    return value


def test_follow_up_creates_new_version_and_closes_previous(session: Session):
    doctor = user(session, "version-doctor@example.com", UserRole.DOCTOR)
    patient = user(session, "version-patient@example.com", UserRole.PATIENT)
    first_at = datetime.now(UTC) - timedelta(days=30)
    first = create_care_plan_target_version(
        session, patient_id=patient.id, doctor_id=doctor.id, target_low_mg_dl=70,
        target_high_mg_dl=180, danger_low_mg_dl=54, danger_high_mg_dl=250,
        risk_group=RiskGroup.STANDARD_TYPE_2, effective_from=first_at,
        source_clinical_note_id=None, source_appointment_id=None, change_reason="Initial visit",
    )
    second_at = datetime.now(UTC)
    second = create_care_plan_target_version(
        session, patient_id=patient.id, doctor_id=doctor.id, target_low_mg_dl=75,
        target_high_mg_dl=170, danger_low_mg_dl=54, danger_high_mg_dl=240,
        risk_group=RiskGroup.OLDER_OR_HIGH_RISK, effective_from=second_at,
        source_clinical_note_id=None, source_appointment_id=None, change_reason="Follow-up review",
    )
    session.refresh(first)
    assert first.version == 1 and first.status == CarePlanTargetStatus.SUPERSEDED
    assert first.effective_to == second.effective_from
    assert second.version == 2 and second.status == CarePlanTargetStatus.ACTIVE


def test_rejects_invalid_threshold_order(session: Session):
    doctor = user(session, "invalid-doctor@example.com", UserRole.DOCTOR)
    patient = user(session, "invalid-patient@example.com", UserRole.PATIENT)
    with pytest.raises(HTTPException) as error:
        create_care_plan_target_version(
            session, patient_id=patient.id, doctor_id=doctor.id, target_low_mg_dl=70,
            target_high_mg_dl=260, danger_low_mg_dl=54, danger_high_mg_dl=250,
            risk_group=RiskGroup.STANDARD_TYPE_2, effective_from=datetime.now(UTC),
            source_clinical_note_id=None, source_appointment_id=None, change_reason="Invalid",
        )
    assert error.value.status_code == 422

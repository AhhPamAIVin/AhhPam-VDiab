from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealContext
from codebase.backend.health.service import create_glucose_record
from codebase.backend.notifications.models import Notification, NotificationType
from codebase.backend.notifications.service import MAX_GLUCOSE_ALERTS_PER_DAY, create_glucose_alert_notification
from codebase.backend.patients.models import CarePlanTarget, RiskGroup

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole, zalo_chat_id: str | None = None) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD), zalo_chat_id=zalo_chat_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def doctor_user(session: Session) -> User:
    return _make_user(session, email="doctor@diab.example.com", role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session) -> User:
    return _make_user(session, email="patient@diab.example.com", role=UserRole.PATIENT, zalo_chat_id="chat-123")


@pytest.fixture
def unlinked_patient_user(session: Session) -> User:
    return _make_user(session, email="patient2@diab.example.com", role=UserRole.PATIENT)


@pytest.fixture
def care_plan(session: Session, patient_user: User, doctor_user: User) -> CarePlanTarget:
    target = CarePlanTarget(
        patient_id=patient_user.id, approved_by_doctor_id=doctor_user.id, risk_group=RiskGroup.STANDARD_TYPE_2,
        target_low_mg_dl=70, target_high_mg_dl=180, danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _seed_history(session: Session, patient_id, doctor_id, *, base_value: float = 100):
    base_time = datetime.now(UTC) - timedelta(days=10)
    for i in range(6):
        session.add(GlucoseRecord(
            patient_id=patient_id, value=base_value + i, unit="mg/dL",
            measured_at=base_time + timedelta(hours=6 * i), meal_context=MealContext.FASTING,
            measurement_source="MANUAL", created_by=doctor_id,
        ))
    session.commit()


def _glucose_alerts(session: Session, patient_id) -> list[Notification]:
    return list(session.exec(
        select(Notification).where(
            Notification.patient_id == patient_id, Notification.notification_type == NotificationType.GLUCOSE_ALERT
        )
    ).all())


def test_watch_reading_creates_notification(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    _seed_history(session, patient_user.id, doctor_user.id, base_value=100)
    # far above a tight low-baseline band — should land WATCH
    record = create_glucose_record(
        session, patient_id=patient_user.id, value=230, unit="mg/dL", measured_at=datetime.now(UTC),
        note=None, created_by=doctor_user.id, meal_context=MealContext.FASTING,
    )
    assert record.threshold_status == "WATCH"
    alerts = _glucose_alerts(session, patient_user.id)
    assert len(alerts) == 1
    assert alerts[0].title == "Cảnh báo theo dõi đường huyết"
    assert "230" in alerts[0].message


def test_danger_reading_creates_urgent_notification(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    _seed_history(session, patient_user.id, doctor_user.id)
    record = create_glucose_record(
        session, patient_id=patient_user.id, value=300, unit="mg/dL", measured_at=datetime.now(UTC),
        note=None, created_by=doctor_user.id, meal_context=MealContext.FASTING,
    )
    assert record.threshold_status == "DANGER"
    alerts = _glucose_alerts(session, patient_user.id)
    assert len(alerts) == 1
    assert "NGUY HIỂM" in alerts[0].title
    assert "300" in alerts[0].message
    assert "bác sĩ" in alerts[0].message


def test_daily_cap_stops_new_watch_alerts(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    _seed_history(session, patient_user.id, doctor_user.id, base_value=100)
    today = datetime.now(UTC)
    for i in range(MAX_GLUCOSE_ALERTS_PER_DAY):
        record = GlucoseRecord(
            patient_id=patient_user.id, value=999, unit="mg/dL", measured_at=today,  # unused directly
            meal_context=MealContext.FASTING, measurement_source="MANUAL", created_by=doctor_user.id,
        )
        notification = create_glucose_alert_notification(
            session, patient=patient_user, glucose_record_id=record.id, measured_at=today,
            value=230, status="WATCH", predicted_min_mg_dl=120, predicted_max_mg_dl=180,
            danger_low_mg_dl=54, danger_high_mg_dl=250,
        )
        assert notification is not None

    over_cap = create_glucose_alert_notification(
        session, patient=patient_user, glucose_record_id="over-cap-id", measured_at=today,
        value=230, status="WATCH", predicted_min_mg_dl=120, predicted_max_mg_dl=180,
        danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    assert over_cap is None
    assert len(_glucose_alerts(session, patient_user.id)) == MAX_GLUCOSE_ALERTS_PER_DAY


def test_daily_cap_never_suppresses_danger_alerts(session: Session, patient_user: User, doctor_user: User, care_plan: CarePlanTarget):
    """A DANGER reading is the doctor-approved safety gate itself, not an AI
    guess — it must never be silently dropped just because the patient
    already used up today's WATCH quota."""
    _seed_history(session, patient_user.id, doctor_user.id, base_value=100)
    today = datetime.now(UTC)
    for i in range(MAX_GLUCOSE_ALERTS_PER_DAY):
        create_glucose_alert_notification(
            session, patient=patient_user, glucose_record_id=f"watch-{i}", measured_at=today,
            value=230, status="WATCH", predicted_min_mg_dl=120, predicted_max_mg_dl=180,
            danger_low_mg_dl=54, danger_high_mg_dl=250,
        )
    assert len(_glucose_alerts(session, patient_user.id)) == MAX_GLUCOSE_ALERTS_PER_DAY

    danger = create_glucose_alert_notification(
        session, patient=patient_user, glucose_record_id="danger-1", measured_at=today,
        value=300, status="DANGER", predicted_min_mg_dl=None, predicted_max_mg_dl=None,
        danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    assert danger is not None
    assert len(_glucose_alerts(session, patient_user.id)) == MAX_GLUCOSE_ALERTS_PER_DAY + 1


def test_same_record_id_is_idempotent(session: Session, patient_user: User, care_plan: CarePlanTarget):
    now = datetime.now(UTC)
    first = create_glucose_alert_notification(
        session, patient=patient_user, glucose_record_id="same-id", measured_at=now,
        value=230, status="WATCH", predicted_min_mg_dl=120, predicted_max_mg_dl=180,
        danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    second = create_glucose_alert_notification(
        session, patient=patient_user, glucose_record_id="same-id", measured_at=now,
        value=230, status="WATCH", predicted_min_mg_dl=120, predicted_max_mg_dl=180,
        danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    assert first.id == second.id
    assert len(_glucose_alerts(session, patient_user.id)) == 1


def test_unlinked_patient_still_gets_in_app_notification(
    session: Session, unlinked_patient_user: User, doctor_user: User
):
    """No zalo_chat_id must not suppress the in-app Notification row — only
    the outbound push is skipped (see notifications/service.py's
    _push_glucose_alert, and scheduler.py's _push_zalo_reminder for the same
    established pattern with the existing reminder types)."""
    target = CarePlanTarget(
        patient_id=unlinked_patient_user.id, approved_by_doctor_id=doctor_user.id, risk_group=RiskGroup.STANDARD_TYPE_2,
        target_low_mg_dl=70, target_high_mg_dl=180, danger_low_mg_dl=54, danger_high_mg_dl=250,
    )
    session.add(target)
    session.commit()
    _seed_history(session, unlinked_patient_user.id, doctor_user.id, base_value=100)

    record = create_glucose_record(
        session, patient_id=unlinked_patient_user.id, value=230, unit="mg/dL", measured_at=datetime.now(UTC),
        note=None, created_by=doctor_user.id, meal_context=MealContext.FASTING,
    )
    assert record.threshold_status == "WATCH"
    assert len(_glucose_alerts(session, unlinked_patient_user.id)) == 1

"""Epic 8.6 — "nhắc tôi uống thuốc lúc 8h tối": the agent drafts a daily
medication reminder card, the time read by code from the patient's words;
confirming adds the reminder. Also the clock reader fix it relies on
("tôi" is not "tối")."""

from datetime import UTC, date, datetime, time

import pytest
from sqlmodel import Session, select

from codebase.backend.agent.record_actions import _stated_clock, stated_time_of_day
from codebase.backend.auth.models import User, UserRole
from codebase.backend.medications.models import Medication
from codebase.backend.notifications.models import Notification, NotificationPreference, NotificationType
from codebase.backend.notifications.scheduler import _materialize_due_reminder
from codebase.models.agents.tools.agent_tools import TOOLS_BY_NAME, MedicationReminderArgs
from tests.test_backend.test_agent_tools_v2 import _confirm, _state
from tests.test_backend.test_patient_agent import _user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com", UserRole.PATIENT)


def _medication(session: Session, patient: User, name: str = "Metformin") -> Medication:
    medication = Medication(
        patient_id=patient.id,
        drug_name=name,
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=datetime(2026, 1, 1),
        created_by=patient.id,
    )
    session.add(medication)
    session.commit()
    return medication


def _set(session: Session, patient: User, message: str, evidence: str | None = None):
    return TOOLS_BY_NAME["set_medication_reminder"].handler(
        _state(patient, message), session, MedicationReminderArgs(evidence=evidence or message)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nhắc tôi uống thuốc lúc 8h tối", time(20, 0)),
        ("nhac toi uong thuoc luc 8h toi", time(20, 0)),
        ("nhắc uống thuốc 20h", time(20, 0)),
        ("7 giờ rưỡi sáng nhắc tôi uống thuốc", time(7, 30)),
        ("nhắc tôi lúc 1 giờ trưa", time(13, 0)),
        ("nhắc tôi lúc 9h15 tối", time(21, 15)),
        ("nhắc tôi uống thuốc lúc 8h", False),  # "tôi" is "I", not "tối"
        ("nhac toi uong thuoc luc 8h", False),
        ("nhắc tôi uống thuốc", None),
    ],
)
def test_reminder_time_is_read_from_the_patients_words(text, expected):
    assert stated_time_of_day(text) == expected


def test_recording_no_longer_reads_toi_as_evening():
    now = datetime(2026, 10, 28, 15, tzinfo=UTC)  # 22:00 in Vietnam
    assert _stated_clock("tôi đo lúc 9h được 120", now) is None
    assert _stated_clock("toi do luc 9h duoc 120", now) is None
    assert _stated_clock("đo lúc 9h tối được 120", now) == datetime(2026, 10, 28, 14, tzinfo=UTC)
    assert _stated_clock("do luc 9h toi duoc 120", now) == datetime(2026, 10, 28, 14, tzinfo=UTC)
    assert _stated_clock("tối qua lúc 9h đo 120", now) == datetime(2026, 10, 27, 14, tzinfo=UTC)


def test_confirming_the_card_adds_a_daily_medication_reminder(session: Session, patient: User):
    _medication(session, patient)
    outcome = _set(
        session, patient, "nhắc tôi uống metformin lúc 10 giờ tối nhé", "nhắc tôi uống metformin lúc 10 giờ tối"
    )
    draft = outcome.state_update["record_drafts"][0]
    assert draft == {
        "record_type": "medication_reminder",
        "time_of_day": "22:00",
        "label": "Nhắc uống thuốc Metformin",
        "summary": "Nhắc uống thuốc Metformin lúc 22:00 hằng ngày",
    }

    reply, _ = _confirm(session, patient, draft)
    assert reply.startswith("Đã đặt lời nhắc uống thuốc lúc 22:00 hằng ngày")
    reminders = session.exec(
        select(NotificationPreference).where(
            NotificationPreference.patient_id == patient.id,
            NotificationPreference.notification_type == NotificationType.MEDICATION_REMINDER,
        )
    ).all()
    assert [(r.time_of_day, r.label, r.is_custom) for r in reminders] == [(time(22), "Nhắc uống thuốc Metformin", True)]

    # Confirming a second card for the same time keeps the one reminder.
    _confirm(session, patient, draft)
    assert (
        len(
            session.exec(
                select(NotificationPreference).where(
                    NotificationPreference.notification_type == NotificationType.MEDICATION_REMINDER
                )
            ).all()
        )
        == 1
    )


def test_unclear_or_missing_time_asks_instead_of_drafting(session: Session, patient: User):
    unclear = _set(session, patient, "nhắc tôi uống thuốc lúc 8h")
    assert "record_drafts" not in unclear.state_update
    assert "sáng hay tối" in unclear.state_update["tool_results"]["medication_reminder"]

    missing = _set(session, patient, "nhắc tôi uống thuốc nhé")
    assert "record_drafts" not in missing.state_update
    assert "lúc mấy giờ" in missing.state_update["tool_results"]["medication_reminder"]


def test_existing_reminder_at_that_time_is_not_duplicated(session: Session, patient: User):
    # Medication reminders ride along with the default 06:00 glucose reminder.
    _medication(session, patient)
    outcome = _set(session, patient, "nhắc tôi uống thuốc lúc 6h sáng")
    assert "record_drafts" not in outcome.state_update
    assert "Đã có lời nhắc uống thuốc lúc 06:00" in outcome.state_update["tool_results"]["medication_reminder"]


def test_evidence_must_be_the_patients_own_words(session: Session, patient: User):
    outcome = _set(session, patient, "nhắc tôi uống thuốc", "nhắc tôi uống thuốc lúc 8h tối")
    assert outcome.state_update == {}


def test_a_medication_reminder_of_its_own_names_the_drugs(session: Session, patient: User):
    from zoneinfo import ZoneInfo

    _medication(session, patient)
    preference = NotificationPreference(
        patient_id=patient.id,
        reminder_kind="custom:x",
        notification_type=NotificationType.MEDICATION_REMINDER,
        label="Nhắc uống thuốc",
        time_of_day=time(21),
        is_custom=True,
    )
    session.add(preference)
    session.commit()
    _materialize_due_reminder(
        session,
        patient_id=patient.id,
        preference=preference,
        today=date(2026, 10, 28),
        chat_id=None,
        tz=ZoneInfo("Asia/Ho_Chi_Minh"),
        window_start_local=None,
    )
    notification = session.exec(select(Notification).where(Notification.patient_id == patient.id)).one()
    assert notification.notification_type == NotificationType.MEDICATION_REMINDER
    assert notification.message == "Đã đến giờ dùng thuốc: Metformin."

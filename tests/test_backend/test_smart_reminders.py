"""Epic 3.3: reminder-time suggestions — when one is made, that it never
changes the reminder by itself, accept/decline, and the 14-day cool-down."""

from datetime import date, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.notifications import service, smart_reminders
from codebase.backend.notifications.models import (
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
    SuggestionStatus,
)

PASSWORD = "AdminPass123"
# 2026-10-25 12:00 local; the reminder is 06:00 local = 23:00 UTC the day before.
NOW_UTC = datetime(2026, 10, 25, 5, 0)


@pytest.fixture
def patient(session: Session) -> User:
    user = User(email="p@diab.example.com", full_name="P", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _preference(session: Session, patient: User) -> NotificationPreference:
    [preference] = [p for p in service.get_effective_preferences(session, patient.id) if p.reminder_kind == "glucose_before_breakfast"]
    return preference


def _responses(session: Session, patient: User, delays_minutes: list[int | None], kind="glucose_before_breakfast"):
    """One reminder per day ending yesterday; None = not answered."""
    for i, delay in enumerate(reversed(delays_minutes)):
        day = date(2026, 10, 24) - timedelta(days=i)
        scheduled = datetime.combine(day, time(23, 0)) - timedelta(days=1)  # 06:00 local
        session.add(
            Notification(
                patient_id=patient.id, notification_type=NotificationType.GLUCOSE_REMINDER, title="t", message="m",
                slot_key=f"{kind}:{day.isoformat()}", slot_date=day, scheduled_for=scheduled,
                status=NotificationStatus.COMPLETED if delay is not None else NotificationStatus.PENDING,
                responded_at=scheduled + timedelta(minutes=delay) if delay is not None else None,
            )
        )
    session.commit()


def test_consistently_late_patient_gets_a_suggestion(session: Session, patient: User):
    preference = _preference(session, patient)
    _responses(session, patient, [55, 60, 65, 10, 60, 62, 58])  # 6 of 7 late, median ~07:00
    [suggestion] = smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)
    assert suggestion.previous_time == time(6, 0)
    assert suggestion.suggested_time == time(7, 0)
    assert (suggestion.late_count, suggestion.sample_count) == (6, 7)
    session.refresh(preference)
    assert preference.time_of_day == time(6, 0)  # never changed without the patient


@pytest.mark.parametrize(
    "delays",
    [
        [55, 60, 10, 10, 10, 62, 58],  # only 4 late
        [5, 10, 0, 15, 20, 5, 10],  # on time
        [300, 320, 310, 290, 305, 300, 315],  # > 4 h: not answers to this reminder
        [20, 25, 20, 25, 20, 25, 20],  # less than 30 min late
    ],
)
def test_no_suggestion(session: Session, patient: User, delays):
    _preference(session, patient)
    _responses(session, patient, delays)
    assert smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC) == []


def test_no_duplicate_while_pending(session: Session, patient: User):
    _preference(session, patient)
    _responses(session, patient, [60] * 7)
    smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)
    assert len(smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)) == 1


def test_decline_keeps_the_time_and_blocks_for_14_days(session: Session, patient: User):
    preference = _preference(session, patient)
    _responses(session, patient, [60] * 7)
    [suggestion] = smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)
    declined = smart_reminders.decline_suggestion(session, suggestion.id, patient.id)
    assert declined.status == SuggestionStatus.DECLINED
    session.refresh(preference)
    assert preference.time_of_day == time(6, 0)
    assert smart_reminders.refresh_suggestions(session, patient.id, datetime.utcnow() + timedelta(days=13)) == []


def test_accept_moves_the_reminder(session: Session, patient: User):
    preference = _preference(session, patient)
    _responses(session, patient, [60] * 7)
    [suggestion] = smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)
    accepted = smart_reminders.accept_suggestion(session, suggestion.id, patient.id)
    assert accepted.status == SuggestionStatus.ACCEPTED
    session.refresh(preference)
    assert preference.time_of_day == time(7, 0)


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_api(client: AsyncClient, session: Session, patient: User):
    _preference(session, patient)
    _responses(session, patient, [60] * 7)
    [suggestion] = smart_reminders.refresh_suggestions(session, patient.id, NOW_UTC)
    headers = await _headers(client, patient)

    listed = (await client.get("/api/v1/notifications/reminder-suggestions", headers=headers)).json()
    assert [s["id"] for s in listed] == [str(suggestion.id)]
    assert listed[0]["label"] == "Đo đường huyết trước bữa sáng"

    other = User(email="o@diab.example.com", full_name="O", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(other)
    session.commit()
    url = f"/api/v1/notifications/reminder-suggestions/{suggestion.id}/accept"
    assert (await client.post(url, headers=await _headers(client, other))).status_code == 404

    assert (await client.post(url, headers=headers)).json()["status"] == "accepted"
    assert (await client.post(url, headers=headers)).status_code == 409

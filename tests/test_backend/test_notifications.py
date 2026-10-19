from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.config import get_settings
from codebase.backend.health.models import BloodPressureRecord, GlucoseRecord
from codebase.backend.medications.models import Medication, MedicationStatus
from codebase.backend.notifications.defaults import DEFAULT_REMINDERS
from codebase.backend.notifications.models import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from codebase.backend.notifications.scheduler import generate_due_reminders, run_reminder_tick
from codebase.backend.notifications.service import (
    auto_complete_pending,
    consume_link_code,
    create_link_code,
    get_effective_preferences,
    get_or_create_notification,
    update_preference,
)
from codebase.backend.patients.models import PatientAssignment

ADMIN_EMAIL = "admin@diab.example.com"
DOCTOR_EMAIL = "doctor@diab.example.com"
PATIENT_EMAIL = "patient@diab.example.com"
OTHER_PATIENT_EMAIL = "patient2@diab.example.com"
PASSWORD = "AdminPass123"

BREAKFAST_BEFORE_TIME = next(d.time_of_day for d in DEFAULT_REMINDERS if d.reminder_kind == "glucose_before_breakfast")


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def admin_user(session: Session) -> User:
    return _make_user(session, email=ADMIN_EMAIL, role=UserRole.ADMIN)


@pytest.fixture
def doctor_user(session: Session) -> User:
    return _make_user(session, email=DOCTOR_EMAIL, role=UserRole.DOCTOR)


@pytest.fixture
def patient_user(session: Session) -> User:
    return _make_user(session, email=PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def other_patient_user(session: Session) -> User:
    return _make_user(session, email=OTHER_PATIENT_EMAIL, role=UserRole.PATIENT)


@pytest.fixture
def assigned(session: Session, doctor_user: User, patient_user: User) -> None:
    session.add(PatientAssignment(doctor_id=doctor_user.id, patient_id=patient_user.id, assigned_by=doctor_user.id))
    session.commit()


def _breakfast_before_now(on_date: date | None = None) -> datetime:
    return datetime.combine(on_date or date.today(), BREAKFAST_BEFORE_TIME)


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- Preferences: materialization + defaults ----


def test_effective_preferences_materializes_all_defaults(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    assert {p.reminder_kind for p in preferences} == {d.reminder_kind for d in DEFAULT_REMINDERS}
    assert all(not p.is_custom for p in preferences)


def test_effective_preferences_is_idempotent(session: Session, patient_user: User):
    first = get_effective_preferences(session, patient_user.id)
    second = get_effective_preferences(session, patient_user.id)
    assert {p.id for p in first} == {p.id for p in second}


# ---- Reminder generation off the preference plan ----


def test_generate_due_reminders_creates_glucose_reminder_at_matching_time(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())

    notifications = list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all())
    assert len(notifications) == 1
    assert notifications[0].notification_type == NotificationType.GLUCOSE_REMINDER
    assert notifications[0].status == NotificationStatus.PENDING


def test_generate_due_reminders_skips_when_no_slot_matches(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    off_schedule = datetime.combine(date.today(), time(3, 17))
    generate_due_reminders(session, now=off_schedule)

    assert list(session.exec(select(Notification)).all()) == []


def test_generate_due_reminders_is_idempotent(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    now = _breakfast_before_now()
    generate_due_reminders(session, now=now)
    generate_due_reminders(session, now=now)

    notifications = list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all())
    assert len(notifications) == 1


def test_generate_due_reminders_respects_disabled_preference(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    breakfast_pref.is_enabled = False
    session.add(breakfast_pref)
    session.commit()

    generate_due_reminders(session, now=_breakfast_before_now())

    assert list(session.exec(select(Notification)).all()) == []


def test_generate_due_reminders_respects_custom_time(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    breakfast_pref.time_of_day = time(6, 45)
    session.add(breakfast_pref)
    session.commit()

    # Original default time no longer fires...
    generate_due_reminders(session, now=_breakfast_before_now())
    assert list(session.exec(select(Notification)).all()) == []

    # ...but the patient's adjusted time does.
    generate_due_reminders(session, now=datetime.combine(date.today(), time(6, 45)))
    assert len(list(session.exec(select(Notification)).all())) == 1


# ---- Preference time changes retrigger today's still-open reminder ----


def test_update_preference_retriggers_today_when_new_time_is_still_ahead(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    today = date.today()
    stale = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key=f"glucose_before_breakfast:{today.isoformat()}",
        slot_date=today,
        scheduled_for=_breakfast_before_now(),
        title="t",
        message="m",
    )

    update_preference(
        session,
        preference=breakfast_pref,
        time_of_day=time(6, 45),
        is_enabled=None,
        label=None,
        now=datetime.combine(today, time(6, 30)),
    )

    assert session.get(Notification, stale.id) is None

    generate_due_reminders(session, now=datetime.combine(today, time(6, 45)))
    fresh = list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all())
    assert len(fresh) == 1
    assert fresh[0].id != stale.id
    # scheduled_for is stored naive-but-UTC (see scheduler.py's _to_utc_naive) —
    # 06:45 is this patient's local reminder_timezone wall-clock target, so the
    # stored value is that instant's UTC equivalent, not the bare local time.
    tz = ZoneInfo(get_settings().reminder_timezone)
    expected_scheduled_for = datetime.combine(today, time(6, 45), tzinfo=tz).astimezone(UTC).replace(tzinfo=None)
    assert fresh[0].scheduled_for == expected_scheduled_for


def test_update_preference_does_not_retrigger_when_new_time_already_passed(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    today = date.today()
    stale = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key=f"glucose_before_breakfast:{today.isoformat()}",
        slot_date=today,
        scheduled_for=_breakfast_before_now(),
        title="t",
        message="m",
    )

    update_preference(
        session,
        preference=breakfast_pref,
        time_of_day=time(6, 0),
        is_enabled=None,
        label=None,
        now=datetime.combine(today, time(12, 0)),
    )

    assert session.get(Notification, stale.id) is not None


def test_update_preference_does_not_resurrect_a_completed_reminder(session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    today = date.today()
    completed = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key=f"glucose_before_breakfast:{today.isoformat()}",
        slot_date=today,
        scheduled_for=_breakfast_before_now(),
        title="t",
        message="m",
    )
    completed.status = NotificationStatus.COMPLETED
    session.add(completed)
    session.commit()

    update_preference(
        session,
        preference=breakfast_pref,
        time_of_day=time(9, 0),
        is_enabled=None,
        label=None,
        now=datetime.combine(today, time(6, 30)),
    )

    assert session.get(Notification, completed.id) is not None


def test_generate_due_reminders_includes_medication_when_active(session: Session, patient_user: User):
    session.add(
        Medication(
            patient_id=patient_user.id,
            drug_name="Metformin",
            dose="500mg",
            frequency="twice daily",
            start_date=datetime.now(UTC).replace(tzinfo=None),
            status=MedicationStatus.ACTIVE,
            created_by=patient_user.id,
        )
    )
    session.commit()
    get_effective_preferences(session, patient_user.id)

    generate_due_reminders(session, now=_breakfast_before_now())

    med_notification = session.exec(
        select(Notification).where(
            Notification.patient_id == patient_user.id,
            Notification.notification_type == NotificationType.MEDICATION_REMINDER,
        )
    ).one()
    assert "Metformin" in med_notification.message


def test_generate_due_reminders_skips_medication_when_none_active(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())

    types = {n.notification_type for n in session.exec(select(Notification)).all()}
    assert NotificationType.MEDICATION_REMINDER not in types


def test_generate_due_reminders_skips_inactive_and_non_patient_users(
    session: Session, patient_user: User, doctor_user: User
):
    patient_user.is_active = False
    session.add(patient_user)
    session.commit()

    generate_due_reminders(session, now=_breakfast_before_now())

    assert list(session.exec(select(Notification)).all()) == []


def test_six_glucose_slots_across_the_day(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    glucose_defaults = [d for d in DEFAULT_REMINDERS if d.notification_type == NotificationType.GLUCOSE_REMINDER]
    assert len(glucose_defaults) == 6
    for default in glucose_defaults:
        generate_due_reminders(session, now=datetime.combine(date.today(), default.time_of_day))

    glucose_notifications = session.exec(
        select(Notification).where(
            Notification.patient_id == patient_user.id,
            Notification.notification_type == NotificationType.GLUCOSE_REMINDER,
        )
    ).all()
    assert len(glucose_notifications) == 6


# ---- A record logged before a reminder's own trigger time pre-empts it ----
#
# auto_complete_pending (service.py) already resolves a reminder logged *after*
# it fired. These cover the other half: logging *before* the reminder would
# ever fire must stop it from being created (and pushed) in the first place —
# see scheduler.py's _already_satisfied / _previous_slot_time.


def _local_utc(on_date: date, at: time) -> datetime:
    tz = ZoneInfo(get_settings().reminder_timezone)
    return datetime.combine(on_date, at, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def test_early_glucose_log_pre_empts_the_reminder_that_would_fire_later(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    today = date.today()
    session.add(
        GlucoseRecord(
            patient_id=patient_user.id,
            value=95,
            measured_at=_local_utc(today, time(5, 30)),
            created_by=patient_user.id,
        )
    )
    session.commit()

    breakfast_before = next(d for d in DEFAULT_REMINDERS if d.reminder_kind == "glucose_before_breakfast")
    generate_due_reminders(session, now=datetime.combine(today, breakfast_before.time_of_day))

    assert list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all()) == []


def test_early_glucose_log_does_not_suppress_a_later_unrelated_slot(session: Session, patient_user: User):
    """The pre-empt window is bounded by the *previous* same-type slot, not the
    whole day — an early morning reading must not silently swallow every later
    glucose reminder too (the patient still needs to check after lunch, etc.)."""
    get_effective_preferences(session, patient_user.id)
    today = date.today()
    session.add(
        GlucoseRecord(
            patient_id=patient_user.id,
            value=95,
            measured_at=_local_utc(today, time(5, 30)),
            created_by=patient_user.id,
        )
    )
    session.commit()

    before_breakfast = next(d for d in DEFAULT_REMINDERS if d.reminder_kind == "glucose_before_breakfast")
    before_lunch = next(d for d in DEFAULT_REMINDERS if d.reminder_kind == "glucose_before_lunch")
    generate_due_reminders(session, now=datetime.combine(today, before_breakfast.time_of_day))
    generate_due_reminders(session, now=datetime.combine(today, before_lunch.time_of_day))

    glucose_notifications = session.exec(
        select(Notification).where(
            Notification.patient_id == patient_user.id,
            Notification.notification_type == NotificationType.GLUCOSE_REMINDER,
        )
    ).all()
    assert len(glucose_notifications) == 1
    assert glucose_notifications[0].slot_key == f"glucose_before_lunch:{today.isoformat()}"


def test_early_blood_pressure_log_pre_empts_the_reminder(session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    today = date.today()
    session.add(
        BloodPressureRecord(
            patient_id=patient_user.id,
            systolic=118,
            diastolic=76,
            measured_at=_local_utc(today, time(6, 0)),
            created_by=patient_user.id,
        )
    )
    session.commit()

    bp_morning = next(d for d in DEFAULT_REMINDERS if d.reminder_kind == "blood_pressure_morning")
    generate_due_reminders(session, now=datetime.combine(today, bp_morning.time_of_day))

    assert list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all()) == []


def test_early_glucose_log_does_not_suppress_the_riding_medication_reminder(session: Session, patient_user: User):
    """A pre-empted glucose reminder must not take its riding medication
    reminder down with it — logging glucose early says nothing about whether
    the dose was taken (see medications/service.py's design note on why
    medication has no equivalent pre-empt check yet)."""
    session.add(
        Medication(
            patient_id=patient_user.id,
            drug_name="Metformin",
            dose="500mg",
            frequency="twice daily",
            start_date=datetime.now(UTC).replace(tzinfo=None),
            status=MedicationStatus.ACTIVE,
            created_by=patient_user.id,
        )
    )
    get_effective_preferences(session, patient_user.id)
    today = date.today()
    session.add(
        GlucoseRecord(
            patient_id=patient_user.id,
            value=95,
            measured_at=_local_utc(today, time(5, 30)),
            created_by=patient_user.id,
        )
    )
    session.commit()

    breakfast_before = next(d for d in DEFAULT_REMINDERS if d.reminder_kind == "glucose_before_breakfast")
    generate_due_reminders(session, now=datetime.combine(today, breakfast_before.time_of_day))

    notifications = list(session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).all())
    assert [n.notification_type for n in notifications] == [NotificationType.MEDICATION_REMINDER]


# ---- Performance: query count must not scale with patient count ----
#
# Real incident this section guards against: with a several-hundred-patient
# demo dataset, generate_due_reminders used to call
# service.get_effective_preferences per patient in a loop (2 individual
# queries each) — ~2000 sequential round trips on every 1-minute tick, which
# meant the tick never finished inside its own interval. APScheduler's
# max_instances=1 then piled it up on itself forever (logs showed "skipped:
# maximum number of running instances reached" every single cycle, meaning
# it likely never completed once), and its long-lived session held a
# connection-pool slot the whole time — starving real request traffic (a
# POST /health/glucose that should take <1s was observed taking ~28s,
# waiting on SQLAlchemy's pool checkout timeout, not the write itself). The
# fix batches preference reads/writes into a handful of queries total
# regardless of patient count (scheduler.py's _preferences_by_patient /
# _materialize_missing_defaults) — these tests prove that directly by
# counting actual SQL statements, the only kind of test that would have
# caught the original bug (the functional tests above all still pass
# whether the query pattern is O(1) or O(patients), since both produce the
# same reminders — just at very different costs).


@contextmanager
def _count_queries(session: Session) -> Generator[list[int], None, None]:
    engine = session.get_bind()
    count = [0]

    def _on_execute(*_args: object, **_kwargs: object) -> None:
        count[0] += 1

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)


def _make_patients(session: Session, *, prefix: str, n: int) -> list[User]:
    return [_make_user(session, email=f"{prefix}-{i}@diab.example.com", role=UserRole.PATIENT) for i in range(n)]


# `now` used across this section: a fixed off-the-hour timestamp (not
# datetime.utcnow()) chosen to not match any DEFAULT_REMINDERS time_of_day
# (all on-the-hour — see notifications/defaults.py). Matching one would
# trigger _materialize_due_reminder's extra per-match queries and make the
# query count depend on wall-clock time when the test happens to run,
# rather than just on patient count.
_OFF_HOUR_NOW = datetime(2026, 1, 15, 3, 17)


def test_generate_due_reminders_read_path_query_count_does_not_scale_with_patient_count(session: Session):
    """The actual production incident, precisely: every tick — every single
    minute, forever, including on patients already fully materialized —
    called service.get_effective_preferences per patient (2 individual
    queries each). That's what made the tick recur forever without ever
    finishing at scale; the *initial* materialize-on-first-sight cost was
    never the problem (see the batching test below for that one).

    Both patient counts are materialized *before* the counted call — this
    isolates the recurring steady-state cost from the one-time materialize
    cost, and it must come out flat: 35 total active patients must query
    exactly as much as 5, or a regression back to the per-patient loop would
    show up as growth here."""
    small = _make_patients(session, prefix="read-flat-small", n=5)
    generate_due_reminders(session, now=_OFF_HOUR_NOW)  # materializes defaults; not counted

    with _count_queries(session) as small_count:
        generate_due_reminders(session, now=_OFF_HOUR_NOW)

    large = _make_patients(session, prefix="read-flat-large", n=30)
    generate_due_reminders(session, now=_OFF_HOUR_NOW)  # materializes the 30 new patients too; not counted

    with _count_queries(session) as large_count:
        generate_due_reminders(session, now=_OFF_HOUR_NOW)

    assert small[0].id != large[0].id  # sanity: distinct patients each scale
    assert small_count[0] > 0
    # Flat, not "generous": with everyone already materialized, the whole
    # job is _active_patient_ids + _preferences_by_patient — two queries, no
    # matter how many patients are active.
    assert large_count[0] == small_count[0]


def test_materialize_missing_defaults_batches_inserts_not_one_per_row(session: Session):
    """Companion to the read-path test above, covering the *other* half of
    generate_due_reminders: seeding brand-new patients and materializing
    their defaults in one tick must not fall back to one INSERT per row.
    Real regression caught by this exact test while writing it:
    session.add_all(...) + a normal ORM commit — a very natural way to
    write "one bulk insert" — actually made the ORM flush one INSERT
    statement per object, so 25 new patients still took ~5x the statements
    of 5 new patients despite the read path above already being flat. Fixed
    by using Core insert() with a list of param dicts instead (see
    scheduler.py's _materialize_missing_defaults).

    Bound is deliberately loose (half of the naive per-row ceiling, not a
    tight pin): SQLite's own per-statement parameter limit already forces
    some chunking that a real Postgres deployment wouldn't need, so a tight
    number here would be pinned to this test backend's quirk rather than
    the thing that actually matters — batched, not one-per-row."""
    patients = _make_patients(session, prefix="materialize-batch", n=25)

    with _count_queries(session) as count:
        generate_due_reminders(session, now=_OFF_HOUR_NOW)

    naive_one_insert_per_row_ceiling = len(patients) * len(DEFAULT_REMINDERS)
    assert count[0] < naive_one_insert_per_row_ceiling * 0.5


def test_generate_due_reminders_still_correct_at_larger_patient_count(session: Session):
    """Sibling to the two performance tests above: batching preference
    reads across many patients (_preferences_by_patient) must not scramble
    which row belongs to which patient. 30 patients share the same default
    schedule; one turns their breakfast glucose reminder off. At that
    shared default time, everyone else must get a reminder and that one
    patient must not — proving each patient's own preferences, read out of
    a single batched query, stay correctly attributed to them even at
    scale, not just correct for one patient in isolation."""
    patients = _make_patients(session, prefix="correctness-at-scale", n=30)
    opted_out = patients[17]
    for patient in patients:
        preferences = get_effective_preferences(session, patient.id)
        if patient.id == opted_out.id:
            breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
            update_preference(session, preference=breakfast_pref, time_of_day=None, is_enabled=False, label=None)

    generate_due_reminders(session, now=_breakfast_before_now())

    notified_patient_ids = {
        n.patient_id
        for n in session.exec(select(Notification)).all()
        if n.notification_type == NotificationType.GLUCOSE_REMINDER
    }
    assert opted_out.id not in notified_patient_ids
    assert notified_patient_ids == {p.id for p in patients if p.id != opted_out.id}


# ---- Reminders that keep popping up until resolved ----


def test_snoozed_reminder_reverts_to_pending_once_snooze_expires(session: Session, patient_user: User):
    now = datetime.utcnow()
    today = now.date()
    notification = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key="glucose_before_breakfast:today",
        slot_date=today,
        scheduled_for=now,
        title="t",
        message="m",
    )
    notification.status = NotificationStatus.SNOOZED
    notification.snoozed_until = now - timedelta(minutes=1)  # already due
    session.add(notification)
    session.commit()

    run_reminder_tick(session, now=now)

    session.refresh(notification)
    assert notification.status == NotificationStatus.PENDING
    assert notification.snoozed_until is None


def test_still_snoozed_reminder_is_left_alone(session: Session, patient_user: User):
    now = datetime.utcnow()
    today = now.date()
    notification = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key="glucose_before_breakfast:today",
        slot_date=today,
        scheduled_for=now,
        title="t",
        message="m",
    )
    notification.status = NotificationStatus.SNOOZED
    notification.snoozed_until = now + timedelta(hours=1)  # not due yet
    session.add(notification)
    session.commit()

    run_reminder_tick(session, now=now)

    session.refresh(notification)
    assert notification.status == NotificationStatus.SNOOZED


def test_pending_reminder_is_renudged_after_interval(session: Session, patient_user: User):
    now = datetime.utcnow()
    today = now.date()
    notification = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key="glucose_before_breakfast:today",
        slot_date=today,
        scheduled_for=now - timedelta(hours=2),
        title="t",
        message="m",
    )
    notification.last_notified_at = now - timedelta(minutes=45)
    session.add(notification)
    session.commit()

    run_reminder_tick(session, now=now)

    session.refresh(notification)
    assert notification.nudge_count == 1
    assert notification.last_notified_at == now


def test_freshly_notified_pending_reminder_is_not_renudged_yet(session: Session, patient_user: User):
    now = datetime.utcnow()
    today = now.date()
    notification = get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key="glucose_before_breakfast:today",
        slot_date=today,
        scheduled_for=now,
        title="t",
        message="m",
    )
    notification.last_notified_at = now - timedelta(minutes=5)
    session.add(notification)
    session.commit()

    run_reminder_tick(session, now=now)

    session.refresh(notification)
    assert notification.nudge_count == 0


# ---- Service: idempotency + auto-complete ----


def test_get_or_create_notification_returns_existing_row(session: Session, patient_user: User):
    kwargs = dict(
        patient_id=patient_user.id,
        notification_type=NotificationType.BLOOD_PRESSURE_REMINDER,
        slot_key="blood_pressure_morning:2026-08-11",
        slot_date=date(2026, 8, 11),
        scheduled_for=datetime(2026, 8, 11, 7, 0),
        title="Nhắc đo huyết áp buổi sáng",
        message="Đã đến giờ đo huyết áp.",
    )
    first = get_or_create_notification(session, **kwargs)
    second = get_or_create_notification(session, **kwargs)
    assert first.id == second.id
    assert len(list(session.exec(select(Notification)).all())) == 1


def test_auto_complete_pending_marks_matching_notifications_completed(session: Session, patient_user: User):
    today = date.today()
    get_or_create_notification(
        session,
        patient_id=patient_user.id,
        notification_type=NotificationType.GLUCOSE_REMINDER,
        slot_key="glucose_before_breakfast:today",
        slot_date=today,
        scheduled_for=datetime.combine(today, time(6, 0)),
        title="t",
        message="m",
    )

    auto_complete_pending(
        session, patient_id=patient_user.id, notification_type=NotificationType.GLUCOSE_REMINDER, on_date=today
    )

    notification = session.exec(select(Notification)).one()
    assert notification.status == NotificationStatus.COMPLETED
    assert notification.responded_at is not None


# ---- API: notifications ----


@pytest.mark.asyncio
async def test_patient_lists_own_notifications(client: AsyncClient, session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    token = await _login(client, PATIENT_EMAIL)

    response = await client.get("/api/v1/notifications", headers=_auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["patient_id"] == str(patient_user.id)
    assert body[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_patient_cannot_list_another_patients_notifications(
    client: AsyncClient, session: Session, patient_user: User, other_patient_user: User
):
    token = await _login(client, PATIENT_EMAIL)

    response = await client.get(
        "/api/v1/notifications", params={"patient_id": str(other_patient_user.id)}, headers=_auth_headers(token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_assigned_doctor_can_list_patient_notifications(
    client: AsyncClient, session: Session, doctor_user: User, patient_user: User, assigned: None
):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    token = await _login(client, DOCTOR_EMAIL)

    response = await client.get(
        "/api/v1/notifications", params={"patient_id": str(patient_user.id)}, headers=_auth_headers(token)
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_patient_marks_reminder_completed(client: AsyncClient, session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    notification = session.exec(select(Notification)).one()
    token = await _login(client, PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}", json={"status": "completed"}, headers=_auth_headers(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_snoozing_reminder_requires_snooze_minutes(client: AsyncClient, session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    notification = session.exec(select(Notification)).one()
    token = await _login(client, PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}", json={"status": "snoozed"}, headers=_auth_headers(token)
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_snoozing_reminder_sets_snoozed_until(client: AsyncClient, session: Session, patient_user: User):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    notification = session.exec(select(Notification)).one()
    token = await _login(client, PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}",
        json={"status": "snoozed", "snooze_minutes": 30},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["snoozed_until"] is not None


@pytest.mark.asyncio
async def test_patient_cannot_update_another_patients_notification(
    client: AsyncClient, session: Session, patient_user: User, other_patient_user: User
):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    notification = session.exec(select(Notification).where(Notification.patient_id == patient_user.id)).one()
    token = await _login(client, OTHER_PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}", json={"status": "completed"}, headers=_auth_headers(token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_logging_glucose_auto_completes_pending_glucose_reminder(
    client: AsyncClient, session: Session, patient_user: User
):
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=_breakfast_before_now())
    token = await _login(client, PATIENT_EMAIL)

    response = await client.post(
        "/api/v1/health/glucose",
        json={
            "patient_id": str(patient_user.id),
            "value": 110.5,
            "unit": "mg/dL",
            "meal_context": "FASTING",
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text

    notification = session.exec(select(Notification)).one()
    assert notification.status == NotificationStatus.COMPLETED


@pytest.mark.asyncio
async def test_logging_blood_pressure_auto_completes_pending_reminder(
    client: AsyncClient, session: Session, patient_user: User
):
    bp_time = next(d.time_of_day for d in DEFAULT_REMINDERS if d.reminder_kind == "blood_pressure_morning")
    get_effective_preferences(session, patient_user.id)
    generate_due_reminders(session, now=datetime.combine(date.today(), bp_time))
    token = await _login(client, PATIENT_EMAIL)

    response = await client.post(
        "/api/v1/health/blood-pressure",
        json={
            "patient_id": str(patient_user.id),
            "systolic": 120,
            "diastolic": 80,
            "measured_at": datetime.now(UTC).isoformat(),
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text

    notification = session.exec(select(Notification)).one()
    assert notification.status == NotificationStatus.COMPLETED


# ---- API: notification preferences ----


@pytest.mark.asyncio
async def test_patient_lists_own_preference_plan(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)

    response = await client.get("/api/v1/notifications/preferences", headers=_auth_headers(token))
    assert response.status_code == 200, response.text
    assert len(response.json()) == len(DEFAULT_REMINDERS)


@pytest.mark.asyncio
async def test_patient_adjusts_reminder_time(client: AsyncClient, session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    token = await _login(client, PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/preferences/{breakfast_pref.id}",
        json={"time_of_day": "06:45:00"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["time_of_day"] == "06:45:00"


@pytest.mark.asyncio
async def test_patient_turns_off_a_reminder(client: AsyncClient, session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    token = await _login(client, PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/preferences/{breakfast_pref.id}",
        json={"is_enabled": False},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_enabled"] is False


@pytest.mark.asyncio
async def test_patient_adds_custom_reminder(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)

    response = await client.post(
        "/api/v1/notifications/preferences",
        json={"label": "Cân nặng buổi sáng", "time_of_day": "06:15:00"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_custom"] is True
    assert body["label"] == "Cân nặng buổi sáng"


@pytest.mark.asyncio
async def test_patient_deletes_custom_reminder(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)
    create_response = await client.post(
        "/api/v1/notifications/preferences",
        json={"label": "Custom", "time_of_day": "06:15:00"},
        headers=_auth_headers(token),
    )
    preference_id = create_response.json()["id"]

    response = await client.delete(f"/api/v1/notifications/preferences/{preference_id}", headers=_auth_headers(token))
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_patient_cannot_delete_a_default_reminder(client: AsyncClient, session: Session, patient_user: User):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    token = await _login(client, PATIENT_EMAIL)

    response = await client.delete(
        f"/api/v1/notifications/preferences/{breakfast_pref.id}", headers=_auth_headers(token)
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_patient_cannot_manage_another_patients_plan(
    client: AsyncClient, session: Session, patient_user: User, other_patient_user: User
):
    preferences = get_effective_preferences(session, patient_user.id)
    breakfast_pref = next(p for p in preferences if p.reminder_kind == "glucose_before_breakfast")
    token = await _login(client, OTHER_PATIENT_EMAIL)

    response = await client.patch(
        f"/api/v1/notifications/preferences/{breakfast_pref.id}",
        json={"is_enabled": False},
        headers=_auth_headers(token),
    )
    assert response.status_code == 403


# ---- Zalo chat linking ----


def test_consume_link_code_links_chat_id_and_marks_consumed(session: Session, patient_user: User):
    link_code = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)

    linked = consume_link_code(session, code=link_code.code, chat_id="chat-abc")

    assert linked is True
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id == "chat-abc"
    session.refresh(link_code)
    assert link_code.consumed_at is not None


def test_consume_link_code_is_case_insensitive_and_trims_whitespace(session: Session, patient_user: User):
    link_code = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)

    linked = consume_link_code(session, code=f"  {link_code.code.lower()}  ", chat_id="chat-abc")

    assert linked is True
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id == "chat-abc"


def test_consume_link_code_rejects_unknown_code(session: Session, patient_user: User):
    create_link_code(session, user_id=patient_user.id, ttl_minutes=10)

    linked = consume_link_code(session, code="NOTREAL1", chat_id="chat-abc")

    assert linked is False
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id is None


def test_consume_link_code_rejects_expired_code(session: Session, patient_user: User):
    now = datetime.utcnow()
    link_code = create_link_code(session, user_id=patient_user.id, ttl_minutes=10, now=now - timedelta(minutes=20))

    linked = consume_link_code(session, code=link_code.code, chat_id="chat-abc", now=now)

    assert linked is False
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id is None


def test_consume_link_code_cannot_be_redeemed_twice(session: Session, patient_user: User):
    link_code = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)
    assert consume_link_code(session, code=link_code.code, chat_id="chat-first") is True

    replay = consume_link_code(session, code=link_code.code, chat_id="chat-second")

    assert replay is False
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id == "chat-first"


def test_create_link_code_invalidates_prior_outstanding_code(session: Session, patient_user: User):
    """A patient re-opening the connect modal gets a fresh code; the old one
    (possibly still visible in a stale browser tab) must no longer work —
    otherwise two valid codes for the same patient could race to link two
    different chat_ids."""
    stale = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)
    fresh = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)
    assert stale.code != fresh.code

    assert consume_link_code(session, code=stale.code, chat_id="chat-stale") is False
    assert consume_link_code(session, code=fresh.code, chat_id="chat-fresh") is True
    session.refresh(patient_user)
    assert patient_user.zalo_chat_id == "chat-fresh"


def test_create_link_code_is_random_per_call(session: Session, patient_user: User, other_patient_user: User):
    codes = {create_link_code(session, user_id=patient_user.id, ttl_minutes=10).code for _ in range(20)}
    assert len(codes) == 20  # no collisions across 20 draws from the 32-char/8-length alphabet

    other_code = create_link_code(session, user_id=other_patient_user.id, ttl_minutes=10)
    assert consume_link_code(session, code=other_code.code, chat_id="chat-other") is True
    session.refresh(other_patient_user)
    session.refresh(patient_user)
    assert other_patient_user.zalo_chat_id == "chat-other"
    assert patient_user.zalo_chat_id is None


@pytest.mark.asyncio
async def test_patient_creates_zalo_link_code(client: AsyncClient, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)

    response = await client.post("/api/v1/notifications/zalo/link-code", headers=_auth_headers(token))

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["code"]) == 8
    assert body["expires_at"]


@pytest.mark.asyncio
async def test_zalo_link_status_reflects_linking(client: AsyncClient, session: Session, patient_user: User):
    token = await _login(client, PATIENT_EMAIL)

    before = await client.get("/api/v1/notifications/zalo/link-status", headers=_auth_headers(token))
    assert before.json() == {"linked": False}

    link_code = create_link_code(session, user_id=patient_user.id, ttl_minutes=10)
    consume_link_code(session, code=link_code.code, chat_id="chat-abc")

    after = await client.get("/api/v1/notifications/zalo/link-status", headers=_auth_headers(token))
    assert after.json() == {"linked": True}


@pytest.mark.asyncio
async def test_doctor_cannot_create_zalo_link_code(client: AsyncClient, doctor_user: User):
    token = await _login(client, DOCTOR_EMAIL)

    response = await client.post("/api/v1/notifications/zalo/link-code", headers=_auth_headers(token))

    assert response.status_code == 403

"""Epic 2: weekly patient report — week bounds, deterministic stats, the
generate/deliver job (idempotent, opt-out, no data, LLM failure) and the API."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import GlucoseRecord, MealAnalysis, MealContext, MealType
from codebase.backend.health.service import create_meal_record
from codebase.backend.notifications.models import Notification, NotificationStatus, NotificationType
from codebase.backend.reports import service
from codebase.backend.reports.models import PatientWeeklyReport, WeeklyReportStatus
from codebase.backend.reports.stats import compute_weekly_stats, pick_tip
from codebase.backend.summaries.models import AgentSummarySnapshot, SummaryRole
from codebase.models.agents.nodes.guardrail import guardrail_check
from codebase.models.agents.tools.agent_tools import TOOLS_BY_NAME, NoArgs
from codebase.models.agents.tools.nutrition_tools import nutrition_item

PASSWORD = "AdminPass123"
# Sunday 2026-10-25 08:00 in Vietnam = 01:00 UTC; the week reported is
# Sun 18/10 – Sat 24/10 local = 17/10 17:00 UTC .. 24/10 17:00 UTC.
SUNDAY_8AM_UTC = datetime(2026, 10, 25, 1, 0)
PERIOD_START = datetime(2026, 10, 17, 17, 0)
PERIOD_END = datetime(2026, 10, 24, 17, 0)
SNAPSHOT = "codebase.backend.reports.service.generate_and_store_snapshot"


def _user(session: Session, email: str, role: UserRole = UserRole.PATIENT, **extra) -> User:
    user = User(email=email, full_name=email, role=role, hashed_password=hash_password(PASSWORD), **extra)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _user(session, "patient@diab.example.com")


def _glucose(session, patient, when, value, context=MealContext.FASTING, status=None):
    session.add(
        GlucoseRecord(
            patient_id=patient.id, value=value, measured_at=when, meal_context=context,
            threshold_status=status, created_by=patient.id,
        )
    )
    session.commit()


def _snapshot_mock(session: Session, text: str = "Tuần này đường huyết của bạn khá ổn định.") -> AsyncMock:
    async def _make(session_arg, *, patient_id, role, period_days, period_start, period_end):
        snap = AgentSummarySnapshot(
            patient_id=patient_id, role=SummaryRole.PATIENT, period_days=period_days,
            period_start=period_start, period_end=period_end, summary=text,
        )
        session_arg.add(snap)
        session_arg.commit()
        session_arg.refresh(snap)
        return snap

    return AsyncMock(side_effect=_make)


async def _generate(session, patient, now=SUNDAY_8AM_UTC):
    week_start, week_end, start, end = service.last_complete_week(now)
    return await service.generate_weekly_report(
        session, patient, week_start=week_start, week_end=week_end, period_start=start, period_end=end, now_utc=now
    )


# --- week bounds --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now_utc", "expected_start"),
    [
        (SUNDAY_8AM_UTC, date(2026, 10, 18)),  # Sunday morning: the week just ended
        (datetime(2026, 10, 26, 3, 0), date(2026, 10, 18)),  # Monday
        (datetime(2026, 10, 31, 3, 0), date(2026, 10, 18)),  # Saturday: its own week isn't over yet
        (datetime(2026, 10, 24, 18, 0), date(2026, 10, 18)),  # Sat 17:00 UTC = Sun 01:00 local
    ],
)
def test_last_complete_week(now_utc, expected_start):
    week_start, week_end, start, end = service.last_complete_week(now_utc)
    assert week_start == expected_start
    assert week_end == expected_start + timedelta(days=6)
    assert (start, end) == (PERIOD_START, PERIOD_END)


# --- stats ---------------------------------------------------------------------


def test_stats(session: Session, patient: User):
    day = PERIOD_START + timedelta(hours=10)
    _glucose(session, patient, day, 110, status="NORMAL")
    _glucose(session, patient, day + timedelta(hours=3), 200, MealContext.AFTER_MEAL, status="WATCH")
    _glucose(session, patient, day + timedelta(days=2), 65, status="DANGER")
    _glucose(session, patient, day + timedelta(days=3), 120)  # no care plan yet: not in the %
    _glucose(session, patient, PERIOD_START - timedelta(days=2), 150, status="NORMAL")  # previous week
    _glucose(session, patient, PERIOD_END + timedelta(hours=1), 300)  # next week: excluded

    stats = compute_weekly_stats(session, patient.id, PERIOD_START, PERIOD_END)

    g = stats["glucose"]
    assert g["count"] == 4 and g["min"] == 65 and g["max"] == 200
    assert g["low_count"] == 1
    assert g["evaluated_count"] == 3 and g["in_target_pct"] == pytest.approx(33.3)
    assert g["by_context"]["AFTER_MEAL"]["avg"] == 200
    assert stats["previous_week"] == {"count": 1, "avg": 150, "in_target_pct": 100.0}
    assert stats["change"]["avg_delta"] == pytest.approx(123.8 - 150, abs=0.1)
    assert stats["days_logged"] == 3


def test_stats_flag_foods_from_meal_response(session: Session, patient: User):
    for day in range(3):
        when = PERIOD_END - timedelta(days=day + 1)
        meal = create_meal_record(
            session, patient_id=patient.id, meal_type=MealType.LUNCH, meal_time=when, description="phở bò",
            note=None, created_by=patient.id,
        )
        session.add(MealAnalysis(meal_id=meal.id, patient_id=patient.id, items=[nutrition_item("phở bò", 1, None)],
                                 total_carbs_g=60, carb_load="high", source="text", status="draft"))
        _glucose(session, patient, when + timedelta(hours=2), 220, MealContext.AFTER_MEAL)
    stats = compute_weekly_stats(session, patient.id, PERIOD_START, PERIOD_END)
    assert [f["name"] for f in stats["meals"]["top_foods"]] == ["phở bò"]
    assert stats["meal_count"] == 3


def _stats(**overrides) -> dict:
    base = {
        "glucose": {"count": 10, "low_count": 0, "in_target_pct": 80.0},
        "blood_pressure": {"count": 0},
        "meal_count": 5,
        "activity_count": 0,
        "days_logged": 6,
        "meals": {"top_foods": []},
    }
    for key, value in overrides.items():
        base[key] = {**base[key], **value} if isinstance(value, dict) else value
    return base


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"glucose": {"low_count": 2}, "days_logged": 1}, "dưới 70 mg/dL"),  # safety first
        ({"glucose": {"count": 0}, "meal_count": 0, "days_logged": 0}, "vài lần"),
        ({"days_logged": 2}, "ít nhất 4 ngày"),
        ({"meals": {"top_foods": [{"name": "phở bò"}]}}, "Món phở bò thường đi kèm"),
        ({"glucose": {"in_target_pct": 30.0}}, "khi tái khám"),
        ({}, "tiếp tục"),
    ],
)
def test_tip_priority(overrides, expected):
    assert expected in pick_tip(_stats(**overrides))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{"glucose": {"low_count": 2}}, {"glucose": {"count": 0}, "meal_count": 0}, {"days_logged": 2},
     {"meals": {"top_foods": [{"name": "phở bò"}]}}, {"glucose": {"in_target_pct": 30.0}}, {}],
)
async def test_every_tip_passes_the_guardrail(overrides):
    result = await guardrail_check({"intent": "SUMMARY", "response": pick_tip(_stats(**overrides)), "retry_count": 0})
    assert not result.get("guardrail_failures")


# --- job ------------------------------------------------------------------------


def _notifications(session: Session, patient: User) -> list[Notification]:
    return list(session.exec(select(Notification).where(Notification.patient_id == patient.id)).all())


@pytest.mark.asyncio
async def test_no_data_week_needs_no_llm(session: Session, patient: User):
    with patch(SNAPSHOT, AsyncMock(side_effect=AssertionError("no LLM for an empty week"))):
        report = await _generate(session, patient)
    assert report.summary == service.NO_DATA_SUMMARY
    assert report.sections_enabled == []
    assert report.status == WeeklyReportStatus.DELIVERED
    [notification] = _notifications(session, patient)
    assert notification.notification_type == NotificationType.WEEKLY_REPORT


@pytest.mark.asyncio
async def test_report_with_data_is_delivered_once(session: Session, patient: User):
    _glucose(session, patient, PERIOD_START + timedelta(days=1), 110, status="NORMAL")
    snapshot = _snapshot_mock(session)
    with patch(SNAPSHOT, snapshot):
        first = await _generate(session, patient)
        again = await _generate(session, patient)  # cron re-run

    assert first.id == again.id
    snapshot.assert_awaited_once()
    assert snapshot.await_args.kwargs["period_start"] == PERIOD_START
    assert snapshot.await_args.kwargs["period_end"] == PERIOD_END
    assert first.summary == "Tuần này đường huyết của bạn khá ổn định."
    assert first.stats["glucose"]["count"] == 1
    assert first.tip
    assert len(_notifications(session, patient)) == 1


@pytest.mark.asyncio
async def test_opted_out_patient_gets_the_report_but_no_notification(session: Session):
    patient = _user(session, "quiet@diab.example.com", weekly_report_enabled=False)
    report = await _generate(session, patient)
    assert report.status == WeeklyReportStatus.GENERATED
    assert _notifications(session, patient) == []


@pytest.mark.asyncio
async def test_zalo_gets_a_pointer_without_health_figures(session: Session, monkeypatch):
    from codebase.backend.config import get_settings

    monkeypatch.setattr(get_settings(), "zalo_bot_enabled", True)
    patient = _user(session, "zalo@diab.example.com", zalo_chat_id="chat-1")
    _glucose(session, patient, PERIOD_START + timedelta(days=1), 245, status="WATCH")
    sent = AsyncMock()
    with patch(SNAPSHOT, _snapshot_mock(session)), patch(
        "codebase.backend.reports.service.ZaloBotNotifier"
    ) as notifier:
        notifier.return_value.send_message = sent
        await _generate(session, patient)
    chat_id, text = sent.await_args.args
    assert chat_id == "chat-1"
    assert "245" not in text and "mg/dL" not in text


@pytest.mark.asyncio
async def test_tick_carries_on_after_a_failure_and_retries_it(session: Session):
    broken = _user(session, "a@diab.example.com")
    fine = _user(session, "b@diab.example.com")
    _glucose(session, broken, PERIOD_START + timedelta(days=1), 110)
    _user(session, "doctor@diab.example.com", UserRole.DOCTOR)

    with patch(SNAPSHOT, AsyncMock(side_effect=RuntimeError("LLM down"))):
        counts = await service.run_weekly_report_tick(session, now_utc=SUNDAY_8AM_UTC)
    assert counts == {"generated": 1, "already_existed": 0, "failed": 1}
    assert service.get_report(session, broken.id, date(2026, 10, 18)).status == WeeklyReportStatus.FAILED
    assert service.get_report(session, fine.id, date(2026, 10, 18)).status == WeeklyReportStatus.DELIVERED

    with patch(SNAPSHOT, _snapshot_mock(session)):
        counts = await service.run_weekly_report_tick(session, now_utc=SUNDAY_8AM_UTC)
    assert counts == {"generated": 1, "already_existed": 1, "failed": 0}
    assert service.get_report(session, broken.id, date(2026, 10, 18)).status == WeeklyReportStatus.DELIVERED
    assert len(session.exec(select(PatientWeeklyReport)).all()) == 2


# --- API ------------------------------------------------------------------------


async def _headers(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_api_list_get_and_read(client: AsyncClient, session: Session, patient: User):
    report = await _generate(session, patient)
    headers = await _headers(client, patient)

    listed = (await client.get("/api/v1/reports/weekly", headers=headers)).json()
    assert [r["id"] for r in listed] == [str(report.id)]
    assert listed[0]["read_at"] is None

    detail = await client.get(f"/api/v1/reports/weekly/{report.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["summary"] == service.NO_DATA_SUMMARY

    read = await client.post(f"/api/v1/reports/weekly/{report.id}/read", headers=headers)
    assert read.json()["read_at"] is not None
    [notification] = _notifications(session, patient)
    session.refresh(notification)
    assert notification.status == NotificationStatus.COMPLETED


@pytest.mark.asyncio
async def test_api_access(client: AsyncClient, session: Session, patient: User):
    report = await _generate(session, patient)
    other = _user(session, "other@diab.example.com")
    doctor = _user(session, "doctor@diab.example.com", UserRole.DOCTOR)
    url = f"/api/v1/reports/weekly/{report.id}"
    assert (await client.get(url, headers=await _headers(client, other))).status_code == 404
    assert (await client.get(url, headers=await _headers(client, doctor))).status_code == 403


@pytest.mark.asyncio
async def test_api_generate_is_idempotent(client: AsyncClient, session: Session, patient: User):
    headers = await _headers(client, patient)
    first = await client.post("/api/v1/reports/weekly/generate", headers=headers)
    second = await client.post("/api/v1/reports/weekly/generate", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_patient_can_turn_report_notifications_off(client: AsyncClient, patient: User):
    response = await client.patch("/api/v1/auth/me", json={"weekly_report_enabled": False}, headers=await _headers(client, patient))
    assert response.json()["weekly_report_enabled"] is False
    assert response.json()["ai_memory_consent"] is False


def _medication_with_reminders(session: Session, patient: User, statuses: list[NotificationStatus]) -> None:
    from codebase.backend.medications.models import Medication

    session.add(Medication(patient_id=patient.id, drug_name="Metformin", dose="500mg", frequency="2 lần/ngày",
                           start_date=datetime(2026, 9, 1), created_by=patient.id))
    for offset, status in enumerate(statuses):
        day = date(2026, 10, 18) + timedelta(days=offset)
        session.add(Notification(
            patient_id=patient.id, notification_type=NotificationType.MEDICATION_REMINDER, title="t", message="m",
            slot_key=f"medication_glucose_before_breakfast:{day}", slot_date=day,
            scheduled_for=datetime.combine(day, datetime.min.time()), status=status,
        ))
    session.commit()


@pytest.mark.asyncio
async def test_medication_section_and_tip(session: Session, patient: User):
    done, skipped = NotificationStatus.COMPLETED, NotificationStatus.SKIPPED
    _medication_with_reminders(session, patient, [done, skipped, done, skipped, done, skipped, done])
    _glucose(session, patient, PERIOD_START + timedelta(days=1), 110, status="NORMAL")
    for day in range(5):  # enough logging days that the medication tip wins on priority
        _glucose(session, patient, PERIOD_START + timedelta(days=day, hours=12), 120, status="NORMAL")
    with patch(SNAPSHOT, _snapshot_mock(session)):
        report = await _generate(session, patient)
    meds = report.stats["medications"]
    assert (meds["days_taken"], meds["days_missed"]) == (4, 3)
    assert [d["status"] for d in meds["daily"]][:2] == ["taken", "missed"]
    assert "medications" in report.sections_enabled
    assert report.tip.startswith("Tuần này có 3 ngày bạn quên hoặc bỏ qua nhắc uống thuốc")


@pytest.mark.asyncio
async def test_no_medication_section_without_medications(session: Session, patient: User):
    _glucose(session, patient, PERIOD_START + timedelta(days=1), 110)
    with patch(SNAPSHOT, _snapshot_mock(session)):
        report = await _generate(session, patient)
    assert "medications" not in report.sections_enabled


@pytest.mark.asyncio
async def test_zalo_links_the_report_when_the_app_url_is_set(session: Session, monkeypatch):
    from codebase.backend.config import get_settings

    monkeypatch.setattr(get_settings(), "zalo_bot_enabled", True)
    monkeypatch.setattr(get_settings(), "frontend_public_url", "https://app.vdiab.vn/")
    patient = _user(session, "link@diab.example.com", zalo_chat_id="chat-2")
    sent = AsyncMock()
    with patch("codebase.backend.reports.service.ZaloBotNotifier") as notifier:
        notifier.return_value.send_message = sent
        await _generate(session, patient)
    assert sent.await_args.args[1].endswith("Xem chi tiết: https://app.vdiab.vn/reports")


@pytest.mark.asyncio
async def test_review_export_rows(session: Session, patient: User):
    from scripts.export_weekly_report_review import COLUMNS, review_rows

    await _generate(session, patient)
    [row] = review_rows(session, 10)
    assert list(row) == COLUMNS
    assert row["case_id"] == "WR-001" and row["category"] == "WEEKLY_REPORT"
    assert row["response"].startswith(service.NO_DATA_SUMMARY)
    assert patient.email not in row["prompt"] and str(patient.id) not in row["prompt"]
    assert row["clinician_pass"] == "" and row["reviewer"] == ""


@pytest.mark.asyncio
async def test_agent_tool_reads_the_latest_report(session: Session, patient: User):
    await _generate(session, patient)
    outcome = TOOLS_BY_NAME["get_weekly_report"].handler(
        {"patient_id": patient.id, "tool_results": {}}, session, NoArgs()
    )
    report = outcome.state_update["tool_results"]["weekly_report"]
    assert report["week"] == "18/10/2026–24/10/2026"
    assert report["stats"]
    # The report's "việc nên làm" tip is advice: the chat assistant never sees it (Epic 8).
    assert "tip" not in report

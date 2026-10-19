"""Tests for the AI Summary path: tools/patient_tools.py, tools/metrics_tools.py,
nodes/fetch_data.py, nodes/check_data_quality.py, nodes/synthesize_summary.py —
exercised both as direct unit calls (deterministic, no DB/LLM) and end-to-end
through POST /api/v1/agent/summary (design doc §11).

The LLM is always mocked (monkeypatching
codebase.models.agents.nodes.synthesize_summary.get_llm) — no real OpenAI call
is ever made here, per the design doc's testing plan and because OPENAI_API_KEY
is not expected to be set in CI. `trends`/`attention_points`/`limitations`/
`suggested_doctor_questions` are built deterministically from seeded records
(see synthesize_summary.py's docstring), so those are asserted exactly; the
LLM only supplies `response` prose, treated as opaque here.

Inline fixtures are anchored to `datetime.utcnow()` rather than the literal
dates in data/mock/demo_patient/*.json, so these tests don't go stale relative
to whatever day they actually run on — see fetch_data.py, which resolves
date_range_days against "now". Any 7 consecutive calendar days contain
exactly 2 weekend days (and any 14 consecutive days contain exactly 4),
regardless of which day "today" is — the weekday/weekend fixture below relies
on that to stay non-flaky.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import MealContext, MealType
from codebase.backend.health.service import (
    create_activity_record,
    create_glucose_record,
    create_meal_record,
)
from codebase.backend.patients.models import PatientAssignment
from codebase.models.agents.nodes import synthesize_summary as synthesize_summary_mod
from codebase.models.agents.nodes.check_data_quality import check_data_quality
from codebase.models.agents.nodes.synthesize_summary import synthesize_summary
from codebase.models.agents.tools.metrics_tools import calculate_health_metrics

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_weekday_weekend_pattern(session: Session, *, patient_id, creator_id, num_days: int) -> None:
    """Inline fixture inspired by data/mock/demo_patient's headline pattern
    (weekend post-dinner glucose runs high, weekday doesn't) but with values
    of our own choosing and dates anchored to `now` — see module docstring
    for why literal mock-file dates aren't used directly here.

    Deliberately anchors every timestamp as "N days before this one captured
    `now`, minus a few minutes" rather than a fixed clock hour (e.g. 21:00) —
    fetch_data.py's window end is `datetime.utcnow()` evaluated later, at
    request time. A fixed hour like 21:00 lands in the *future* relative to
    that later `end` whenever the suite happens to run before 21:00 UTC,
    which would silently drop "today"'s records from every window. Minute-
    level offsets from a single captured instant can't do that.
    """
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    for i in range(num_days):
        day_ts = now - timedelta(days=i)
        is_weekend = day_ts.weekday() >= 5
        fasting_value = 140.0 if is_weekend else 128.0
        after_dinner_value = 208.0 if is_weekend else 165.0  # weekend value is over the 180 mg/dL target

        create_glucose_record(
            session,
            patient_id=patient_id,
            value=fasting_value,
            unit="mg/dL",
            measured_at=day_ts - timedelta(minutes=4),
            note="Đo lúc đói",
            created_by=creator_id,
            meal_context=MealContext.FASTING,
        )
        create_glucose_record(
            session,
            patient_id=patient_id,
            value=after_dinner_value,
            unit="mg/dL",
            measured_at=day_ts - timedelta(minutes=1),
            note="Sau bữa tối",
            created_by=creator_id,
            meal_context=MealContext.AFTER_MEAL,
        )
        create_meal_record(
            session,
            patient_id=patient_id,
            meal_type=MealType.DINNER,
            meal_time=day_ts - timedelta(minutes=2),
            description="Bữa tối",
            note=None,
            created_by=creator_id,
        )
        if not is_weekend:
            create_activity_record(
                session,
                patient_id=patient_id,
                activity_type="Đi bộ buổi tối",
                duration_minutes=30,
                activity_time=day_ts - timedelta(minutes=3),
                note=None,
                created_by=creator_id,
            )


# ---------------------------------------------------------------------------
# Pure-function / node-level unit tests — no DB, no HTTP, fully deterministic.
# ---------------------------------------------------------------------------


def test_calculate_health_metrics_weekday_vs_weekend_and_targets():
    """A hand-built, controlled fixture (fixed literal dates, not `now`) so
    the exact numbers can be asserted precisely — mirrors the shape of the
    demo patient's pattern (weekend post-dinner over the 180 mg/dL BYT-5481
    target) without depending on wall-clock time."""
    monday = datetime(2026, 6, 8, 21, 0)  # 2026-06-08 is a Monday
    saturday = datetime(2026, 6, 13, 21, 0)  # same week's Saturday

    records = {
        "glucose": [
            {"value": 160.0, "measured_at": monday.isoformat(), "meal_context": "AFTER_MEAL"},
            {"value": 210.0, "measured_at": saturday.isoformat(), "meal_context": "AFTER_MEAL"},
            {"value": 120.0, "measured_at": monday.replace(hour=7).isoformat(), "meal_context": "FASTING"},
            {"value": 65.0, "measured_at": monday.replace(hour=15).isoformat(), "meal_context": "RANDOM", "symptoms": "chóng mặt nhẹ"},
        ],
        "meals": [{"meal_time": monday.isoformat()}],
        "activities": [{"activity_time": monday.isoformat(), "duration_minutes": 30}],
    }

    metrics = calculate_health_metrics(records)

    assert metrics["glucose_count"] == 4
    assert metrics["meal_count"] == 1
    assert metrics["activity_count"] == 1
    assert metrics["activity_total_minutes"] == 30

    assert metrics["post_prandial"]["count"] == 2
    assert metrics["post_prandial"]["over_target_count"] == 1
    assert metrics["post_prandial"]["over_target_pct"] == 50.0

    assert metrics["fasting"]["count"] == 1
    assert metrics["fasting"]["in_range_pct"] == 100.0

    assert metrics["weekday_vs_weekend"]["weekday"]["count"] == 3
    assert metrics["weekday_vs_weekend"]["weekend"]["count"] == 1
    assert metrics["weekday_vs_weekend"]["weekend"]["avg"] == 210.0

    assert metrics["low_glucose_count"] == 1
    assert metrics["symptomatic_count"] == 1


def test_calculate_health_metrics_blood_pressure():
    records = {
        "glucose": [],
        "blood_pressure": [
            {"systolic": 130.0, "diastolic": 85.0, "measured_at": "2026-06-08T07:00:00"},
            {"systolic": 150.0, "diastolic": 95.0, "measured_at": "2026-06-09T07:00:00"},
        ],
        "meals": [],
        "activities": [],
    }

    metrics = calculate_health_metrics(records)

    assert metrics["blood_pressure_count"] == 2
    assert metrics["blood_pressure_days_logged"] == 2
    assert metrics["blood_pressure_systolic"] == {"count": 2, "avg": 140.0, "min": 130.0, "max": 150.0}
    assert metrics["blood_pressure_diastolic"] == {"count": 2, "avg": 90.0, "min": 85.0, "max": 95.0}


def test_calculate_health_metrics_empty_records_is_safe():
    metrics = calculate_health_metrics({"glucose": [], "meals": [], "activities": []})
    assert metrics["glucose_count"] == 0
    assert metrics["glucose_overall"] == {"count": 0, "avg": None, "min": None, "max": None}
    assert metrics["post_prandial"]["count"] == 0
    assert metrics["low_glucose_count"] == 0


def test_calculate_health_metrics_missing_keys_defaults_empty():
    """records dict with no keys at all (e.g. a not-yet-populated tool_results)
    must not raise — get() with a default, not records["glucose"]."""
    assert calculate_health_metrics({})["glucose_count"] == 0


@pytest.mark.asyncio
async def test_check_data_quality_reports_gaps_honestly():
    date_range = (datetime(2026, 6, 10), datetime(2026, 6, 16))  # 7 days inclusive
    records = {
        "glucose": [{"value": 120, "measured_at": "2026-06-10T07:00:00", "meal_context": "FASTING"} for _ in range(5)],
        "meals": [],
        "activities": [],
    }
    state = {"intent": "SUMMARY", "date_range": date_range, "tool_results": {"records": records}}

    result = await check_data_quality(state)
    completeness = result["data_completeness"]

    assert completeness["days_in_range"] == 7
    assert completeness["glucose"]["expected"] == 21  # 7 days * 3/day
    assert completeness["glucose"]["found"] == 5
    assert completeness["meals"]["found"] == 0
    assert completeness["meals"]["expected"] == 21
    assert completeness["activities"]["found"] == 0
    assert "note" in completeness["activities"]  # honest that activity has no fixed daily expectation
    assert completeness["sufficient_for_trends"] is True  # 5 >= MIN_GLUCOSE_READINGS_FOR_TRENDS (4)
    # tool_results is spread, not clobbered, and now also carries the metrics.
    assert result["tool_results"]["records"] is records
    assert result["tool_results"]["metrics"]["glucose_count"] == 5


@pytest.mark.asyncio
async def test_check_data_quality_blood_pressure_no_fixed_expectation():
    """Same lighter scoring as activities (found + days_logged only, no
    expected/completeness_pct) — blood pressure is patient-initiated with no
    fixed daily cadence, same reasoning as activity logging."""
    date_range = (datetime(2026, 6, 10), datetime(2026, 6, 16))
    records = {
        "glucose": [],
        "blood_pressure": [{"systolic": 130, "diastolic": 85, "measured_at": "2026-06-10T07:00:00"}],
        "meals": [],
        "activities": [],
    }
    state = {"intent": "SUMMARY", "date_range": date_range, "tool_results": {"records": records}}

    result = await check_data_quality(state)
    completeness = result["data_completeness"]

    assert completeness["blood_pressure"]["found"] == 1
    assert completeness["blood_pressure"]["days_logged"] == 1
    assert "note" in completeness["blood_pressure"]
    assert "expected" not in completeness["blood_pressure"]


@pytest.mark.asyncio
async def test_check_data_quality_zero_records_not_sufficient():
    date_range = (datetime(2026, 6, 10), datetime(2026, 6, 13))
    state = {
        "intent": "SUMMARY",
        "date_range": date_range,
        "tool_results": {"records": {"glucose": [], "meals": [], "activities": []}},
    }
    result = await check_data_quality(state)
    assert result["data_completeness"]["sufficient_for_trends"] is False
    assert result["data_completeness"]["glucose"]["found"] == 0


@pytest.mark.asyncio
async def test_check_data_quality_noop_for_short_circuit_intents():
    """Truly a no-op only for intents that never reach fetch_data at all
    (JAILBREAK/HIGH_RISK/OUT_OF_SCOPE short-circuit straight to `respond` in
    graph.py) — QA now gets a narrower metrics-only pass, see the test below."""
    assert await check_data_quality({"intent": "JAILBREAK"}) == {}
    assert await check_data_quality({"intent": "OUT_OF_SCOPE"}) == {}


@pytest.mark.asyncio
async def test_check_data_quality_computes_metrics_for_qa_without_completeness():
    """QA doesn't need the SUMMARY-only completeness/expected-cadence scoring,
    but it does need calculate_health_metrics's precomputed averages stashed
    into tool_results — see synthesize_qa.py's _PATIENT_DATA_SECTIONS, which
    hands the model this instead of making it average a raw records list."""
    records = {
        "glucose": [{"value": 120.0, "measured_at": "2026-06-10T07:00:00", "meal_context": "FASTING"}],
        "blood_pressure": [],
        "meals": [],
        "activities": [],
    }
    state = {"intent": "QA", "tool_results": {"profile": {"full_name": "Test"}, "records": records}}

    result = await check_data_quality(state)

    assert "data_completeness" not in result
    assert result["tool_results"]["metrics"]["glucose_count"] == 1
    assert result["tool_results"]["profile"] == {"full_name": "Test"}  # spread, not clobbered


@pytest.mark.asyncio
async def test_synthesize_summary_zero_records_says_insufficient_no_llm_call(monkeypatch):
    """Patient with zero records: must say insufficient data, never invent a
    trend — and must never even try to call the LLM for this case (§2)."""

    def _must_not_be_called():
        raise AssertionError("get_llm() must not be called when there is no glucose data")

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", _must_not_be_called)

    state = {
        "intent": "SUMMARY",
        "user_role": "patient",
        "date_range": (datetime(2026, 6, 1), datetime(2026, 6, 7)),
        "tool_results": {
            "profile": {"full_name": "Test Patient"},
            "records": {"glucose": [], "meals": [], "activities": []},
        },
        "data_completeness": {"sufficient_for_trends": False, "glucose": {}, "meals": {}, "activities": {}},
    }

    result = await synthesize_summary(state)

    assert result["trends"] == []
    assert result["attention_points"] == []
    assert result["suggested_doctor_questions"] == []
    assert result["limitations"]
    assert "chưa thấy dữ liệu" in result["response"] or "Không có dữ liệu" in result["response"]


@pytest.mark.asyncio
async def test_synthesize_summary_surfaces_adverse_reaction_even_with_zero_glucose_data(monkeypatch):
    """A self-reported adverse reaction is safety-relevant regardless of whether there's enough
    glucose data to trend — must appear in attention_points even on the no-LLM-call insufficient
    path (§2: still deterministic, no fabrication, just not gated on glucose sufficiency)."""

    def _must_not_be_called():
        raise AssertionError("get_llm() must not be called when there is no glucose data")

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", _must_not_be_called)

    state = {
        "intent": "SUMMARY",
        "user_role": "doctor",
        "date_range": (datetime(2026, 6, 1), datetime(2026, 6, 7)),
        "tool_results": {
            "profile": {"full_name": "Test Patient"},
            "records": {"glucose": [], "meals": [], "activities": []},
            "medications": {
                "active": [{"drug_name": "Metformin", "dose": "500mg", "frequency": "twice daily"}],
                "notes": [{"note_type": "adverse_reaction", "text": "Buồn nôn dữ dội"}],
            },
        },
        "data_completeness": {"sufficient_for_trends": False, "glucose": {}, "meals": {}, "activities": {}},
    }

    result = await synthesize_summary(state)

    assert result["trends"] == []  # still no fabricated glucose trend
    assert any("Buồn nôn dữ dội" in point for point in result["attention_points"])
    assert any("medication_notes" in item for item in result["data_used"])


@pytest.mark.asyncio
async def test_synthesize_summary_noop_for_non_summary_intent():
    assert await synthesize_summary({"intent": "QA"}) == {}


@pytest.mark.asyncio
async def test_synthesize_summary_doctor_tone_has_observation_labels(monkeypatch):
    mock_response = type("R", (), {"content": "Bản tóm tắt bác sĩ."})()

    class _FakeLLM:
        async def ainvoke(self, messages):
            return mock_response

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: _FakeLLM())

    records = {
        "glucose": [
            {"value": 165.0, "measured_at": "2026-06-08T21:00:00", "meal_context": "AFTER_MEAL"},  # Mon
            {"value": 208.0, "measured_at": "2026-06-13T21:00:00", "meal_context": "AFTER_MEAL"},  # Sat
            {"value": 128.0, "measured_at": "2026-06-08T07:00:00", "meal_context": "FASTING"},
            {"value": 140.0, "measured_at": "2026-06-13T07:00:00", "meal_context": "FASTING"},
        ],
        "meals": [],
        "activities": [],
    }
    completeness = {
        "sufficient_for_trends": True,
        "glucose": {"found": 4, "expected": 21, "completeness_pct": 19.0, "days_logged": 2},
        "meals": {"found": 0, "expected": 21, "completeness_pct": 0.0, "days_logged": 0},
        "activities": {"found": 0, "days_logged": 0},
        "days_in_range": 7,
    }
    state = {
        "intent": "SUMMARY",
        "user_role": "doctor",
        "date_range": (datetime(2026, 6, 8), datetime(2026, 6, 14)),
        "tool_results": {"profile": {"full_name": "Test Patient"}, "records": records},
        "data_completeness": completeness,
    }

    result = await synthesize_summary(state)

    assert result["response"] == "Bản tóm tắt bác sĩ."
    assert any("[Quan sát]" in t for t in result["trends"])
    assert result["suggested_doctor_questions"] == []  # doctor role gets no suggested-questions list
    assert any("chưa đầy đủ" in item for item in result["limitations"])  # low completeness flagged honestly


@pytest.mark.asyncio
async def test_synthesize_summary_includes_blood_pressure_trend_and_data_used(monkeypatch):
    mock_response = type("R", (), {"content": "Bản tóm tắt."})()

    class _FakeLLM:
        async def ainvoke(self, messages):
            return mock_response

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: _FakeLLM())

    records = {
        "glucose": [],
        "blood_pressure": [
            {"systolic": 130.0, "diastolic": 85.0, "measured_at": "2026-06-08T07:00:00"},
            {"systolic": 140.0, "diastolic": 90.0, "measured_at": "2026-06-09T07:00:00"},
        ],
        "meals": [],
        "activities": [],
    }
    completeness = {
        # Explicitly forced True here (independent of the zero glucose count
        # above) — glucose sufficiency gates the whole summary path (see
        # synthesize_summary.py), which is out of scope for this test; this
        # isolates the blood-pressure trend/data_used wiring on its own.
        "sufficient_for_trends": True,
        "glucose": {"found": 0, "expected": 21, "completeness_pct": 0.0, "days_logged": 0},
        "meals": {"found": 0, "expected": 21, "completeness_pct": 0.0, "days_logged": 0},
        "activities": {"found": 0, "days_logged": 0},
        "blood_pressure": {"found": 2, "days_logged": 2},
        "days_in_range": 7,
    }
    state = {
        "intent": "SUMMARY",
        "user_role": "patient",
        "date_range": (datetime(2026, 6, 8), datetime(2026, 6, 14)),
        "tool_results": {"profile": {"full_name": "Test Patient"}, "records": records},
        "data_completeness": completeness,
    }

    result = await synthesize_summary(state)

    assert any("135.0/87.5 mmHg" in t for t in result["trends"])
    assert any("blood_pressure_records (n=2)" in d for d in result["data_used"])


@pytest.mark.asyncio
async def test_synthesize_summary_flags_repeated_missed_doses_and_lists_current_regimen(monkeypatch):
    """2+ missed-dose self-reports in the window surface as an attention point (a single one-off
    is normal and shouldn't be flagged — see _MISSED_DOSE_ATTENTION_THRESHOLD); the current
    regimen reaches the prompt via medications_str regardless of the note count."""
    captured_prompt: dict[str, str] = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            captured_prompt["text"] = messages[-1].content
            return type("R", (), {"content": "Bản tóm tắt."})()

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: _FakeLLM())

    records = {
        "glucose": [
            {"value": 165.0, "measured_at": "2026-06-08T21:00:00", "meal_context": "AFTER_MEAL"},
            {"value": 140.0, "measured_at": "2026-06-08T07:00:00", "meal_context": "FASTING"},
        ],
        "meals": [],
        "activities": [],
    }
    completeness = {
        "sufficient_for_trends": True,
        "glucose": {"found": 2, "expected": 21, "completeness_pct": 10.0, "days_logged": 1},
        "meals": {"found": 0, "expected": 21, "completeness_pct": 0.0, "days_logged": 0},
        "activities": {"found": 0, "days_logged": 0},
        "days_in_range": 7,
    }
    state = {
        "intent": "SUMMARY",
        "user_role": "doctor",
        "date_range": (datetime(2026, 6, 8), datetime(2026, 6, 14)),
        "tool_results": {
            "profile": {"full_name": "Test Patient"},
            "records": records,
            "medications": {
                "active": [{"drug_name": "Metformin", "dose": "500mg", "frequency": "twice daily"}],
                "notes": [
                    {"note_type": "missed_dose", "text": "Quên buổi sáng"},
                    {"note_type": "missed_dose", "text": "Quên buổi tối"},
                ],
            },
        },
        "data_completeness": completeness,
    }

    result = await synthesize_summary(state)

    assert any("2 lần quên uống thuốc" in point for point in result["attention_points"])
    assert "Metformin 500mg, twice daily" in captured_prompt["text"]


@pytest.mark.asyncio
async def test_synthesize_summary_falls_back_when_llm_raises(monkeypatch):
    """An LLM/network failure must not crash the summary — fall back to the
    deterministic bullet points instead of propagating the exception."""

    class _BrokenLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: _BrokenLLM())

    records = {
        "glucose": [{"value": 120.0, "measured_at": "2026-06-08T07:00:00", "meal_context": "FASTING"} for _ in range(5)],
        "meals": [],
        "activities": [],
    }
    state = {
        "intent": "SUMMARY",
        "user_role": "patient",
        "date_range": (datetime(2026, 6, 8), datetime(2026, 6, 14)),
        "tool_results": {"profile": {}, "records": records},
        "data_completeness": {
            "sufficient_for_trends": True,
            "glucose": {"found": 5, "expected": 21, "completeness_pct": 23.8, "days_logged": 5},
            "meals": {"found": 0, "expected": 21, "completeness_pct": 0.0, "days_logged": 0},
            "activities": {"found": 0, "days_logged": 0},
            "days_in_range": 7,
        },
    }

    result = await synthesize_summary(state)

    assert result["response"]  # non-empty fallback text, no exception raised
    assert result["trends"]


# ---------------------------------------------------------------------------
# End-to-end tests through POST /api/v1/agent/summary.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_summary_with_data(client: AsyncClient, session: Session, monkeypatch, mock_llm):
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    patient = _make_user(session, email="summary-patient@diab.example.com", role=UserRole.PATIENT)
    _seed_weekday_weekend_pattern(session, patient_id=patient.id, creator_id=patient.id, num_days=7)

    token = await _login(client, patient.email)
    response = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["data_completeness"]["glucose"]["found"] == 14  # 2 readings/day * 7 days
    assert body["trends"], "expected at least one deterministic trend for 14 glucose readings"
    assert any("cuối tuần" in t for t in body["trends"])  # weekday/weekend gap is guaranteed present
    assert body["data_used"]
    assert any("glucose_records" in d for d in body["data_used"])
    assert len(body["suggested_doctor_questions"]) > 0  # patient role gets suggested questions
    assert body["summary"]  # LLM-produced prose, non-empty
    assert body["disclaimer"]


@pytest.mark.asyncio
async def test_patient_summary_zero_records_says_insufficient(client: AsyncClient, session: Session, monkeypatch):
    def _must_not_be_called():
        raise AssertionError("get_llm() must not be called for a patient with zero records")

    monkeypatch.setattr(synthesize_summary_mod, "get_llm", _must_not_be_called)

    patient = _make_user(session, email="empty-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=_auth_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["trends"] == []
    assert body["attention_points"] == []
    assert body["data_completeness"]["glucose"]["found"] == 0
    assert body["data_completeness"]["sufficient_for_trends"] is False
    assert "dữ liệu" in body["summary"]  # explicitly names the data gap, doesn't invent a trend


@pytest.mark.asyncio
async def test_doctor_summary_for_assigned_patient(client: AsyncClient, session: Session, monkeypatch, mock_llm):
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    doctor = _make_user(session, email="summary-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="summary-doc-patient@diab.example.com", role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    _seed_weekday_weekend_pattern(session, patient_id=patient.id, creator_id=doctor.id, num_days=14)

    token = await _login(client, doctor.email)
    response = await client.post(
        "/api/v1/agent/summary",
        json={"patient_id": str(patient.id), "days": 30},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["data_completeness"]["glucose"]["found"] == 28  # 2 readings/day * 14 days
    assert body["trends"]
    assert any("[Quan sát]" in t for t in body["trends"]), "doctor tone must separate observation vs inference"
    assert body["suggested_doctor_questions"] == []  # doctor role: no suggested-questions list


@pytest.mark.asyncio
async def test_doctor_summary_surfaces_patient_reported_adverse_reaction(
    client: AsyncClient, session: Session, monkeypatch, mock_llm
):
    """Full path through the real API: doctor prescribes a medication, patient self-reports an
    adverse reaction against it, doctor's AI summary must surface that report as an attention
    point — proving fetch_data -> synthesize_summary actually wires medications end-to-end,
    not just at the unit level."""
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    doctor = _make_user(session, email="med-summary-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="med-summary-patient@diab.example.com", role=UserRole.PATIENT)
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()
    _seed_weekday_weekend_pattern(session, patient_id=patient.id, creator_id=doctor.id, num_days=14)

    doctor_token = await _login(client, doctor.email)
    med_resp = await client.post(
        "/api/v1/medications",
        json={
            "patient_id": str(patient.id),
            "drug_name": "Metformin",
            "dose": "500mg",
            "frequency": "twice daily",
            "start_date": datetime.utcnow().isoformat(),
        },
        headers=_auth_headers(doctor_token),
    )
    assert med_resp.status_code == 201, med_resp.text
    medication_id = med_resp.json()["id"]

    patient_token = await _login(client, patient.email)
    note_resp = await client.post(
        f"/api/v1/medications/{medication_id}/notes",
        json={"note_type": "adverse_reaction", "text": "Buồn nôn dữ dội sau khi uống thuốc"},
        headers=_auth_headers(patient_token),
    )
    assert note_resp.status_code == 201, note_resp.text

    response = await client.post(
        "/api/v1/agent/summary",
        json={"patient_id": str(patient.id), "days": 30},
        headers=_auth_headers(doctor_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert any("Buồn nôn dữ dội" in point for point in body["attention_points"])
    assert any("medication_notes" in item for item in body["data_used"])


@pytest.mark.asyncio
async def test_unassigned_doctor_cannot_summarize_patient(client: AsyncClient, session: Session, monkeypatch, mock_llm):
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    doctor = _make_user(session, email="unassigned-doctor@diab.example.com", role=UserRole.DOCTOR)
    patient = _make_user(session, email="unassigned-doc-patient@diab.example.com", role=UserRole.PATIENT)

    token = await _login(client, doctor.email)
    response = await client.post(
        "/api/v1/agent/summary",
        json={"patient_id": str(patient.id), "days": 7},
        headers=_auth_headers(token),
    )

    # Permission denial is a real authorization failure — resolve_context.py's
    # graph-level short-circuit is translated into HTTP 403 by router.py,
    # same convention as every other patient-data endpoint in this codebase
    # (health/router.py's ensure_patient_access). tests/test_models/test_graph.py
    # still covers the graph layer itself (asserts on result.get("error")
    # directly against agent.ainvoke(), unaffected by this HTTP-layer mapping).
    assert response.status_code == 403, response.text
    assert "quyền" in response.json()["detail"]


@pytest.mark.asyncio
async def test_summary_date_range_7_vs_30_days(client: AsyncClient, session: Session, monkeypatch, mock_llm):
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    patient = _make_user(session, email="range-patient@diab.example.com", role=UserRole.PATIENT)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    recent = now - timedelta(days=2)
    old = now - timedelta(days=20)  # outside a 7-day window, inside a 30-day window

    for when in (recent, old):
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=150.0,
            unit="mg/dL",
            measured_at=when,
            note=None,
            created_by=patient.id,
            meal_context=MealContext.FASTING,
        )

    token = await _login(client, patient.email)
    headers = _auth_headers(token)

    resp_7 = await client.post("/api/v1/agent/summary", json={"days": 7}, headers=headers)
    assert resp_7.status_code == 200, resp_7.text
    assert resp_7.json()["data_completeness"]["glucose"]["found"] == 1

    resp_30 = await client.post("/api/v1/agent/summary", json={"days": 30}, headers=headers)
    assert resp_30.status_code == 200, resp_30.text
    assert resp_30.json()["data_completeness"]["glucose"]["found"] == 2

    # date_range itself must actually widen, not just the record count.
    range_7 = resp_7.json()["date_range"]
    range_30 = resp_30.json()["date_range"]
    assert range_7 is not None and range_30 is not None
    start_7 = datetime.fromisoformat(range_7[0])
    start_30 = datetime.fromisoformat(range_30[0])
    assert start_30 < start_7


@pytest.mark.asyncio
async def test_summary_date_range_capped_at_30_days(client: AsyncClient, session: Session, monkeypatch, mock_llm):
    """PRD §15.7: even if a caller could somehow ask for more, the tool layer
    itself caps at 30 days — exercised here via the request schema's own
    le=30 validation, i.e. the API rejects an out-of-range request outright."""
    monkeypatch.setattr(synthesize_summary_mod, "get_llm", lambda: mock_llm)

    patient = _make_user(session, email="cap-patient@diab.example.com", role=UserRole.PATIENT)
    token = await _login(client, patient.email)

    response = await client.post("/api/v1/agent/summary", json={"days": 90}, headers=_auth_headers(token))
    assert response.status_code == 422

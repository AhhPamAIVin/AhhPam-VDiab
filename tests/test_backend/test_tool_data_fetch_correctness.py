"""DB-fetch / tool-calling correctness for the respond-agent's data layer:
tools/patient_tools.py (get_patient_profile, get_health_records, get_medication_summary,
get_assigned_doctor), nodes/fetch_data.py (orchestration + date-range resolution), and
nodes/check_data_quality.py (completeness scoring against what was actually fetched).

Every number the agent ever states about a patient traces back to what these functions
pulled from the DB (fetch_data.py's own docstring: "no downstream node is allowed to
invent one") — so a wrong patient_id, a wrong date-range boundary, or a wrong tool
argument here is a more fundamental failure than a routing or guardrail miss: it's the
agent confidently reporting the wrong facts about a real patient's own health data.
Existing coverage (test_agent_summary.py, test_agent_chat.py) checks aggregate counts and
LLM-prompt string content; this file asserts directly on tool_results/state shape and on
exact seeded-record content round-tripping through the real DB — no LLM involved anywhere,
since fetch_data.py and check_data_quality.py both run before any LLM node.

No HTTP client, no mocked LLM — everything here calls the tool/node functions directly
against the real in-memory-SQLite `session` fixture (tests/conftest.py), same DB-access
path production code takes (session passed explicitly, never opened by the tool itself).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from codebase.backend.health.models import MealContext, MealType
from codebase.backend.health.service import (
    create_activity_record,
    create_blood_pressure_record,
    create_glucose_record,
    create_meal_record,
)
from codebase.backend.medications.models import MedicationNoteType
from codebase.backend.medications.service import create_medication, create_medication_note
from codebase.backend.patients.models import PatientAssignment
from codebase.models.agents.nodes.check_data_quality import check_data_quality
from codebase.models.agents.nodes.fetch_data import (
    DEFAULT_DATE_RANGE_DAYS,
    DOCTOR_SUMMARY_MAX_DAYS,
    MAX_DATE_RANGE_DAYS,
    fetch_data,
)
from codebase.models.agents.tools.patient_tools import (
    get_assigned_doctor,
    get_health_records,
    get_medication_summary,
    get_patient_profile,
)

PASSWORD = "AdminPass123"


def _make_user(session: Session, *, email: str, role: UserRole) -> User:
    user = User(email=email, full_name=email.split("@")[0], role=role, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def patient(session: Session) -> User:
    return _make_user(session, email="fetch-correctness-patient@diab.example.com", role=UserRole.PATIENT)


@pytest.fixture
def other_patient(session: Session) -> User:
    return _make_user(session, email="fetch-correctness-other-patient@diab.example.com", role=UserRole.PATIENT)


@pytest.fixture
def doctor(session: Session) -> User:
    return _make_user(session, email="fetch-correctness-doctor@diab.example.com", role=UserRole.DOCTOR)


NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)


# ---------------------------------------------------------------------------
# get_health_records — glucose/blood_pressure/meals/activities
# ---------------------------------------------------------------------------


def test_get_health_records_returns_exact_seeded_content(session: Session, patient: User):
    """Round-trip check, not just a count: the specific value/unit/meal_context/note
    seeded must come back unchanged — a correctness bug that silently mutates or drops a
    field wouldn't show up in any count-only assertion."""
    start, end = NOW - timedelta(days=1), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=142.5,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=2),
        note="sau bữa trưa",
        created_by=patient.id,
        meal_context=MealContext.AFTER_MEAL,
    )

    records = get_health_records(session, patient.id, start, end)

    assert len(records["glucose"]) == 1
    g = records["glucose"][0]
    assert g["value"] == 142.5
    assert g["unit"] == "mg/dL"
    assert g["note"] == "sau bữa trưa"
    assert g["meal_context"] == MealContext.AFTER_MEAL


def test_get_health_records_all_four_record_types_populated(session: Session, patient: User):
    start, end = NOW - timedelta(days=1), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=1),
        note=None,
        created_by=patient.id,
    )
    create_blood_pressure_record(
        session,
        patient_id=patient.id,
        systolic=120.0,
        diastolic=80.0,
        unit="mmHg",
        measured_at=NOW - timedelta(hours=1),
        note=None,
        created_by=patient.id,
    )
    create_meal_record(
        session,
        patient_id=patient.id,
        meal_type=MealType.LUNCH,
        meal_time=NOW - timedelta(hours=1),
        description="cơm gà",
        note=None,
        created_by=patient.id,
    )
    create_activity_record(
        session,
        patient_id=patient.id,
        activity_type="đi bộ",
        duration_minutes=30,
        activity_time=NOW - timedelta(hours=1),
        note=None,
        created_by=patient.id,
    )

    records = get_health_records(session, patient.id, start, end)

    assert len(records["glucose"]) == 1
    assert len(records["blood_pressure"]) == 1
    assert records["blood_pressure"][0]["systolic"] == 120.0
    assert len(records["meals"]) == 1
    assert records["meals"][0]["description"] == "cơm gà"
    assert len(records["activities"]) == 1
    assert records["activities"][0]["duration_minutes"] == 30


def test_get_health_records_excludes_records_outside_range(session: Session, patient: User):
    start, end = NOW - timedelta(days=7), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=90.0,
        unit="mg/dL",
        measured_at=start - timedelta(minutes=1),
        note="before window",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=95.0,
        unit="mg/dL",
        measured_at=end + timedelta(minutes=1),
        note="after window",
        created_by=patient.id,
    )

    records = get_health_records(session, patient.id, start, end)

    assert records["glucose"] == []


def test_get_health_records_includes_boundary_timestamps_inclusive(session: Session, patient: User):
    start, end = NOW - timedelta(days=7), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=91.0,
        unit="mg/dL",
        measured_at=start,
        note="exactly at start",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=92.0,
        unit="mg/dL",
        measured_at=end,
        note="exactly at end",
        created_by=patient.id,
    )

    records = get_health_records(session, patient.id, start, end)

    assert {r["note"] for r in records["glucose"]} == {"exactly at start", "exactly at end"}


def test_get_health_records_isolates_by_patient(session: Session, patient: User, other_patient: User):
    start, end = NOW - timedelta(days=1), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=1),
        note="mine",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=other_patient.id,
        value=200.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=1),
        note="not mine",
        created_by=other_patient.id,
    )

    records = get_health_records(session, patient.id, start, end)

    assert len(records["glucose"]) == 1
    assert records["glucose"][0]["note"] == "mine"


def test_get_health_records_swaps_reversed_start_end(session: Session, patient: User):
    """start/end reversed (a caller bug elsewhere) must not silently return zero
    results — get_health_records swaps them rather than propagating the mistake."""
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=1),
        note=None,
        created_by=patient.id,
    )

    reversed_start, reversed_end = NOW, NOW - timedelta(days=1)
    records = get_health_records(session, patient.id, reversed_start, reversed_end)

    assert len(records["glucose"]) == 1


def test_get_health_records_caps_at_30_days_even_when_called_directly(session: Session, patient: User):
    """PRD §15.7's 30-day cap is enforced inside get_health_records itself (not just by
    fetch_data.py's own cap upstream), so any future caller gets the same guarantee."""
    end = NOW
    start = end - timedelta(days=45)
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=end - timedelta(days=40),
        note="outside the effective 30-day window",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=101.0,
        unit="mg/dL",
        measured_at=end - timedelta(days=10),
        note="inside the effective 30-day window",
        created_by=patient.id,
    )

    records = get_health_records(session, patient.id, start, end)

    assert [r["note"] for r in records["glucose"]] == ["inside the effective 30-day window"]


# ---------------------------------------------------------------------------
# get_medication_summary
# ---------------------------------------------------------------------------


def test_get_medication_summary_active_list_ignores_date_range(session: Session, patient: User):
    """`active` is the patient's current regimen, not scoped to [start, end] — a medication
    started long before a narrow recent window must still appear. This is a real, easy
    tool-argument-omission bug to introduce accidentally (start/end are right there in the
    function signature), so it's asserted explicitly rather than only implicitly relied on."""
    create_medication(
        session,
        patient_id=patient.id,
        drug_name="Metformin",
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=200),
        note=None,
        created_by=patient.id,
    )
    narrow_start, narrow_end = NOW - timedelta(days=1), NOW

    summary = get_medication_summary(session, patient.id, narrow_start, narrow_end)

    assert len(summary["active"]) == 1
    assert summary["active"][0]["drug_name"] == "Metformin"


def test_get_medication_summary_excludes_discontinued(session: Session, patient: User):
    active_med = create_medication(
        session,
        patient_id=patient.id,
        drug_name="Metformin",
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=30),
        note=None,
        created_by=patient.id,
    )
    discontinued_med = create_medication(
        session,
        patient_id=patient.id,
        drug_name="Gliclazide",
        dose="30mg",
        frequency="1 lần/ngày",
        start_date=NOW - timedelta(days=60),
        note=None,
        created_by=patient.id,
    )
    discontinued_med.status = "discontinued"
    session.add(discontinued_med)
    session.commit()

    summary = get_medication_summary(session, patient.id, NOW - timedelta(days=1), NOW)

    assert [m["drug_name"] for m in summary["active"]] == [active_med.drug_name]


def test_get_medication_summary_notes_filtered_inclusive_by_range(session: Session, patient: User):
    med = create_medication(
        session,
        patient_id=patient.id,
        drug_name="Metformin",
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=30),
        note=None,
        created_by=patient.id,
    )
    start, end = NOW - timedelta(days=7), NOW
    create_medication_note(
        session,
        medication=med,
        note_type=MedicationNoteType.MISSED_DOSE,
        text="before window",
        occurred_at=start - timedelta(minutes=1),
        created_by=patient.id,
    )
    create_medication_note(
        session,
        medication=med,
        note_type=MedicationNoteType.MISSED_DOSE,
        text="at start boundary",
        occurred_at=start,
        created_by=patient.id,
    )
    create_medication_note(
        session,
        medication=med,
        note_type=MedicationNoteType.ADVERSE_REACTION,
        text="at end boundary",
        occurred_at=end,
        created_by=patient.id,
    )
    create_medication_note(
        session,
        medication=med,
        note_type=MedicationNoteType.MISSED_DOSE,
        text="after window",
        occurred_at=end + timedelta(minutes=1),
        created_by=patient.id,
    )

    summary = get_medication_summary(session, patient.id, start, end)

    assert {n["text"] for n in summary["notes"]} == {"at start boundary", "at end boundary"}


def test_get_medication_summary_isolates_by_patient(session: Session, patient: User, other_patient: User):
    create_medication(
        session,
        patient_id=patient.id,
        drug_name="Metformin",
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=30),
        note=None,
        created_by=patient.id,
    )
    create_medication(
        session,
        patient_id=other_patient.id,
        drug_name="Insulin",
        dose="10u",
        frequency="1 lần/ngày",
        start_date=NOW - timedelta(days=30),
        note=None,
        created_by=other_patient.id,
    )

    summary = get_medication_summary(session, patient.id, NOW - timedelta(days=1), NOW)

    assert [m["drug_name"] for m in summary["active"]] == ["Metformin"]


# ---------------------------------------------------------------------------
# get_patient_profile / get_assigned_doctor
# ---------------------------------------------------------------------------


def test_get_patient_profile_found(session: Session, patient: User):
    profile = get_patient_profile(session, patient.id)

    assert profile == {
        "patient_id": str(patient.id),
        "full_name": patient.full_name,
        "role": "patient",
        "found": True,
    }


def test_get_patient_profile_not_found_returns_false_not_raise(session: Session):
    profile = get_patient_profile(session, uuid4())

    assert profile["found"] is False


def test_get_assigned_doctor_when_assigned(session: Session, patient: User, doctor: User):
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    session.commit()

    result = get_assigned_doctor(session, patient.id)

    assert result == {"assigned": True, "doctor_id": str(doctor.id), "full_name": doctor.full_name}


def test_get_assigned_doctor_when_unassigned_returns_false_not_raise(session: Session, patient: User):
    result = get_assigned_doctor(session, patient.id)

    assert result == {"assigned": False, "doctor_id": None, "full_name": None}


# ---------------------------------------------------------------------------
# fetch_data.py — orchestration + date-range resolution
# ---------------------------------------------------------------------------


def _state(**overrides) -> dict:
    # needs_health_data=True: this whole file exercises fetch_data.py's real
    # fetching behavior for QA, which is now conditional on nodes/
    # plan_response.py's LLM judgment (was unconditional for any QA intent
    # before that node existed) — every test here wants the fetch to actually
    # run unless it explicitly overrides this to test the opposite.
    base = {
        "intent": "QA",
        "patient_id": None,
        "date_range_days": None,
        "date_range_override": None,
        "needs_health_data": True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_fetch_data_qa_without_needs_health_data_clears_stale_tool_results(session: Session, patient: User):
    """Regression test for a staleness hazard this redesign introduces:
    fetch_data ran unconditionally for every QA turn before nodes/
    plan_response.py's needs_health_data field existed, so tool_results was
    always freshly overwritten. Now that a QA turn can skip fetching
    entirely, it must not silently inherit a *previous* turn's fetched
    records from checkpointed state — simulated here by passing in
    tool_results/date_range as if a prior turn on the same thread had
    already populated them."""
    result = await fetch_data(
        _state(
            intent="QA",
            patient_id=patient.id,
            needs_health_data=False,
            tool_results={"profile": {"name": "stale"}, "records": {"glucose": [{"value": 999}]}},
            date_range=(NOW - timedelta(days=7), NOW),
        ),
        {"configurable": {"session": session}},
    )
    assert result == {"tool_results": {}, "date_range": None}


@pytest.mark.asyncio
async def test_fetch_data_noop_for_non_summary_qa_intent(session: Session, patient: User):
    result = await fetch_data(_state(intent="JAILBREAK", patient_id=patient.id), {"configurable": {"session": session}})

    assert result == {}


@pytest.mark.asyncio
async def test_fetch_data_noop_when_patient_id_missing(session: Session):
    result = await fetch_data(_state(intent="SUMMARY", patient_id=None), {"configurable": {"session": session}})

    assert result == {"tool_results": {}, "date_range": None}


@pytest.mark.asyncio
async def test_fetch_data_default_trailing_window_is_7_days(session: Session, patient: User):
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(days=3),
        note="in window",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=999.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(days=10),
        note="outside default 7-day window",
        created_by=patient.id,
    )

    result = await fetch_data(_state(intent="SUMMARY", patient_id=patient.id), {"configurable": {"session": session}})

    start, end = result["date_range"]
    assert (end - start).days == DEFAULT_DATE_RANGE_DAYS
    assert [r["note"] for r in result["tool_results"]["records"]["glucose"]] == ["in window"]


@pytest.mark.asyncio
async def test_fetch_data_uses_date_range_days_when_set(session: Session, patient: User):
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(days=10),
        note="within 14 but not default 7",
        created_by=patient.id,
    )

    result = await fetch_data(
        _state(intent="SUMMARY", patient_id=patient.id, date_range_days=14), {"configurable": {"session": session}}
    )

    assert [r["note"] for r in result["tool_results"]["records"]["glucose"]] == ["within 14 but not default 7"]


@pytest.mark.asyncio
async def test_fetch_data_caps_date_range_days_at_30(session: Session, patient: User):
    result = await fetch_data(
        _state(intent="SUMMARY", patient_id=patient.id, date_range_days=90), {"configurable": {"session": session}}
    )

    start, end = result["date_range"]
    assert (end - start).days == MAX_DATE_RANGE_DAYS


@pytest.mark.asyncio
async def test_fetch_data_override_takes_priority_over_date_range_days(session: Session, patient: User):
    """Both set at once (state.py's docstring: override takes priority) — proven here by
    seeding a record that's inside the wider date_range_days=30 window but OUTSIDE the
    narrower override window, and asserting it does NOT come back."""
    override_start, override_end = NOW - timedelta(days=3), NOW
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(days=1),
        note="inside override window",
        created_by=patient.id,
    )
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=200.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(days=20),
        note="inside date_range_days=30 but outside override",
        created_by=patient.id,
    )

    result = await fetch_data(
        _state(
            intent="QA",
            patient_id=patient.id,
            date_range_days=30,
            date_range_override=(override_start, override_end),
        ),
        {"configurable": {"session": session}},
    )

    assert [r["note"] for r in result["tool_results"]["records"]["glucose"]] == ["inside override window"]
    assert result["date_range"] == (override_start, override_end)


@pytest.mark.asyncio
async def test_fetch_data_override_capped_at_30_days(session: Session, patient: User):
    override_end = NOW
    override_start = override_end - timedelta(days=60)

    result = await fetch_data(
        _state(intent="QA", patient_id=patient.id, date_range_override=(override_start, override_end)),
        {"configurable": {"session": session}},
    )

    start, end = result["date_range"]
    assert end == override_end
    assert (end - start).days == MAX_DATE_RANGE_DAYS


@pytest.mark.asyncio
async def test_doctor_summary_override_spans_up_to_90_days_with_previous_window(session: Session, patient: User):
    """Epic 4: a pre-visit brief covers the time since the last visit (up to
    90 days) and compares it with the equally long window before it."""
    override_end = NOW
    for days_ago, value, note in ((50, 150.0, "since last visit"), (70, 170.0, "before last visit")):
        create_glucose_record(
            session, patient_id=patient.id, value=value, unit="mg/dL",
            measured_at=NOW - timedelta(days=days_ago), note=note, created_by=patient.id,
        )

    result = await fetch_data(
        _state(
            intent="SUMMARY", user_role="doctor", patient_id=patient.id,
            date_range_override=(override_end - timedelta(days=60), override_end),
        ),
        {"configurable": {"session": session}},
    )

    start, end = result["date_range"]
    assert (end - start).days == 60
    assert [r["note"] for r in result["tool_results"]["records"]["glucose"]] == ["since last visit"]
    previous = result["tool_results"]["previous_period"]
    assert (previous["start"], previous["end"]) == (start - timedelta(days=60), start)
    assert previous["metrics"]["glucose_overall"]["avg"] == 170

    capped = await fetch_data(
        _state(
            intent="SUMMARY", user_role="doctor", patient_id=patient.id,
            date_range_override=(override_end - timedelta(days=200), override_end),
        ),
        {"configurable": {"session": session}},
    )
    capped_start, capped_end = capped["date_range"]
    assert (capped_end - capped_start).days == DOCTOR_SUMMARY_MAX_DAYS


@pytest.mark.asyncio
async def test_patient_summary_has_no_previous_window(session: Session, patient: User):
    result = await fetch_data(
        _state(intent="SUMMARY", user_role="patient", patient_id=patient.id), {"configurable": {"session": session}}
    )
    assert "previous_period" not in result["tool_results"]


@pytest.mark.asyncio
async def test_fetch_data_populates_all_tool_results_from_real_db(session: Session, patient: User, doctor: User):
    session.add(PatientAssignment(doctor_id=doctor.id, patient_id=patient.id, assigned_by=doctor.id))
    create_medication(
        session,
        patient_id=patient.id,
        drug_name="Metformin",
        dose="500mg",
        frequency="2 lần/ngày",
        start_date=NOW - timedelta(days=30),
        note=None,
        created_by=patient.id,
    )
    session.commit()

    result = await fetch_data(_state(intent="SUMMARY", patient_id=patient.id), {"configurable": {"session": session}})

    tool_results = result["tool_results"]
    # SUMMARY always carries the meal <-> glucose groups (Epic 1.4) and
    # medication adherence (Epic 3.2); the previous window is doctor-only.
    assert set(tool_results.keys()) == {
        "profile", "records", "medications", "assigned_doctor", "meal_glucose_response", "medication_adherence",
    }
    assert tool_results["meal_glucose_response"]["by_food"] == []
    assert tool_results["profile"]["found"] is True
    assert tool_results["profile"]["patient_id"] == str(patient.id)
    assert tool_results["assigned_doctor"] == {
        "assigned": True,
        "doctor_id": str(doctor.id),
        "full_name": doctor.full_name,
    }
    assert [m["drug_name"] for m in tool_results["medications"]["active"]] == ["Metformin"]


# ---------------------------------------------------------------------------
# check_data_quality.py — completeness scored against REAL fetched records,
# not hand-built dicts (test_agent_summary.py already covers the arithmetic
# in isolation; this closes the gap of proving it against an actual DB round-trip)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_data_quality_completeness_matches_real_fetched_records(session: Session, patient: User):
    days = 7
    for day_offset in range(3):
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=100.0 + day_offset,
            unit="mg/dL",
            measured_at=NOW - timedelta(days=day_offset, hours=1),
            note=None,
            created_by=patient.id,
            meal_context=MealContext.FASTING,
        )
    for day_offset in range(2):
        create_meal_record(
            session,
            patient_id=patient.id,
            meal_type=MealType.LUNCH,
            meal_time=NOW - timedelta(days=day_offset, hours=2),
            description="cơm",
            note=None,
            created_by=patient.id,
        )

    fetch_result = await fetch_data(
        _state(intent="SUMMARY", patient_id=patient.id, date_range_days=days), {"configurable": {"session": session}}
    )
    state = {**_state(intent="SUMMARY"), **fetch_result}
    result = await check_data_quality(state)

    # days_in_range is check_data_quality's own inclusive CALENDAR-day count
    # ((end.date() - start.date()).days + 1) over the actual start/end fetch_data
    # resolved — NOT necessarily equal to the requested date_range_days=7: a
    # trailing datetime.utcnow()-based window rarely lands on a local midnight
    # boundary, so 7*24h of elapsed time commonly spans 8 distinct calendar
    # dates. Deriving the expectation from the same real start/end (rather than
    # hardcoding 7) is what actually proves the arithmetic is self-consistent
    # against real fetched data, which is this test's point.
    start, end = fetch_result["date_range"]
    expected_days_in_range = (end.date() - start.date()).days + 1
    completeness = result["data_completeness"]
    assert completeness["days_in_range"] == expected_days_in_range
    assert completeness["glucose"]["found"] == 3
    assert completeness["glucose"]["expected"] == expected_days_in_range * 3
    assert completeness["glucose"]["completeness_pct"] == round(3 / (expected_days_in_range * 3) * 100, 1)
    assert completeness["meals"]["found"] == 2
    assert completeness["meals"]["expected"] == expected_days_in_range * 3
    assert completeness["sufficient_for_trends"] is False  # 3 < MIN_GLUCOSE_READINGS_FOR_TRENDS (4)


@pytest.mark.asyncio
async def test_check_data_quality_sufficient_for_trends_threshold(session: Session, patient: User):
    for hour_offset in range(4):
        create_glucose_record(
            session,
            patient_id=patient.id,
            value=100.0,
            unit="mg/dL",
            measured_at=NOW - timedelta(hours=hour_offset),
            note=None,
            created_by=patient.id,
        )

    fetch_result = await fetch_data(
        _state(intent="SUMMARY", patient_id=patient.id, date_range_days=7), {"configurable": {"session": session}}
    )
    state = {**_state(intent="SUMMARY"), **fetch_result}
    result = await check_data_quality(state)

    assert result["data_completeness"]["sufficient_for_trends"] is True  # exactly 4, the threshold


@pytest.mark.asyncio
async def test_check_data_quality_noop_for_non_summary_qa_intent():
    result = await check_data_quality(_state(intent="JAILBREAK"))

    assert result == {}


@pytest.mark.asyncio
async def test_check_data_quality_qa_gets_metrics_but_no_completeness_scoring(session: Session, patient: User):
    create_glucose_record(
        session,
        patient_id=patient.id,
        value=100.0,
        unit="mg/dL",
        measured_at=NOW - timedelta(hours=1),
        note=None,
        created_by=patient.id,
    )
    fetch_result = await fetch_data(
        _state(intent="QA", patient_id=patient.id, date_range_days=7), {"configurable": {"session": session}}
    )
    state = {**_state(intent="QA"), **fetch_result}

    result = await check_data_quality(state)

    assert "data_completeness" not in result
    assert result["tool_results"]["metrics"]["glucose_count"] == 1

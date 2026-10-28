import os
from collections.abc import Generator
from unittest.mock import AsyncMock

# Keep tests off any locally configured tracing server / Qdrant instance, no
# matter what the developer's .env enables (env vars win over .env). Must run
# before the app import below caches Settings.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
# In-process Qdrant: no server needed, nothing written to a real collection.
os.environ["QDRANT_LOCATION"] = ":memory:"
# Every test logs in again; per-IP login limits would trip across tests.
# tests/test_backend/test_rate_limit.py turns them back on for itself.
os.environ["RATE_LIMIT_ENABLED"] = "false"
# The chat tests pin plan_response's contract with a mocked planner LLM; the
# patient agent (Epic 8, on by default) would otherwise make a real LLM call.
# tests/test_backend/test_patient_agent.py turns it on for itself.
os.environ["PATIENT_AGENT_ENABLED"] = "false"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from codebase.backend.db import get_session
from codebase.backend.main import app


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """Fresh in-memory SQLite database per test — keeps tests off real Postgres."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


@pytest_asyncio.fixture
async def client(session: Session):
    """Async HTTP client for testing API endpoints, backed by the in-memory `session` fixture."""
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock


@pytest.fixture(autouse=True)
def _no_background_summary_refresh(monkeypatch):
    """health/router.py's 4 record-creation endpoints schedule
    summaries.service.refresh_patient_summaries as a FastAPI BackgroundTask
    on every write. Left alone, that function opens its own `Session(engine)`
    against the real configured DATABASE_URL (not the in-memory SQLite the
    `session`/`client` fixtures above point requests at) and calls the real
    LLM unless mocked — every test in the suite that creates a health record
    via the HTTP client would otherwise silently do both in the background.

    Same reasoning this codebase already applies to its other
    Session(engine)-opening background triggers (notifications/scheduler.py,
    insights/scheduler.py): those are only ever exercised in tests by calling
    their inner, session-taking function directly against the test `session`
    fixture (see test_insights_scheduler.py), never through the
    engine-opening outer wrapper. tests/test_backend/test_summaries.py
    follows the same convention for generate_and_store_snapshot/
    ensure_snapshot, and overrides this no-op locally where it wants to
    assert the write-path trigger actually fires."""

    async def _noop(patient_id) -> None:
        return None

    monkeypatch.setattr("codebase.backend.health.router.refresh_patient_summaries", _noop)
    monkeypatch.setattr("codebase.backend.agent.router.refresh_patient_summaries", _noop)


@pytest.fixture(autouse=True)
def _no_background_meal_analysis(monkeypatch):
    """Same reason as above for the meal analysis BackgroundTask (Epic 1.3):
    analyze_meal_in_background opens Session(engine) on the real database.
    Tests run the analysis through health.meal_analysis.analyze_meal on the
    test session instead."""

    async def _noop(meal_id) -> None:
        return None

    monkeypatch.setattr("codebase.backend.health.meal_analysis.analyze_meal_in_background", _noop)


@pytest.fixture(autouse=True)
def _no_background_appointment_reflection(monkeypatch):
    """Same again for the clinical-note reflect BackgroundTask: tests call
    insights.appointment_reflection.reflect_on_clinical_note on the test
    session instead."""

    async def _noop(note_id) -> None:
        return None

    monkeypatch.setattr(
        "codebase.backend.insights.appointment_reflection.reflect_on_clinical_note_in_background", _noop
    )

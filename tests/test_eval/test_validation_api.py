from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from codebase.backend.auth.models import User, UserRole
from codebase.backend.auth.security import hash_password
from eval.harness.datasets import load_cases

PASSWORD = "EvalPass123"
EMAIL = "golden-validation@diab.example.com"
GLUCOSE_CASES = [case for case in load_cases("data_validation_cases.json") if case["entity"] == "glucose_record"]


@pytest.fixture
def eval_patient(session: Session) -> User:
    user = User(email=EMAIL, full_name="Golden Validation", role=UserRole.PATIENT, hashed_password=hash_password(PASSWORD))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


async def _headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", data={"username": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
@pytest.mark.parametrize("case", GLUCOSE_CASES, ids=lambda case: case["id"])
async def test_glucose_validation_golden_case_through_real_endpoint(
    client: AsyncClient, eval_patient: User, case: dict
):
    headers = await _headers(client)
    payload = {**case["payload"], "patient_id": str(eval_patient.id)}
    if case.get("context", {}).get("existing_record"):
        seed = {**payload, "measured_at": case["context"]["existing_record"]["measured_at"]}
        first = await client.post("/api/v1/health/glucose", json=seed, headers=headers)
        assert first.status_code == 201, first.text

    response = await client.post("/api/v1/health/glucose", json=payload, headers=headers)
    expected = case["expected_result"]
    assert response.status_code == expected["http_code"], response.text
    if response.status_code == 422 and expected.get("error_field"):
        locations = [item.get("loc", []) for item in response.json().get("detail", [])]
        assert any(expected["error_field"] in location for location in locations), response.text


@pytest.mark.unit
def test_future_validation_case_remains_future():
    future_case = next(case for case in GLUCOSE_CASES if case["id"] == "TC-VAL-003")
    measured_at = datetime.fromisoformat(future_case["payload"]["measured_at"].replace("Z", "+00:00"))
    assert measured_at > datetime.now(UTC)

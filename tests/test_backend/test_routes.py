import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_production_requires_qdrant_api_key():
    from types import SimpleNamespace

    import pytest

    from codebase.backend.main import check_production_settings

    def settings(**overrides):
        base = {"app_env": "production", "qdrant_location": "", "qdrant_api_key": ""}
        return SimpleNamespace(**{**base, **overrides})

    with pytest.raises(RuntimeError, match="QDRANT_API_KEY"):
        check_production_settings(settings())
    check_production_settings(settings(qdrant_api_key="secret"))
    check_production_settings(settings(app_env="development"))
    check_production_settings(settings(qdrant_location=":memory:"))

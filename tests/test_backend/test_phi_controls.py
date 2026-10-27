"""Task 6.4 — patient data does not leave through tracing or logs."""

import logging

import pytest

from codebase.backend.config import Settings
from codebase.backend.db import engine
from codebase.backend.main import check_production_settings
from codebase.models import tracing


def _settings(**overrides) -> Settings:
    return Settings(jwt_secret_key="x", qdrant_api_key="k", **overrides)


@pytest.mark.parametrize(
    ("app_env", "override", "expected"),
    [
        ("production", None, True),  # default in production: redact
        ("development", None, False),
        ("test", None, False),
        ("production", False, False),  # explicit opt-out still possible
        ("development", True, True),
    ],
)
def test_trace_content_is_redacted_by_default_in_production(monkeypatch, app_env, override, expected):
    monkeypatch.setattr(
        tracing, "get_settings", lambda: _settings(app_env=app_env, langfuse_redact_content=override)
    )
    assert tracing.redact_content_enabled() is expected


def test_redaction_mask_drops_every_payload():
    prompt = [{"role": "user", "content": "Đường huyết của tôi sáng nay 250 mg/dL"}]
    assert tracing.redact_all(data=prompt) == tracing.REDACTED
    assert tracing.redact_all(data={"glucose": 250}) == tracing.REDACTED
    assert tracing.redact_all(data=None) is None


def test_langfuse_client_gets_the_mask_in_production(monkeypatch):
    seen = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    class FakeHandler:
        def __init__(self, **kwargs):
            pass

    import langfuse
    import langfuse.langchain

    monkeypatch.setattr(langfuse, "Langfuse", FakeLangfuse)
    monkeypatch.setattr(langfuse.langchain, "CallbackHandler", FakeHandler)
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: _settings(app_env="production", langfuse_public_key="pk", langfuse_secret_key="sk"),
    )
    tracing.get_langfuse_handler.cache_clear()
    try:
        assert tracing.get_langfuse_handler() is not None
    finally:
        tracing.get_langfuse_handler.cache_clear()
    assert seen["mask"] is tracing.redact_all


@pytest.mark.parametrize("name", ["LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"])
def test_production_refuses_langsmith_tracing(name):
    with pytest.raises(RuntimeError, match=name):
        check_production_settings(_settings(app_env="production"), environ={name: "true"})
    check_production_settings(_settings(app_env="production"), environ={name: "false"})
    check_production_settings(_settings(app_env="development"), environ={name: "true"})


def test_sql_errors_do_not_carry_bound_values():
    assert engine.hide_parameters is True


def test_httpx_request_urls_are_not_logged():
    # The Zalo Bot token sits in the request URL path.
    import codebase.backend.main  # noqa: F401 — sets the levels

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING

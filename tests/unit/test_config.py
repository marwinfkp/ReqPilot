"""Configuration tests (ADR-011).

The properties that matter: valid config loads, genuinely-required config fails
fast, and secrets never appear in a representation that could reach a log.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from reqpilot.config import AppEnv, LLMProvider, Settings

pytestmark = pytest.mark.unit


def test_valid_configuration_loads(settings: Settings) -> None:
    assert settings.app_env is AppEnv.TEST
    assert settings.llm_provider is LLMProvider.STUB


def test_defaults_require_no_api_key() -> None:
    """P0 must be usable with no credentials at all.

    This is the test that keeps 'install, migrate, run and test without an API
    key' true as configuration grows.
    """
    s = Settings(_env_file=None)
    assert s.llm_provider is LLMProvider.STUB
    assert s.llm_api_key is None


def test_network_provider_requires_a_key() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        Settings(_env_file=None, LLM_PROVIDER=LLMProvider.ANTHROPIC, LLM_API_KEY=None)


def test_network_provider_accepts_a_key() -> None:
    s = Settings(_env_file=None, LLM_PROVIDER=LLMProvider.ANTHROPIC, LLM_API_KEY="placeholder")
    assert s.llm_api_key == "placeholder"


def test_ollama_needs_no_key() -> None:
    """A local provider is not a network provider for credential purposes."""
    s = Settings(_env_file=None, LLM_PROVIDER=LLMProvider.OLLAMA)
    assert s.llm_api_key is None


def test_production_rejects_the_development_secret() -> None:
    with pytest.raises(ValidationError, match="must be set to a real value"):
        Settings(_env_file=None, REQPILOT_ENV=AppEnv.PRODUCTION)


@pytest.mark.parametrize("bad", ["", "sometimes", "RECORD "])
def test_invalid_fixture_mode_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="LLM_FIXTURE_MODE"):
        Settings(_env_file=None, LLM_FIXTURE_MODE=bad)


def test_invalid_checkpoint_backend_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LANGGRAPH_CHECKPOINT_BACKEND"):
        Settings(_env_file=None, LANGGRAPH_CHECKPOINT_BACKEND="redis")


def test_out_of_range_numeric_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_TEMPERATURE=5.0)


# --- secret safety -------------------------------------------------------


def test_safe_dump_redacts_every_secret() -> None:
    s = Settings(
        _env_file=None,
        REQPILOT_SECRET_KEY="super-secret-value",
        DATABASE_URL="postgresql+psycopg://u:hunter2@localhost/db",
        LLM_PROVIDER=LLMProvider.ANTHROPIC,
        LLM_API_KEY="key-abc123",
    )
    dumped = s.safe_dump()

    assert dumped["secret_key"] == "***redacted***"
    assert dumped["llm_api_key"] == "***redacted***"
    # The database URL counts as a secret because it carries a password.
    assert dumped["database_url"] == "***redacted***"

    blob = str(dumped)
    for secret in ("super-secret-value", "hunter2", "key-abc123"):
        assert secret not in blob


def test_repr_does_not_leak_secrets() -> None:
    """``repr`` is what ends up in tracebacks and log lines."""
    s = Settings(_env_file=None, REQPILOT_SECRET_KEY="leak-me-if-broken")
    assert "leak-me-if-broken" not in repr(s)


def test_logging_settings_does_not_leak(caplog: pytest.LogCaptureFixture) -> None:
    s = Settings(_env_file=None, REQPILOT_SECRET_KEY="must-not-appear")
    with caplog.at_level(logging.INFO):
        logging.getLogger("reqpilot.test").info("settings=%s", s.safe_dump())
    assert "must-not-appear" not in caplog.text

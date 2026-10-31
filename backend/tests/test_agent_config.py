"""Environment settings are validated without printing sensitive values."""

import pytest

from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError


def test_configuration_defaults_and_redaction(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-token")
    monkeypatch.setenv("OPENROUTER_MODEL", " fixture/model ")
    settings = AgentSettings.from_environment()
    assert settings.model == "fixture/model"
    assert settings.max_iterations == 8
    assert settings.timeout_seconds == 30
    assert settings.base_url == "https://openrouter.ai/api/v1"
    assert "private-test-token" not in repr(settings)
    assert "private-test-token" not in settings.model_dump_json()


def test_configuration_reads_all_optional_settings(monkeypatch):
    values = {
        "OPENROUTER_API_KEY": "fixture-token", "OPENROUTER_MODEL": "fixture/model",
        "OPENROUTER_BASE_URL": "https://example.test/api/v1/", "OPENROUTER_TIMEOUT_SECONDS": "7.5",
        "OPENROUTER_TEMPERATURE": "0.4", "OPENROUTER_MAX_TOKENS": "1200",
        "AGENT_MAX_ITERATIONS": "4", "AGENT_MAX_TOOL_CALLS_PER_ITERATION": "2",
        "AGENT_TOOL_TIMEOUT_SECONDS": "3", "AGENT_RUN_TIMEOUT_SECONDS": "20",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = AgentSettings.from_environment()
    assert settings.base_url == "https://example.test/api/v1"
    assert settings.timeout_seconds == 7.5
    assert settings.temperature == 0.4
    assert settings.max_tokens == 1200
    assert settings.max_iterations == 4
    assert settings.max_tool_calls_per_iteration == 2
    assert settings.tool_timeout_seconds == 3
    assert settings.run_timeout_seconds == 20


@pytest.mark.parametrize(("name", "value"), [
    ("OPENROUTER_API_KEY", ""), ("OPENROUTER_API_KEY", "two words"),
    ("OPENROUTER_API_KEY", "invalid-\u00e7-token"), ("OPENROUTER_API_KEY", "invalid-\x01-token"),
    ("OPENROUTER_MODEL", " "), ("OPENROUTER_TIMEOUT_SECONDS", "0"),
    ("OPENROUTER_TIMEOUT_SECONDS", "nan"), ("OPENROUTER_TEMPERATURE", "inf"),
    ("OPENROUTER_TEMPERATURE", "3"), ("OPENROUTER_MAX_TOKENS", "0"),
    ("AGENT_MAX_ITERATIONS", "0"), ("AGENT_MAX_ITERATIONS", "33"),
    ("AGENT_MAX_TOOL_CALLS_PER_ITERATION", "-1"), ("AGENT_TOOL_TIMEOUT_SECONDS", "-2"),
    ("AGENT_RUN_TIMEOUT_SECONDS", "not-a-number"),
    ("OPENROUTER_BASE_URL", "http://example.test"),
    ("OPENROUTER_BASE_URL", "https://private-token@example.test"),
    ("OPENROUTER_BASE_URL", "https://example.test?token=private-token"),
    ("OPENROUTER_BASE_URL", "https://example.test/#private-token"),
])
def test_invalid_settings_are_safe(monkeypatch, name, value):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-token")
    monkeypatch.setenv("OPENROUTER_MODEL", "fixture/model")
    monkeypatch.setenv(name, value)
    with pytest.raises(AgentError) as caught:
        AgentSettings.from_environment()
    assert caught.value.code == "invalid_configuration"
    assert "private" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_no_credentials_required_until_agent_is_used():
    with pytest.raises(AgentError, match="configuration"):
        AgentSettings.from_environment()

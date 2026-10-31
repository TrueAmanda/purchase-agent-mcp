"""The manual entry point does not require database initialization."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_cli import run
from app.core.agent_errors import AgentError
from app.schemas.agent import AgentResult

pytestmark = pytest.mark.anyio


async def test_cli_missing_key_returns_safe_error(capsys):
    assert await run("Hello") == 1
    assert "invalid_configuration" in capsys.readouterr().out


async def test_cli_success_without_storage(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "fixture/model")
    result = AgentResult(content="Fixture answer", iterations=1, tool_calls=0)
    with patch("app.agent_cli.AgentService.run", new=AsyncMock(return_value=result)):
        assert await run("Hello") == 0
    output = capsys.readouterr().out
    assert "Fixture answer" in output
    assert "fixture-key" not in output


async def test_cli_provider_error(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "fixture/model")
    with patch("app.agent_cli.AgentService.run", new=AsyncMock(side_effect=AgentError("llm_timeout"))):
        assert await run("Hello") == 1
    assert "llm_timeout" in capsys.readouterr().out

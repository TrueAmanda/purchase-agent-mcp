"""Bound paid executions before provider I/O; always release admission slots."""

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.dependencies import get_agent
from app.schemas.purchase_requests import PurchaseMessageInput

pytestmark = pytest.mark.anyio


async def test_agent_admission_is_bounded_and_released_even_after_cancellation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "fixture/model")
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_RUNS", "1")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={})
    first = get_agent(PurchaseMessageInput(message="Hello"), request)
    await anext(first)
    assert request.app.state.active_agents == 1
    second = get_agent(PurchaseMessageInput(message="Hello"), request)
    with pytest.raises(HTTPException) as error:
        await anext(second)
    assert error.value.status_code == 503 and error.value.detail["code"] == "agent_busy"
    await first.aclose()
    assert request.app.state.active_agents == 0
    third = get_agent(PurchaseMessageInput(message="Hello"), request)
    await anext(third)
    await third.aclose()
    assert request.app.state.active_agents == 0


async def test_invalid_intent_header_never_reaches_provider(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "fixture/model")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), headers={"Idempotency-Key": "invalid"})
    generator = get_agent(PurchaseMessageInput(message="Hello"), request)
    with pytest.raises(HTTPException) as error:
        await anext(generator)
    assert error.value.status_code == 422
    assert not hasattr(request.app.state, "active_agents")

"""HTTP endpoints with the real agent/MCP adapters and simulated provider HTTP."""

from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI, Request
import httpx
import pytest

from app.api.dependencies import get_agent
from app.api.router import api_router
from app.services.agent import AgentService
from app.services.mcp_tools import MCPToolProvider
from app.services.openrouter import OpenRouterClient


@pytest.fixture
async def flow(bridge, harness, llm_http):
    application = FastAPI()
    application.include_router(api_router)
    application.state.session_factory = bridge.sessions

    def agent(request: Request):
        # Like production, each HTTP request gets a fresh MCP adapter.
        key = request.headers.get("Idempotency-Key")
        provider = MCPToolProvider(bridge.provider.settings, bridge.provider.http_client, intent_key=UUID(key) if key else None)
        return AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, provider)

    application.dependency_overrides[get_agent] = agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        yield SimpleNamespace(http=client, app=application)

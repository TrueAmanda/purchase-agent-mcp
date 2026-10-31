"""Offline agent fixtures: no LLM credentials, real tools or database are used."""

from copy import deepcopy
import inspect
import json
import os

import httpx
import pytest

from app.core.agent_config import AgentSettings
from app.schemas.agent import ToolDefinition
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def offline_agent_environment(monkeypatch):
    for name in os.environ:
        if name.startswith(("OPENROUTER_", "AGENT_")):
            monkeypatch.delenv(name, raising=False)

    async def reject_async(*args, **kwargs):
        raise AssertionError("Unmocked HTTP is forbidden in backend tests.")

    def reject_sync(*args, **kwargs):
        raise AssertionError("Unmocked HTTP is forbidden in backend tests.")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_sync)


class FakeTools:
    def __init__(self):
        self.definitions = [
            ToolDefinition(name="buscar_produtos", description="Fixture search", parameters={
                "type": "object", "properties": {"termo": {"type": "string", "minLength": 1}},
                "required": ["termo"], "additionalProperties": False,
            }),
            ToolDefinition(name="detalhar_produto", description="Fixture details", parameters={
                "type": "object", "properties": {"id": {"type": "integer", "minimum": 1}},
                "required": ["id"], "additionalProperties": False,
            }),
            ToolDefinition(name="criar_requisicao", description="Fixture write, no persistence", parameters={
                "type": "object", "properties": {"request": {
                    "type": "object", "properties": {
                        "items": {"type": "array", "minItems": 1, "items": {
                            "type": "object", "properties": {
                                "product_id": {"type": "integer", "minimum": 1},
                                "quantity": {"type": "integer", "minimum": 1},
                            }, "required": ["product_id", "quantity"], "additionalProperties": False,
                        }},
                    }, "required": ["items"], "additionalProperties": False,
                }}, "required": ["request"], "additionalProperties": False,
            }),
        ]
        self.calls = []
        self.failure = None
        self.result = {"product": {"id": 1, "name": "Fixture only", "price": "19.99"}}

    async def list_tools(self):
        return deepcopy(self.definitions)

    async def call_tool(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if self.failure is not None:
            raise self.failure
        return deepcopy(self.result)


class AgentHarness:
    def __init__(self):
        self.responses = []
        self.requests = []
        self.tools = FakeTools()
        self.settings = AgentSettings(api_key="test-only-not-a-real-key", model="fixture/model")

    async def handle(self, request):
        assert request.method == "POST"
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-not-a-real-key"
        self.requests.append(json.loads(request.content))
        assert self.responses, "Unexpected LLM call"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(request)
            if inspect.isawaitable(response):
                response = await response
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=deepcopy(response))

    def agent(self, client, **overrides):
        settings = AgentSettings(**(self.settings.model_dump() | overrides))
        return AgentService(OpenRouterClient(settings, client), settings, self.tools)


@pytest.fixture
def harness():
    return AgentHarness()


@pytest.fixture
async def llm_http(harness):
    async with httpx.AsyncClient(transport=httpx.MockTransport(harness.handle), trust_env=False) as client:
        yield client

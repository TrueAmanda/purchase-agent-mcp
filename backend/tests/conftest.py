"""Offline LLM fixtures and real MCP protocol over isolated SQL storage."""

from copy import deepcopy
import inspect
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.agent_config import AgentSettings
from app.schemas.agent import ToolDefinition
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient
from app.core.mcp_config import MCPSettings
from app.services.mcp_tools import MCPToolProvider
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def offline_agent_environment(monkeypatch):
    for name in os.environ:
        if name.startswith(("OPENROUTER_", "AGENT_", "CONVERSATION_")):
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


@pytest.fixture
async def bridge(isolated_db_engine, monkeypatch):
    engine = isolated_db_engine
    sessions = sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(calls=[], failure=None, empty=False, path_failures={},
                            mcp_calls=[], mcp_failure=None, mcp_failure_method="tools/call")
    products = {
        1: {"id": 1, "title": "Fixture laptop", "price": 19.99, "description": "Fixture laptop only",
            "category": "laptops", "stock": 20, "minimumOrderQuantity": 1},
        2: {"id": 2, "title": "Fixture keyboard", "price": 0.1, "description": "Fixture keyboard only"},
    }

    def catalog(request):
        assert request.url.host == "dummyjson.com"
        state.calls.append(request.url.path)
        failure = state.path_failures.get(request.url.path, state.failure)
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return httpx.Response(failure, text="private-upstream-content")
        if request.url.path == "/products/search":
            items = [] if state.empty else list(products.values())
            return httpx.Response(200, json={"products": items, "total": len(items), "skip": 0, "limit": 30})
        if request.url.path == "/products/category-list":
            return httpx.Response(200, json=[])
        product = products.get(int(request.url.path.rsplit("/", 1)[1]))
        return httpx.Response(200, json=product) if product else httpx.Response(404)

    monkeypatch.setattr("purchase_mcp.server.create_http_client", lambda timeout: httpx.AsyncClient(
        base_url="https://dummyjson.com", transport=httpx.MockTransport(catalog), trust_env=False,
    ))
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8001")
    repository = PurchaseRequestRepository(sessions)
    app = create_server(repository).streamable_http_app()
    asgi_transport = httpx.ASGITransport(app=app)

    async def mcp_transport(request):
        payload = json.loads(request.content) if request.method == "POST" and request.content else {}
        if payload.get("method") == "tools/call":
            state.mcp_calls.append(deepcopy(payload["params"]))
        if payload.get("method") == state.mcp_failure_method and state.mcp_failure is not None:
            if isinstance(state.mcp_failure, Exception):
                raise state.mcp_failure
            return httpx.Response(state.mcp_failure, text="private-mcp-failure")
        return await asgi_transport.handle_async_request(request)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(mcp_transport), trust_env=False) as http:
            provider = MCPToolProvider(MCPSettings(server_url="http://127.0.0.1:8001/mcp"), http)
            yield SimpleNamespace(provider=provider, sessions=sessions, state=state, app=app,
                                  engine=engine, repository=repository, products=products)


@pytest.fixture
def purchase_args():
    return {"request": {
        "title": "Integration test", "requester_name": "Fixture requester",
        "idempotency_key": "11111111-1111-4111-8111-111111111111",
        "items": [{"product_id": 1, "quantity": 3}, {"product_id": 2, "quantity": 3}],
    }}

"""Backend -> official MCP protocol -> existing tools, with isolated storage/HTTP."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI
import httpx
from mcp.types import CallToolResult, TextContent
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import get_agent
from app.api.router import api_router
from app.core.agent_errors import AgentError, ToolExecutionError
from app.core.mcp_config import MCPSettings
from app.services.agent import AgentService
from app.services.mcp_tools import MCPToolProvider
from app.services.openrouter import OpenRouterClient
from app.services.purchase_requests import PurchaseRequestService
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.server import create_server
from purchase_storage import Base, PurchaseRequest, PurchaseRequestItem
from agent_fixtures import completion, tool_call

pytestmark = pytest.mark.anyio


@pytest.fixture
async def bridge(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'bridge.sqlite').as_posix()}",
                           connect_args={"check_same_thread": False}, hide_parameters=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(calls=[], failure=None, empty=False)
    products = {
        1: {"id": 1, "title": "Fixture laptop", "price": 19.99, "description": "Fixture laptop only",
            "category": "laptops", "stock": 20, "minimumOrderQuantity": 1},
        2: {"id": 2, "title": "Fixture keyboard", "price": 0.1, "description": "Fixture keyboard only"},
    }

    def catalog(request):
        assert request.url.host == "dummyjson.com"
        state.calls.append(request.url.path)
        if isinstance(state.failure, Exception):
            raise state.failure
        if state.failure is not None:
            return httpx.Response(state.failure, text="private-upstream-content")
        if request.url.path == "/products/search":
            items = [] if state.empty else list(products.values())
            return httpx.Response(200, json={"products": items, "total": len(items), "skip": 0, "limit": 30})
        product = products.get(int(request.url.path.rsplit("/", 1)[1]))
        return httpx.Response(200, json=product) if product else httpx.Response(404)

    monkeypatch.setattr("purchase_mcp.server.create_http_client", lambda timeout: httpx.AsyncClient(
        base_url="https://dummyjson.com", transport=httpx.MockTransport(catalog), trust_env=False,
    ))
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8001")
    app = create_server(PurchaseRequestRepository(sessions)).streamable_http_app()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), trust_env=False) as http:
                provider = MCPToolProvider(MCPSettings(server_url="http://127.0.0.1:8001/mcp"), http)
                yield SimpleNamespace(provider=provider, sessions=sessions, state=state, app=app)
    finally:
        engine.dispose()


@pytest.fixture
def purchase_args():
    return {"request": {
        "title": "Integration test", "requester_name": "Fixture requester",
        "idempotency_key": "11111111-1111-4111-8111-111111111111",
        "items": [{"product_id": 1, "quantity": 3}, {"product_id": 2, "quantity": 3}],
    }}


def counts(bridge):
    with bridge.sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (PurchaseRequest, PurchaseRequestItem))


async def test_discovers_schemas_and_returns_real_structured_results(bridge, purchase_args):
    tools = await bridge.provider.list_tools()
    assert {tool.name for tool in tools} == {"buscar_produtos", "detalhar_produto", "criar_requisicao"}
    suggested = bridge.provider._suggested_write_key
    assert str(UUID(suggested)) == suggested
    assert suggested in next(tool.description for tool in tools if tool.name == "criar_requisicao")
    schemas = {tool.name: tool.as_openrouter_tool()["function"]["parameters"] for tool in tools}
    assert schemas["criar_requisicao"]["$defs"]["PurchaseItemInput"]["additionalProperties"] is False
    search = await bridge.provider.call_tool("buscar_produtos", {"termo": "laptop"})
    assert search["products"][0]["price"] == "19.99"
    assert search["products"][0]["currency"] == "USD"
    detail = await bridge.provider.call_tool("detalhar_produto", {"id": 1})
    assert detail["product"] == search["products"][0]
    created = await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert created["total"] == "60.27" and counts(bridge) == (1, 2)
    assert created["currency"] == "USD"
    replay = await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert replay["replayed"] is True and replay["request_number"] == created["request_number"]
    assert counts(bridge) == (1, 2)


@pytest.mark.parametrize("case", ["price", "quantity", "empty", "uuid", "id"])
async def test_invalid_arguments_rejected_before_mcp_execution(bridge, purchase_args, case):
    await bridge.provider.list_tools()
    if case == "price":
        purchase_args["request"]["items"][0]["unit_price"] = "0.01"
    elif case == "quantity":
        purchase_args["request"]["items"][0]["quantity"] = 0
    elif case == "empty":
        purchase_args["request"]["items"] = []
    elif case == "uuid":
        purchase_args["request"]["idempotency_key"] = "not-a-uuid"
    else:
        purchase_args["request"]["items"][0]["product_id"] = "1"
    with pytest.raises(ToolExecutionError) as caught:
        await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert caught.value.code == "invalid_tool_arguments"
    assert bridge.state.calls == [] and counts(bridge) == (0, 0)


@pytest.mark.parametrize("failure,code,status", [
    (503, "api_http_error", 502),
    (httpx.ConnectError("private-details"), "api_unavailable", 503),
    (httpx.ReadTimeout("private-details"), "api_timeout", 504),
])
async def test_external_failures_reach_llm_and_safe_http_errors(
    bridge, harness, llm_http, purchase_args, failure, code, status,
):
    bridge.state.failure = failure
    harness.responses = [completion(None, [tool_call("criar_requisicao", purchase_args)]), completion("API unavailable.")]
    agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, bridge.provider)
    app = FastAPI()
    app.include_router(api_router)
    app.state.session_factory = bridge.sessions
    app.dependency_overrides[get_agent] = lambda: agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/requisicoes", json={"message": "Create fixture purchase"})
    assert response.status_code == status and response.json()["detail"]["code"] == code
    history = harness.requests[1]["messages"]
    tool_result = json.loads(history[-1]["content"])
    assert tool_result["error"]["code"] == code
    assert "private" not in json.dumps(history) and "private" not in response.text
    assert counts(bridge) == (0, 0)


async def test_nonexistent_product_prevents_partial_creation(bridge, purchase_args):
    await bridge.provider.list_tools()
    purchase_args["request"]["items"].append({"product_id": 999999, "quantity": 1})
    result = await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert result["error"]["code"] == "product_not_found"
    assert counts(bridge) == (0, 0)


@pytest.mark.parametrize("sequence", [
    ["criar_requisicao"],
    ["buscar_produtos", "detalhar_produto", "criar_requisicao"],
    ["detalhar_produto", "buscar_produtos", "criar_requisicao"],
])
async def test_model_selects_order_and_post_confirms_actual_rows(bridge, harness, llm_http, purchase_args, sequence):
    args = {"buscar_produtos": {"termo": "laptop"}, "detalhar_produto": {"id": 1}, "criar_requisicao": purchase_args}
    harness.responses = [completion(None, [tool_call(name, args[name], f"call-{index}")])
                         for index, name in enumerate(sequence)] + [completion("Confirmed fixture purchase.")]
    agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, bridge.provider)
    app = FastAPI()
    app.include_router(api_router)
    app.state.session_factory = bridge.sessions
    app.dependency_overrides[get_agent] = lambda: agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/requisicoes", json={"message": "Create this purchase", "include_tool_trace": True})
        saved = await http.get("/requisicoes")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created" and body["requisitions"][0]["total"] == "60.27"
    assert saved.json()["requisitions"][0]["request_number"] == body["requisitions"][0]["request_number"]
    assert [event["name"] for event in body["tool_trace"]] == sequence
    assert all(event["duration_ms"] >= 0 for event in body["tool_trace"])
    assert body["tool_trace"][-1]["result"] == {"item_count": 2, "replayed": False}
    assert "Fixture requester" not in json.dumps(body["tool_trace"])
    assert "NO TOOLS ARE CONNECTED" not in harness.requests[0]["messages"][0]["content"]
    assert counts(bridge) == (1, 2)


async def test_multiple_calls_one_round_and_next_round_uses_results(bridge, harness, llm_http):
    harness.responses = [completion(None, [
        tool_call("buscar_produtos", {"termo": "laptop"}, "search"),
        tool_call("detalhar_produto", {"id": 1}, "details"),
    ]), completion("Catalog comparison only.")]
    agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, bridge.provider)
    result = await agent.run("Compare products without creating a requisition")
    assert result.tool_calls == 2 and result.iterations == 2
    history = harness.requests[1]["messages"]
    assert [message["tool_call_id"] for message in history if message["role"] == "tool"] == ["search", "details"]
    assert counts(bridge) == (0, 0)


async def test_empty_search_is_no_results_without_any_write(bridge, harness, llm_http):
    bridge.state.empty = True
    harness.responses = [completion(None, [tool_call("buscar_produtos", {"termo": "missing"})]),
                         completion("Nenhum produto encontrado.")]
    agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, bridge.provider)
    response, status = await PurchaseRequestService(bridge.sessions).submit("Find missing products", agent, True)
    assert status == 200 and response.status == "no_results"
    assert response.tool_trace[0].result == {"product_count": 0}
    assert counts(bridge) == (0, 0)


@pytest.mark.parametrize("error", [httpx.ConnectError("secret-url"), httpx.HTTPStatusError(
    "private-body", request=httpx.Request("POST", "http://test"), response=httpx.Response(503),
)])
async def test_mcp_unavailable_never_falls_back_to_empty_tools(error, harness, llm_http):
    def fail(request):
        raise error
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        provider = MCPToolProvider(MCPSettings(timeout_seconds=0.5), http)
        agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, provider)
        with pytest.raises(AgentError) as caught:
            await agent.run("Search")
    assert caught.value.code == "mcp_unavailable" and harness.requests == []
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


async def test_mcp_call_timeout_and_invalid_result_are_not_success(bridge, monkeypatch):
    await bridge.provider.list_tools()
    for result in [CallToolResult(content=[], structuredContent={"product": {"id": 1}}),
                   CallToolResult(content=[TextContent(type="text", text='{"fake": true}')])]:
        @asynccontextmanager
        async def session():
            yield SimpleNamespace(call_tool=AsyncMock(return_value=result))
        monkeypatch.setattr(bridge.provider, "_session", session)
        with pytest.raises(ToolExecutionError) as caught:
            await bridge.provider.call_tool("detalhar_produto", {"id": 1})
        assert caught.value.code == "invalid_tool_result"
    @asynccontextmanager
    async def timeout_session():
        raise ExceptionGroup("private", [httpx.ReadTimeout("private")])
        yield
    monkeypatch.setattr(bridge.provider, "_session", timeout_session)
    with pytest.raises(ToolExecutionError) as caught:
        await bridge.provider.call_tool("detalhar_produto", {"id": 1})
    assert caught.value.code == "tool_timeout"


@pytest.mark.parametrize("value", ["file:///tmp/mcp", "http://user:secret@localhost/mcp", "http://localhost/mcp?key=secret"])
async def test_invalid_mcp_url_is_sanitized(monkeypatch, value):
    monkeypatch.setenv("MCP_SERVER_URL", value)
    with pytest.raises(AgentError) as caught:
        MCPSettings.from_environment()
    assert caught.value.code == "invalid_mcp_configuration" and "secret" not in str(caught.value)


async def test_cancellation_closes_session_and_does_not_retry(bridge, monkeypatch):
    await bridge.provider.list_tools()
    closed = []
    calls = []
    async def wait(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(30)
    @asynccontextmanager
    async def session():
        try:
            yield SimpleNamespace(call_tool=wait)
        finally:
            closed.append(True)
    monkeypatch.setattr(bridge.provider, "_session", session)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await bridge.provider.call_tool("detalhar_produto", {"id": 1})
    assert closed == [True] and calls == [1]


async def test_conflicting_retry_rejected_without_extra_rows(bridge, purchase_args):
    await bridge.provider.list_tools()
    await bridge.provider.call_tool("criar_requisicao", purchase_args)
    changed = deepcopy(purchase_args)
    changed["request"]["items"][0]["quantity"] = 4
    result = await bridge.provider.call_tool("criar_requisicao", changed)
    assert result["error"]["code"] == "idempotency_conflict" and counts(bridge) == (1, 2)


async def test_same_intent_cannot_retry_with_new_key(bridge, purchase_args):
    await bridge.provider.list_tools()
    await bridge.provider.call_tool("criar_requisicao", purchase_args)
    changed = deepcopy(purchase_args)
    changed["request"]["idempotency_key"] = "22222222-2222-4222-8222-222222222222"
    result = await bridge.provider.call_tool("criar_requisicao", changed)
    assert result["error"]["code"] == "idempotency_conflict" and counts(bridge) == (1, 2)


async def test_candidates_do_not_trigger_automatic_creation(bridge, harness, llm_http):
    harness.responses = [completion(None, [tool_call("buscar_produtos", {"termo": "laptop"})]),
                         completion("No item satisfies the budget and category constraints.")]
    agent = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, bridge.provider)
    response, status = await PurchaseRequestService(bridge.sessions).submit("Buy only suitable items", agent, True)
    assert status == 200 and response.status == "response" and not response.requisitions
    assert counts(bridge) == (0, 0)


async def test_mcp_disconnect_during_call_is_not_empty_search(bridge, monkeypatch):
    await bridge.provider.list_tools()
    @asynccontextmanager
    async def disconnected():
        raise httpx.ConnectError("private")
        yield
    monkeypatch.setattr(bridge.provider, "_session", disconnected)
    with pytest.raises(ToolExecutionError) as caught:
        await bridge.provider.call_tool("buscar_produtos", {"termo": "laptop"})
    assert caught.value.code == "mcp_unavailable" and counts(bridge) == (0, 0)


async def test_timeout_keeps_original_write_key_without_automatic_retry(bridge, purchase_args, monkeypatch):
    await bridge.provider.list_tools()
    original = bridge.provider._session
    attempts = []
    @asynccontextmanager
    async def timed_out():
        attempts.append(1)
        raise TimeoutError
        yield
    monkeypatch.setattr(bridge.provider, "_session", timed_out)
    with pytest.raises(ToolExecutionError) as caught:
        await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert caught.value.code == "tool_timeout" and attempts == [1]
    changed = deepcopy(purchase_args)
    changed["request"]["idempotency_key"] = "22222222-2222-4222-8222-222222222222"
    error = await bridge.provider.call_tool("criar_requisicao", changed)
    assert error["error"]["code"] == "idempotency_conflict" and attempts == [1]
    monkeypatch.setattr(bridge.provider, "_session", original)
    confirmed = await bridge.provider.call_tool("criar_requisicao", purchase_args)
    assert confirmed["replayed"] is False and counts(bridge) == (1, 2)

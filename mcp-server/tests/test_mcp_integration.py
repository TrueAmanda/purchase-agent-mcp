"""Official SDK discovery and tool calls through the actual MCP protocol."""

from copy import deepcopy
from decimal import Decimal
import os
from uuid import UUID

import httpx
from jsonschema import validate
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from purchase_storage import PurchaseRequestItem

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_discovers_exact_public_contracts_without_llm_keys(mcp_connection):
    assert "OPENROUTER_API_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ
    async with mcp_connection() as session:
        discovered = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert set(discovered) == {"buscar_produtos", "detalhar_produto", "criar_requisicao"}
    for name, argument in (("buscar_produtos", "termo"), ("detalhar_produto", "id"), ("criar_requisicao", "request")):
        tool = discovered[name]
        assert tool.inputSchema["required"] == [argument]
        assert set(tool.inputSchema["properties"]) == {argument}
        assert tool.outputSchema
    product_id = discovered["detalhar_produto"].inputSchema["properties"]["id"]
    assert product_id["type"] == "integer" and product_id["exclusiveMinimum"] == 0
    creation = discovered["criar_requisicao"]
    assert creation.annotations.readOnlyHint is False
    assert creation.annotations.idempotentHint is True
    contract = creation.inputSchema["$defs"]["PurchaseRequestInput"]
    assert contract["additionalProperties"] is False
    assert contract["properties"]["items"]["minItems"] == 1
    assert "idempotency_key" in contract["required"]
    item = creation.inputSchema["$defs"]["PurchaseItemInput"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {"product_id", "quantity"}
    assert item["properties"]["quantity"]["exclusiveMinimum"] == 0


async def test_all_tools_over_tcp_validate_input_output_and_persist(
    mcp_tcp_session, request_payload, catalog, db_counts
):
    session = mcp_tcp_session
    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    cases = [
        ("buscar_produtos", {"termo": "phone"}),
        ("detalhar_produto", {"id": 1}),
        ("criar_requisicao", {"request": request_payload}),
    ]
    outputs = {}
    for name, arguments in cases:
        validate(arguments, tools[name].inputSchema)
        result = await session.call_tool(name, arguments)
        assert not result.isError, result.model_dump_json()
        assert result.content
        validate(result.structuredContent, tools[name].outputSchema)
        outputs[name] = result.structuredContent
    assert [p["id"] for p in outputs["buscar_produtos"]["products"]] == [1, 2]
    assert outputs["detalhar_produto"]["product"]["name"] == catalog.products[1]["title"]
    created = outputs["criar_requisicao"]
    assert UUID(created["request_number"])
    assert created["replayed"] is False and created["currency"] is None
    assert type(created["total"]) is str and Decimal(created["total"]) == Decimal("59.97")
    assert created["items"][0]["quantity"] == 3
    assert type(created["items"][0]["unit_price"]) is str
    assert db_counts() == (1, 1)
    assert len(catalog.calls) == 3


@pytest.mark.parametrize("name,arguments", [
    ("buscar_produtos", {}), ("buscar_produtos", {"termo": "   "}),
    ("detalhar_produto", {}), ("detalhar_produto", {"id": 0}),
    ("detalhar_produto", {"id": -1}), ("detalhar_produto", {"id": True}),
    ("detalhar_produto", {"id": "1"}), ("detalhar_produto", {"id": 1.0}),
])
async def test_invalid_catalog_arguments_fail_before_http(mcp_connection, catalog, db_counts, name, arguments):
    async with mcp_connection() as session:
        result = await session.call_tool(name, arguments)
    assert result.isError and result.structuredContent is None
    assert catalog.calls == []
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("case", ["empty_items", "zero_quantity", "negative_quantity", "price", "total", "missing_key"])
async def test_creation_public_contract_rejects_invalid_data(
    mcp_connection, request_payload, catalog, db_counts, case
):
    if case == "empty_items":
        request_payload["items"] = []
    elif case in {"zero_quantity", "negative_quantity"}:
        request_payload["items"][0]["quantity"] = 0 if case == "zero_quantity" else -1
    elif case == "price":
        request_payload["items"][0]["unit_price"] = "0.01"
    elif case == "total":
        request_payload["total"] = "0.01"
    else:
        del request_payload["idempotency_key"]
    async with mcp_connection() as session:
        result = await session.call_tool("criar_requisicao", {"request": request_payload})
    assert result.isError and result.structuredContent is None
    assert catalog.calls == []
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("name,path,arguments", [
    ("buscar_produtos", "/products/search", {"termo": "phone"}),
    ("detalhar_produto", "/products/1", {"id": 1}),
    ("criar_requisicao", "/products/1", None),
])
@pytest.mark.parametrize("failure,code", [
    (httpx.Response(503, text="private upstream body"), "api_http_error"),
    (httpx.ReadTimeout("private upstream details"), "api_timeout"),
])
async def test_upstream_failures_are_mcp_tool_errors(
    mcp_connection, catalog, request_payload, db_counts, name, path, arguments, failure, code
):
    catalog.overrides[path] = failure
    async with mcp_connection() as session:
        result = await session.call_tool(name, arguments or {"request": request_payload})
    assert result.isError and result.structuredContent is None
    assert code in result.content[0].text
    if isinstance(failure, httpx.Response):
        assert "503" in result.content[0].text
    assert "private upstream" not in result.content[0].text
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("name", ["detalhar_produto", "criar_requisicao"])
async def test_nonexistent_product_is_404_without_fallback_or_partial_write(
    mcp_connection, request_payload, db_counts, name
):
    request_payload["items"].append({"product_id": 999999, "quantity": 1})
    arguments = {"id": 999999} if name == "detalhar_produto" else {"request": request_payload}
    async with mcp_connection() as session:
        result = await session.call_tool(name, arguments)
    assert result.isError and result.structuredContent is None
    assert "product_not_found" in result.content[0].text and "404" in result.content[0].text
    assert db_counts() == (0, 0)


async def test_no_results_is_not_an_mcp_error(mcp_connection, catalog):
    catalog.search = {"products": [], "total": 0, "skip": 0, "limit": 0}
    async with mcp_connection() as session:
        result = await session.call_tool("buscar_produtos", {"termo": "not-found"})
    assert not result.isError
    assert result.structuredContent["status"] == "no_results"
    assert result.structuredContent["products"] == []


async def test_creation_can_be_called_directly_then_retried_and_conflicted(
    mcp_connection, request_payload, db_counts, catalog
):
    async with mcp_connection() as session:
        first = await session.call_tool("criar_requisicao", {"request": request_payload})
        assert not first.isError
        catalog.overrides["/products/1"] = httpx.ReadTimeout("API unavailable after first call")
        replay = await session.call_tool("criar_requisicao", {"request": request_payload})
        assert not replay.isError and replay.structuredContent["replayed"]
        assert replay.structuredContent["request_number"] == first.structuredContent["request_number"]
        changed = deepcopy(request_payload)
        changed["items"][0]["quantity"] = 4
        conflict = await session.call_tool("criar_requisicao", {"request": changed})
        assert conflict.isError and "idempotency_conflict" in conflict.content[0].text
    assert len(catalog.calls) == 1
    assert db_counts() == (1, 1)


async def test_database_errors_are_safe_mcp_errors(mcp_connection, repository, request_payload, monkeypatch):
    from purchase_mcp.services.purchase_errors import PurchaseRequestError

    def unavailable(*args):
        raise PurchaseRequestError("database_unavailable", "Unable to retrieve request.")

    monkeypatch.setattr(repository, "find_existing", unavailable)
    async with mcp_connection() as session:
        result = await session.call_tool("criar_requisicao", {"request": request_payload})
    assert result.isError and "database_unavailable" in result.content[0].text


async def test_real_constraint_failure_over_mcp_rolls_back_everything(mcp_connection, request_payload, db_counts):
    request_payload["items"].append({"product_id": 2, "quantity": 1})

    def invalid_second_item(mapper, connection, item):
        if item.product_id == "2":
            item.quantity = 0

    event.listen(PurchaseRequestItem, "before_insert", invalid_second_item)
    try:
        async with mcp_connection() as session:
            result = await session.call_tool("criar_requisicao", {"request": request_payload})
    finally:
        event.remove(PurchaseRequestItem, "before_insert", invalid_second_item)
    assert result.isError and result.structuredContent is None
    assert "database_error" in result.content[0].text
    assert request_payload["requester_name"] not in result.content[0].text
    assert "INSERT" not in result.content[0].text
    assert db_counts() == (0, 0)


async def test_health_checks_database_without_calling_dummyjson(mcp_app_factory, catalog, repository, monkeypatch):
    app = mcp_app_factory()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001") as http:
            healthy = await http.get("/health")
            assert healthy.status_code == 200 and healthy.json() == {"status": "ok"}

            def unavailable():
                raise OperationalError("SELECT 1", {}, Exception("private credentials"))

            monkeypatch.setattr(repository, "check_connection", unavailable)
            unhealthy = await http.get("/health")
            assert unhealthy.status_code == 503
            assert "private" not in unhealthy.text
    assert catalog.calls == []

"""POST -> model-driven loop -> official MCP protocol -> SQL -> GET, offline."""

from copy import deepcopy
from decimal import Decimal
import json
import os
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from agent_fixtures import completion, tool_call
from purchase_storage import PurchaseRequest, PurchaseRequestItem

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def counts(bridge):
    with bridge.sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (PurchaseRequest, PurchaseRequestItem))


def program(harness, purchase_args, sequence, final="Fixture purchase conclusion."):
    arguments = {"buscar_produtos": {"termo": "laptop"}, "detalhar_produto": {"id": 1},
                 "criar_requisicao": purchase_args}
    harness.responses = [completion(None, [tool_call(name, arguments[name], f"call-{index}")])
                         for index, name in enumerate(sequence)] + [completion(final)]


async def post(flow, message="Create the fixture purchase"):
    return await flow.http.post("/requisicoes", json={"message": message, "include_tool_trace": True})


def assert_trace(body, names, statuses=None):
    trace = body["tool_trace"]
    assert [entry["name"] for entry in trace] == names
    assert [entry["order"] for entry in trace] == list(range(1, len(names) + 1))
    assert [entry["status"] for entry in trace] == (statuses or ["success"] * len(names))
    assert all(entry["duration_ms"] >= 0 for entry in trace)
    serialized = json.dumps(trace)
    assert all(value not in serialized for value in ("arguments", "Fixture requester", "idempotency_key", "private"))


@pytest.mark.parametrize("sequence", [
    ["criar_requisicao"],
    ["buscar_produtos", "detalhar_produto", "criar_requisicao"],
    ["detalhar_produto", "buscar_produtos", "criar_requisicao"],
    ["buscar_produtos", "buscar_produtos", "criar_requisicao"],
])
async def test_model_selected_sequences_persist_exact_totals_and_get_returns_real_rows(
    flow, bridge, harness, purchase_args, sequence,
):
    assert not os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPENROUTER_MODEL")
    assert (await flow.http.get("/requisicoes")).json()["requisitions"] == []
    program(harness, purchase_args, sequence)
    response = await post(flow)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created" and len(body["requisitions"]) == 1
    record = body["requisitions"][0]
    UUID(record["request_number"])
    assert record["currency"] == "USD" and record["replayed"] is False
    assert {item["product_id"] for item in record["items"]} == {"1", "2"}
    assert all(type(item["unit_price"]) is str and type(item["subtotal"]) is str for item in record["items"])
    for item in record["items"]:
        upstream = bridge.products[int(item["product_id"])]
        assert item["name"] == upstream["title"] and item["quantity"] == 3
        assert Decimal(item["unit_price"]) == Decimal(str(upstream["price"]))
        assert Decimal(item["subtotal"]) == Decimal(item["unit_price"]) * item["quantity"]
    assert Decimal(record["total"]) == sum(Decimal(item["subtotal"]) for item in record["items"]) == Decimal("60.27")
    assert_trace(body, sequence)
    assert [call["name"] for call in bridge.state.mcp_calls] == sequence
    assert all(request["tool_choice"] == "auto" for request in harness.requests)
    advertised = {tool["function"]["name"] for tool in harness.requests[0]["tools"]}
    assert advertised == {"buscar_produtos", "detalhar_produto", "criar_requisicao"}
    replies = [entry for entry in harness.requests[-1]["messages"] if entry["role"] == "tool"]
    assert len(replies) == len(sequence)
    assert json.loads(replies[-1]["content"])["request_number"] == record["request_number"]
    saved = (await flow.http.get("/requisicoes")).json()["requisitions"]
    assert saved == [{key: value for key, value in record.items() if key != "replayed"}]
    with bridge.sessions() as session:
        stored = session.get(PurchaseRequest, record["id"])
        assert stored.request_number == record["request_number"]
        assert stored.total_amount == Decimal("60.27")
        assert all(item.purchase_request_id == stored.id for item in stored.items)
    assert counts(bridge) == (1, 2)


@pytest.mark.parametrize("empty", [True, False], ids=["no_catalog_matches", "no_suitable_candidates"])
async def test_unsuitable_requests_do_not_trigger_writes_or_fabricate_receipts(flow, bridge, harness, purchase_args, empty):
    bridge.state.empty = empty
    program(harness, purchase_args, ["buscar_produtos"], final="Não encontrei opções adequadas ao orçamento.")
    response = await post(flow, "Pesquise opções dentro das minhas restrições")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ("no_results" if empty else "response") and body["requisitions"] == []
    assert_trace(body, ["buscar_produtos"])
    assert [call["name"] for call in bridge.state.mcp_calls] == ["buscar_produtos"]
    assert counts(bridge) == (0, 0)
    assert (await flow.http.get("/requisicoes")).json()["requisitions"] == []


@pytest.mark.parametrize("payload", [{}, {"message": " "}, {"message": None}, {"message": 3},
                                     {"message": "Buy", "unit_price": "0.01"}, {"pedido": "Buy"}])
async def test_invalid_requests_never_reach_llm_mcp_or_storage(flow, bridge, harness, payload):
    response = await flow.http.post("/requisicoes", json=payload)
    assert response.status_code == 422
    assert harness.requests == [] and bridge.state.mcp_calls == [] and counts(bridge) == (0, 0)


@pytest.mark.parametrize("phase", ["search", "second_product_validation"])
@pytest.mark.parametrize("failure,status,code", [
    (httpx.ReadTimeout("private-details"), 504, "api_timeout"),
    (httpx.ConnectError("private-details"), 503, "api_unavailable"),
    (503, 502, "api_http_error"),
])
async def test_dummyjson_failures_are_not_empty_catalogs_and_never_save_partial_requests(
    flow, bridge, harness, purchase_args, phase, failure, status, code,
):
    sequence = ["buscar_produtos"] if phase == "search" else ["buscar_produtos", "criar_requisicao"]
    path = "/products/search" if phase == "search" else "/products/2"
    bridge.state.path_failures[path] = failure
    # Even a misleading LLM narrative cannot replace the executor's error evidence.
    program(harness, purchase_args, sequence, final="Nenhum produto encontrado.")
    response = await post(flow)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    body = response.json()
    assert "requisitions" not in body and "private" not in response.text
    assert_trace(body, sequence, ["success"] * (len(sequence) - 1) + ["timeout" if code == "api_timeout" else "error"])
    assert body["tool_trace"][-1]["code"] == code
    assert [call["name"] for call in bridge.state.mcp_calls] == sequence
    assert counts(bridge) == (0, 0)
    assert (await flow.http.get("/requisicoes")).json()["requisitions"] == []


@pytest.mark.parametrize("failure,status,code", [
    (503, 503, "mcp_unavailable"),
    (httpx.ConnectError("private-mcp-address"), 503, "mcp_unavailable"),
    (httpx.ReadTimeout("private-mcp-address"), 504, "tool_timeout"),
])
async def test_mcp_call_failures_are_safe_and_traced(flow, bridge, harness, purchase_args, failure, status, code):
    bridge.state.mcp_failure = failure
    program(harness, purchase_args, ["buscar_produtos"], final="Fixture service error.")
    response = await post(flow)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert_trace(response.json(), ["buscar_produtos"], ["timeout" if status == 504 else "error"])
    assert "private" not in response.text and bridge.state.calls == [] and counts(bridge) == (0, 0)


async def test_discovery_failure_returns_before_the_llm_and_does_not_advertise_fake_tools(flow, bridge, harness):
    bridge.state.mcp_failure = 503
    bridge.state.mcp_failure_method = "initialize"
    response = await post(flow)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "mcp_unavailable"
    assert response.json()["tool_trace"] == []
    assert harness.requests == [] and bridge.state.mcp_calls == [] and counts(bridge) == (0, 0)


@pytest.mark.parametrize("failure,status,code", [
    (httpx.Response(503, text="private-provider-body"), 503, "llm_unavailable"),
    (httpx.Response(401, text="private-provider-body"), 503, "llm_authentication"),
    (httpx.Response(429, text="private-provider-body"), 503, "llm_rate_limit"),
    (httpx.ReadTimeout("private-provider-address"), 504, "llm_timeout"),
    (httpx.Response(200, text="not JSON"), 502, "invalid_llm_response"),
])
async def test_openrouter_http_failures_after_search_preserve_trace_without_writes(
    flow, bridge, harness, purchase_args, failure, status, code,
):
    program(harness, purchase_args, ["buscar_produtos"])
    harness.responses[-1] = failure
    response = await post(flow)
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert_trace(response.json(), ["buscar_produtos"])
    assert "private" not in response.text and counts(bridge) == (0, 0)


@pytest.mark.parametrize("phase", ["item_insert", "commit"])
async def test_database_failure_rolls_back_header_and_items_through_the_entire_stack(
    flow, bridge, harness, purchase_args, phase,
):
    observed = []

    def after_insert(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("INSERT"):
            observed.append(statement)
            if phase == "item_insert" and "purchase_request_items" in statement:
                raise SQLAlchemyError("private-item-insert-failure")

    def before_commit(connection):
        if phase == "commit":
            raise OperationalError("private-commit-failure", {}, Exception("private-database-host"))

    event.listen(bridge.engine, "after_cursor_execute", after_insert)
    event.listen(bridge.engine, "commit", before_commit)
    try:
        program(harness, purchase_args, ["criar_requisicao"])
        response = await post(flow)
    finally:
        event.remove(bridge.engine, "after_cursor_execute", after_insert)
        event.remove(bridge.engine, "commit", before_commit)
    assert any("purchase_requests" in statement for statement in observed)
    assert any("purchase_request_items" in statement for statement in observed)
    assert response.status_code == (502 if phase == "item_insert" else 503)
    assert response.json()["detail"]["code"] == ("database_error" if phase == "item_insert" else "database_unavailable")
    assert_trace(response.json(), ["criar_requisicao"], ["error"])
    assert "private" not in response.text and counts(bridge) == (0, 0)
    assert (await flow.http.get("/requisicoes")).json()["requisitions"] == []


async def test_provider_failure_after_commit_is_recoverable_by_get_and_same_key_replay(
    flow, bridge, harness, purchase_args,
):
    program(harness, purchase_args, ["criar_requisicao"])
    harness.responses[-1] = httpx.Response(503, text="private-provider-failure-after-write")
    response = await post(flow)
    assert response.status_code == 503 and "requisitions" not in response.json()
    assert_trace(response.json(), ["criar_requisicao"])
    assert counts(bridge) == (1, 2)
    saved = (await flow.http.get("/requisicoes")).json()["requisitions"]
    assert len(saved) == 1 and saved[0]["total"] == "60.27"
    # Persisted idempotency survives a new POST/provider and even catalog downtime.
    bridge.state.failure = httpx.ReadTimeout("private-catalog-failure")
    catalog_requests = list(bridge.state.calls)
    program(harness, purchase_args, ["criar_requisicao"])
    replay = await post(flow)
    assert replay.status_code == 200 and replay.json()["status"] == "created"
    assert replay.json()["requisitions"][0]["replayed"] is True
    assert replay.json()["requisitions"][0]["request_number"] == saved[0]["request_number"]
    assert bridge.state.calls == catalog_requests and counts(bridge) == (1, 2)


async def test_backend_must_confirm_storage_even_after_a_successful_real_mcp_write(
    flow, bridge, harness, purchase_args,
):
    program(harness, purchase_args, ["criar_requisicao"])

    def unavailable(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT") and "purchase_requests" in statement:
            raise OperationalError("private-confirmation-failure", {}, Exception("private"))

    def read_failure(request):
        assert counts(bridge) == (1, 2)
        event.listen(bridge.engine, "before_cursor_execute", unavailable)
        return completion("Creation was reported by the tool.")

    harness.responses[-1] = read_failure
    try:
        response = await post(flow)
    finally:
        event.remove(bridge.engine, "before_cursor_execute", unavailable)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "database_unavailable"
    assert "requisitions" not in response.json() and "private" not in response.text
    assert_trace(response.json(), ["criar_requisicao"])
    assert counts(bridge) == (1, 2)
    assert len((await flow.http.get("/requisicoes")).json()["requisitions"]) == 1


async def test_nonexistent_product_cannot_leave_a_partial_request(flow, bridge, harness, purchase_args):
    purchase_args["request"]["items"][1]["product_id"] = 999999
    program(harness, purchase_args, ["criar_requisicao"])
    response = await post(flow)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "product_not_found"
    assert_trace(response.json(), ["criar_requisicao"], ["error"])
    assert bridge.state.calls == ["/products/1", "/products/999999"]
    assert counts(bridge) == (0, 0)
    assert (await flow.http.get("/requisicoes")).json()["requisitions"] == []


async def test_invalid_llm_prices_are_rejected_before_mcp_and_never_persisted(flow, bridge, harness, purchase_args):
    tampered = deepcopy(purchase_args)
    tampered["request"]["items"][0]["unit_price"] = "0.01"
    harness.responses = [completion(None, [tool_call("criar_requisicao", tampered)]), completion("Invalid tool arguments.")]
    response = await post(flow)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_tool_arguments"
    assert response.json()["tool_trace"] == []
    assert bridge.state.mcp_calls == [] and counts(bridge) == (0, 0)

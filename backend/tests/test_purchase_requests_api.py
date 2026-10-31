"""Purchase HTTP contracts with a simulated agent and isolated SQLAlchemy storage."""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError as SQLTimeoutError
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import get_agent
from app.api.router import api_router
from app.core.agent_errors import AgentError
from app.schemas.agent import AgentResult, ToolExecutionEvent
from agent_fixtures import completion, tool_call
from purchase_storage import Base, PurchaseRequest, PurchaseRequestItem

pytestmark = pytest.mark.anyio


class FakeAgent:
    def __init__(self):
        self.messages = []
        self.result = AgentResult(content="Quais equipamentos você precisa?", iterations=1, tool_calls=0)
        self.failure = None

    async def run(self, message):
        self.messages.append(message)
        if self.failure:
            raise self.failure
        return self.result


@pytest.fixture
def api_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'purchases.sqlite3').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    application = FastAPI()
    application.include_router(api_router)
    application.state.session_factory = sessions
    agent = FakeAgent()
    application.dependency_overrides[get_agent] = lambda: agent
    yield SimpleNamespace(app=application, engine=engine, sessions=sessions, agent=agent)
    engine.dispose()


@pytest.fixture
async def api_client(api_context):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_context.app), base_url="http://testserver") as client:
        yield client


@pytest.fixture
def seed_purchase(api_context):
    def seed(number="11111111-1111-4111-8111-111111111111", title="Office", legacy=False):
        with api_context.sessions.begin() as session:
            record = PurchaseRequest(
                title=title, requester_name="Fixture requester", currency=None,
                request_number=number, total_amount=None if legacy else Decimal("59.97"),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                items=[PurchaseRequestItem(
                    product_id=None if legacy else "1", product_name=None if legacy else "Fixture keyboard",
                    description="Fixture description", quantity=3, unit_price=Decimal("19.99"),
                    subtotal=None if legacy else Decimal("59.97"),
                )],
            )
            session.add(record)
            session.flush()
            if legacy:
                record.request_number = None
        return record
    return seed


@contextmanager
def forbid_writes(engine):
    def guard(connection, cursor, statement, parameters, context, many):
        assert statement.lstrip().split()[0].upper() not in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}, "API attempted a database write"
    event.listen(engine, "before_cursor_execute", guard)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", guard)


def set_events(context, *events, message="Fixture answer"):
    context.agent.result = AgentResult(content=message, iterations=2, tool_calls=len(events), tool_events=list(events))


def creation(record, *, replayed=False, **overrides):
    return ToolExecutionEvent(name="criar_requisicao", outcome="ok", result={
        "request_id": record.id, "request_number": record.request_number, "replayed": replayed,
        "total": "0.01", "items": [{"quantity": 999, "unit_price": "0.01"}], **overrides,
    })


async def test_post_validates_and_forwards_message_without_writing(api_client, api_context):
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "  Preciso de equipamentos  "})
    assert response.status_code == 200
    assert api_context.agent.messages == ["Preciso de equipamentos"]
    assert response.json() == {
        "status": "response", "message": "Quais equipamentos você precisa?", "requisitions": [], "tool_trace": None,
    }
    with api_context.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 0


@pytest.mark.parametrize("payload", [
    {}, {"message": ""}, {"message": " \n\t"}, {"message": None}, {"message": 42},
    {"message": False}, {"message": []}, {"message": {}}, {"message": "x" * 20001},
    {"message": "Buy", "price": "0.01"}, {"message": "Buy", "include_tool_trace": "true"},
    [], None,
])
async def test_invalid_body_is_422_and_never_runs_agent(api_client, api_context, payload):
    response = await api_client.post("/requisicoes", json=payload)
    assert response.status_code == 422
    assert not api_context.agent.messages


async def test_malformed_json_is_422(api_client, api_context):
    response = await api_client.post("/requisicoes", content="{broken", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert not api_context.agent.messages


async def test_no_results_requires_a_successful_empty_search(api_client, api_context):
    set_events(api_context, ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={
        "status": "no_results", "products": [], "total": 0,
    }), message="Não foram encontrados produtos.")
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "Busque teclados"})
    assert response.status_code == 200
    assert response.json()["status"] == "no_results"
    assert response.json()["requisitions"] == []


@pytest.mark.parametrize("content", [
    "Não encontrei produtos adequados ao orçamento.",
    "Criei a requisição 99999999-9999-4999-8999-999999999999 no valor de 0.01.",
])
async def test_assistant_text_alone_never_creates_a_receipt_or_status(api_client, api_context, content):
    set_events(api_context, message=content)
    api_context.app.state.session_factory = MagicMock(side_effect=AssertionError("No receipt should access the database"))
    response = await api_client.post("/requisicoes", json={"message": "Compre um teclado"})
    assert response.status_code == 200
    assert response.json()["status"] == "response"
    assert response.json()["requisitions"] == []
    api_context.app.state.session_factory.assert_not_called()


async def test_candidates_over_budget_are_a_response_without_registration(api_client, api_context):
    set_events(api_context, ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={
        "status": "success", "products": [{"id": 1, "price": "500.00"}], "total": 1,
    }), message="O produto ultrapassa o orçamento.")
    response = await api_client.post("/requisicoes", json={"message": "Procure um teclado por até 30 reais"})
    assert response.json()["status"] == "response"
    assert response.json()["requisitions"] == []


async def test_confirmed_purchase_uses_persisted_snapshot_not_receipt_prices(api_client, api_context, seed_purchase):
    record = seed_purchase()
    set_events(api_context, creation(record))
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "Crie uma requisição"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    saved = body["requisitions"][0]
    assert saved["request_number"] == record.request_number
    assert saved["total"] == "59.97" and saved["currency"] is None
    assert saved["items"][0]["quantity"] == 3
    assert saved["items"][0]["unit_price"] == "19.99"
    assert saved["items"][0]["subtotal"] == "59.97"
    assert saved["items"][0]["name"] == "Fixture keyboard"
    assert saved["replayed"] is False


async def test_confirmed_replay_returns_200_without_duplicate_rows(api_client, api_context, seed_purchase):
    record = seed_purchase()
    set_events(api_context, creation(record, replayed=True), creation(record, replayed=True))
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "Tente novamente"})
    assert response.status_code == 200
    assert len(response.json()["requisitions"]) == 1
    assert response.json()["requisitions"][0]["replayed"] is True


async def test_multiple_confirmed_registrations_are_preserved(api_client, api_context, seed_purchase):
    first = seed_purchase()
    second = seed_purchase("22222222-2222-4222-8222-222222222222", title="Second")
    set_events(api_context, creation(first), creation(second))
    response = await api_client.post("/requisicoes", json={"message": "Crie duas requisições"})
    assert response.status_code == 201
    assert [item["id"] for item in response.json()["requisitions"]] == [first.id, second.id]


@pytest.mark.parametrize("receipt", [
    {}, {"request_id": 1, "request_number": "invented", "replayed": False},
    {"request_id": True, "request_number": "11111111-1111-4111-8111-111111111111", "replayed": False},
    {"request_id": 1, "request_number": "11111111-1111-4111-8111-111111111111", "replayed": "false"},
    {"request_id": 1, "request_number": "11111111-1111-4111-8111-111111111111", "replayed": False},
])
async def test_invalid_or_nonexistent_confirmation_is_502_without_writes(api_client, api_context, receipt):
    set_events(api_context, ToolExecutionEvent(name="criar_requisicao", outcome="ok", result=receipt))
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "Crie uma requisição"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "creation_unconfirmed"


async def test_valid_number_with_wrong_record_id_is_not_confirmation(api_client, api_context, seed_purchase):
    record = seed_purchase()
    set_events(api_context, creation(record, request_id=record.id + 1))
    response = await api_client.post("/requisicoes", json={"message": "Crie"})
    assert response.status_code == 502


@pytest.mark.parametrize(("outcome", "status"), [("tool_timeout", 504), ("tool_execution_failed", 502)])
async def test_failed_creation_does_not_claim_absence_or_success(api_client, api_context, outcome, status):
    set_events(api_context, ToolExecutionEvent(name="criar_requisicao", outcome=outcome), message="Fixture failure")
    response = await api_client.post("/requisicoes", json={"message": "Crie"})
    assert response.status_code == status
    assert "may have completed" in response.json()["detail"]["message"]
    assert "requisitions" not in response.json()


async def test_tool_api_error_is_not_an_empty_search(api_client, api_context):
    set_events(api_context, ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={"error": {"code": "api_unavailable"}}))
    response = await api_client.post("/requisicoes", json={"message": "Busque"})
    assert response.status_code == 502


async def test_trace_is_opt_in_and_never_contains_arguments_or_results(api_client, api_context):
    set_events(api_context, ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={"private": "private-sensitive-data"}))
    response = await api_client.post("/requisicoes", json={"message": "Busque", "include_tool_trace": True})
    assert response.json()["tool_trace"] == [{"name": "buscar_produtos", "status": "success", "code": None}]
    assert "private-sensitive-data" not in response.text
    assert "arguments" not in response.text


@pytest.mark.parametrize(("code", "status"), [
    ("invalid_configuration", 503), ("llm_authentication", 503), ("llm_credits", 503),
    ("llm_rate_limit", 503), ("llm_unavailable", 503), ("tool_catalog_error", 503),
    ("llm_timeout", 504), ("agent_timeout", 504), ("invalid_llm_response", 502),
    ("llm_incomplete", 502), ("llm_http_error", 502), ("iteration_limit", 502), ("tool_call_limit", 502),
])
async def test_agent_errors_have_stable_http_contract(api_client, api_context, code, status):
    api_context.agent.failure = AgentError(code)
    response = await api_client.post("/requisicoes", json={"message": "Compre"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


async def test_missing_llm_key_does_not_block_get_or_validation(api_client, api_context):
    api_context.app.dependency_overrides.pop(get_agent)
    response = await api_client.post("/requisicoes", json={"message": "Compre"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "invalid_configuration"
    response = await api_client.post("/requisicoes", json={"message": " "})
    assert response.status_code == 422
    assert (await api_client.get("/requisicoes")).status_code == 200


async def test_get_empty_list_without_llm(api_client, api_context):
    api_context.agent.failure = AssertionError("GET must not call the LLM")
    with forbid_writes(api_context.engine):
        response = await api_client.get("/requisicoes")
    assert response.json() == {"requisitions": [], "limit": 50, "offset": 0, "has_more": False}
    assert not api_context.agent.messages


async def test_get_reads_real_rows_with_pagination_and_relationships(api_client, api_context, seed_purchase):
    first = seed_purchase()
    second = seed_purchase("22222222-2222-4222-8222-222222222222", title="Second")
    with forbid_writes(api_context.engine):
        page = await api_client.get("/requisicoes?limit=1&offset=0")
        next_page = await api_client.get("/requisicoes?limit=1&offset=1")
    assert page.json()["has_more"] is True
    assert page.json()["requisitions"][0]["id"] == second.id
    assert next_page.json()["has_more"] is False
    saved = next_page.json()["requisitions"][0]
    assert saved["id"] == first.id
    assert saved["items"][0]["product_id"] == "1"
    assert saved["total"] == "59.97"
    assert not api_context.agent.messages


async def test_legacy_fields_remain_null_without_inventing_values(api_client, seed_purchase):
    seed_purchase(legacy=True)
    response = await api_client.get("/requisicoes")
    saved = response.json()["requisitions"][0]
    assert saved["request_number"] is None and saved["total"] is None and saved["currency"] is None
    assert saved["items"][0]["product_id"] is None
    assert saved["items"][0]["name"] is None
    assert saved["items"][0]["subtotal"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
async def test_invalid_pagination_is_422(api_client, query):
    assert (await api_client.get("/requisicoes?" + query)).status_code == 422


@pytest.mark.parametrize(("failure", "status", "code"), [
    (OperationalError("SELECT private", {}, Exception("private-password")), 503, "database_unavailable"),
    (SQLTimeoutError("private-connection"), 503, "database_unavailable"),
    (SQLAlchemyError("private-sql-error"), 500, "database_error"),
])
async def test_database_errors_are_safe_for_get_and_confirmation(api_client, api_context, seed_purchase, failure, status, code, caplog):
    record = seed_purchase()
    set_events(api_context, creation(record))
    api_context.app.state.session_factory = MagicMock(side_effect=failure)
    for response in (
        await api_client.get("/requisicoes"),
        await api_client.post("/requisicoes", json={"message": "Crie"}),
    ):
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code
        assert "private" not in response.text
    assert "private" not in caplog.text


async def test_openapi_exposes_models_codes_and_decimal_strings(api_client):
    document = (await api_client.get("/openapi.json")).json()
    endpoints = document["paths"]["/requisicoes"]
    assert {"get", "post"} <= endpoints.keys()
    assert {"200", "201", "422", "500", "502", "503", "504"} <= endpoints["post"]["responses"].keys()
    schemas = document["components"]["schemas"]
    response_schema = schemas["PurchaseMessageResponse"]
    assert response_schema["properties"]["status"]["enum"] == ["created", "no_results", "response"]
    assert schemas["PurchaseItemRead"]["properties"]["unit_price"]["type"] == "string"
    assert "total" in schemas["PurchaseRequestRead"]["properties"]
    assert "total_amount" not in schemas["PurchaseRequestRead"]["properties"]


async def test_executor_receipt_reaches_endpoint_without_llm_text_parsing(
    api_client, api_context, seed_purchase, harness, llm_http,
):
    record = seed_purchase()
    harness.tools.result = {"request_id": record.id, "request_number": record.request_number, "replayed": False}
    harness.responses = [completion(None, [tool_call("criar_requisicao", {
        "request": {"items": [{"product_id": 1, "quantity": 3}]},
    })]), completion("Fixture registration confirmed.")]
    api_context.app.dependency_overrides[get_agent] = lambda: harness.agent(llm_http)
    with forbid_writes(api_context.engine):
        response = await api_client.post("/requisicoes", json={"message": "Crie uma requisição", "include_tool_trace": True})
    assert response.status_code == 201
    assert response.json()["requisitions"][0]["total"] == "59.97"
    assert response.json()["tool_trace"] == [{"name": "criar_requisicao", "status": "success", "code": None}]
    assert harness.tools.calls == [("criar_requisicao", {"request": {"items": [{"product_id": 1, "quantity": 3}]}})]


async def test_executor_failure_is_a_gateway_error_not_no_results(api_client, api_context, harness, llm_http):
    harness.tools.failure = TimeoutError("private-tool-data")
    harness.responses = [completion(None, [tool_call("buscar_produtos", {"termo": "teclado"})]), completion("Fixture timeout.")]
    api_context.app.dependency_overrides[get_agent] = lambda: harness.agent(llm_http)
    response = await api_client.post("/requisicoes", json={"message": "Busque"})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "tool_timeout"
    assert "private-tool-data" not in response.text


@pytest.mark.parametrize("error_payload", [{"error": {}}, {"error": None}, {"isError": True}])
async def test_error_wrappers_cannot_confirm_an_existing_record(api_client, api_context, seed_purchase, error_payload):
    record = seed_purchase()
    set_events(api_context, creation(record, **error_payload))
    response = await api_client.post("/requisicoes", json={"message": "Crie"})
    assert response.status_code == 502


async def test_empty_search_after_found_candidates_does_not_erase_them(api_client, api_context):
    set_events(api_context,
        ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={"status": "success", "products": [{"id": 1}], "total": 1}),
        ToolExecutionEvent(name="buscar_produtos", outcome="ok", result={"status": "no_results", "products": [], "total": 0}),
    )
    response = await api_client.post("/requisicoes", json={"message": "Compare opções"})
    assert response.json()["status"] == "response"

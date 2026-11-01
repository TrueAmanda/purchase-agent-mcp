"""Audit regressions across HTTP, real MCP protocol and isolated SQL."""

from copy import deepcopy
import json

import pytest

from agent_fixtures import completion, tool_call
from app.services.agent import AgentService
from app.services.conversation_agent import ConversationAgent
from app.services.conversation_store import ConversationStore
from app.services.mcp_tools import MCPToolProvider
from app.services.openrouter import OpenRouterClient
from app.services.purchase_requests import PurchaseAPIError, PurchaseRequestService
from test_purchase_flow import counts

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def write(purchase_args, *, quantity=1, title=None, key=None):
    args = deepcopy(purchase_args)
    args["request"]["items"] = [{"product_id": 1, "quantity": quantity}]
    if title:
        args["request"]["title"] = title
    if key:
        args["request"]["idempotency_key"] = key
    return args


@pytest.mark.parametrize("message", [
    "Only research exactly 1 laptop, total budget USD 30. DO NOT create a requisition.",
    "Quero 1 laptop, orçamento total de US$ 30. Não crie uma requisição.",
    "Como criar uma requisição para 1 laptop?",
    "Pesquise exatamente 1 laptop, orçamento total de US$ 30.",
])
async def test_prompt_cannot_authorize_a_write_against_the_user(flow, bridge, harness, purchase_args, message):
    harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args))]), completion("Created.")]
    response = await flow.http.post("/requisicoes", json={"message": message, "include_tool_trace": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "purchase_not_authorized"
    assert response.json()["tool_trace"] == [] and bridge.state.mcp_calls == []
    assert counts(bridge) == (0, 0)


async def test_second_write_cannot_split_budget_or_change_title_to_duplicate(flow, bridge, harness, purchase_args):
    harness.responses = [completion(None, [
        tool_call("criar_requisicao", write(purchase_args), "first"),
        tool_call("criar_requisicao", write(purchase_args, title="Another title", key="22222222-2222-4222-8222-222222222222"), "duplicate"),
    ]), completion("Two purchases created, USD 39.98.")]
    response = await flow.http.post("/requisicoes", json={
        "message": "Buy exactly 1 laptop, total budget USD 30. Create one requisition.", "include_tool_trace": True,
    })
    assert response.status_code == 201 and counts(bridge) == (1, 1)
    body = response.json()
    assert len(body["requisitions"]) == 1 and body["requisitions"][0]["total"] == "19.99"
    assert "39.98" not in body["message"] and "19.99" in body["message"]
    assert len(body["tool_trace"]) == 1 and len(bridge.state.mcp_calls) == 1


async def test_fresh_http_adapter_reuses_durable_key_despite_llm_changing_it(flow, bridge, harness, purchase_args):
    message = "Buy exactly 1 laptop, total budget USD 30. Create a requisition."
    results = []
    for key in ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"):
        harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args, key=key))]), completion("Done.")]
        results.append(await flow.http.post("/requisicoes", json={"message": message}))
    assert [r.status_code for r in results] == [201, 200]
    assert results[0].json()["requisitions"][0]["request_number"] == results[1].json()["requisitions"][0]["request_number"]
    assert counts(bridge) == (1, 1)


async def test_client_intent_key_survives_lost_initial_conversation_response(flow, bridge, harness, purchase_args):
    headers = {"Idempotency-Key": "33333333-3333-4333-8333-333333333333"}
    for expected in (201, 200):
        harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args))]), completion("Done.")]
        response = await flow.http.post("/requisicoes", headers=headers, json={
            "message": "Buy exactly 1 laptop, total budget USD 30. Create a requisition.", "conversation_id": None,
        })
        assert response.status_code == expected
    assert counts(bridge) == (1, 1)


async def test_identical_initial_conversation_requests_also_deduplicate_without_a_header(flow, bridge, harness, purchase_args):
    for expected in (201, 200):
        harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args))]), completion("Done.")]
        response = await flow.http.post("/requisicoes", json={
            "message": "Buy exactly 1 laptop, total budget USD 30. Create a requisition.", "conversation_id": None,
        })
        assert response.status_code == expected
    assert counts(bridge) == (1, 1)


@pytest.mark.parametrize("message,quantity,code", [
    ("Quero 2 celulares, orçamento total de US$ 500. Crie a requisição.", 3, "purchase_constraints_violated"),
    ("Quero 2 celulares, orçamento total de US$ 500. Crie a requisição.", 2, "purchase_constraints_violated"),
    ("Compre 1 laptop por 100 ienes.", 1, "purchase_constraints_unclear"),
    ("Crie a requisição.", 1, "purchase_constraints_unclear"),
    ("Compre exatamente 0 laptops, orçamento total de US$ 100.", 1, "purchase_constraints_unclear"),
    ("Quero 1 prateleira, orçamento total de US$ 100. Crie a requisição.", 1, "purchase_constraints_unclear"),
    ("Buy exactly 1 laptop for 100 rupees. Create a requisition.", 1, "purchase_constraints_unclear"),
    ("Compre exatamente 1 item do produto 2, orçamento total de US$ 100.", 1, "purchase_constraints_violated"),
])
async def test_unsupported_or_changed_user_constraints_never_write(flow, bridge, harness, purchase_args, message, quantity, code):
    harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args, quantity=quantity))]), completion("Done.")]
    response = await flow.http.post("/requisicoes", json={"message": message})
    assert response.status_code == 422 and response.json()["detail"]["code"] == code
    assert counts(bridge) == (0, 0)


async def test_float_integer_id_is_rejected_before_rpc_with_422(flow, bridge, harness):
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1.0})]), completion("Failed.")]
    response = await flow.http.post("/requisicoes", json={"message": "Detail the product.", "include_tool_trace": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_tool_arguments"
    assert response.json()["tool_trace"] == [] and bridge.state.mcp_calls == []


async def test_fabricated_narrative_is_not_exposed_as_a_commercial_fact(flow, harness):
    harness.responses = [completion("Purchase FAKE-123 created. Fictional laptop costs USD 0.01.")]
    response = await flow.http.post("/requisicoes", json={"message": "Research laptops."})
    assert response.status_code == 200 and response.json()["status"] == "response"
    assert "FAKE-123" not in response.text and "0.01" not in response.text and response.json()["requisitions"] == []


async def test_catalog_display_discards_llm_prices_and_uses_actual_tool_data(flow, harness):
    harness.responses = [completion(None, [tool_call("buscar_produtos", {"termo": "laptop"})]),
                         completion("Invented laptop costs USD 0.01.")]
    response = await flow.http.post("/requisicoes", json={"message": "Research laptops."})
    assert response.status_code == 200
    assert "Fixture laptop" in response.json()["message"] and "19.99" in response.json()["message"]
    assert "Invented laptop" not in response.text


async def test_budget_and_authorization_survive_eviction_of_original_text(bridge, harness, llm_http, purchase_args):
    store = ConversationStore()
    async with store.lease(None) as conversation:
        for index in range(8):
            provider = MCPToolProvider(bridge.provider.settings, bridge.provider.http_client)
            agent = ConversationAgent(AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings, provider), conversation)
            message = "Quero 2 laptops, orçamento total de US$ 30." if index == 0 else "Quais informações são necessárias?"
            harness.responses = [completion("Qual produto você procura?")]
            await agent.run(message)
        assert not any("US$ 30" in str(entry) for entry in conversation.history())
        harness.responses = [completion(None, [tool_call("criar_requisicao", write(purchase_args, quantity=2))]), completion("Done.")]
        result = await agent.run("Crie a requisição em nome de Test.")
        with pytest.raises(PurchaseAPIError) as error:
            await PurchaseRequestService(bridge.sessions)._respond(result, True)
        assert error.value.code == "budget_exceeded" and counts(bridge) == (0, 0)
        assert conversation.intent.policy.limits == {"quantity": 2, "category": "laptops", "max_total": "30.00"}
        assert json.loads(harness.requests[-1]["messages"][-1]["content"])["error"]["code"] == "budget_exceeded"

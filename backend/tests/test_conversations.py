"""Server-owned context, bounded retention and protocol-safe follow-ups."""

import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from app.core.agent_errors import AgentError
from app.core.conversation_config import ConversationSettings
from app.services.conversation_agent import ConversationAgent
from app.services.conversation_store import ConversationStore
from agent_fixtures import completion, tool_call

pytestmark = pytest.mark.anyio


async def test_store_isolates_conversations_and_returns_defensive_history_copies():
    store = ConversationStore()
    async with store.lease(None) as first:
        first.append([{"role": "user", "content": "First private message"}, {"role": "assistant", "content": "First answer"}])
        first_id = first.id
        copied = first.history()
        copied[0]["content"] = "tampered"
    async with store.lease(None) as second:
        assert second.id != first_id and second.history() == []
    async with store.lease(first_id) as first:
        assert first.history()[0]["content"] == "First private message"


async def test_ttl_expiry_does_not_silently_start_an_empty_conversation():
    now = [0.0]
    store = ConversationStore(ConversationSettings(ttl_seconds=10), clock=lambda: now[0])
    async with store.lease(None) as record:
        identifier = record.id
    now[0] = 10
    with pytest.raises(AgentError) as caught:
        async with store.lease(identifier):
            pass
    assert caught.value.code == "conversation_expired"
    assert str(identifier) not in str(caught.value)


async def test_busy_conversation_is_not_mutated_or_evicted():
    store = ConversationStore(ConversationSettings(max_conversations=1))
    async with store.lease(None) as record:
        with pytest.raises(AgentError, match="still being processed"):
            async with store.lease(record.id):
                pass
        with pytest.raises(AgentError) as caught:
            async with store.lease(None):
                pass
        assert caught.value.code == "conversation_capacity"
    async with store.lease(record.id) as available:
        assert not available.history()


async def test_capacity_evicts_oldest_idle_conversation_only():
    store = ConversationStore(ConversationSettings(max_conversations=1))
    async with store.lease(None) as first:
        first_id = first.id
    async with store.lease(None) as second:
        assert second.id != first_id
    with pytest.raises(AgentError) as caught:
        async with store.lease(first_id):
            pass
    assert caught.value.code == "conversation_expired"


async def test_unknown_identifier_cannot_retrieve_or_forge_context():
    store = ConversationStore()
    with pytest.raises(AgentError) as caught:
        async with store.lease(uuid4()):
            pass
    assert caught.value.code == "conversation_expired"


async def test_retention_removes_whole_turns_and_bounds_oversized_context():
    settings = ConversationSettings(max_turns=2, max_history_characters=1000)
    store = ConversationStore(settings)
    async with store.lease(None) as record:
        for index in range(3):
            record.append([{"role": "user", "content": str(index)}, {"role": "assistant", "content": str(index)}])
        assert [entry["content"] for entry in record.history()] == ["1", "1", "2", "2"]
        record.append([{"role": "user", "content": "x" * 2000}])
        identifier = record.id
        assert record.history() == [] and record.retained is False
    with pytest.raises(AgentError) as caught:
        async with store.lease(identifier):
            pass
    assert caught.value.code == "conversation_expired"


@pytest.mark.parametrize("name,value", [
    ("CONVERSATION_TTL_SECONDS", "nan"), ("CONVERSATION_TTL_SECONDS", "0"),
    ("CONVERSATION_MAX_COUNT", "0"), ("CONVERSATION_MAX_TURNS", "-1"),
    ("CONVERSATION_MAX_HISTORY_CHARACTERS", "0"),
])
async def test_invalid_configuration_is_safe(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(AgentError) as caught:
        ConversationSettings.from_environment()
    assert caught.value.code == "invalid_conversation_configuration"


async def test_follow_up_receives_original_product_ids_and_current_trace_only(harness, llm_http):
    store = ConversationStore()
    catalog = {"status": "success", "products": [
        {"id": 2, "name": "Fixture palette", "price": "19.99", "currency": "USD"},
        {"id": 3, "name": "Fixture Powder Canister", "price": "14.99", "currency": "USD"},
    ], "total": 2}
    harness.tools.result = deepcopy(catalog)
    harness.responses = [completion(None, [tool_call("buscar_produtos", {"termo": "makeup"}, "search")]),
                         completion("Encontrei uma paleta e um pó compacto.")]
    async with store.lease(None) as conversation:
        first = await ConversationAgent(harness.agent(llm_http), conversation).run("Quero maquiagem até US$ 30")
        identifier = conversation.id
    harness.tools.result = {"product": catalog["products"][1]}
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 3}, "detail")]), completion("Detalhes do pó compacto.")]
    async with store.lease(identifier) as conversation:
        follow_up = await ConversationAgent(harness.agent(llm_http), conversation).run("Quero mais detalhes do pó compacto")
    history = harness.requests[2]["messages"]
    earlier_tools = [entry for entry in history if entry["role"] == "tool"]
    assert json.loads(earlier_tools[0]["content"]) == catalog
    assert any(entry.get("content") == "Encontrei uma paleta e um pó compacto." for entry in history)
    assert [name for name, _ in harness.tools.calls] == ["buscar_produtos", "detalhar_produto"]
    assert [entry.name for entry in first.tool_events] == ["buscar_produtos"]
    assert [entry.name for entry in follow_up.tool_events] == ["detalhar_produto"]
    assert set(follow_up.model_dump()) == {"content", "iterations", "tool_calls"}


async def test_interrupted_batch_remains_protocol_valid_without_fabricating_tool_activity(harness, llm_http):
    store = ConversationStore()
    entered = []
    async def interrupted(name, arguments):
        entered.append(name)
        await asyncio.sleep(10)
    harness.tools.call_tool = interrupted
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 3}, "details"),
                                          tool_call("buscar_produtos", {"termo": "powder"}, "not-started")])]
    async with store.lease(None) as conversation:
        with pytest.raises(AgentError) as caught:
            await ConversationAgent(harness.agent(llm_http, run_timeout_seconds=0.1), conversation).run("Compare")
        assert caught.value.code == "agent_timeout"
        assert len(caught.value.tool_events) == 1
        history = conversation.history()
    assert entered == ["detalhar_produto"]
    tool_messages = [entry for entry in history if entry["role"] == "tool"]
    assert [entry["tool_call_id"] for entry in tool_messages] == ["details", "not-started"]
    assert all(json.loads(entry["content"])["error"]["code"] == "execution_unconfirmed" for entry in tool_messages)
    assert history[-1]["role"] == "assistant"


async def test_confirmed_write_evidence_and_original_key_survive_later_provider_failure(harness, llm_http):
    import httpx

    store = ConversationStore()
    # Fixture schema accepts only IDs/quantities; this is server-owned history, not client prices.
    schema = harness.tools.definitions[2].parameters["properties"]["request"]
    schema["properties"]["idempotency_key"] = {"type": "string", "format": "uuid"}
    schema["required"].append("idempotency_key")
    original_key = "44444444-4444-4444-8444-444444444444"
    arguments = {"request": {"items": [{"product_id": 1, "quantity": 3}], "idempotency_key": original_key}}
    receipt = {"request_id": 1, "request_number": "11111111-1111-4111-8111-111111111111", "replayed": False}
    harness.tools.result = receipt
    harness.responses = [completion(None, [tool_call("criar_requisicao", arguments, "write")]), httpx.Response(503)]
    async with store.lease(None) as conversation:
        with pytest.raises(AgentError) as caught:
            await ConversationAgent(harness.agent(llm_http), conversation).run("Crie o pedido")
        assert caught.value.code == "llm_unavailable"
        history = conversation.history()
    assert json.loads(next(entry["content"] for entry in history if entry["role"] == "tool")) == receipt
    previous_call = next(entry for entry in history if entry["role"] == "assistant" and entry.get("tool_calls"))["tool_calls"][0]
    assert json.loads(previous_call["function"]["arguments"])["request"]["idempotency_key"] == original_key
    assert len(caught.value.tool_events) == 1
    assert [name for name, _ in harness.tools.calls] == ["criar_requisicao"]

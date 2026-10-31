"""Dynamic tool decisions and bounded execution, with simulated tools only."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.core.agent_errors import AgentError, ToolExecutionError
from app.core.agent_prompt import NO_TOOLS_PROMPT, SYSTEM_PROMPT
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient
from agent_fixtures import completion, tool_call

pytestmark = pytest.mark.anyio


async def test_direct_answer_without_tool_calls(harness, llm_http):
    harness.responses = [completion("What quantity do you need?")]
    result = await harness.agent(llm_http).run("  Help me buy supplies  ")
    assert result.model_dump() == {"content": "What quantity do you need?", "iterations": 1, "tool_calls": 0}
    assert harness.requests[0]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "Help me buy supplies"},
    ]
    assert not harness.tools.calls


async def test_default_provider_has_no_fake_catalog(harness, llm_http):
    harness.responses = [completion("Catalog tools are not connected.")]
    service = AgentService(OpenRouterClient(harness.settings, llm_http), harness.settings)
    await service.run("Find a product")
    assert "tools" not in harness.requests[0]
    assert "tool_choice" not in harness.requests[0]
    system_message = harness.requests[0]["messages"][0]
    assert system_message["role"] == "system"
    assert "Runtime capabilities for this execution: NO TOOLS ARE CONNECTED." in system_message["content"]
    assert "first\nexplicitly state this limitation" in system_message["content"]
    assert "Do not say you will search, check, register, or create anything." in system_message["content"]
    assert "Do not promise an action" in system_message["content"]
    assert not harness.tools.calls


async def test_runtime_capabilities_follow_the_current_catalog_on_each_run(harness, llm_http):
    definitions = harness.tools.definitions
    service = harness.agent(llm_http)
    harness.responses = [completion(), completion(), completion()]
    harness.tools.definitions = []
    await service.run("Help me plan a purchase")
    harness.tools.definitions = definitions
    await service.run("Help me plan a purchase")
    harness.tools.definitions = []
    await service.run("Help me plan a purchase")
    for index in (0, 2):
        assert harness.requests[index]["messages"][0]["content"] == SYSTEM_PROMPT + NO_TOOLS_PROMPT
        assert "tools" not in harness.requests[index]
    assert harness.requests[1]["messages"][0]["content"] == SYSTEM_PROMPT
    assert len(harness.requests[1]["tools"]) == 3


async def test_dynamic_consecutive_rounds_keep_full_protocol_history(harness, llm_http):
    # The model starts with details; there is no mandatory preceding search.
    first = tool_call("detalhar_produto", {"id": 1})
    second = tool_call("buscar_produtos", {"termo": "alternative"}, "fixture-call-2")
    harness.responses = [completion(None, [first]), completion(None, [second]), completion("Fixture conclusion")]
    result = await harness.agent(llm_http).run("Compare product 1 with alternatives, quantity 3, budget 60")
    assert [name for name, _ in harness.tools.calls] == ["detalhar_produto", "buscar_produtos"]
    assert result.iterations == 3
    assert result.tool_calls == 2
    messages = harness.requests[2]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert messages[2]["tool_calls"] == [first]
    assert messages[3]["tool_call_id"] == first["id"]
    assert json.loads(messages[3]["content"]) == harness.tools.result
    assert messages[5]["tool_call_id"] == second["id"]
    assert all(request["tool_choice"] == "auto" for request in harness.requests)
    assert all(request["tools"] == harness.requests[0]["tools"] for request in harness.requests)


async def test_multiple_tools_in_one_response(harness, llm_http):
    harness.responses = [completion(None, [
        tool_call("detalhar_produto", {"id": 2}),
        tool_call("detalhar_produto", {"id": 1}, "fixture-call-2"),
    ]), completion()]
    result = await harness.agent(llm_http).run("Compare product 2 and product 1")
    assert harness.tools.calls == [("detalhar_produto", {"id": 2}), ("detalhar_produto", {"id": 1})]
    assert result.tool_calls == 2
    assert [m["tool_call_id"] for m in harness.requests[1]["messages"] if m["role"] == "tool"] == ["fixture-call-1", "fixture-call-2"]


async def test_model_can_request_creation_without_a_fixed_tool_sequence(harness, llm_http):
    arguments = {"request": {"items": [{"product_id": 1, "quantity": 3}]}}
    harness.responses = [completion(None, [tool_call("criar_requisicao", arguments)]), completion()]
    await harness.agent(llm_http).run("Create a requisition for 3 units of product 1")
    assert harness.tools.calls == [("criar_requisicao", arguments)]


@pytest.mark.parametrize("message", ["", " ", None, 123, "x" * 20001])
async def test_invalid_user_message_is_rejected_before_llm(harness, llm_http, message):
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run(message)
    assert caught.value.code == "invalid_message"
    assert not harness.requests


@pytest.mark.parametrize(("name", "raw_arguments", "code"), [
    ("unadvertised-tool", "{}", "unknown_tool"),
    ("detalhar_produto", "not-json", "invalid_tool_arguments"),
    ("detalhar_produto", "[]", "invalid_tool_arguments"),
    ("detalhar_produto", '{"id": 0}', "invalid_tool_arguments"),
    ("detalhar_produto", '{"id": "1"}', "invalid_tool_arguments"),
    ("detalhar_produto", '{"id": true}', "invalid_tool_arguments"),
    ("detalhar_produto", '{"id": NaN}', "invalid_tool_arguments"),
    ("detalhar_produto", '{"id": 1, "price": 0.01}', "invalid_tool_arguments"),
    ("criar_requisicao", '{"request":{"items":[{"product_id":1,"quantity":0}]}}', "invalid_tool_arguments"),
])
async def test_invalid_calls_return_correlated_errors_to_model(harness, llm_http, name, raw_arguments, code):
    call = tool_call(name, {})
    call["function"]["arguments"] = raw_arguments
    harness.responses = [completion(None, [call]), completion("I cannot confirm this operation.")]
    await harness.agent(llm_http).run("Fixture request")
    assert not harness.tools.calls
    tool_message = harness.requests[1]["messages"][-1]
    assert tool_message["tool_call_id"] == call["id"]
    assert json.loads(tool_message["content"])["error"]["code"] == code


async def test_tool_error_is_safe_and_model_can_continue(harness, llm_http, caplog):
    harness.tools.failure = ToolExecutionError("private-database-credentials")
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1})]), completion()]
    with caplog.at_level(logging.INFO):
        await harness.agent(llm_http).run("private-user-text")
    error = json.loads(harness.requests[1]["messages"][-1]["content"])["error"]
    assert error["code"] == "tool_execution_failed"
    assert "private" not in caplog.text
    assert "private" not in str(error)
    assert "duration_ms" in caplog.text
    assert "agent_round iteration=2" in caplog.text
    assert "test-only-not-a-real-key" not in caplog.text


async def test_model_can_correct_invalid_arguments_in_a_later_round(harness, llm_http):
    harness.responses = [
        completion(None, [tool_call("detalhar_produto", {"id": -1})]),
        completion(None, [tool_call("detalhar_produto", {"id": 1}, "fixture-call-2")]),
        completion(),
    ]
    result = await harness.agent(llm_http).run("Fixture question")
    assert result.iterations == 3
    assert harness.tools.calls == [("detalhar_produto", {"id": 1})]
    assert json.loads(harness.requests[1]["messages"][-1]["content"])["error"]["code"] == "invalid_tool_arguments"


async def test_mixed_batch_returns_one_result_per_call(harness, llm_http):
    harness.responses = [completion(None, [
        tool_call("detalhar_produto", {"id": 1}),
        tool_call("unknown", {}, "fixture-call-2"),
    ]), completion()]
    await harness.agent(llm_http).run("Fixture question")
    results = [message for message in harness.requests[1]["messages"] if message["role"] == "tool"]
    assert len(results) == 2
    assert json.loads(results[0]["content"]) == harness.tools.result
    assert json.loads(results[1]["content"])["error"]["code"] == "unknown_tool"
    assert harness.tools.calls == [("detalhar_produto", {"id": 1})]


async def test_tool_timeout_returns_error_to_model(harness, llm_http):
    async def slow_tool(*args):
        await asyncio.Event().wait()

    harness.tools.call_tool = slow_tool
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1})]), completion()]
    await harness.agent(llm_http, tool_timeout_seconds=0.01).run("Fixture question")
    assert json.loads(harness.requests[1]["messages"][-1]["content"])["error"]["code"] == "tool_timeout"


@pytest.mark.parametrize("bad_result", [["unsupported"], {"price": float("nan")}, {"huge": "x" * 100001}])
async def test_invalid_tool_output_becomes_safe_error(harness, llm_http, bad_result):
    harness.tools.result = bad_result
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1})]), completion()]
    await harness.agent(llm_http).run("Fixture question")
    assert json.loads(harness.requests[1]["messages"][-1]["content"])["error"]["code"] == "tool_execution_failed"


async def test_iteration_limit_stops_before_last_round_side_effects(harness, llm_http):
    harness.responses = [
        completion(None, [tool_call("detalhar_produto", {"id": 1})]),
        completion(None, [tool_call("detalhar_produto", {"id": 1}, "fixture-call-2")]),
    ]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http, max_iterations=2).run("Fixture question")
    assert caught.value.code == "iteration_limit"
    assert len(harness.requests) == 2
    assert len(harness.tools.calls) == 1


async def test_tool_call_limit_rejects_entire_batch(harness, llm_http):
    harness.responses = [completion(None, [
        tool_call("detalhar_produto", {"id": 1}),
        tool_call("detalhar_produto", {"id": 2}, "fixture-call-2"),
    ])]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http, max_tool_calls_per_iteration=1).run("Fixture question")
    assert caught.value.code == "tool_call_limit"
    assert not harness.tools.calls


async def test_repeated_call_id_across_rounds_is_not_executed_twice(harness, llm_http):
    call = tool_call("detalhar_produto", {"id": 1})
    harness.responses = [completion(None, [call]), completion(None, [call])]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "invalid_llm_response"
    assert len(harness.tools.calls) == 1


async def test_overall_timeout_cancels_llm(harness, llm_http):
    cancelled = asyncio.Event()

    async def stalled_provider(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    harness.responses = [stalled_provider]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http, run_timeout_seconds=0.01).run("Fixture question")
    assert caught.value.code == "agent_timeout"
    assert cancelled.is_set()


@pytest.mark.parametrize("catalog_failure", ["exception", "duplicate", "invalid-schema", "not-object"])
async def test_invalid_catalog_is_not_sent_to_model(harness, llm_http, catalog_failure):
    if catalog_failure == "exception":
        harness.tools.list_tools = AsyncMock(side_effect=RuntimeError("private-catalog-info"))
    elif catalog_failure == "duplicate":
        harness.tools.definitions.append(harness.tools.definitions[0])
    elif catalog_failure == "invalid-schema":
        harness.tools.definitions[0].parameters = {"type": "object", "required": 123}
    else:
        harness.tools.definitions[0].parameters = {"type": "array"}
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "tool_catalog_error"
    assert not harness.requests
    assert "private" not in str(caught.value)


async def test_catalog_discovery_has_timeout(harness, llm_http):
    async def stalled_catalog():
        await asyncio.Event().wait()

    harness.tools.list_tools = stalled_catalog
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http, tool_timeout_seconds=0.01).run("Fixture question")
    assert caught.value.code == "tool_catalog_error"
    assert not harness.requests


async def test_external_schema_references_do_not_access_network(harness, llm_http):
    harness.tools.definitions[1].parameters = {"type": "object", "$ref": "https://example.test/schema"}
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1})]), completion()]
    await harness.agent(llm_http).run("Fixture question")
    assert not harness.tools.calls
    assert json.loads(harness.requests[1]["messages"][-1]["content"])["error"]["code"] == "invalid_tool_arguments"


async def test_reasoning_details_are_preserved_between_rounds(harness, llm_http):
    details = [{"type": "reasoning.encrypted", "data": "fixture-encrypted-value"}]
    harness.responses = [completion(None, [tool_call("detalhar_produto", {"id": 1})], reasoning_details=details), completion()]
    await harness.agent(llm_http).run("Fixture question")
    assert harness.requests[1]["messages"][2]["reasoning_details"] == details


async def test_concurrent_runs_do_not_share_history(harness, llm_http):
    harness.responses = [completion("answer one"), completion("answer two")]
    service = harness.agent(llm_http)
    await asyncio.gather(service.run("message one"), service.run("message two"))
    assert len(harness.requests) == 2
    assert all(len(request["messages"]) == 2 for request in harness.requests)
    assert {request["messages"][1]["content"] for request in harness.requests} == {"message one", "message two"}


async def test_caller_cancellation_propagates(harness, llm_http):
    harness.tools.list_tools = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await harness.agent(llm_http).run("Fixture question")


async def test_system_prompt_contains_purchasing_constraints():
    for rule in (
        "Never invent", "quantities", "total budget", "idempotency key",
        "untrusted data", "no required sequence", "successful tool result",
    ):
        assert rule in SYSTEM_PROMPT

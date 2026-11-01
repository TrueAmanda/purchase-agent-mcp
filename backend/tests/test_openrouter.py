"""Provider request format and error boundaries using HTTPX MockTransport."""

import httpx
import pytest

from app.core.agent_errors import AgentError
from app.services.openrouter import OpenRouterClient
from agent_fixtures import completion, tool_call

pytestmark = pytest.mark.anyio


async def test_provider_parameters_and_optional_tools(harness, llm_http):
    harness.responses = [completion()]
    agent = harness.agent(llm_http, max_tokens=1234, temperature=0.2, timeout_seconds=2)
    await agent.run("Fixture question")
    body = harness.requests[0]
    assert body["model"] == "fixture/model"
    assert body["max_tokens"] == 1234
    assert body["temperature"] == 0.2
    assert body["stream"] is False
    assert body["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in body
    assert body["provider"] == {"require_parameters": True}
    assert body["tools"] == [definition.as_openrouter_tool() for definition in harness.tools.definitions]


@pytest.mark.parametrize(("status", "code"), [
    (400, "llm_http_error"), (401, "llm_authentication"), (403, "llm_forbidden"),
    (402, "llm_credits"), (408, "llm_timeout"), (429, "llm_rate_limit"),
    (500, "llm_unavailable"), (502, "llm_unavailable"), (503, "llm_unavailable"),
    (307, "llm_http_error"),
])
async def test_http_failures_are_safe_and_not_retried(harness, llm_http, status, code, caplog):
    harness.responses = [httpx.Response(status, text="private-provider-body")]
    with caplog.at_level("INFO"), pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("private-user-message")
    assert caught.value.code == code
    assert "private" not in str(caught.value)
    assert "private" not in caplog.text
    assert len(harness.requests) == 1


@pytest.mark.parametrize("metadata,message", [
    ({"limit_source": "openrouter_key_limit"}, "private-provider-body"),
    ({"limit_source": "openrouter_workspace_budget"}, "private-provider-body"),
    ({"limit_source": "openrouter_guardrail_budget"}, "private-provider-body"),
    ({}, "Key limit exceeded (total limit). private-provider-body"),
    ({}, "Budget limit exceeded (weekly limit). private-provider-body"),
])
async def test_spending_limit_403_is_not_misclassified_as_authentication(harness, llm_http, metadata, message, caplog):
    harness.responses = [httpx.Response(403, json={"error": {"code": 403, "message": message, "metadata": metadata}})]
    with caplog.at_level("INFO"), pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "llm_budget_exceeded"
    assert "private-provider-body" not in str(caught.value) and "private-provider-body" not in caplog.text
    assert len(harness.requests) == 1 and not harness.tools.calls


@pytest.mark.parametrize("body", [{"error": {"metadata": {"limit_source": []}}}, {"error": []}, {"error": {"metadata": []}}, []])
async def test_malformed_forbidden_body_is_still_a_safe_permission_error(harness, llm_http, body):
    harness.responses = [httpx.Response(403, json=body)]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "llm_forbidden" and len(harness.requests) == 1


@pytest.mark.parametrize(("failure", "code"), [
    (httpx.ReadTimeout("private-network-data"), "llm_timeout"),
    (httpx.ConnectError("private-network-data"), "llm_unavailable"),
    (httpx.RemoteProtocolError("private-network-data"), "llm_unavailable"),
])
async def test_transport_failure(harness, llm_http, failure, code):
    harness.responses = [failure]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == code
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not-json"), {}, {"choices": []}, {"choices": [{}]},
    completion(content=None), completion(content=" "),
    completion(content=[{"type": "text", "text": "unsupported"}]),
    completion(role="user"), completion(finish_reason="unknown"),
    completion(calls=[], finish_reason="tool_calls"),
    completion(calls=[tool_call("buscar_produtos", {})], finish_reason="stop"),
    completion(calls=[tool_call("buscar_produtos", {}), tool_call("buscar_produtos", {})]),
    completion(calls=[{"id": "bad", "type": "function", "function": {"name": "x", "arguments": {}}}]),
])
async def test_invalid_llm_response_does_not_execute_tools(harness, llm_http, response):
    harness.responses = [response]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "invalid_llm_response"
    assert not harness.tools.calls


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error"])
async def test_incomplete_responses_never_execute_tools(harness, llm_http, finish_reason):
    harness.responses = [completion(calls=[tool_call("buscar_produtos", {"termo": "fixture"})], finish_reason=finish_reason)]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "llm_incomplete"
    assert not harness.tools.calls


async def test_error_in_successful_http_body(harness, llm_http):
    harness.responses = [{"error": {"code": 502, "message": "private-provider-error"}}]
    with pytest.raises(AgentError) as caught:
        await harness.agent(llm_http).run("Fixture question")
    assert caught.value.code == "llm_unavailable"
    assert "private" not in str(caught.value)


async def test_httpx_timeout_and_no_redirect(harness):
    async def handle(request):
        assert request.extensions["timeout"] == {"connect": 2, "read": 2, "write": 2, "pool": 2}
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        settings = harness.settings.model_copy(update={"timeout_seconds": 2})
        result = await OpenRouterClient(settings, client).complete([], [])
    assert result.content == "Fixture final answer"


async def test_null_tool_calls_are_a_valid_direct_answer(harness, llm_http):
    response = completion()
    response["choices"][0]["message"]["tool_calls"] = None
    harness.responses = [response]
    result = await harness.agent(llm_http).run("Fixture question")
    assert result.tool_calls == 0


async def test_redirect_never_forwards_credentials_even_with_redirecting_client(harness):
    requests = []

    async def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://example.test/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        with pytest.raises(AgentError) as caught:
            await harness.agent(client).run("Fixture question")
    assert caught.value.code == "llm_http_error"
    assert len(requests) == 1

"""Small non-streaming OpenRouter Chat Completions client using HTTPX."""

import logging
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError
from app.schemas.agent import AssistantMessage, ToolDefinition

logger = logging.getLogger(__name__)


class _Choice(BaseModel):
    message: AssistantMessage
    finish_reason: str


class _Completion(BaseModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)


def _http_error(status: int) -> AgentError:
    codes = {
        401: "llm_authentication", 403: "llm_authentication", 402: "llm_credits",
        408: "llm_timeout", 429: "llm_rate_limit",
    }
    return AgentError(codes.get(status, "llm_unavailable" if status >= 500 else "llm_http_error"))


class OpenRouterClient:
    def __init__(self, settings: AgentSettings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolDefinition],
    ) -> AssistantMessage:
        started = perf_counter()
        outcome = "ok"
        payload = {
            "model": self.settings.model, "messages": messages,
            "temperature": self.settings.temperature, "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        if tools:
            payload.update({
                "tools": [tool.as_openrouter_tool() for tool in tools],
                # Do not require optional flags absent from the model's endpoint capabilities.
                # Multiple returned calls remain supported by the agent loop.
                "tool_choice": "auto",
                "provider": {"require_parameters": True},
            })
        try:
            response = await self.client.post(
                self.settings.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.api_key.get_secret_value()},
                json=payload, timeout=self.settings.timeout_seconds, follow_redirects=False,
            )
            if not response.is_success:
                raise _http_error(response.status_code)
            try:
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    # OpenRouter can return generation errors inside HTTP 200 bodies.
                    raise AgentError("llm_unavailable")
                choice = _Completion.model_validate(data).choices[0]
            except (ValueError, ValidationError):
                raise AgentError("invalid_llm_response") from None
            if choice.finish_reason in {"length", "content_filter", "error"}:
                raise AgentError("llm_incomplete")
            message = choice.message
            if message.tool_calls:
                ids = [call.id for call in message.tool_calls]
                if choice.finish_reason != "tool_calls" or len(ids) != len(set(ids)):
                    raise AgentError("invalid_llm_response")
            elif choice.finish_reason != "stop" or not message.content or not message.content.strip():
                raise AgentError("invalid_llm_response")
            return message
        except httpx.TimeoutException:
            outcome = "llm_timeout"
            raise AgentError(outcome) from None
        except httpx.RequestError:
            outcome = "llm_unavailable"
            raise AgentError(outcome) from None
        except AgentError as error:
            outcome = error.code
            raise
        finally:
            logger.info("llm_request outcome=%s duration_ms=%.1f", outcome, (perf_counter() - started) * 1000)


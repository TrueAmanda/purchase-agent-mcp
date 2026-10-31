"""Bounded, model-driven tool loop, independent of routes, MCP and storage."""

import asyncio
import json
import logging
from time import perf_counter
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError, ToolExecutionError, TOOL_ERROR_MESSAGES
from app.core.agent_prompt import build_system_prompt
from app.schemas.agent import AgentResult, ToolCall, ToolDefinition, ToolExecutionEvent
from app.services.agent_tools import EmptyToolProvider, ToolProvider
from app.services.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(
        self, llm: OpenRouterClient, settings: AgentSettings, tools: ToolProvider | None = None,
    ):
        self.llm = llm
        self.settings = settings
        self.tools = tools if tools is not None else EmptyToolProvider()

    async def run(self, message: str) -> AgentResult:
        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
            raise AgentError("invalid_message")
        started = perf_counter()
        outcome = "ok"
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                return await self._run(message.strip())
        except TimeoutError:
            outcome = "agent_timeout"
            raise AgentError(outcome) from None
        except AgentError as error:
            outcome = error.code
            raise
        finally:
            logger.info("agent_run outcome=%s duration_ms=%.1f", outcome, (perf_counter() - started) * 1000)

    async def _load_tools(self) -> tuple[list[ToolDefinition], dict[str, Draft202012Validator]]:
        try:
            async with asyncio.timeout(self.settings.tool_timeout_seconds):
                definitions = await self.tools.list_tools()
            validators = {}
            for definition in definitions:
                if not isinstance(definition, ToolDefinition) or definition.name in validators:
                    raise ValueError
                if definition.parameters.get("type") != "object":
                    raise ValueError
                Draft202012Validator.check_schema(definition.parameters)
                # An empty registry forbids network retrieval of external $ref schemas.
                validators[definition.name] = Draft202012Validator(definition.parameters, registry=Registry())
            return definitions, validators
        except AgentError:
            raise
        except Exception:
            raise AgentError("tool_catalog_error") from None

    async def _run(self, message: str) -> AgentResult:
        definitions, validators = await self._load_tools()
        prompt = build_system_prompt(tools_available=bool(definitions))
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": message}]
        executed = 0
        events = []
        seen_call_ids = set()
        for iteration in range(1, self.settings.max_iterations + 1):
            reply = await self.llm.complete(messages, definitions)
            logger.info("agent_round iteration=%d requested_tools=%d", iteration, len(reply.tool_calls))
            if not reply.tool_calls:
                return AgentResult(content=reply.content, iterations=iteration, tool_calls=executed, tool_events=events)
            if len(reply.tool_calls) > self.settings.max_tool_calls_per_iteration:
                raise AgentError("tool_call_limit")
            if any(call.id in seen_call_ids for call in reply.tool_calls):
                raise AgentError("invalid_llm_response")
            # Reserve a round for a final answer; do not execute writes at exhaustion.
            if iteration == self.settings.max_iterations:
                raise AgentError("iteration_limit")
            messages.append(reply.as_history_message())
            # Multiple calls are processed in model order, without a fixed business flow.
            for call in reply.tool_calls:
                seen_call_ids.add(call.id)
                result, event = await self._execute_tool(call, validators)
                events.append(event)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                executed += 1
        raise AgentError("iteration_limit")

    async def _execute_tool(
        self, call: ToolCall, validators: dict[str, Draft202012Validator],
    ) -> tuple[str, ToolExecutionEvent]:
        started = perf_counter()
        outcome = "ok"
        result: dict[str, Any]
        if call.function.name not in validators:
            outcome = "unknown_tool"
        else:
            try:
                arguments = json.loads(call.function.arguments)
                json.dumps(arguments, allow_nan=False)
                if not isinstance(arguments, dict) or not validators[call.function.name].is_valid(arguments):
                    raise ValueError
            except Exception:
                outcome = "invalid_tool_arguments"
            if outcome == "ok":
                try:
                    async with asyncio.timeout(self.settings.tool_timeout_seconds):
                        result = await self.tools.call_tool(call.function.name, arguments)
                    if not isinstance(result, dict):
                        raise ValueError
                    content = json.dumps(result, ensure_ascii=False, allow_nan=False)
                    if len(content) > 100000:
                        raise ValueError
                except TimeoutError:
                    outcome = "tool_timeout"
                except ToolExecutionError as error:
                    outcome = error.code
                except Exception:
                    outcome = "tool_execution_failed"
        if outcome != "ok":
            result = {"error": {"code": outcome, "message": TOOL_ERROR_MESSAGES.get(
                outcome, "Tool execution was not confirmed; do not invent a result.",
            )}}
            content = json.dumps(result)
        duration_ms = round((perf_counter() - started) * 1000, 1)
        # Only fixed capability names may enter logs; never args, bodies, or user text.
        safe_name = call.function.name if call.function.name in {
            "buscar_produtos", "detalhar_produto", "criar_requisicao",
        } else "other_tool"
        result_count = None
        if outcome == "ok" and "error" not in result:
            values = result.get("products", result.get("items"))
            if isinstance(values, list):
                result_count = len(values)
            elif isinstance(result.get("product"), dict):
                result_count = 1
        logger.info("agent_tool name=%s outcome=%s result_count=%s duration_ms=%.1f", safe_name,
                    "tool_error" if "error" in result and outcome == "ok" else outcome, result_count, duration_ms)
        event = ToolExecutionEvent(
            name=call.function.name if call.function.name in validators else "unknown_tool",
            outcome=outcome, result=result if outcome == "ok" else None, duration_ms=duration_ms,
        )
        return content, event


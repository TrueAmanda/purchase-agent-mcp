"""Official MCP adapter. Discovery supplies schemas; the LLM supplies call order."""

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import hashlib
import logging
import re
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from app.core.agent_errors import AgentError, ToolExecutionError
from app.core.mcp_config import MCPSettings
from app.schemas.agent import ToolDefinition, ToolExecutionResult
from app.services.purchase_policy import PurchasePolicy
from app.services.purchase_intent import PurchaseIntent
from app.services.tool_validation import tool_validator as _validator

logger = logging.getLogger(__name__)

# Capability boundary, not a sequence: schemas are always discovered over MCP.
PURCHASE_TOOLS = frozenset({"buscar_produtos", "detalhar_produto", "criar_requisicao"})
SAFE_TOOL_ERRORS = {
    "api_timeout": "DummyJSON timed out. No catalog result was confirmed.",
    "api_unavailable": "DummyJSON is unavailable. Try again later.",
    "api_http_error": "DummyJSON returned an HTTP error.",
    "invalid_response": "DummyJSON returned invalid product data.",
    "product_not_found": "The product does not exist in DummyJSON (404).",
    "database_unavailable": "Purchase storage is unavailable; creation is unconfirmed.",
    "database_error": "Purchase storage failed; creation is unconfirmed.",
    "idempotency_conflict": "This key was already used for different purchase data.",
    "invalid_amount": "The catalog amount exceeds supported precision.",
    "budget_exceeded": "Current catalog prices exceed the user's total or per-unit limit. Nothing was saved. Choose an eligible option without changing the limits.",
    "purchase_constraints_violated": "The items violate the requested quantity/category, stock or minimum order. Nothing was saved. Correct the selection without relaxing user constraints.",
}


def _timed_out(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(_timed_out(child) for child in error.exceptions)
    return isinstance(error, (TimeoutError, httpx.TimeoutException)) or (
        isinstance(error, McpError) and error.error.code == -32001
    )


class MCPToolProvider:
    def __init__(self, settings: MCPSettings, http_client: httpx.AsyncClient | None = None, *, intent_key: UUID | None = None):
        self.settings = settings
        self.http_client = http_client
        self._tools = {}
        self._write_keys = {}
        self._suggested_write_key = str(uuid4())
        self._purchase_policy = None
        self._intent = None
        self._client_intent_key = intent_key

    def bind_intent(self, intent: PurchaseIntent) -> None:
        self._intent = intent
        if not intent.started and self._client_intent_key is not None:
            intent.key = self._client_intent_key
            intent.client_key = True

    def bind_purchase_context(self, message: str, history: list[dict]) -> str:
        if self._intent is None:
            # Identical independent requests reuse a durable SQL idempotency key.
            # A caller can explicitly identify a distinct intent via Idempotency-Key.
            self._intent = PurchaseIntent(policy=PurchasePolicy.from_messages(history, message))
            source = [entry["content"] for entry in history if entry.get("role") == "user"] + [message.strip()]
            self._intent.source_hash = hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()
            if self._client_intent_key is not None:
                self._intent.key = self._client_intent_key
                self._intent.client_key = True
        else:
            self._intent.policy.update(message)
            self._intent.source_hash = hashlib.sha256((self._intent.source_hash + json.dumps([message.strip()], ensure_ascii=False)).encode()).hexdigest()
        if not self._intent.key_bound and not self._intent.client_key:
            # Include user context, so a shared greeting does not conflate purchases.
            # Identical initial requests also deduplicate after a lost conversation ID.
            self._intent.key = uuid5(NAMESPACE_URL, "purchase-agent-mcp:" + self._intent.source_hash)
        self._intent.started = True
        self._purchase_policy = self._intent.policy
        return self._purchase_policy.prompt()

    @asynccontextmanager
    async def _session(self):
        # All SDK cancel scopes enter/exit in the same task, including on timeout.
        # The server is stateless; reconnect per operation, never retry a write.
        async with asyncio.timeout(self.settings.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds, trust_env=False, follow_redirects=False,
            ) as owned_http:
                async with streamable_http_client(
                    self.settings.server_url, http_client=self.http_client or owned_http,
                ) as (read, write, _):
                    async with ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=self.settings.timeout_seconds),
                    ) as session:
                        await session.initialize()
                        yield session

    async def list_tools(self) -> list[ToolDefinition]:
        self._tools = {}
        try:
            async with self._session() as session:
                cursor = None
                cursors = set()
                definitions = []
                for _ in range(10):
                    page = await session.list_tools(cursor=cursor)
                    for tool in page.tools:
                        if tool.name not in PURCHASE_TOOLS:
                            continue
                        if tool.name in self._tools or not tool.outputSchema:
                            raise ValueError("Invalid tool catalog")
                        description = tool.description or tool.name
                        if tool.name == "criar_requisicao":
                            description += (
                                " For a NEW purchase intent without a previous key in conversation history, "
                                f"use this valid idempotency_key: {self._suggested_write_key}. "
                                "For retries, reuse the original key from history instead. This is not a requisition number."
                            )
                        else:
                            description += (
                                " Catalog prices are denominated in USD by project policy, without conversion. "
                                "Requests in other currencies are outside the supported scope."
                            )
                        definition = ToolDefinition(
                            name=tool.name, description=description,
                            parameters=tool.inputSchema,
                        )
                        self._tools[tool.name] = (_validator(tool.inputSchema), _validator(tool.outputSchema))
                        definitions.append(definition)
                    cursor = page.nextCursor
                    if not cursor:
                        logger.info("mcp_discovery outcome=ok tool_count=%d", len(definitions))
                        return definitions
                    if cursor in cursors:
                        raise ValueError("Repeated catalog page")
                    cursors.add(cursor)
                raise ValueError("Too many catalog pages")
        except Exception:
            self._tools = {}
            raise AgentError("mcp_unavailable") from None

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if name not in self._tools:
            raise ToolExecutionError(code="unknown_tool", invoked=False)
        inputs, outputs = self._tools[name]
        try:
            inputs.validate(arguments)
        except Exception:
            raise ToolExecutionError(code="invalid_tool_arguments", invoked=False) from None
        if name == "criar_requisicao":
            arguments = deepcopy(arguments)
            request = arguments["request"]
            if self._purchase_policy is not None:
                if not self._purchase_policy.authorized:
                    return ToolExecutionResult({"error": {"code": "purchase_not_authorized", "message":
                        "No write was attempted. The user has not authorized creation, or explicitly prohibited it. Research remains available."}}, invoked=False)
                if (self._purchase_policy.clarification or self._purchase_policy.limits.get("quantity", 0) <= 0
                        or not self._purchase_policy.target_known):
                    return ToolExecutionResult({"error": {"code": "purchase_constraints_unclear", "message":
                        "No write was attempted. Clarify numeric quantities, USD limits and an explicit official category, exact product name or catalog IDs. Never guess unsupported requirements."}}, invoked=False)
                if self._purchase_policy.requested_ids and not {
                    item["product_id"] for item in request["items"]
                }.issubset(self._purchase_policy.requested_ids):
                    return ToolExecutionResult({"error": {"code": "purchase_constraints_violated", "message":
                        "The selected IDs differ from the user's explicit catalog IDs. Nothing was saved."}}, invoked=False)
                # The model cannot weaken, omit or invent limits extracted from user text.
                if self._purchase_policy.limits:
                    request["constraints"] = dict(self._purchase_policy.limits)
                else:
                    request.pop("constraints", None)
                request["idempotency_key"] = str(self._intent.key)
                if self._intent.attempted_request is not None and request != self._intent.attempted_request:
                    return ToolExecutionResult({"error": {"code": "idempotency_conflict", "message":
                        "This intent already has a write attempt. Do not split it or change its data after confirmation/uncertainty. Check the existing requisition; start a new conversation only for a genuinely new purchase."}}, invoked=False)
                if self._intent.confirmed_result is not None:
                    return ToolExecutionResult({**deepcopy(self._intent.confirmed_result), "replayed": True}, invoked=False)
                try:
                    inputs.validate(arguments)
                except Exception:
                    raise ToolExecutionError(code="invalid_tool_arguments", invoked=False) from None
            intent = json.dumps({key: value for key, value in request.items() if key != "idempotency_key"},
                                sort_keys=True, ensure_ascii=False)
            previous_key = self._write_keys.setdefault(intent, request["idempotency_key"])
            if previous_key != request["idempotency_key"]:
                return ToolExecutionResult({"error": {"code": "idempotency_conflict", "message":
                    "Retry the same purchase data with the original idempotency key, never a new key."}}, invoked=False)
        invoked = False
        try:
            async with self._session() as session:
                if name == "criar_requisicao" and self._intent is not None:
                    self._intent.key_bound = True
                    self._intent.attempted_request = deepcopy(arguments["request"])
                invoked = True
                result = await session.call_tool(name, arguments=arguments)
        except TimeoutError:
            raise ToolExecutionError(code="tool_timeout", invoked=invoked) from None
        except asyncio.CancelledError as error:
            # Preserve the original cancellation object so asyncio.timeout keeps working.
            error.tool_invoked = invoked
            raise
        except Exception as error:
            raise ToolExecutionError(
                code="tool_timeout" if _timed_out(error) else "mcp_unavailable", invoked=invoked,
            ) from None
        if result.isError:
            # MCP v1 ToolError is text. Extract only known codes, never forward bodies.
            error_text = " ".join(block.text for block in result.content if block.type == "text")
            for code, message in SAFE_TOOL_ERRORS.items():
                if re.search(r"\b" + code + r":", error_text):
                    if self._intent is not None and code in {
                        "api_timeout", "api_unavailable", "api_http_error", "invalid_response",
                        "product_not_found", "invalid_amount", "budget_exceeded", "purchase_constraints_violated",
                    } and name == "criar_requisicao":
                        # These failures occur before the write transaction.
                        self._intent.attempted_request = None
                    return {"error": {"code": code, "message": message}}
            raise ToolExecutionError()
        try:
            data = result.structuredContent
            if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False)) > 100000:
                raise ValueError
            outputs.validate(data)
            if name == "criar_requisicao" and self._intent is not None:
                self._intent.confirmed_result = deepcopy(data)
            if self._purchase_policy is not None and name in {"buscar_produtos", "detalhar_produto"}:
                return self._purchase_policy.with_budget_checks(data)
            return data
        except Exception:
            raise ToolExecutionError(code="invalid_tool_result") from None

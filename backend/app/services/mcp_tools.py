"""Official MCP adapter. Discovery supplies schemas; the LLM supplies call order."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import logging
import re
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from referencing import Registry

from app.core.agent_errors import AgentError, ToolExecutionError
from app.core.mcp_config import MCPSettings
from app.schemas.agent import ToolDefinition

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
}


def _validator(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=Registry(), format_checker=FormatChecker())


def _timed_out(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(_timed_out(child) for child in error.exceptions)
    return isinstance(error, (TimeoutError, httpx.TimeoutException)) or (
        isinstance(error, McpError) and error.error.code == -32001
    )


class MCPToolProvider:
    def __init__(self, settings: MCPSettings, http_client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.http_client = http_client
        self._tools = {}
        self._write_keys = {}
        self._suggested_write_key = str(uuid4())

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
                                " For this execution, use this valid UUID as idempotency_key for the first purchase "
                                f"intent: {self._suggested_write_key}. Reuse it on retries; it is not a requisition number."
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
            raise ToolExecutionError(code="unknown_tool")
        inputs, outputs = self._tools[name]
        try:
            inputs.validate(arguments)
        except Exception:
            raise ToolExecutionError(code="invalid_tool_arguments") from None
        if name == "criar_requisicao":
            request = arguments["request"]
            intent = json.dumps({key: value for key, value in request.items() if key != "idempotency_key"},
                                sort_keys=True, ensure_ascii=False)
            previous_key = self._write_keys.setdefault(intent, request["idempotency_key"])
            if previous_key != request["idempotency_key"]:
                return {"error": {"code": "idempotency_conflict", "message":
                    "Retry the same purchase data with the original idempotency key, never a new key."}}
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments=arguments)
        except TimeoutError:
            raise ToolExecutionError(code="tool_timeout") from None
        except Exception as error:
            raise ToolExecutionError(code="tool_timeout" if _timed_out(error) else "mcp_unavailable") from None
        if result.isError:
            # MCP v1 ToolError is text. Extract only known codes, never forward bodies.
            error_text = " ".join(block.text for block in result.content if block.type == "text")
            for code, message in SAFE_TOOL_ERRORS.items():
                if re.search(r"\b" + code + r":", error_text):
                    return {"error": {"code": code, "message": message}}
            raise ToolExecutionError()
        try:
            data = result.structuredContent
            if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False)) > 100000:
                raise ValueError
            outputs.validate(data)
            return data
        except Exception:
            raise ToolExecutionError(code="invalid_tool_result") from None

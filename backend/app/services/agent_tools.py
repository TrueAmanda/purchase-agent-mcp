"""A future MCP adapter implements this interface; no MCP imports are needed."""

from typing import Any, Protocol

from app.schemas.agent import ToolDefinition


class ToolProvider(Protocol):
    async def list_tools(self) -> list[ToolDefinition]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return JSON-compatible data; raise ToolExecutionError on execution failure."""
        ...


class EmptyToolProvider:
    """Default until the real MCP adapter is connected in the next stage."""

    async def list_tools(self) -> list[ToolDefinition]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise LookupError("No tools configured.")


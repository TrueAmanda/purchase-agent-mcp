"""Tool-provider boundary shared by the MCP adapter and offline test doubles."""

from typing import Any, Protocol

from app.schemas.agent import ToolDefinition


class ToolProvider(Protocol):
    async def list_tools(self) -> list[ToolDefinition]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return JSON-compatible data; raise ToolExecutionError on execution failure."""
        ...


class EmptyToolProvider:
    """Explicit offline/no-tools provider; production entry points inject MCP."""

    async def list_tools(self) -> list[ToolDefinition]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise LookupError("No tools configured.")


"""Internal, provider-compatible tool and agent contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(min_length=1, max_length=4096)
    parameters: dict[str, Any]

    def as_openrouter_tool(self) -> dict[str, Any]:
        return {"type": "function", "function": self.model_dump()}


class ToolFunction(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(max_length=64000)


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    type: Literal["function"]
    function: ToolFunction


class AssistantMessage(BaseModel):
    role: Literal["assistant"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=32)
    # Required by some reasoning models when continuing a tool calling turn.
    reasoning_details: list[dict[str, Any]] | None = None

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_absent_calls(cls, value):
        return [] if value is None else value

    def as_history_message(self) -> dict[str, Any]:
        message = self.model_dump(exclude_none=True)
        if not self.tool_calls:
            message.pop("tool_calls", None)
        return message


class ToolExecutionEvent(BaseModel):
    """Executor evidence, never parsed from assistant text or exposed as raw trace."""

    name: str
    outcome: Literal[
        "ok", "unknown_tool", "invalid_tool_arguments", "tool_timeout", "tool_execution_failed",
        "mcp_unavailable", "invalid_tool_result",
        "tool_cancelled",
    ]
    invoked: bool = True
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    result: dict[str, Any] | None = Field(default=None, exclude=True, repr=False)


class ToolExecutionResult(dict[str, Any]):
    """JSON-compatible result with private adapter evidence, never a JSON field."""

    def __init__(self, data: dict[str, Any], *, invoked: bool):
        super().__init__(data)
        self.invoked = invoked


class AgentResult(BaseModel):
    content: str
    iterations: int
    tool_calls: int
    # Internal evidence keeps the existing CLI serialization contract unchanged.
    tool_events: list[ToolExecutionEvent] = Field(default_factory=list, exclude=True, repr=False)
    conversation_messages: list[dict[str, Any]] = Field(default_factory=list, exclude=True, repr=False)


"""Public agent failures never include provider bodies or exception details."""

MESSAGES = {
    "invalid_configuration": "Invalid agent configuration. Check the OpenRouter environment variables.",
    "invalid_message": "Provide a nonempty message with at most 20000 characters.",
    "llm_timeout": "The language model request timed out.",
    "llm_unavailable": "The language model provider is unavailable.",
    "llm_authentication": "The language model provider rejected the credentials.",
    "llm_credits": "The language model account has insufficient credits.",
    "llm_rate_limit": "The language model provider rate limit was reached.",
    "llm_http_error": "The language model provider rejected the request.",
    "invalid_llm_response": "The language model returned an invalid response.",
    "llm_incomplete": "The language model response was interrupted or truncated.",
    "iteration_limit": "The agent reached its iteration limit without a final answer.",
    "tool_call_limit": "The language model requested too many tools in one round.",
    "agent_timeout": "The agent execution time limit was reached.",
    "tool_catalog_error": "The tool catalog could not be loaded or is invalid.",
    "invalid_mcp_configuration": "Invalid MCP connection configuration.",
    "mcp_unavailable": "The MCP server is unavailable or returned an invalid tool catalog.",
}


class AgentError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


class ToolExecutionError(Exception):
    """Adapters signal failure without forwarding external messages or payloads."""

    def __init__(self, message: str = "Tool execution was not confirmed.", *, code: str = "tool_execution_failed"):
        self.code = code if code in {
            "unknown_tool", "invalid_tool_arguments", "tool_timeout", "mcp_unavailable", "invalid_tool_result",
        } else "tool_execution_failed"
        super().__init__("Tool execution was not confirmed.")


TOOL_ERROR_MESSAGES = {
    "invalid_tool_arguments": (
        "Arguments do not match the advertised JSON schema. Check required fields and types; "
        "idempotency_key must be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx), not a descriptive label. "
        "Items accept only product_id and quantity, never prices. Correct the arguments before retrying."
    ),
    "mcp_unavailable": "The MCP server is unavailable. Execution is unconfirmed; do not retry writes with a new key.",
    "tool_timeout": "The tool timed out. Completion is uncertain; do not claim failure or retry with a new key.",
}


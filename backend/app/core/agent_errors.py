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
}


class AgentError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


class ToolExecutionError(Exception):
    """Adapters signal failure without forwarding external messages or payloads."""


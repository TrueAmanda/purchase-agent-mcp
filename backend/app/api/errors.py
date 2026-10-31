"""Map internal failures to stable HTTP errors without provider/SQL payloads."""

from fastapi import HTTPException

from app.core.agent_errors import AgentError
from app.services.purchase_requests import PurchaseAPIError


def http_error(error: AgentError | PurchaseAPIError) -> HTTPException:
    if isinstance(error, PurchaseAPIError):
        status, message = error.status_code, error.message
    else:
        if error.code in {"llm_timeout", "agent_timeout"}:
            status = 504
        elif error.code in {
            "invalid_configuration", "llm_authentication", "llm_credits", "llm_rate_limit",
            "llm_unavailable", "tool_catalog_error", "invalid_mcp_configuration", "mcp_unavailable",
        }:
            status = 503
        elif error.code == "invalid_message":
            status = 422
        else:
            status = 502
        message = str(error)
    return HTTPException(status_code=status, detail={"code": error.code, "message": message})

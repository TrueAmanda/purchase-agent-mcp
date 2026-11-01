"""Map internal failures to stable HTTP errors without provider/SQL payloads."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.core.agent_errors import AgentError
from app.services.purchase_requests import PurchaseAPIError
from app.schemas.purchase_requests import APIErrorDetail, APIErrorResponse
from app.services.tool_trace import build_tool_trace


def http_error(error: AgentError | PurchaseAPIError) -> HTTPException:
    if isinstance(error, PurchaseAPIError):
        status, message = error.status_code, error.message
    else:
        if error.code in {"llm_timeout", "agent_timeout"}:
            status = 504
        elif error.code in {
            "invalid_configuration", "llm_authentication", "llm_credits", "llm_rate_limit",
            "llm_unavailable", "tool_catalog_error", "invalid_mcp_configuration", "mcp_unavailable",
            "invalid_conversation_configuration", "conversation_capacity",
        }:
            status = 503
        elif error.code == "invalid_message":
            status = 422
        elif error.code == "conversation_expired":
            status = 410
        elif error.code == "conversation_busy":
            status = 409
        else:
            status = 502
        message = str(error)
    return HTTPException(status_code=status, detail={"code": error.code, "message": message})


def purchase_error_response(error: AgentError | PurchaseAPIError, *, include_trace: bool) -> JSONResponse:
    failure = http_error(error)
    payload = APIErrorResponse(detail=APIErrorDetail.model_validate(failure.detail))
    if include_trace:
        events = error.tool_events
        payload.tool_trace = build_tool_trace(events) if events is not None else None
    if getattr(error, "conversation_id", None) is not None:
        payload.conversation_id = error.conversation_id
    return JSONResponse(status_code=failure.status_code, content=payload.model_dump(mode="json", exclude_unset=True))

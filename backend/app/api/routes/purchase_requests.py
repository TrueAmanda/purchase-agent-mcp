"""Natural-language orchestration and persisted purchase request listing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import get_agent, get_purchase_service
from app.api.errors import http_error
from app.core.agent_errors import AgentError
from app.schemas.purchase_requests import (
    APIErrorResponse, InputValidationErrorResponse, PurchaseMessageInput, PurchaseMessageResponse, PurchaseRequestListResponse,
)
from app.services.agent import AgentService
from app.services.purchase_requests import PurchaseAPIError, PurchaseRequestService

router = APIRouter(prefix="/requisicoes", tags=["purchase requests"])


@router.post(
    "", response_model=PurchaseMessageResponse, status_code=200,
    summary="Process a natural-language purchase request",
    description=(
        "Runs the purchasing agent. Writes belong exclusively to criar_requisicao. "
        "Created status requires executor evidence and a matching persisted record. "
        "Tools and their schemas are discovered through MCP; the model chooses the call sequence. "
        "200: agent response, empty catalog, or confirmed idempotent replay. 201: confirmed new registration."
    ),
    responses={
        201: {"model": PurchaseMessageResponse, "description": "New registration confirmed in storage"},
        409: {"model": APIErrorResponse, "description": "Idempotency key reused for different purchase data"},
        422: {"model": APIErrorResponse | InputValidationErrorResponse,
              "description": "Invalid input, nonexistent product or unsupported catalog amount"},
        500: {"model": APIErrorResponse, "description": "Persisted data or database query error"},
        502: {"model": APIErrorResponse, "description": "Invalid upstream response or unconfirmed tool operation"},
        503: {"model": APIErrorResponse, "description": "Database, MCP, DummyJSON or LLM unavailable/misconfigured"},
        504: {"model": APIErrorResponse, "description": "Agent, provider or tool timeout; a write may have completed"},
    },
)
async def submit_purchase_request(
    payload: PurchaseMessageInput, response: Response,
    agent: Annotated[AgentService, Depends(get_agent)],
    service: Annotated[PurchaseRequestService, Depends(get_purchase_service)],
) -> PurchaseMessageResponse:
    try:
        result, status = await service.submit(payload.message, agent, payload.include_tool_trace)
    except (AgentError, PurchaseAPIError) as error:
        raise http_error(error) from None
    response.status_code = status
    return result


@router.get(
    "", response_model=PurchaseRequestListResponse,
    summary="List persisted purchase requisitions",
    description="Reads shared SQLAlchemy models, newest first. Missing legacy fields remain null. No LLM is used.",
    responses={
        500: {"model": APIErrorResponse, "description": "Persisted data or database query error"},
        503: {"model": APIErrorResponse, "description": "Database unavailable"},
    },
)
def list_purchase_requests(
    service: Annotated[PurchaseRequestService, Depends(get_purchase_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseRequestListResponse:
    try:
        return service.list_requests(limit, offset)
    except PurchaseAPIError as error:
        raise http_error(error) from None

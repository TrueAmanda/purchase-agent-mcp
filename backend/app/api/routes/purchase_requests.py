"""Natural-language orchestration and persisted purchase request listing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import get_agent, get_conversation_store, get_purchase_service
from app.api.errors import http_error, purchase_error_response
from app.core.agent_errors import AgentError
from app.schemas.purchase_requests import (
    APIErrorResponse, InputValidationErrorResponse, PurchaseMessageInput, PurchaseMessageResponse, PurchaseRequestListResponse,
)
from app.services.agent import AgentService
from app.services.purchase_requests import PurchaseAPIError, PurchaseRequestService
from app.services.conversation_agent import ConversationAgent
from app.services.conversation_store import ConversationStore

router = APIRouter(prefix="/requisicoes", tags=["purchase requests"])


@router.post(
    "", response_model=PurchaseMessageResponse, status_code=200,
    summary="Process a natural-language purchase request",
    description=(
        "Runs the purchasing agent. Writes belong exclusively to criar_requisicao. "
        "Created status requires executor evidence and a matching persisted record. "
        "Tools and their schemas are discovered through MCP; the model chooses the call sequence. "
        "include_tool_trace returns sanitized invocation order, status and duration on responses and execution errors. "
        "Send conversation_id=null to start server-owned context, then the returned UUID to continue; omit for independent requests. "
        "200: agent response, empty catalog, or confirmed idempotent replay. 201: confirmed new registration."
    ),
    responses={
        201: {"model": PurchaseMessageResponse, "description": "New registration confirmed in storage"},
        409: {"model": APIErrorResponse, "description": "Idempotency conflict or another message still processing"},
        410: {"model": APIErrorResponse, "description": "Conversation expired, was evicted or service restarted"},
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
    conversations: Annotated[ConversationStore, Depends(get_conversation_store)],
) -> PurchaseMessageResponse | JSONResponse:
    conversation = None
    try:
        if "conversation_id" in payload.model_fields_set:
            async with conversations.lease(payload.conversation_id) as conversation:
                runner = ConversationAgent(agent, conversation)
                result, status = await service.submit(payload.message, runner, payload.include_tool_trace)
                result.conversation_id = conversation.id
        else:
            result, status = await service.submit(payload.message, agent, payload.include_tool_trace)
    except (AgentError, PurchaseAPIError) as error:
        if conversation is not None:
            error.conversation_id = conversation.id
        return purchase_error_response(error, include_trace=payload.include_tool_trace)
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

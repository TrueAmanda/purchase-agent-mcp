"""Request-scoped application services, with lazy LLM configuration."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
import httpx

from app.api.errors import http_error
from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError
from app.core.mcp_config import MCPSettings
from app.core.conversation_config import ConversationSettings
from app.schemas.purchase_requests import PurchaseMessageInput
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient
from app.services.mcp_tools import MCPToolProvider
from app.services.purchase_requests import PurchaseRequestService
from app.services.conversation_store import ConversationStore


async def get_agent(payload: PurchaseMessageInput, request: Request) -> AsyncIterator[AgentService]:
    # Validate the same body before loading provider configuration; invalid input is 422.
    try:
        settings = AgentSettings.from_environment()
        mcp_settings = MCPSettings.from_environment()
        key = request.headers.get("Idempotency-Key")
        intent_key = UUID(key) if key else None
    except ValueError:
        raise http_error(AgentError("invalid_message")) from None
    except AgentError as error:
        raise http_error(error) from None
    active = getattr(request.app.state, "active_agents", 0)
    if active >= settings.max_concurrent_runs:
        raise http_error(AgentError("agent_busy"))
    # No await between check/increment; scoped to this application's event loop.
    request.app.state.active_agents = active + 1
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            yield AgentService(OpenRouterClient(settings, client), settings, MCPToolProvider(mcp_settings, intent_key=intent_key))
    finally:
        request.app.state.active_agents -= 1


def get_purchase_service(request: Request) -> PurchaseRequestService:
    return PurchaseRequestService(request.app.state.session_factory)


async def get_conversation_store(request: Request) -> ConversationStore:
    if not hasattr(request.app.state, "conversations"):
        try:
            request.app.state.conversations = ConversationStore(ConversationSettings.from_environment())
        except AgentError as error:
            raise http_error(error) from None
    return request.app.state.conversations

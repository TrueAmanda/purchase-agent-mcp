"""Request-scoped application services, with lazy LLM configuration."""

from collections.abc import AsyncIterator

from fastapi import Request
import httpx

from app.api.errors import http_error
from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError
from app.schemas.purchase_requests import PurchaseMessageInput
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient
from app.services.purchase_requests import PurchaseRequestService


async def get_agent(payload: PurchaseMessageInput) -> AsyncIterator[AgentService]:
    # Validate the same body before loading provider configuration; invalid input is 422.
    try:
        settings = AgentSettings.from_environment()
    except AgentError as error:
        raise http_error(error) from None
    async with httpx.AsyncClient(trust_env=False) as client:
        yield AgentService(OpenRouterClient(settings, client), settings)


def get_purchase_service(request: Request) -> PurchaseRequestService:
    return PurchaseRequestService(request.app.state.session_factory)

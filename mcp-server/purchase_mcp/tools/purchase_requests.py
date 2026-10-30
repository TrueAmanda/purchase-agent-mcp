"""Mutating MCP tool; callers choose when to invoke it and supply a stable retry key."""

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from purchase_mcp.schemas.purchase_requests import PurchaseRequestInput, PurchaseRequestResult
from purchase_mcp.services.product_client import ProductAPIError
from purchase_mcp.services.purchase_errors import PurchaseRequestError
from purchase_mcp.services.purchase_requests import PurchaseRequestService


def register_purchase_tools(server: FastMCP) -> None:
    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ))
    async def criar_requisicao(request: PurchaseRequestInput, ctx: Context) -> PurchaseRequestResult:
        """Persist a purchase request when creation is intended. Reuse the same idempotency_key on retries.

        Supply product IDs and quantities, never prices. Product prices are verified with DummyJSON.
        No prior search/detail tool call is required. This tool writes to PostgreSQL.
        """
        dependencies = ctx.request_context.lifespan_context
        repository = dependencies["purchase_repository"]
        if repository is None:
            raise ToolError("database_unavailable: Purchase persistence is not configured.")
        try:
            return await PurchaseRequestService(dependencies["product_client"], repository).create(request)
        except (ProductAPIError, PurchaseRequestError) as error:
            raise ToolError(str(error)) from None

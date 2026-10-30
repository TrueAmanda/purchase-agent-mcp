"""MCP registration for the product catalog tools."""

from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from purchase_mcp.schemas.products import ProductDetailResult, ProductSearchResult
from purchase_mcp.services.product_client import ProductAPIError, ProductClient


def register_product_tools(server: FastMCP) -> None:
    @server.tool()
    async def buscar_produtos(termo: str, ctx: Context) -> ProductSearchResult:
        """Search DummyJSON products by a nonempty term; prices have no assumed currency."""
        client: ProductClient = ctx.request_context.lifespan_context["product_client"]
        try:
            return await client.search_products(termo)
        except ProductAPIError as error:
            raise ToolError(str(error)) from None

    @server.tool()
    async def detalhar_produto(
        id: Annotated[int, Field(gt=0, strict=True)], ctx: Context
    ) -> ProductDetailResult:
        """Get a DummyJSON product by positive integer ID; report missing products as errors."""
        client: ProductClient = ctx.request_context.lifespan_context["product_client"]
        try:
            return await client.get_product(id)
        except ProductAPIError as error:
            raise ToolError(str(error)) from None

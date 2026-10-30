"""MCP registration for the product search tool."""

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from purchase_mcp.schemas.products import ProductSearchResult
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

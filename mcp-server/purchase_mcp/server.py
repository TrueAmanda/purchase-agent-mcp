"""Streamable HTTP MCP server with request-scoped product API clients."""

import logging
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from purchase_mcp.config import Settings
from purchase_mcp.services.product_client import ProductClient, create_http_client
from purchase_mcp.tools.product_search import register_product_tools


def create_server() -> FastMCP:
    # HTTPX logs full request URLs at INFO, which would expose search terms.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_environment()

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        # In stateless mode the SDK enters this lifespan for each MCP request.
        async with create_http_client(settings.product_api_timeout_seconds) as http_client:
            yield {"product_client": ProductClient(http_client)}

    server = FastMCP(
        "purchase-agent-mcp",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8001")),
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
    )
    register_product_tools(server)

    @server.custom_route("/health", methods=["GET"])
    async def get_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server


server = create_server()


def main() -> None:
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()

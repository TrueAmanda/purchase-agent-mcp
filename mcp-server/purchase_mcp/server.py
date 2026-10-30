"""Streamable HTTP MCP server with request-scoped product API clients."""

import logging
import os
from contextlib import asynccontextmanager

from anyio import to_thread
from mcp.server.fastmcp import FastMCP
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
import uvicorn

from purchase_storage.config import Settings as DatabaseSettings
from purchase_storage.database import build_engine, initialize_database

from purchase_mcp.config import Settings
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.services.product_client import ProductClient, create_http_client
from purchase_mcp.tools.product_search import register_product_tools
from purchase_mcp.tools.purchase_requests import register_purchase_tools


def create_server(repository: PurchaseRequestRepository | None = None) -> FastMCP:
    # HTTPX logs full request URLs at INFO, which would expose search terms.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_environment()

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        # In stateless mode the SDK enters this lifespan for each MCP request.
        async with create_http_client(settings.product_api_timeout_seconds) as http_client:
            yield {"product_client": ProductClient(http_client), "purchase_repository": repository}

    server = FastMCP(
        "purchase-agent-mcp",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8001")),
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
    )
    register_product_tools(server)
    register_purchase_tools(server)

    @server.custom_route("/health", methods=["GET"])
    async def get_health(request: Request) -> JSONResponse:
        if repository is not None:
            try:
                await to_thread.run_sync(repository.check_connection)
            except SQLAlchemyError:
                return JSONResponse({"status": "unavailable", "database": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return server


def create_app() -> Starlette:
    """Build an independent HTTP process with its own engine and startup migration."""
    settings = DatabaseSettings.from_environment()
    engine = build_engine(settings)
    repository = PurchaseRequestRepository(sessionmaker(bind=engine, expire_on_commit=False))
    mcp_app = create_server(repository).streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            await to_thread.run_sync(initialize_database, engine, settings)
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            await to_thread.run_sync(engine.dispose)

    return Starlette(routes=[Mount("/", app=mcp_app)], lifespan=lifespan)


def main() -> None:
    uvicorn.run(create_app(), host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8001")))


if __name__ == "__main__":
    main()

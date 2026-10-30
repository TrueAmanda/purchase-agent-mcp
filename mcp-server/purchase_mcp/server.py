"""MCP entry point; no tools are registered yet."""

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

server = FastMCP(
    "purchase-agent-mcp",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8001")),
    stateless_http=True,
    json_response=True,
)


@server.custom_route("/health", methods=["GET"])
async def get_health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main() -> None:
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()

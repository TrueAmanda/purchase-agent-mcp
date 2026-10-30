"""MCP entry point; no tools are registered yet."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("purchase-agent-mcp", host="0.0.0.0", port=8001)


def main() -> None:
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()

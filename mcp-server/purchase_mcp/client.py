"""Invoke tools over the real MCP protocol without an LLM."""

import argparse
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def search_products(url: str, term: str) -> bool:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Discovered tools:", ", ".join(tool.name for tool in tools.tools))
            if not any(tool.name == "buscar_produtos" for tool in tools.tools):
                raise RuntimeError("buscar_produtos was not discovered on this MCP server.")
            result = await session.call_tool("buscar_produtos", arguments={"termo": term})
            print(result.model_dump_json(indent=2, exclude_none=True))
            return not result.isError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("term", help="Product search term.")
    parser.add_argument("--url", default=os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp"))
    arguments = parser.parse_args()
    try:
        succeeded = asyncio.run(search_products(arguments.url, arguments.term))
    except Exception:
        parser.exit(1, "Unable to complete the MCP request. Check the server URL and availability.\n")
    if not succeeded:
        parser.exit(1, "The MCP tool reported an error; see the result above.\n")


if __name__ == "__main__":
    main()

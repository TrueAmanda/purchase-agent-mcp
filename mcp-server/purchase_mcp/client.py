"""Invoke tools over the real MCP protocol without an LLM."""

import argparse
import asyncio
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def invoke_tool(url: str, tool_name: str, arguments: dict[str, Any]) -> bool:
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Discovered tools:", ", ".join(tool.name for tool in tools.tools))
            if not any(tool.name == tool_name for tool in tools.tools):
                raise RuntimeError(f"{tool_name} was not discovered on this MCP server.")
            result = await session.call_tool(tool_name, arguments=arguments)
            print(result.model_dump_json(indent=2, exclude_none=True))
            return not result.isError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("term", nargs="?", help="Product search term.")
    parser.add_argument("--id", type=int, dest="product_id", help="Fetch details for a product ID.")
    parser.add_argument("--url", default=os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp"))
    arguments = parser.parse_args()
    if (arguments.term is None) == (arguments.product_id is None):
        parser.error("Provide either a search term or --id, but not both.")
    if arguments.product_id is not None:
        tool_name, tool_arguments = "detalhar_produto", {"id": arguments.product_id}
    else:
        tool_name, tool_arguments = "buscar_produtos", {"termo": arguments.term}
    try:
        succeeded = asyncio.run(invoke_tool(arguments.url, tool_name, tool_arguments))
    except Exception:
        parser.exit(1, "Unable to complete the MCP request. Check the server URL and availability.\n")
    if not succeeded:
        parser.exit(1, "The MCP tool reported an error; see the result above.\n")


if __name__ == "__main__":
    main()

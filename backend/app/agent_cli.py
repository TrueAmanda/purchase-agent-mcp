"""Manual OpenRouter agent with real MCP tools (writes are possible)."""

import argparse
import asyncio
import logging

import httpx

from app.core.agent_config import AgentSettings
from app.core.agent_errors import AgentError
from app.core.mcp_config import MCPSettings
from app.services.agent import AgentService
from app.services.openrouter import OpenRouterClient
from app.services.mcp_tools import MCPToolProvider


async def run(message: str) -> int:
    try:
        settings = AgentSettings.from_environment()
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await AgentService(
                OpenRouterClient(settings, client), settings, MCPToolProvider(MCPSettings.from_environment()),
            ).run(message)
        print(result.model_dump_json(indent=2))
        return 0
    except AgentError as error:
        print(f"{error.code}: {error}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the purchase agent with OpenRouter and MCP tools.")
    parser.add_argument("message")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return asyncio.run(run(args.message))


if __name__ == "__main__":
    raise SystemExit(main())

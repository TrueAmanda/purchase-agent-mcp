"""Trusted server configuration, never derived from model arguments."""

import os

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.agent_errors import AgentError


class MCPSettings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    server_url: str = "http://localhost:8001/mcp"
    timeout_seconds: float = Field(default=15, gt=0, le=300)

    @field_validator("server_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("Invalid MCP server URL.")
        return str(url)

    @classmethod
    def from_environment(cls) -> "MCPSettings":
        try:
            return cls(**{
                field: os.environ[name] for field, name in {
                    "server_url": "MCP_SERVER_URL", "timeout_seconds": "MCP_TIMEOUT_SECONDS",
                }.items() if name in os.environ
            })
        except (ValueError, httpx.InvalidURL):
            raise AgentError("invalid_mcp_configuration") from None

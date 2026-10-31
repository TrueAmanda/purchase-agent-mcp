"""Lazy agent settings, independent of database and FastAPI startup."""

import os

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.agent_errors import AgentError


class AgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    api_key: SecretStr = Field(repr=False)
    model: str = Field(min_length=1, max_length=255)
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int = Field(default=2048, gt=0, le=128000)
    max_iterations: int = Field(default=8, gt=0, le=32)
    max_tool_calls_per_iteration: int = Field(default=8, gt=0, le=32)
    tool_timeout_seconds: float = Field(default=15, gt=0, le=300)
    run_timeout_seconds: float = Field(default=120, gt=0, le=1800)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value().strip()
        if not token or not token.isascii() or any(ord(character) < 33 or ord(character) > 126 for character in token):
            raise ValueError("Invalid API key.")
        return SecretStr(token)

    @field_validator("model", mode="before")
    @classmethod
    def strip_model(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        url = httpx.URL(value)
        if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("Provide an HTTPS base URL without credentials, query or fragment.")
        return str(url).rstrip("/")

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        fields = {
            "api_key": "OPENROUTER_API_KEY", "model": "OPENROUTER_MODEL",
            "base_url": "OPENROUTER_BASE_URL", "timeout_seconds": "OPENROUTER_TIMEOUT_SECONDS",
            "temperature": "OPENROUTER_TEMPERATURE", "max_tokens": "OPENROUTER_MAX_TOKENS",
            "max_iterations": "AGENT_MAX_ITERATIONS",
            "max_tool_calls_per_iteration": "AGENT_MAX_TOOL_CALLS_PER_ITERATION",
            "tool_timeout_seconds": "AGENT_TOOL_TIMEOUT_SECONDS",
            "run_timeout_seconds": "AGENT_RUN_TIMEOUT_SECONDS",
        }
        try:
            return cls(**{field: os.environ[name] for field, name in fields.items() if name in os.environ})
        except (ValueError, httpx.InvalidURL):
            raise AgentError("invalid_configuration") from None


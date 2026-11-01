"""Finite, process-local conversation retention, independent of provider secrets."""

import os

from pydantic import BaseModel, ConfigDict, Field

from app.core.agent_errors import AgentError


class ConversationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    ttl_seconds: float = Field(default=1800, gt=0, le=86400)
    max_conversations: int = Field(default=100, gt=0, le=1000)
    max_turns: int = Field(default=6, gt=0, le=30)
    max_history_characters: int = Field(default=200000, ge=1000, le=2000000)

    @classmethod
    def from_environment(cls) -> "ConversationSettings":
        names = {
            "ttl_seconds": "CONVERSATION_TTL_SECONDS", "max_conversations": "CONVERSATION_MAX_COUNT",
            "max_turns": "CONVERSATION_MAX_TURNS", "max_history_characters": "CONVERSATION_MAX_HISTORY_CHARACTERS",
        }
        try:
            return cls(**{field: os.environ[name] for field, name in names.items() if name in os.environ})
        except ValueError:
            raise AgentError("invalid_conversation_configuration") from None

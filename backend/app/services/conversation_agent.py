"""Bind a request-scoped agent to server-owned history; never accept client facts."""

import asyncio

from app.core.agent_errors import AgentError
from app.services.agent import AgentService
from app.services.conversation_store import Conversation


class ConversationAgent:
    def __init__(self, agent: AgentService, conversation: Conversation):
        self.agent, self.conversation = agent, conversation

    async def run(self, message):
        try:
            result = await self.agent.run(message, history=self.conversation.history())
        except (AgentError, asyncio.CancelledError) as error:
            self.conversation.append(getattr(error, "conversation_messages", []))
            raise
        self.conversation.append(result.conversation_messages)
        return result

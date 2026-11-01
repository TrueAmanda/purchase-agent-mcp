"""Bounded in-memory conversations: opaque IDs, complete turns and one writer."""

from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import json
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from app.core.agent_errors import AgentError
from app.core.conversation_config import ConversationSettings
from app.services.purchase_intent import PurchaseIntent


@dataclass(repr=False)
class Conversation:
    id: UUID
    touched_at: float
    settings: ConversationSettings = field(repr=False)
    turns: list[list[dict[str, Any]]] = field(default_factory=list, repr=False)
    busy: bool = False
    retained: bool = True
    intent: PurchaseIntent = field(default_factory=PurchaseIntent, repr=False)

    def history(self) -> list[dict[str, Any]]:
        return deepcopy([message for turn in self.turns for message in turn])

    def append(self, turn: list[dict[str, Any]]) -> None:
        if not turn:
            return
        self.turns.append(deepcopy(turn))
        # Drop whole turns, never orphan tool replies or fabricate a summary.
        while len(self.turns) > self.settings.max_turns:
            self.turns.pop(0)
        while len(json.dumps(self.turns, ensure_ascii=False)) > self.settings.max_history_characters:
            self.turns.pop(0)
            if not self.turns:
                # The next message must get an explicit expiry, not silently lose context.
                self.retained = False
                break


class ConversationStore:
    def __init__(self, settings: ConversationSettings | None = None, *, clock: Callable[[], float] = monotonic):
        self.settings = settings or ConversationSettings()
        self.clock = clock
        self._entries: OrderedDict[UUID, Conversation] = OrderedDict()

    @asynccontextmanager
    async def lease(self, identifier: UUID | None):
        # These mutations run without awaits on the application's event loop.
        now = self.clock()
        for key, value in list(self._entries.items()):
            if not value.busy and (not value.retained or now - value.touched_at >= self.settings.ttl_seconds):
                del self._entries[key]
        if identifier is None:
            if len(self._entries) >= self.settings.max_conversations:
                idle = next((key for key, value in self._entries.items() if not value.busy), None)
                if idle is None:
                    raise AgentError("conversation_capacity")
                del self._entries[idle]
            entry = Conversation(uuid4(), now, self.settings)
            self._entries[entry.id] = entry
        else:
            entry = self._entries.get(identifier)
            if entry is None:
                raise AgentError("conversation_expired")
        if entry.busy:
            raise AgentError("conversation_busy")
        entry.busy = True
        try:
            yield entry
        finally:
            entry.busy = False
            entry.touched_at = self.clock()
            self._entries.move_to_end(entry.id)
            if not entry.retained:
                del self._entries[entry.id]

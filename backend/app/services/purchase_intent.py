"""Write evidence and constraints survive conversation text eviction."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.services.purchase_policy import PurchasePolicy


@dataclass(repr=False)
class PurchaseIntent:
    key: UUID = field(default_factory=uuid4)
    policy: PurchasePolicy = field(default_factory=PurchasePolicy)
    attempted_request: dict[str, Any] | None = None
    confirmed_result: dict[str, Any] | None = None
    started: bool = False
    source_hash: str = ""
    client_key: bool = False
    key_bound: bool = False

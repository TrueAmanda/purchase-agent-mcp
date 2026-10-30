"""Strict purchase creation input and persisted, price-authoritative output."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonemptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class PurchaseItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=2147483647, strict=True)


class PurchaseRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonemptyText
    requester_name: NonemptyText
    idempotency_key: UUID = Field(description="Stable key for one purchase intent; reuse for every retry.")
    items: list[PurchaseItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_repeated_products(self) -> "PurchaseRequestInput":
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("Use a single item per product ID.")
        return self


class PurchaseItemResult(BaseModel):
    product_id: int
    name: str
    description: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class PurchaseRequestResult(BaseModel):
    request_id: int
    request_number: UUID
    created_at: datetime
    replayed: bool
    currency: str | None
    items: list[PurchaseItemResult]
    total: Decimal

"""Shared purchase request tables; nullable extensions preserve legacy rows."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    __table_args__ = (
        UniqueConstraint("request_number", name="uq_purchase_requests_request_number"),
        UniqueConstraint("idempotency_key", name="uq_purchase_requests_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    requester_name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_number: Mapped[str | None] = mapped_column(String(36), default=lambda: str(uuid4()))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2, asdecimal=True))
    items: Mapped[list["PurchaseRequestItem"]] = relationship(
        back_populates="purchase_request", cascade="all, delete-orphan", passive_deletes=True,
        order_by="PurchaseRequestItem.id",
    )


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchase_item_positive_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_purchase_item_nonnegative_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_request_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(String(128))
    product_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2, asdecimal=True))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(20, 2, asdecimal=True))
    purchase_request: Mapped[PurchaseRequest] = relationship(back_populates="items")

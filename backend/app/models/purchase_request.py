"""Purchase request and related item storage models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    requester_name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), server_default="BRL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    items: Mapped[list["PurchaseRequestItem"]] = relationship(
        back_populates="purchase_request",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2, asdecimal=True))
    purchase_request: Mapped[PurchaseRequest] = relationship(back_populates="items")

"""Register all models before creating database tables."""

from app.models.base import Base
from app.models.purchase_request import PurchaseRequest, PurchaseRequestItem

__all__ = ["Base", "PurchaseRequest", "PurchaseRequestItem"]

"""Create a purchase from upstream prices, without prescribing an agent workflow."""

import hashlib
import json
import logging
from decimal import Decimal, ROUND_HALF_UP

from anyio import to_thread

from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.schemas.purchase_requests import PurchaseItemResult, PurchaseRequestInput, PurchaseRequestResult
from purchase_mcp.services.product_client import ProductClient
from purchase_mcp.services.purchase_errors import PurchaseRequestError

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")
MAX_UNIT_PRICE = Decimal("999999999999.99")
MAX_TOTAL = Decimal("999999999999999999.99")


def request_fingerprint(request: PurchaseRequestInput) -> str:
    data = request.model_dump(mode="json", exclude={"idempotency_key"})
    data["items"] = sorted(data["items"], key=lambda item: item["product_id"])
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PurchaseRequestService:
    def __init__(self, products: ProductClient, repository: PurchaseRequestRepository):
        self.products = products
        self.repository = repository

    async def create(self, request: PurchaseRequestInput) -> PurchaseRequestResult:
        fingerprint = request_fingerprint(request)
        existing = await to_thread.run_sync(self.repository.find_existing, str(request.idempotency_key), fingerprint)
        if existing is not None:
            logger.info("Purchase request completed: replayed=true count=%s", len(existing.items))
            return existing

        items = []
        total = Decimal("0.00")
        # All upstream validation finishes before opening the write transaction.
        for entry in sorted(request.items, key=lambda item: item.product_id):
            product = (await self.products.get_product(entry.product_id)).product
            if product.price > MAX_UNIT_PRICE:
                raise PurchaseRequestError("invalid_amount", "Upstream price exceeds supported storage precision.")
            unit_price = product.price.quantize(CENT, rounding=ROUND_HALF_UP)
            subtotal = (unit_price * entry.quantity).quantize(CENT, rounding=ROUND_HALF_UP)
            total += subtotal
            if total > MAX_TOTAL:
                raise PurchaseRequestError("invalid_amount", "Request total exceeds supported storage precision.")
            items.append(PurchaseItemResult(
                product_id=product.id, name=product.name, description=product.description,
                quantity=entry.quantity, unit_price=unit_price, subtotal=subtotal,
            ))
        result = await to_thread.run_sync(self.repository.create, request, fingerprint, items)
        logger.info("Purchase request completed: replayed=%s count=%s", result.replayed, len(result.items))
        return result

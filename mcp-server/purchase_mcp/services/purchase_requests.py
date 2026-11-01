"""Create a purchase from upstream prices, without prescribing an agent workflow."""

import hashlib
import json
import logging
import unicodedata
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


def normalize_product_name(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.strip().casefold()) if not unicodedata.combining(c))


def request_fingerprint(request: PurchaseRequestInput) -> str:
    # Omitting absent optional constraints preserves fingerprints of legacy requests.
    data = request.model_dump(mode="json", exclude={"idempotency_key"}, exclude_none=True)
    data["items"] = sorted(data["items"], key=lambda item: item["product_id"])
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PurchaseRequestService:
    def __init__(self, products: ProductClient, repository: PurchaseRequestRepository):
        self.products = products
        self.repository = repository

    async def create(self, request: PurchaseRequestInput) -> PurchaseRequestResult:
        constraints = request.constraints
        if constraints and constraints.quantity is not None and sum(item.quantity for item in request.items) != constraints.quantity:
            raise PurchaseRequestError("purchase_constraints_violated", "Item quantities differ from the authorized quantity. Nothing was saved.")
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
            if constraints:
                if constraints.max_unit_price is not None and unit_price > constraints.max_unit_price:
                    raise PurchaseRequestError("budget_exceeded", "An upstream unit price exceeds the per-unit limit. Nothing was saved.")
                if constraints.max_total is not None and total > constraints.max_total:
                    raise PurchaseRequestError("budget_exceeded", "The upstream item sum exceeds the TOTAL request budget. Nothing was saved.")
                if constraints.category is not None and product.category != constraints.category:
                    raise PurchaseRequestError("purchase_constraints_violated", "Product category differs from the authorized category. Nothing was saved.")
                if constraints.product_name is not None:
                    if normalize_product_name(constraints.product_name) not in {
                        normalize_product_name(value) for value in (product.name, product.sku) if value
                    }:
                        raise PurchaseRequestError("purchase_constraints_violated", "Product name/SKU differs from the exact product requested. Nothing was saved.")
            if product.stock is not None and entry.quantity > product.stock:
                raise PurchaseRequestError("purchase_constraints_violated", "Quantity exceeds confirmed stock. Nothing was saved.")
            if product.minimum_order_quantity is not None and entry.quantity < product.minimum_order_quantity:
                raise PurchaseRequestError("purchase_constraints_violated", "Quantity is below the catalog minimum order. Nothing was saved.")
            items.append(PurchaseItemResult(
                product_id=product.id, name=product.name, description=product.description,
                quantity=entry.quantity, unit_price=unit_price, subtotal=subtotal,
            ))
        result = await to_thread.run_sync(self.repository.create, request, fingerprint, items)
        logger.info("Purchase request completed: replayed=%s count=%s", result.replayed, len(result.items))
        return result

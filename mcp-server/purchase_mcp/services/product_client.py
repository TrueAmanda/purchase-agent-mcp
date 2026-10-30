"""HTTPX access to DummyJSON, independent of the MCP tool definition."""

import logging
import time
from decimal import Decimal
from typing import TypeVar

import httpx
from pydantic import BaseModel

from purchase_mcp.schemas.products import Product, ProductDetailResult, ProductSearchPayload, ProductSearchResult

logger = logging.getLogger(__name__)
PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class ProductAPIError(Exception):
    """Safe error category and message suitable for an MCP client."""

    def __init__(self, code: str, message: str, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


def create_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://dummyjson.com",
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


class ProductClient:
    def __init__(self, http_client: httpx.AsyncClient):
        self.http_client = http_client

    async def search_products(self, term: str) -> ProductSearchResult:
        term = term.strip()
        if not term:
            logger.warning("Product search rejected: code=invalid_term")
            raise ProductAPIError("invalid_term", "termo must contain non-whitespace characters.")

        started_at = time.monotonic()
        payload = await self._get_model(
            "/products/search", ProductSearchPayload, params={"q": term}
        )
        status = "success" if payload.products else "no_results"
        logger.info(
            "Product search completed: status=%s count=%s total=%s duration_ms=%s",
            status, len(payload.products), payload.total,
            round((time.monotonic() - started_at) * 1000),
        )
        return ProductSearchResult(
            status=status, query=term, products=payload.products,
            total=payload.total, skip=payload.skip, limit=payload.limit,
        )

    async def get_product(self, product_id: int) -> ProductDetailResult:
        # bool is a subclass of int, but it is not a valid product identifier.
        if type(product_id) is not int or product_id <= 0:
            logger.warning("Product detail rejected: code=invalid_id")
            raise ProductAPIError("invalid_id", "id must be a positive integer.")

        started_at = time.monotonic()
        product = await self._get_model(
            f"/products/{product_id}", Product, product_not_found=True
        )
        if product.id != product_id:
            logger.warning("Product detail failed: code=invalid_response")
            raise ProductAPIError(
                "invalid_response", "Product API returned a different product identifier."
            )
        logger.info(
            "Product detail completed: status=success duration_ms=%s",
            round((time.monotonic() - started_at) * 1000),
        )
        return ProductDetailResult(product=product)

    async def _get_model(
        self,
        path: str,
        model: type[PayloadModel],
        *,
        params: dict[str, str] | None = None,
        product_not_found: bool = False,
    ) -> PayloadModel:
        """Shared HTTP request, error mapping, JSON decoding and schema validation."""
        try:
            response = await self.http_client.get(path, params=params)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("Product API request failed: code=api_timeout")
            raise ProductAPIError("api_timeout", "Product API request timed out. Try again later.") from None
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            if product_not_found and status_code == 404:
                logger.warning("Product API request failed: code=product_not_found status_code=404")
                raise ProductAPIError(
                    "product_not_found", "Product not found (HTTP 404).", status_code=404
                ) from None
            logger.warning("Product API request failed: code=api_http_error status_code=%s", status_code)
            raise ProductAPIError(
                "api_http_error", f"Product API returned HTTP {status_code}. Request could not be completed.",
                status_code=status_code,
            ) from None
        except httpx.RequestError:
            logger.warning("Product API request failed: code=api_unavailable")
            raise ProductAPIError("api_unavailable", "Product API is unreachable. Try again later.") from None

        try:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise ValueError("Expected JSON.")
            return model.model_validate(response.json(parse_float=Decimal))
        except ValueError:
            logger.warning("Product API request failed: code=invalid_response")
            raise ProductAPIError(
                "invalid_response", "Product API returned malformed or inconsistent product data."
            ) from None

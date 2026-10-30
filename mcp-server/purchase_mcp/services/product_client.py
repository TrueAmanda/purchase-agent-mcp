"""HTTPX access to DummyJSON, independent of the MCP tool definition."""

import logging
import time
from decimal import Decimal

import httpx
from pydantic import ValidationError

from purchase_mcp.schemas.products import ProductSearchPayload, ProductSearchResult

logger = logging.getLogger(__name__)


class ProductAPIError(Exception):
    """Safe error category and message suitable for an MCP client."""

    def __init__(self, code: str, message: str):
        self.code = code
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
        try:
            response = await self.http_client.get("/products/search", params={"q": term})
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("Product search failed: code=api_timeout")
            raise ProductAPIError("api_timeout", "Product API request timed out. Try again later.") from None
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            logger.warning("Product search failed: code=api_http_error status_code=%s", status_code)
            raise ProductAPIError(
                "api_http_error", f"Product API returned HTTP {status_code}. Search could not be completed."
            ) from None
        except httpx.RequestError:
            logger.warning("Product search failed: code=api_unavailable")
            raise ProductAPIError("api_unavailable", "Product API is unreachable. Try again later.") from None

        try:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise ValueError("Expected JSON.")
            payload = ProductSearchPayload.model_validate(response.json(parse_float=Decimal))
        except (ValueError, ValidationError):
            logger.warning("Product search failed: code=invalid_response")
            raise ProductAPIError(
                "invalid_response", "Product API returned malformed or inconsistent product data."
            ) from None

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

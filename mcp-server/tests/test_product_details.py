"""detalhar_produto: strict IDs and exact mapping of upstream product data."""

from decimal import Decimal

import httpx
import pytest

from purchase_mcp.services.product_client import ProductAPIError

pytestmark = pytest.mark.anyio


async def test_existing_product_matches_upstream_fields_and_types(product_client, product_payload, catalog):
    result = await product_client.get_product(1)
    product = result.product
    assert result.status == "success"
    assert result.source == "dummyjson"
    assert type(product.id) is int and product.id == product_payload["id"]
    assert product.name == product_payload["title"]
    assert product.description == product_payload["description"]
    assert product.price == Decimal("19.99") and isinstance(product.price, Decimal)
    assert product.stock == product_payload["stock"] and type(product.stock) is int
    assert product.rating == product_payload["rating"] and isinstance(product.rating, float)
    assert product.discount_percentage == product_payload["discountPercentage"]
    for field, upstream in (
        ("category", "category"), ("brand", "brand"), ("sku", "sku"),
        ("warranty_information", "warrantyInformation"), ("shipping_information", "shippingInformation"),
        ("availability_status", "availabilityStatus"), ("return_policy", "returnPolicy"),
        ("minimum_order_quantity", "minimumOrderQuantity"), ("images", "images"), ("tags", "tags"),
    ):
        assert getattr(product, field) == product_payload[upstream]
    assert product.model_dump(mode="json")["price"] == "19.99"
    assert "reviews" not in product.model_dump()
    assert "currency" not in product.model_dump()
    assert catalog.calls[0].url.path == "/products/1"
    assert dict(catalog.calls[0].url.params) == {}


async def test_optional_fields_are_not_invented(product_client):
    product = (await product_client.get_product(2)).product
    assert product.brand is None
    assert product.stock is None
    assert product.warranty_information is None
    assert product.images is None


async def test_missing_product_returns_404_error_without_product(product_client):
    with pytest.raises(ProductAPIError) as error:
        await product_client.get_product(999999)
    assert error.value.code == "product_not_found"
    assert error.value.status_code == 404
    assert "404" in str(error.value)


@pytest.mark.parametrize("product_id", [0, -1, True, False, "1", "abc", 1.0, 1.5, None])
async def test_invalid_ids_are_rejected_before_http(product_client, catalog, product_id):
    with pytest.raises(ProductAPIError) as error:
        await product_client.get_product(product_id)
    assert error.value.code == "invalid_id"
    assert catalog.calls == []


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "api_timeout"), (httpx.ConnectTimeout, "api_timeout"),
    (httpx.ConnectError, "api_unavailable"), (httpx.RemoteProtocolError, "api_unavailable"),
])
async def test_timeouts_and_network_errors_are_sanitized(product_client, catalog, exception, code, caplog):
    catalog.overrides["/products/1"] = exception("private details")
    with pytest.raises(ProductAPIError) as error:
        await product_client.get_product(1)
    assert error.value.code == code
    assert "private" not in str(error.value) + caplog.text


@pytest.mark.parametrize("status", [302, 400, 429, 500, 503])
async def test_other_http_errors_are_distinct_from_not_found(product_client, catalog, status):
    catalog.overrides["/products/1"] = httpx.Response(status, text="sensitive body")
    with pytest.raises(ProductAPIError) as error:
        await product_client.get_product(1)
    assert error.value.code == "api_http_error"
    assert error.value.status_code == status
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("case", ["missing_price", "missing_name", "negative_price", "wrong_id", "empty_object", "html", "broken_json"])
async def test_malformed_or_wrong_product_is_not_returned(product_client, catalog, case):
    payload = catalog.products[1]
    if case == "missing_price":
        del payload["price"]
    elif case == "missing_name":
        del payload["title"]
    elif case == "negative_price":
        payload["price"] = -1
    elif case == "wrong_id":
        payload["id"] = 2
    elif case == "empty_object":
        catalog.overrides["/products/1"] = httpx.Response(200, json={})
    elif case == "html":
        catalog.overrides["/products/1"] = httpx.Response(200, text="<html>failure</html>")
    else:
        catalog.overrides["/products/1"] = httpx.Response(200, content=b"invalid", headers={"content-type": "application/json"})
    with pytest.raises(ProductAPIError) as error:
        await product_client.get_product(1)
    assert error.value.code == "invalid_response"

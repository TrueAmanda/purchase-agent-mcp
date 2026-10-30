"""buscar_produtos: upstream fidelity, empty results, validation and failures."""

from decimal import Decimal
import logging

import httpx
import pytest

from purchase_mcp.services.product_client import ProductAPIError

pytestmark = pytest.mark.anyio


async def test_found_products_match_only_the_api_response(product_client, catalog, caplog):
    with caplog.at_level(logging.INFO, logger="purchase_mcp.services.product_client"):
        result = await product_client.search_products("  phone & charger/?  ")
    assert result.status == "success"
    assert result.source == "dummyjson"
    assert result.query == "phone & charger/?"
    assert (result.total, result.skip, result.limit) == (2, 0, 2)
    assert [product.id for product in result.products] == [p["id"] for p in catalog.search["products"]]
    for product, original in zip(result.products, catalog.search["products"]):
        assert type(product.id) is int
        assert product.name == original["title"]
        assert product.description == original["description"]
        assert isinstance(product.price, Decimal)
        assert product.price == Decimal(str(original["price"]))
        assert product.brand == original.get("brand")
    assert result.products[1].stock is None
    assert "reviews" not in result.products[0].model_dump()
    assert "currency" not in result.products[0].model_dump()
    assert "private@example.test" not in result.model_dump_json()
    assert "phone & charger" not in caplog.text
    assert "count=2" in caplog.text
    assert len(catalog.calls) == 1
    request = catalog.calls[0]
    assert request.url.path == "/products/search"
    assert dict(request.url.params) == {"q": "phone & charger/?"}
    assert request.extensions["timeout"]["read"] == 2.5


async def test_no_results_is_successful_and_has_no_invented_products(product_client, catalog):
    catalog.search = {"products": [], "total": 0, "skip": 0, "limit": 0}
    result = await product_client.search_products("not-found")
    assert result.status == "no_results"
    assert result.products == []
    assert result.total == 0


@pytest.mark.parametrize("term", ["", " ", "\t\n", "\u00a0"])
async def test_empty_terms_are_rejected_before_http(product_client, catalog, term):
    with pytest.raises(ProductAPIError) as error:
        await product_client.search_products(term)
    assert error.value.code == "invalid_term"
    assert catalog.calls == []


@pytest.mark.parametrize("term", [None, 12, True, [], {}])
async def test_non_string_terms_are_rejected_by_public_tool(mcp_connection, catalog, term):
    async with mcp_connection() as session:
        result = await session.call_tool("buscar_produtos", {"termo": term})
    assert result.isError
    assert result.structuredContent is None
    assert catalog.calls == []


@pytest.mark.parametrize("exception", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout])
async def test_timeouts_are_not_empty_searches(product_client, catalog, exception):
    catalog.overrides["/products/search"] = exception("private upstream details")
    with pytest.raises(ProductAPIError) as error:
        await product_client.search_products("phone")
    assert error.value.code == "api_timeout"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.RemoteProtocolError])
async def test_network_failures_are_reported_safely(product_client, catalog, exception, caplog):
    catalog.overrides["/products/search"] = exception("sensitive connection details")
    with pytest.raises(ProductAPIError) as error:
        await product_client.search_products("private-term")
    assert error.value.code == "api_unavailable"
    assert "sensitive" not in str(error.value) + caplog.text
    assert "private-term" not in caplog.text


@pytest.mark.parametrize("status", [302, 400, 404, 429, 500, 503])
async def test_http_failures_do_not_return_fallback_products(product_client, catalog, status):
    catalog.overrides["/products/search"] = httpx.Response(status, text="private response body")
    with pytest.raises(ProductAPIError) as error:
        await product_client.search_products("phone")
    assert error.value.code == "api_http_error"
    assert error.value.status_code == status
    assert "private response body" not in str(error.value)


@pytest.mark.parametrize("case", ["html", "broken_json", "missing_products", "null_products", "missing_price", "negative_price", "inconsistent_total", "invalid_total"])
async def test_malformed_responses_are_errors_not_no_results(product_client, catalog, case):
    if case == "html":
        response = httpx.Response(200, text="<html>upstream failure</html>")
    elif case == "broken_json":
        response = httpx.Response(200, content=b"broken", headers={"Content-Type": "application/json"})
    else:
        payload = catalog.search
        if case == "missing_products":
            del payload["products"]
        elif case == "null_products":
            payload["products"] = None
        elif case == "missing_price":
            del payload["products"][0]["price"]
        elif case == "negative_price":
            payload["products"][0]["price"] = -1
        elif case == "inconsistent_total":
            payload.update(products=[], total=2)
        else:
            payload["total"] = "2"
        response = httpx.Response(200, json=payload)
    catalog.overrides["/products/search"] = response
    with pytest.raises(ProductAPIError) as error:
        await product_client.search_products("phone")
    assert error.value.code == "invalid_response"

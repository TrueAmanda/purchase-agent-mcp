"""HTTP client and configuration behavior, without calling external services."""

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx

from fixtures import search_payload
from purchase_mcp.config import Settings
from purchase_mcp.services.product_client import ProductAPIError, ProductClient, create_http_client


class SettingsTests(unittest.TestCase):
    def test_timeout_default_and_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_environment().product_api_timeout_seconds, 10)
        with patch.dict(os.environ, {"PRODUCT_API_TIMEOUT_SECONDS": "2.5"}):
            self.assertEqual(Settings.from_environment().product_api_timeout_seconds, 2.5)

    def test_invalid_timeouts(self):
        for value in ("0", "-1", "nan", "inf", "not-a-number", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"PRODUCT_API_TIMEOUT_SECONDS": value}):
                with self.assertRaisesRegex(RuntimeError, "finite positive"):
                    Settings.from_environment()


class ProductClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_mapping_decimal_encoding_and_timeout(self):
        def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, "dummyjson.com")
            self.assertEqual(request.url.path, "/products/search")
            self.assertEqual(dict(request.url.params), {"q": "phone & charger/?"})
            self.assertEqual(request.extensions["timeout"]["read"], 2.5)
            return httpx.Response(200, json=search_payload())

        async with httpx.AsyncClient(
            base_url="https://dummyjson.com", transport=httpx.MockTransport(handler), timeout=2.5
        ) as http_client:
            with self.assertLogs("purchase_mcp.services.product_client", level="INFO") as logs:
                result = await ProductClient(http_client).search_products("  phone & charger/?  ")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.query, "phone & charger/?")
        self.assertEqual(result.products[0].name, "Test fixture product")
        self.assertEqual(result.products[0].price, Decimal("19.99"))
        self.assertIsNone(result.products[0].brand)
        self.assertNotIn("reviews", result.products[0].model_dump())
        self.assertNotIn("currency", result.products[0].model_dump())
        self.assertNotIn("phone", " ".join(logs.output))
        self.assertNotIn("private@example.test", result.model_dump_json())

    async def test_production_client_has_configured_timeout(self):
        async with create_http_client(2.5) as client:
            self.assertEqual(client.base_url, httpx.URL("https://dummyjson.com"))
            self.assertEqual(client.timeout, httpx.Timeout(2.5))
            self.assertFalse(client.follow_redirects)

    async def test_empty_input_never_calls_api(self):
        def handler(request):
            self.fail("An empty term must not trigger an HTTP request.")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            for term in ("", " \t\n "):
                with self.subTest(term=term), self.assertRaises(ProductAPIError) as error:
                    await ProductClient(http_client).search_products(term)
                self.assertEqual(error.exception.code, "invalid_term")

    async def test_no_results_is_a_successful_empty_search(self):
        async with httpx.AsyncClient(
            base_url="https://dummyjson.com",
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, json={"products": [], "total": 0, "skip": 0, "limit": 0}
            )),
        ) as client:
            result = await ProductClient(client).search_products("nothing")
        self.assertEqual(result.status, "no_results")
        self.assertEqual(result.products, [])
        self.assertEqual(result.total, 0)

    async def test_network_and_timeout_errors_are_distinct_and_safe(self):
        for exception_type, code in (
            (httpx.ReadTimeout, "api_timeout"),
            (httpx.ConnectError, "api_unavailable"),
            (httpx.RemoteProtocolError, "api_unavailable"),
        ):
            def handler(request):
                raise exception_type("secret upstream details", request=request)

            with self.subTest(code=code):
                async with httpx.AsyncClient(
                    base_url="https://dummyjson.com", transport=httpx.MockTransport(handler)
                ) as client:
                    with self.assertLogs("purchase_mcp.services.product_client") as logs:
                        with self.assertRaises(ProductAPIError) as error:
                            await ProductClient(client).search_products("private-term")
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("secret", str(error.exception))
                self.assertNotIn("private-term", " ".join(logs.output))
                self.assertNotIn("secret", " ".join(logs.output))

    async def test_http_errors_are_not_empty_searches(self):
        for status in (302, 400, 404, 429, 500, 503):
            with self.subTest(status=status):
                async with httpx.AsyncClient(
                    base_url="https://dummyjson.com", transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, text="private upstream body")
                    )
                ) as client:
                    with self.assertRaises(ProductAPIError) as error:
                        await ProductClient(client).search_products("phone")
                self.assertEqual(error.exception.code, "api_http_error")
                self.assertIn(str(status), str(error.exception))
                self.assertNotIn("private upstream body", str(error.exception))

    async def test_invalid_json_content_and_schema(self):
        missing_price = search_payload()
        del missing_price["products"][0]["price"]
        negative_price = search_payload()
        negative_price["products"][0]["price"] = -1
        invalid_products = search_payload()
        invalid_products["products"] = None
        responses = [
            httpx.Response(200, text="<html>upstream error</html>"),
            httpx.Response(200, content=b"not JSON", headers={"content-type": "application/json"}),
            httpx.Response(200, json={}),
            httpx.Response(200, json=missing_price),
            httpx.Response(200, json=negative_price),
            httpx.Response(200, json=invalid_products),
            httpx.Response(200, json={"products": [], "total": 2, "skip": 0, "limit": 0}),
        ]
        for response in responses:
            with self.subTest(response=response):
                async with httpx.AsyncClient(
                    base_url="https://dummyjson.com",
                    transport=httpx.MockTransport(lambda request: response),
                ) as client:
                    with self.assertRaises(ProductAPIError) as error:
                        await ProductClient(client).search_products("phone")
                self.assertEqual(error.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()

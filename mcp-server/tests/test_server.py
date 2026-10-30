"""Discover and invoke tools with the official MCP client over Streamable HTTP."""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from fixtures import search_payload
from purchase_mcp.server import create_server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def connected_session(self, handler):
        clients = []

        def create_api_client(timeout_seconds):
            client = httpx.AsyncClient(
                base_url="https://dummyjson.com", timeout=timeout_seconds,
                transport=httpx.MockTransport(handler),
            )
            clients.append(client)
            return client

        with patch("purchase_mcp.server.create_http_client", side_effect=create_api_client):
            app = create_server().streamable_http_app()

            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as mcp_client:
                    async with streamable_http_client(
                        "http://testserver/mcp", http_client=mcp_client
                    ) as (read_stream, write_stream, _):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            yield session
        self.assertTrue(clients)
        self.assertTrue(all(client.is_closed for client in clients))

    async def test_discovery_and_structured_search_with_official_client(self):
        async with self.connected_session(
            lambda request: httpx.Response(200, json=search_payload())
        ) as session:
            tools = await session.list_tools()
            discovered = {tool.name: tool for tool in tools.tools}
            self.assertEqual(set(discovered), {"buscar_produtos", "detalhar_produto"})
            tool = discovered["buscar_produtos"]
            self.assertEqual(tool.inputSchema["required"], ["termo"])
            self.assertEqual(set(tool.inputSchema["properties"]), {"termo"})
            self.assertIn("products", tool.outputSchema["properties"])

            result = await session.call_tool("buscar_produtos", {"termo": "phone"})
            self.assertFalse(result.isError, result.model_dump_json())
            self.assertEqual(result.structuredContent["status"], "success")
            product = result.structuredContent["products"][0]
            self.assertEqual(product["id"], 1)
            self.assertEqual(product["name"], "Test fixture product")
            self.assertEqual(product["price"], "19.99")

    async def test_detail_discovery_and_structured_result(self):
        async with self.connected_session(
            lambda request: httpx.Response(200, json=search_payload()["products"][0])
        ) as session:
            tools = await session.list_tools()
            detail = next(tool for tool in tools.tools if tool.name == "detalhar_produto")
            self.assertEqual(detail.inputSchema["required"], ["id"])
            self.assertEqual(set(detail.inputSchema["properties"]), {"id"})
            self.assertEqual(detail.inputSchema["properties"]["id"]["type"], "integer")
            self.assertEqual(detail.inputSchema["properties"]["id"]["exclusiveMinimum"], 0)
            self.assertIn("product", detail.outputSchema["properties"])
            result = await session.call_tool("detalhar_produto", {"id": 1})
            self.assertFalse(result.isError, result.model_dump_json())
            self.assertEqual(result.structuredContent["status"], "success")
            self.assertEqual(result.structuredContent["product"]["id"], 1)
            self.assertEqual(result.structuredContent["product"]["price"], "19.99")
            self.assertIsNone(result.structuredContent["product"]["brand"])

    async def test_detail_invalid_ids_do_not_call_api(self):
        def handler(request):
            self.fail("An invalid product ID must not reach the API.")

        async with self.connected_session(handler) as session:
            for arguments in ({}, {"id": 0}, {"id": -1}, {"id": True}, {"id": 1.0},
                              {"id": "1"}, {"id": "abc"}, {"id": None}):
                with self.subTest(arguments=arguments):
                    result = await session.call_tool("detalhar_produto", arguments)
                    self.assertTrue(result.isError)
                    self.assertIsNone(result.structuredContent)

    async def test_detail_404_is_reported_over_mcp(self):
        async with self.connected_session(
            lambda request: httpx.Response(404, json={"message": "private upstream message"})
        ) as session:
            result = await session.call_tool("detalhar_produto", {"id": 999999})
            self.assertTrue(result.isError)
            self.assertIsNone(result.structuredContent)
            self.assertIn("product_not_found", result.content[0].text)
            self.assertIn("404", result.content[0].text)
            self.assertNotIn("private", result.content[0].text)

    async def test_no_results_and_empty_input_are_different(self):
        api_calls = []

        def handler(request):
            api_calls.append(request)
            return httpx.Response(200, json={"products": [], "total": 0, "skip": 0, "limit": 0})

        async with self.connected_session(handler) as session:
            empty = await session.call_tool("buscar_produtos", {"termo": "not-found"})
            self.assertFalse(empty.isError)
            self.assertEqual(empty.structuredContent["status"], "no_results")
            self.assertEqual(empty.structuredContent["products"], [])
            invalid = await session.call_tool("buscar_produtos", {"termo": " \t "})
            self.assertTrue(invalid.isError)
            self.assertIn("invalid_term", invalid.content[0].text)
            self.assertEqual(len(api_calls), 1)

    async def test_upstream_failure_is_a_tool_error(self):
        async with self.connected_session(
            lambda request: httpx.Response(503, text="sensitive upstream body")
        ) as session:
            for tool_name, arguments in (("buscar_produtos", {"termo": "phone"}),
                                         ("detalhar_produto", {"id": 1})):
                with self.subTest(tool_name=tool_name):
                    result = await session.call_tool(tool_name, arguments)
                    self.assertTrue(result.isError)
                    self.assertIn("api_http_error", result.content[0].text)
                    self.assertIn("503", result.content[0].text)
                    self.assertNotIn("sensitive upstream body", result.content[0].text)

    async def test_invalid_arguments_do_not_call_api(self):
        def handler(request):
            self.fail("Invalid arguments must not reach the API.")

        async with self.connected_session(handler) as session:
            for arguments in ({}, {"termo": None}, {"termo": 42}):
                with self.subTest(arguments=arguments):
                    result = await session.call_tool("buscar_produtos", arguments)
                    self.assertTrue(result.isError)

    async def test_health_is_independent_of_upstream(self):
        app = create_server().streamable_http_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                health = await client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

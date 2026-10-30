"""Check the HTTP transport without registering any catalog tools."""

import unittest

import httpx

from purchase_mcp.server import server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_and_empty_tool_list(self):
        app = server.streamable_http_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                health = await client.get("/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})

                headers = {"Accept": "application/json, text/event-stream"}
                initialized = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "infrastructure-test", "version": "1.0"},
                    },
                })
                self.assertEqual(initialized.status_code, 200)
                self.assertIn("serverInfo", initialized.json()["result"])
                tools = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
                })
                self.assertEqual(tools.status_code, 200)
                self.assertEqual(tools.json()["result"]["tools"], [])


if __name__ == "__main__":
    unittest.main()

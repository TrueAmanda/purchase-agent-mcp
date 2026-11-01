"""Manual test entry point: keep MCP/SQL real, return HTTP 503 for DummyJSON."""

import httpx

from purchase_mcp import server


def create_failure_client(timeout_seconds):
    def unavailable(request):
        return httpx.Response(503, json={"message": "Simulated DummyJSON unavailability for a manual test."})

    return httpx.AsyncClient(
        base_url="https://dummyjson.com", timeout=httpx.Timeout(timeout_seconds),
        trust_env=False, follow_redirects=False, transport=httpx.MockTransport(unavailable),
    )


if __name__ == "__main__":
    server.create_http_client = create_failure_client
    server.main()

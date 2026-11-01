"""Check the opt-in manual fault helper without starting or changing services."""

from pathlib import Path
import runpy

import pytest

from purchase_mcp import server
from purchase_mcp.services.product_client import ProductAPIError, ProductClient

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_manual_dummyjson_failure_is_explicit_and_uses_the_real_error_mapping():
    original_factory = server.create_http_client
    script = Path(__file__).resolve().parents[3] / "tests/manual/simulate_dummyjson_failure.py"
    helper = runpy.run_path(str(script))
    assert server.create_http_client is original_factory
    async with helper["create_failure_client"](1.5) as client:
        assert client.timeout.read == 1.5 and "Authorization" not in client.headers
        with pytest.raises(ProductAPIError) as caught:
            await ProductClient(client).search_products("soap")
    assert caught.value.code == "api_http_error" and caught.value.status_code == 503

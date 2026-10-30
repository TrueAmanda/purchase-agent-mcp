"""Runtime configuration and hard isolation guards for the test suite."""

import httpx
import pytest

from purchase_mcp.config import Settings
from purchase_mcp.services.product_client import create_http_client


def test_timeout_default(monkeypatch):
    monkeypatch.delenv("PRODUCT_API_TIMEOUT_SECONDS")
    assert Settings.from_environment().product_api_timeout_seconds == 10


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad", ""])
def test_invalid_timeout_configuration_fails_safely(monkeypatch, value):
    monkeypatch.setenv("PRODUCT_API_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="finite positive"):
        Settings.from_environment()


@pytest.mark.anyio
async def test_http_client_uses_configured_timeout_and_no_redirects():
    async with create_http_client(2.5) as client:
        assert client.timeout == httpx.Timeout(2.5)
        assert client.base_url == httpx.URL("https://dummyjson.com")
        assert not client.follow_redirects


@pytest.mark.anyio
async def test_unmocked_external_http_is_blocked():
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(AssertionError, match="Unmocked external HTTP"):
            await client.get("https://dummyjson.com/products/1")


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://test:test@localhost/purchase_agent",
    "postgresql+psycopg://test:test@localhost/postgres",
    "sqlite:///development.sqlite",
])
def test_development_database_urls_are_rejected_before_connecting(monkeypatch, request, url):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    with pytest.raises(ValueError, match="database named purchase_mcp_test"):
        request.getfixturevalue("db_engine")

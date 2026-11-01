"""Shared offline HTTP, isolated database and official MCP client fixtures."""

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
import socket

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
import uvicorn

from purchase_storage import PurchaseRequest, PurchaseRequestItem
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.server import create_server
from purchase_mcp.services.product_client import ProductClient
from purchase_mcp.services.purchase_requests import PurchaseRequestService
from test_support.database import isolated_db_engine as isolated_db_engine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    """Ignore development/LLM settings; only explicit local MCP HTTP is allowed."""
    for name in (
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENROUTER_MODEL", "DATABASE_URL",
        "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRODUCT_API_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8001")
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request

    async def guarded_async(transport, request):
        assert request.url.host in {"127.0.0.1", "localhost", "::1"}, "Unmocked external HTTP request"
        return await original_async(transport, request)

    def guarded_sync(transport, request):
        assert request.url.host in {"127.0.0.1", "localhost", "::1"}, "Unmocked external HTTP request"
        return original_sync(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)


@pytest.fixture
def product_payload():
    # Synthetic fixtures only: no test data is loaded by production code.
    return {
        "id": 1, "title": "Fixture product one", "price": 19.99,
        "description": "Fixture description one.", "category": "fixture-category",
        "brand": "Fixture brand", "sku": "FIXTURE-1", "stock": 10, "rating": 4.5,
        "discountPercentage": 2.5, "availabilityStatus": "In Stock", "minimumOrderQuantity": 1,
        "warrantyInformation": "Fixture warranty", "shippingInformation": "Fixture delivery",
        "returnPolicy": "Fixture return policy", "thumbnail": "https://example.test/thumbnail.webp",
        "images": ["https://example.test/image.webp"], "tags": ["fixture"],
        "reviews": [{"reviewerEmail": "private@example.test"}],
    }


class MockCatalog:
    def __init__(self, product):
        second = {"id": 2, "title": "Fixture product two", "description": "Fixture description two.", "price": 0.1}
        self.products = {1: deepcopy(product), 2: second}
        self.search = {"products": deepcopy(list(self.products.values())), "total": 2, "skip": 0, "limit": 2}
        self.categories = []
        self.category_results = {}
        self.overrides = {}
        self.calls = []

    def handle(self, request):
        assert request.method == "GET"
        assert request.url.host == "dummyjson.com"
        self.calls.append(request)
        override = self.overrides.get(request.url.path)
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        if request.url.path == "/products/search":
            return httpx.Response(200, json=deepcopy(self.search))
        if request.url.path == "/products/category-list":
            return httpx.Response(200, json=deepcopy(self.categories))
        if request.url.path.startswith("/products/category/"):
            return httpx.Response(200, json=deepcopy(self.category_results[request.url.path.rsplit("/", 1)[1]]))
        product_id = int(request.url.path.rsplit("/", 1)[1])
        if product_id not in self.products:
            return httpx.Response(404, json={"message": "Fixture product not found"})
        return httpx.Response(200, json=deepcopy(self.products[product_id]))

    def client(self, timeout=2.5):
        return httpx.AsyncClient(
            base_url="https://dummyjson.com", timeout=timeout, trust_env=False,
            transport=httpx.MockTransport(self.handle),
        )


@pytest.fixture
def catalog(product_payload):
    return MockCatalog(product_payload)


@pytest.fixture
async def product_client(catalog):
    async with catalog.client() as client:
        yield ProductClient(client)


@pytest.fixture
def db_engine(isolated_db_engine):
    return isolated_db_engine


@pytest.fixture
def db_sessions(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def repository(db_sessions):
    return PurchaseRequestRepository(db_sessions)


@pytest.fixture
def db_counts(db_sessions):
    def count():
        with db_sessions() as session:
            return (session.scalar(select(func.count()).select_from(PurchaseRequest)),
                    session.scalar(select(func.count()).select_from(PurchaseRequestItem)))
    return count


@pytest.fixture
def request_payload():
    # The same fixed key is safe: every test gets its own database/schema.
    return {
        "title": "Fixture purchase", "requester_name": "Fixture requester",
        "idempotency_key": "11111111-1111-4111-8111-111111111111",
        "items": [{"product_id": 1, "quantity": 3}],
    }


@pytest.fixture
async def purchase_service(product_client, repository):
    return PurchaseRequestService(product_client, repository)


@pytest.fixture
def mcp_app_factory(monkeypatch, catalog, repository):
    clients = []

    def create_client(timeout_seconds):
        client = catalog.client(timeout_seconds)
        clients.append(client)
        return client

    monkeypatch.setattr("purchase_mcp.server.create_http_client", create_client)
    yield lambda: create_server(repository).streamable_http_app()
    assert all(client.is_closed for client in clients), "HTTP clients must close after MCP requests"


@pytest.fixture
def mcp_connection(mcp_app_factory):
    @asynccontextmanager
    async def connect():
        app = mcp_app_factory()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), trust_env=False) as http:
                async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
    return connect


@pytest.fixture
async def mcp_tcp_session(mcp_app_factory):
    """Real TCP Streamable HTTP using the SDK, with no fixed port or external API."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp_app_factory(), log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("MCP HTTP server failed to start")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False) as http:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()

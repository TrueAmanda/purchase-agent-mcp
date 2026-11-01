"""Both HTTP processes create the shared tables during startup on an empty DB."""

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import inspect

from app import main as backend_main
from app.api.router import api_router
from purchase_mcp import server as mcp_server
from purchase_storage import Base
from purchase_storage.config import Settings

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.mark.parametrize("component", ["backend", "mcp"])
async def test_http_startup_initializes_empty_storage_without_llm_credentials(isolated_db_engine, monkeypatch, component):
    engine = isolated_db_engine
    Base.metadata.drop_all(engine)
    assert not {"purchase_requests", "purchase_request_items"} & set(inspect(engine).get_table_names())
    settings = Settings(engine.url, 1, 0)
    monkeypatch.setattr(Settings, "from_environment", lambda: settings)
    if component == "backend":
        monkeypatch.setattr(backend_main, "build_engine", lambda settings: engine)
        application = FastAPI(lifespan=backend_main.lifespan)
        application.include_router(api_router)
    else:
        monkeypatch.setattr(mcp_server, "build_engine", lambda settings: engine)
        application = mcp_server.create_app()
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200 and response.json()["status"] == "ok"
        assert {"purchase_requests", "purchase_request_items"} <= set(inspect(engine).get_table_names())

"""Explicit integration check in a disposable database; never removes Docker volumes.

Run by piping this file to Python in the MCP container. The configured PostgreSQL
role must have CREATEDB permission. Only the randomly named test database is removed.
"""

import asyncio
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from purchase_storage import PurchaseRequest, PurchaseRequestItem
from purchase_storage.config import Settings
from purchase_storage.database import build_engine, initialize_database
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.schemas.purchase_requests import PurchaseItemResult, PurchaseRequestInput
from purchase_mcp.server import create_app
from purchase_mcp.services.purchase_errors import PurchaseRequestError
from purchase_mcp.services.purchase_requests import request_fingerprint


def input_data():
    return {"title": "Integration fixture", "requester_name": "Integration tester",
            "idempotency_key": str(uuid4()), "items": [{"product_id": 1, "quantity": 2}]}


def prepare_legacy_schema(engine):
    statements = [
        "CREATE TABLE purchase_requests (id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, "
        "requester_name VARCHAR(255) NOT NULL, currency VARCHAR(3) NOT NULL DEFAULT 'BRL', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
        "CREATE TABLE purchase_request_items (id SERIAL PRIMARY KEY, purchase_request_id INTEGER NOT NULL "
        "REFERENCES purchase_requests(id) ON DELETE CASCADE, product_id VARCHAR(128), "
        "description VARCHAR(255) NOT NULL, quantity INTEGER NOT NULL CHECK (quantity > 0), "
        "unit_price NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0))",
        "INSERT INTO purchase_requests(title, requester_name) VALUES ('Legacy fixture', 'Legacy tester')",
        "INSERT INTO purchase_request_items(purchase_request_id, description, quantity, unit_price) "
        "VALUES (1, 'Legacy fixture item', 2, 1.23)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


async def verify_protocol_and_persistence(engine):
    # create_app uses this isolated database, including the normal startup migration.
    application = create_app()
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=8011, log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Temporary MCP server did not start.")
                await asyncio.sleep(0.05)
        async with streamable_http_client("http://127.0.0.1:8011/mcp") as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {"buscar_produtos", "detalhar_produto", "criar_requisicao"}
                data = input_data()
                # No search/detail tool invocation is required before creation.
                created = await client.call_tool("criar_requisicao", {"request": data})
                assert not created.isError, created
                result = created.structuredContent
                assert not result["replayed"] and result["currency"] is None
                async with httpx.AsyncClient(timeout=15) as http:
                    response = await http.get("https://dummyjson.com/products/1")
                    response.raise_for_status()
                    upstream = response.json(parse_float=Decimal)
                price = upstream["price"].quantize(Decimal("0.01"))
                assert Decimal(result["items"][0]["unit_price"]) == price
                assert Decimal(result["total"]) == price * 2

                replay = await client.call_tool("criar_requisicao", {"request": data})
                assert not replay.isError and replay.structuredContent["replayed"]
                assert replay.structuredContent["request_number"] == result["request_number"]
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "purchase_mcp.client", "--create", "-", "--url", "http://127.0.0.1:8011/mcp",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate(json.dumps(data).encode("utf-8"))
                assert process.returncode == 0, stderr.decode()
                cli = json.loads(stdout.decode().split("\n", 1)[1])
                assert cli["structuredContent"]["replayed"]
                assert cli["structuredContent"]["request_number"] == result["request_number"]
                changed = {**data, "items": [{"product_id": 1, "quantity": 3}]}
                conflict = await client.call_tool("criar_requisicao", {"request": changed})
                assert conflict.isError and "idempotency_conflict" in conflict.content[0].text

                missing_data = input_data()
                missing_data["items"].append({"product_id": 999999, "quantity": 1})
                missing = await client.call_tool("criar_requisicao", {"request": missing_data})
                assert missing.isError and "product_not_found" in missing.content[0].text
                invalid = await client.call_tool("criar_requisicao", {"request": {**input_data(), "total": "0.01"}})
                assert invalid.isError
                search = await client.call_tool("buscar_produtos", {"termo": "phone"})
                assert not search.isError and search.structuredContent["products"]
                detail = await client.call_tool("detalhar_produto", {"id": 1})
                assert not detail.isError and detail.structuredContent["product"]["id"] == 1

        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        with sessions() as session:
            record = session.scalar(select(PurchaseRequest).where(PurchaseRequest.idempotency_key == data["idempotency_key"]))
            assert record.request_number == result["request_number"] and record.total_amount == price * 2
            assert len(record.items) == 1 and record.items[0].subtotal == price * 2
            assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 2  # legacy + new
        print("PASS: MCP/TCP creation, authoritative prices, replay, conflict, invalid input, 404 and catalog regression.")
        return sessions, created.structuredContent
    finally:
        server.should_exit = True
        await task


def verify_database_race_and_rollback(sessions, snapshot):
    barrier = threading.Barrier(2, timeout=15)
    state = threading.local()

    class RaceRepository(PurchaseRequestRepository):
        def _lookup(self, session, key, fingerprint):
            result = super()._lookup(session, key, fingerprint)
            if result is None and not getattr(state, "checked", False):
                state.checked = True
                barrier.wait()
            return result

    repository = RaceRepository(sessions)
    request = PurchaseRequestInput.model_validate(input_data())
    items = [PurchaseItemResult.model_validate(item) for item in snapshot["items"]]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(repository.create, request, request_fingerprint(request), items) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0].request_number == results[1].request_number
    assert {result.replayed for result in results} == {True, False}

    invalid_request = PurchaseRequestInput.model_validate(input_data())
    invalid_item = items[0].model_copy(update={"quantity": 0})
    try:
        PurchaseRequestRepository(sessions).create(invalid_request, request_fingerprint(invalid_request), [*items, invalid_item])
        raise AssertionError("Expected a real PostgreSQL CHECK failure.")
    except PurchaseRequestError as error:
        assert error.code == "database_error"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 3  # legacy, MCP, race winner
        assert session.scalar(select(func.count()).select_from(PurchaseRequest).where(
            PurchaseRequest.idempotency_key == str(invalid_request.idempotency_key)
        )) == 0
        assert session.scalar(select(func.count()).select_from(PurchaseRequestItem)) == 3
    print("PASS: real concurrent unique-key race and rollback of header/items on a PostgreSQL constraint failure.")


def main():
    original = Settings.from_environment()
    database = "purchase_integration_" + uuid4().hex
    assert re.fullmatch(r"purchase_integration_[a-f0-9]{32}", database)
    admin = build_engine(original)
    settings = replace(original, database_url=original.database_url.set(database=database))
    engine = None
    created = False
    previous_url = os.environ.get("DATABASE_URL")
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        engine = build_engine(settings)
        prepare_legacy_schema(engine)
        initialize_database(engine, settings)
        initialize_database(engine, settings)
        with engine.connect() as connection:
            legacy = connection.execute(text("SELECT currency, total_amount FROM purchase_requests WHERE id=1")).one()
            assert legacy.currency == "BRL" and legacy.total_amount is None
            assert connection.execute(text("SELECT unit_price FROM purchase_request_items WHERE id=1")).scalar_one() == Decimal("1.23")
            assert connection.execute(text("SELECT count(*) FROM purchase_schema_migrations")).scalar_one() == 1
        currency = next(column for column in inspect(engine).get_columns("purchase_requests") if column["name"] == "currency")
        assert currency["nullable"] and currency["default"] is None
        print("PASS: versioned migration preserves legacy data and is repeatable.")
        os.environ["DATABASE_URL"] = settings.database_url.render_as_string(hide_password=False)
        sessions, snapshot = asyncio.run(verify_protocol_and_persistence(engine))
        verify_database_race_and_rollback(sessions, snapshot)
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(f'DROP DATABASE "{database}"'))
            print("Disposable integration database removed; application database and volumes preserved.")
        admin.dispose()


if __name__ == "__main__":
    main()

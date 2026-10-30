"""Shared schema, real transaction constraints and concurrent idempotency."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import threading

import pytest
from sqlalchemy import inspect, text

from purchase_storage import Base
from purchase_storage.config import Settings
from purchase_storage.database import initialize_database
from purchase_mcp.schemas.purchase_requests import PurchaseRequestInput
from purchase_mcp.services.purchase_requests import request_fingerprint


def test_schema_initialization_is_repeatable(db_engine):
    settings = Settings(db_engine.url, 1, 0)
    initialize_database(db_engine, settings)
    initialize_database(db_engine, settings)
    assert {"purchase_requests", "purchase_request_items"} <= set(inspect(db_engine).get_table_names())


@pytest.mark.postgres
def test_legacy_postgresql_migration_preserves_data_and_runs_once(db_engine):
    if db_engine.dialect.name != "postgresql":
        pytest.skip("Run compose.test.yaml to validate PostgreSQL ALTER migrations")
    # All DDL is confined to this fixture's randomly named private test schema.
    Base.metadata.drop_all(db_engine)
    statements = [
        "CREATE TABLE purchase_requests (id SERIAL PRIMARY KEY, title VARCHAR(255) NOT NULL, "
        "requester_name VARCHAR(255) NOT NULL, currency VARCHAR(3) NOT NULL DEFAULT 'BRL', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
        "CREATE TABLE purchase_request_items (id SERIAL PRIMARY KEY, purchase_request_id INTEGER NOT NULL "
        "REFERENCES purchase_requests(id) ON DELETE CASCADE, product_id VARCHAR(128), description VARCHAR(255) NOT NULL, "
        "quantity INTEGER NOT NULL CHECK (quantity > 0), unit_price NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0))",
        "INSERT INTO purchase_requests(title, requester_name) VALUES ('Legacy fixture', 'Legacy tester')",
        "INSERT INTO purchase_request_items(purchase_request_id, description, quantity, unit_price) VALUES (1, 'Legacy fixture', 2, 1.23)",
    ]
    with db_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
    settings = Settings(db_engine.url, 1, 0)
    initialize_database(db_engine, settings)
    initialize_database(db_engine, settings)
    with db_engine.connect() as connection:
        row = connection.execute(text("SELECT currency, total_amount, request_number FROM purchase_requests WHERE id=1")).one()
        assert row.currency == "BRL" and row.total_amount is None and row.request_number is None
        assert connection.execute(text("SELECT unit_price FROM purchase_request_items WHERE id=1")).scalar_one() == Decimal("1.23")
        assert connection.execute(text("SELECT count(*) FROM purchase_schema_migrations")).scalar_one() == 1
    currency = next(column for column in inspect(db_engine).get_columns("purchase_requests") if column["name"] == "currency")
    assert currency["nullable"] and currency["default"] is None


@pytest.mark.anyio
async def test_concurrent_unique_key_attempts_create_only_one_request(
    purchase_service, request_payload, repository, monkeypatch, db_counts
):
    first = await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    race_data = deepcopy(request_payload)
    race_data["idempotency_key"] = "22222222-2222-4222-8222-222222222222"
    request = PurchaseRequestInput.model_validate(race_data)
    original_lookup = repository._lookup
    barrier = threading.Barrier(2, timeout=10)
    state = threading.local()

    def simultaneous_lookup(*args):
        result = original_lookup(*args)
        if result is None and not getattr(state, "checked", False):
            state.checked = True
            barrier.wait()
        return result

    monkeypatch.setattr(repository, "_lookup", simultaneous_lookup)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(repository.create, request, request_fingerprint(request), first.items) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0].request_number == results[1].request_number
    assert {result.replayed for result in results} == {False, True}
    assert db_counts() == (2, 2)  # the initial fixture request and one race winner

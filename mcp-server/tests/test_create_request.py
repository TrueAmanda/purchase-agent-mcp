"""criar_requisicao: authoritative prices, atomic writes and durable idempotency."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from unittest.mock import MagicMock

from purchase_storage import PurchaseRequest, PurchaseRequestItem
from purchase_mcp.schemas.purchase_requests import PurchaseRequestInput
from purchase_mcp.services.product_client import ProductAPIError
from purchase_mcp.services.purchase_errors import PurchaseRequestError
from purchase_mcp.services.purchase_requests import request_fingerprint

pytestmark = pytest.mark.anyio


async def test_valid_request_persists_api_snapshot(purchase_service, request_payload, db_sessions, db_counts, product_payload):
    result = await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert type(result.request_id) is int
    assert isinstance(result.request_number, UUID)
    assert isinstance(result.created_at, datetime)
    assert result.replayed is False
    assert result.currency is None
    assert result.total == Decimal("59.97") and isinstance(result.total, Decimal)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.product_id == 1 and item.quantity == 3
    assert item.name == product_payload["title"]
    assert item.description == product_payload["description"]
    assert item.unit_price == Decimal("19.99")
    assert item.subtotal == Decimal("59.97")
    assert db_counts() == (1, 1)
    with db_sessions() as session:
        record = session.get(PurchaseRequest, result.request_id)
        assert record.request_number == str(result.request_number)
        assert record.idempotency_key == request_payload["idempotency_key"]
        assert record.total_amount == result.total
        assert record.currency is None
        assert len(record.items) == 1
        stored = record.items[0]
        assert stored.purchase_request_id == record.id
        assert stored.purchase_request.id == record.id
        assert stored.product_id == "1"
        assert stored.product_name == item.name and stored.description == item.description
        assert stored.quantity == item.quantity
        assert stored.unit_price == item.unit_price and stored.subtotal == item.subtotal


async def test_multiple_items_have_exact_decimal_totals_and_relationships(purchase_service, request_payload, db_sessions, db_counts):
    request_payload["items"].append({"product_id": 2, "quantity": 7})
    result = await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert result.total == Decimal("60.67")
    assert [item.subtotal for item in result.items] == [Decimal("59.97"), Decimal("0.70")]
    assert all(isinstance(value, Decimal) for item in result.items for value in (item.unit_price, item.subtotal))
    assert db_counts() == (1, 2)
    with db_sessions() as session:
        items = session.scalars(select(PurchaseRequestItem)).all()
        assert {item.purchase_request_id for item in items} == {result.request_id}
        assert {item.product_id for item in items} == {"1", "2"}


@pytest.mark.parametrize("items", [
    [], [{"product_id": 1, "quantity": 0}], [{"product_id": 1, "quantity": -1}],
    [{"product_id": 1, "quantity": True}], [{"product_id": 1, "quantity": 1.5}],
    [{"product_id": 1, "quantity": "3"}], [{"product_id": 0, "quantity": 1}],
    [{"product_id": -1, "quantity": 1}], [{"product_id": "1", "quantity": 1}],
    [{"product_id": 1, "quantity": 2147483648}],
    [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}],
])
def test_invalid_items_are_rejected_without_writes(request_payload, items, catalog, db_counts):
    with pytest.raises(ValidationError):
        PurchaseRequestInput.model_validate({**request_payload, "items": items})
    assert catalog.calls == []
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("field", ["price", "unit_price", "subtotal", "total", "currency"])
@pytest.mark.parametrize("location", ["request", "item"])
def test_arbitrary_llm_amounts_are_forbidden(request_payload, field, location, db_counts):
    target = request_payload if location == "request" else request_payload["items"][0]
    target[field] = "0.01"
    with pytest.raises(ValidationError):
        PurchaseRequestInput.model_validate(request_payload)
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("values", [{"title": " "}, {"requester_name": ""}, {"idempotency_key": "invalid"}])
def test_request_metadata_is_validated(request_payload, values):
    with pytest.raises(ValidationError):
        PurchaseRequestInput.model_validate({**request_payload, **values})


@pytest.mark.parametrize("failure,code", [
    (httpx.Response(404, json={"message": "missing"}), "product_not_found"),
    (httpx.Response(429, text="rate limited"), "api_http_error"),
    (httpx.Response(503, text="private body"), "api_http_error"),
    (httpx.ReadTimeout("private details"), "api_timeout"),
    (httpx.ConnectError("private details"), "api_unavailable"),
    (httpx.Response(200, json={}), "invalid_response"),
])
async def test_validation_failure_on_later_product_never_saves_partial_request(
    purchase_service, request_payload, catalog, db_counts, failure, code
):
    request_payload["items"].append({"product_id": 2, "quantity": 1})
    catalog.overrides["/products/2"] = failure
    with pytest.raises(ProductAPIError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert error.value.code == code
    assert [call.url.path for call in catalog.calls] == ["/products/1", "/products/2"]
    assert db_counts() == (0, 0)


async def test_nonexistent_id_does_not_create_a_request(purchase_service, request_payload, db_counts):
    request_payload["items"] = [{"product_id": 999999, "quantity": 1}]
    with pytest.raises(ProductAPIError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert error.value.code == "product_not_found"
    assert db_counts() == (0, 0)


async def test_database_check_failure_rolls_back_header_and_every_item(
    purchase_service, request_payload, db_counts
):
    request_payload["items"].append({"product_id": 2, "quantity": 1})

    def fail_second_item(mapper, connection, item):
        if item.product_id == "2":
            item.quantity = 0  # Force a real CHECK failure during the multi-row write.

    event.listen(PurchaseRequestItem, "before_insert", fail_second_item)
    try:
        with pytest.raises(PurchaseRequestError) as error:
            await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    finally:
        event.remove(PurchaseRequestItem, "before_insert", fail_second_item)
    assert error.value.code == "database_error"
    assert db_counts() == (0, 0)


async def test_unavailable_database_is_sanitized_and_prevents_api_calls(
    purchase_service, repository, request_payload, catalog, monkeypatch, caplog
):
    sessions = MagicMock()
    sessions.return_value.__enter__.side_effect = OperationalError("SELECT", {}, Exception("secret credentials"))
    monkeypatch.setattr(repository, "sessions", sessions)
    with pytest.raises(PurchaseRequestError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert error.value.code == "database_unavailable"
    assert "secret" not in str(error.value) + caplog.text
    assert catalog.calls == []


async def test_retry_returns_unchanged_snapshot_without_repricing(
    purchase_service, request_payload, catalog, db_counts, caplog
):
    request = PurchaseRequestInput.model_validate(request_payload)
    first = await purchase_service.create(request)
    catalog.overrides["/products/1"] = httpx.ReadTimeout("API now unavailable")
    repeated = await purchase_service.create(request)
    assert repeated.replayed is True
    assert repeated.model_dump(exclude={"replayed"}) == first.model_dump(exclude={"replayed"})
    assert len(catalog.calls) == 1
    assert db_counts() == (1, 1)
    for sensitive in (request.title, request.requester_name, str(request.idempotency_key), "19.99", "59.97"):
        assert sensitive not in caplog.text


@pytest.mark.parametrize("field", ["quantity", "title", "requester_name", "product_id"])
async def test_reusing_key_with_other_data_conflicts_without_writes(
    purchase_service, request_payload, field, db_counts, catalog
):
    await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    changed = deepcopy(request_payload)
    if field in {"quantity", "product_id"}:
        changed["items"][0][field] = 4 if field == "quantity" else 2
    else:
        changed[field] = "Changed fixture"
    with pytest.raises(PurchaseRequestError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(changed))
    assert error.value.code == "idempotency_conflict"
    assert len(catalog.calls) == 1
    assert db_counts() == (1, 1)


async def test_item_order_does_not_change_idempotency(purchase_service, request_payload, db_counts):
    request_payload["items"].append({"product_id": 2, "quantity": 1})
    first = PurchaseRequestInput.model_validate(request_payload)
    second = PurchaseRequestInput.model_validate({**request_payload, "items": list(reversed(request_payload["items"]))})
    assert request_fingerprint(first) == request_fingerprint(second)
    created = await purchase_service.create(first)
    replay = await purchase_service.create(second)
    assert replay.replayed and replay.request_number == created.request_number
    assert db_counts() == (1, 2)


@pytest.mark.parametrize("price,quantity,unit,subtotal", [
    (0.1, 3, "0.10", "0.30"), (19.995, 2, "20.00", "40.00"), (0.005, 3, "0.01", "0.03"),
])
async def test_decimal_rounding_is_explicit(purchase_service, request_payload, catalog, price, quantity, unit, subtotal):
    catalog.products[1]["price"] = price
    request_payload["items"][0]["quantity"] = quantity
    result = await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert result.items[0].unit_price == Decimal(unit)
    assert result.items[0].subtotal == Decimal(subtotal)
    assert result.total == Decimal(subtotal)


@pytest.mark.parametrize("price,quantity", [(1000000000000, 1), (999999999999.99, 2147483647)])
async def test_amounts_outside_storage_precision_are_not_saved(purchase_service, request_payload, catalog, db_counts, price, quantity):
    catalog.products[1]["price"] = price
    request_payload["items"][0]["quantity"] = quantity
    with pytest.raises(PurchaseRequestError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert error.value.code == "invalid_amount"
    assert db_counts() == (0, 0)

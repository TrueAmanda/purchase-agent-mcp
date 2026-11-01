"""Financial/stock constraints are enforced before transaction, with real SQL."""

from decimal import Decimal

from pydantic import ValidationError
import pytest

from purchase_mcp.schemas.purchase_requests import PurchaseRequestInput
from purchase_mcp.services.purchase_errors import PurchaseRequestError
from purchase_mcp.services.purchase_requests import request_fingerprint

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("constraints,code", [
    ({"max_total": "59.96"}, "budget_exceeded"),
    ({"max_unit_price": "19.98"}, "budget_exceeded"),
    ({"max_total": "100", "max_unit_price": "19.98"}, "budget_exceeded"),
    ({"quantity": 2}, "purchase_constraints_violated"),
    ({"quantity": 0}, "purchase_constraints_violated"),
    ({"category": "different-category"}, "purchase_constraints_violated"),
    ({"product_name": "different exact model"}, "purchase_constraints_violated"),
])
async def test_constraints_reject_invalid_selection_without_any_write(
    purchase_service, request_payload, db_counts, constraints, code,
):
    request_payload["constraints"] = constraints
    with pytest.raises(PurchaseRequestError) as error:
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert error.value.code == code
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("constraints", [
    {"max_total": "59.97"}, {"max_unit_price": "19.99"},
    {"max_total": "59.97", "max_unit_price": "19.99", "quantity": 3, "category": "fixture-category"},
    {"product_name": "fixture product one"}, {"product_name": "FIXTURE-1"},
])
async def test_exact_budget_boundary_and_per_unit_are_not_confused(
    purchase_service, request_payload, db_counts, constraints,
):
    request_payload["constraints"] = constraints
    result = await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert result.total == Decimal("59.97") and db_counts() == (1, 1)


async def test_combined_items_exceed_total_even_when_each_is_below_it(purchase_service, request_payload, db_counts):
    request_payload["items"].append({"product_id": 2, "quantity": 3})
    request_payload["constraints"] = {"max_total": "60.26"}
    with pytest.raises(PurchaseRequestError, match="budget_exceeded"):
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert db_counts() == (0, 0)


@pytest.mark.parametrize("field,value", [("stock", 2), ("minimumOrderQuantity", 4)])
async def test_catalog_stock_and_order_minimum_are_not_merely_prompt_rules(
    purchase_service, request_payload, catalog, db_counts, field, value,
):
    catalog.products[1][field] = value
    with pytest.raises(PurchaseRequestError, match="purchase_constraints_violated"):
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert db_counts() == (0, 0)


async def test_current_prices_not_search_snapshot_determine_budget(purchase_service, request_payload, catalog, db_counts):
    request_payload["constraints"] = {"max_total": "60"}
    catalog.products[1]["price"] = "20.01"
    with pytest.raises(PurchaseRequestError, match="budget_exceeded"):
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert db_counts() == (0, 0)


async def test_constraint_retry_is_idempotent_and_changed_cap_conflicts(purchase_service, request_payload, catalog, db_counts):
    request_payload["constraints"] = {"max_total": "60"}
    request = PurchaseRequestInput.model_validate(request_payload)
    first = await purchase_service.create(request)
    request_payload["constraints"]["max_total"] = "60.00"
    second = PurchaseRequestInput.model_validate(request_payload)
    assert request_fingerprint(request) == request_fingerprint(second)
    replay = await purchase_service.create(second)
    assert replay.replayed and replay.request_number == first.request_number
    request_payload["constraints"]["max_total"] = "10"
    with pytest.raises(PurchaseRequestError, match="idempotency_conflict"):
        await purchase_service.create(PurchaseRequestInput.model_validate(request_payload))
    assert db_counts() == (1, 1) and len(catalog.calls) == 1


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "100.001", True])
def test_invalid_monetary_constraints_are_rejected(request_payload, value):
    request_payload["constraints"] = {"max_total": value}
    with pytest.raises(ValidationError):
        PurchaseRequestInput.model_validate(request_payload)

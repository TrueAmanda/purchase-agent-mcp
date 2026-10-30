"""Validation, authoritative pricing, idempotency and transactional persistence."""

import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from purchase_storage import Base, PurchaseRequest, PurchaseRequestItem
from fixtures import search_payload
from purchase_mcp.repositories.purchase_requests import PurchaseRequestRepository
from purchase_mcp.schemas.purchase_requests import PurchaseRequestInput
from purchase_mcp.services.product_client import ProductAPIError, ProductClient
from purchase_mcp.services.purchase_errors import PurchaseRequestError
from purchase_mcp.services.purchase_requests import PurchaseRequestService, request_fingerprint


def request_data():
    return {
        "title": "Test purchase", "requester_name": "Test requester", "idempotency_key": str(uuid4()),
        "items": [{"product_id": 1, "quantity": 2}],
    }


class PurchaseValidationTests(unittest.TestCase):
    def test_rejects_empty_items_invalid_counts_and_untrusted_amounts(self):
        invalid_items = [[], [{"product_id": 1, "quantity": 0}], [{"product_id": 1, "quantity": -1}],
                         [{"product_id": 0, "quantity": 1}], [{"product_id": 1, "quantity": True}],
                         [{"product_id": "1", "quantity": 1}], [{"product_id": 1, "quantity": 1.5}],
                         [{"product_id": 1, "quantity": 1, "unit_price": "0.01"}],
                         [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}]]
        for items in invalid_items:
            with self.subTest(items=items), self.assertRaises(ValidationError):
                PurchaseRequestInput.model_validate({**request_data(), "items": items})
        for extra in ({"total": "0.01"}, {"price": "0.01"}, {"currency": "BRL"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                PurchaseRequestInput.model_validate({**request_data(), **extra})
        for values in ({"title": "   "}, {"requester_name": ""}, {"idempotency_key": "invalid"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                PurchaseRequestInput.model_validate({**request_data(), **values})

    def test_fingerprint_normalizes_item_order_and_ignores_retry_key(self):
        data = request_data()
        data["items"].append({"product_id": 2, "quantity": 3})
        original = PurchaseRequestInput.model_validate(data)
        reordered = PurchaseRequestInput.model_validate({
            **data, "idempotency_key": str(uuid4()), "items": list(reversed(data["items"]))
        })
        self.assertEqual(request_fingerprint(original), request_fingerprint(reordered))


class PurchaseServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.repository = PurchaseRequestRepository(self.sessions)
        self.api_calls = []

    def tearDown(self):
        self.engine.dispose()

    def counts(self):
        with self.sessions() as session:
            return (session.scalar(select(func.count()).select_from(PurchaseRequest)),
                    session.scalar(select(func.count()).select_from(PurchaseRequestItem)))

    def handler(self, request):
        self.api_calls.append(request)
        payload = search_payload()["products"][0]
        payload["id"] = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=payload)

    async def test_prices_totals_and_stored_snapshots_come_from_api(self):
        request = PurchaseRequestInput.model_validate(request_data())
        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(self.handler)) as http:
            result = await PurchaseRequestService(ProductClient(http), self.repository).create(request)
        self.assertFalse(result.replayed)
        self.assertEqual(result.total, Decimal("39.98"))
        self.assertIsNone(result.currency)
        self.assertEqual(result.items[0].unit_price, Decimal("19.99"))
        self.assertEqual(result.items[0].subtotal, Decimal("39.98"))
        self.assertEqual(self.counts(), (1, 1))
        with self.sessions() as session:
            record = session.get(PurchaseRequest, result.request_id)
            self.assertEqual(record.request_number, str(result.request_number))
            self.assertEqual(record.total_amount, result.total)
            self.assertEqual(record.items[0].description, result.items[0].description)

    async def test_retry_returns_persisted_data_without_another_api_call(self):
        request = PurchaseRequestInput.model_validate(request_data())
        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(self.handler)) as http:
            service = PurchaseRequestService(ProductClient(http), self.repository)
            first = await service.create(request)
            second = await service.create(request)
        self.assertTrue(second.replayed)
        self.assertEqual(first.request_number, second.request_number)
        self.assertEqual(first.total, second.total)
        self.assertEqual(len(self.api_calls), 1)
        self.assertEqual(self.counts(), (1, 1))

    async def test_database_unavailability_is_sanitized_before_calling_api(self):
        def handler(request):
            self.fail("An unavailable database must fail before consulting products.")

        with patch.object(self.repository, "sessions") as sessions:
            sessions.return_value.__enter__.side_effect = OperationalError("SELECT", {}, Exception("secret credentials"))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                with self.assertLogs("purchase_mcp.repositories.purchase_requests", level="ERROR") as logs:
                    with self.assertRaises(PurchaseRequestError) as error:
                        await PurchaseRequestService(ProductClient(http), self.repository).create(PurchaseRequestInput.model_validate(request_data()))
        self.assertEqual(error.exception.code, "database_unavailable")
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("secret", " ".join(logs.output))

    async def test_same_key_different_payload_conflicts(self):
        request = PurchaseRequestInput.model_validate(request_data())
        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(self.handler)) as http:
            service = PurchaseRequestService(ProductClient(http), self.repository)
            await service.create(request)
            changed = request.model_copy(deep=True)
            changed.items[0].quantity = 3
            with self.assertRaises(PurchaseRequestError) as error:
                await service.create(changed)
        self.assertEqual(error.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.api_calls), 1)
        self.assertEqual(self.counts(), (1, 1))

    async def test_upstream_failure_leaves_no_partial_request(self):
        data = request_data()
        data["items"].append({"product_id": 999999, "quantity": 1})

        def handler(request):
            if request.url.path.endswith("999999"):
                return httpx.Response(404, json={"message": "missing"})
            return self.handler(request)

        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(handler)) as http:
            with self.assertRaises(ProductAPIError) as error:
                await PurchaseRequestService(ProductClient(http), self.repository).create(PurchaseRequestInput.model_validate(data))
        self.assertEqual(error.exception.code, "product_not_found")
        self.assertEqual(self.counts(), (0, 0))

    async def test_database_failure_rolls_back_header_and_all_items(self):
        data = request_data()
        data["items"].append({"product_id": 2, "quantity": 1})

        def fail_second_item(mapper, connection, target):
            if target.product_id == "2":
                target.quantity = 0  # Force a genuine database CHECK failure during flush.

        event.listen(PurchaseRequestItem, "before_insert", fail_second_item)
        try:
            async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(self.handler)) as http:
                with self.assertRaises(PurchaseRequestError) as error:
                    await PurchaseRequestService(ProductClient(http), self.repository).create(PurchaseRequestInput.model_validate(data))
        finally:
            event.remove(PurchaseRequestItem, "before_insert", fail_second_item)
        self.assertEqual(error.exception.code, "database_error")
        self.assertEqual(self.counts(), (0, 0))

    async def test_unique_constraint_race_recovers_existing_request(self):
        request = PurchaseRequestInput.model_validate(request_data())
        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(self.handler)) as http:
            first = await PurchaseRequestService(ProductClient(http), self.repository).create(request)
        lookup = self.repository._lookup
        calls = 0

        def race_lookup(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else lookup(*args)

        with patch.object(self.repository, "_lookup", side_effect=race_lookup):
            replay = self.repository.create(request, request_fingerprint(request), first.items)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.request_number, first.request_number)
        self.assertEqual(self.counts(), (1, 1))

    async def test_cent_rounding_and_storage_limits(self):
        def rounded(request):
            payload = search_payload()["products"][0]
            payload["price"] = 19.995
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(rounded)) as http:
            result = await PurchaseRequestService(ProductClient(http), self.repository).create(PurchaseRequestInput.model_validate(request_data()))
        self.assertEqual(result.items[0].unit_price, Decimal("20.00"))
        self.assertEqual(result.total, Decimal("40.00"))

        def excessive(request):
            payload = search_payload()["products"][0]
            payload["price"] = 999999999999.99
            return httpx.Response(200, json=payload)

        data = request_data()
        data["items"][0]["quantity"] = 2147483647
        async with httpx.AsyncClient(base_url="https://dummyjson.com", transport=httpx.MockTransport(excessive)) as http:
            with self.assertRaisesRegex(PurchaseRequestError, "invalid_amount"):
                await PurchaseRequestService(ProductClient(http), self.repository).create(PurchaseRequestInput.model_validate(data))
        self.assertEqual(self.counts(), (1, 1))


if __name__ == "__main__":
    unittest.main()

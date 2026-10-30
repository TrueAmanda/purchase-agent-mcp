"""Infrastructure checks without an external database; run using unittest."""

import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.database import initialize_database
from app.main import app
from app.models import Base, PurchaseRequest, PurchaseRequestItem


class ConfigurationTests(unittest.TestCase):
    def test_password_is_not_interpreted_as_url_syntax(self):
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "p@ss:/word?#"}, clear=True):
            settings = Settings.from_environment()
        self.assertEqual(settings.database_url.password, "p@ss:/word?#")
        self.assertNotIn("p@ss", str(settings.database_url))

    def test_invalid_configuration_does_not_expose_secrets(self):
        for values in (
            {},
            {"DATABASE_URL": "secret-invalid-url"},
            {"POSTGRES_PASSWORD": "secret", "DB_STARTUP_ATTEMPTS": "0"},
        ):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(RuntimeError) as context:
                    Settings.from_environment()
                self.assertNotIn("secret", str(context.exception))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "test"}, clear=True):
            self.settings = Settings.from_environment()

    def tearDown(self):
        self.engine.dispose()

    def test_initialization_is_repeatable(self):
        initialize_database(self.engine, self.settings)
        initialize_database(self.engine, self.settings)
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {"purchase_requests", "purchase_request_items"},
        )

    def test_postgres_schema_uses_numeric_and_foreign_key(self):
        ddl = str(CreateTable(PurchaseRequestItem.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn("NUMERIC(14, 2)", ddl)
        self.assertIn("REFERENCES purchase_requests (id) ON DELETE CASCADE", ddl)

    def test_item_relationship_decimal_and_constraints(self):
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            request = PurchaseRequest(title="Office", requester_name="Test")
            request.items.append(PurchaseRequestItem(
                description="Paper", quantity=2, unit_price=Decimal("19.99")
            ))
            session.add(request)
            session.commit()
            session.expire_all()
            item = session.query(PurchaseRequestItem).one()
            self.assertEqual(item.unit_price, Decimal("19.99"))
            self.assertIsInstance(item.unit_price, Decimal)
            item.quantity = 0
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.delete(request)
            session.commit()
            self.assertEqual(session.query(PurchaseRequestItem).count(), 0)

    def test_connection_retries_then_initializes(self):
        engine = MagicMock()
        connection = MagicMock()
        engine.connect.side_effect = [
            OperationalError("SELECT 1", {}, Exception("offline")), connection
        ]
        with patch("app.database.time.sleep") as sleep, patch.object(Base.metadata, "create_all") as create:
            initialize_database(engine, self.settings)
        self.assertEqual(engine.connect.call_count, 2)
        sleep.assert_called_once()
        create.assert_called_once_with(engine)

    def test_retry_exhaustion_does_not_create_tables(self):
        engine = MagicMock()
        engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("secret"))
        settings = Settings(self.settings.database_url, 2, 0)
        with patch("app.database.time.sleep"), patch.object(Base.metadata, "create_all") as create:
            with self.assertRaisesRegex(RuntimeError, "Database unavailable") as context:
                initialize_database(engine, settings)
        self.assertEqual(engine.connect.call_count, 2)
        self.assertNotIn("secret", str(context.exception))
        create.assert_not_called()

    def test_schema_failure_aborts_startup(self):
        with patch.object(Base.metadata, "create_all", side_effect=SQLAlchemyError("secret")):
            with self.assertRaisesRegex(RuntimeError, "Unable to initialize"):
                initialize_database(self.engine, self.settings)


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_and_database_outage(self):
        engine = MagicMock()
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "test"}, clear=True), patch(
            "app.main.build_engine", return_value=engine
        ), patch("app.main.initialize_database"):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    response = await client.get("/health")
                    self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
                    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("secret"))
                    response = await client.get("/health")
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("secret", response.text)
        engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()

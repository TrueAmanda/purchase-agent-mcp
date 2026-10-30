"""Database connections and versioned startup migrations, without web imports."""

import logging
import time
from importlib.resources import files

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from purchase_storage.config import Settings
from purchase_storage.models import Base

logger = logging.getLogger(__name__)


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url, pool_pre_ping=True, pool_timeout=5, hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000"},
    )


def initialize_database(engine: Engine, settings: Settings) -> None:
    for attempt in range(1, settings.startup_attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            break
        except OperationalError:
            if attempt == settings.startup_attempts:
                raise RuntimeError("Database unavailable after startup retries; check PostgreSQL availability and credentials.") from None
            logger.warning("Database unavailable; retrying startup (%s/%s).", attempt, settings.startup_attempts)
            time.sleep(settings.startup_interval_seconds)

    try:
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                # Serialize schema initialization across the two independent processes.
                connection.execute(text("SELECT pg_advisory_xact_lock(735102904)"))
            Base.metadata.create_all(connection)
            if engine.dialect.name == "postgresql":
                connection.execute(text(
                    "CREATE TABLE IF NOT EXISTS purchase_schema_migrations "
                    "(version VARCHAR(64) PRIMARY KEY)"
                ))
                applied = connection.execute(text(
                    "SELECT version FROM purchase_schema_migrations WHERE version = :version"
                ), {"version": "001_purchase_creation"}).scalar_one_or_none()
                if applied is None:
                    migration = files("purchase_storage").joinpath("migrations/001_purchase_creation.sql").read_text()
                    for statement in migration.split(";"):
                        if statement.strip():
                            connection.execute(text(statement))
                    connection.execute(text(
                        "INSERT INTO purchase_schema_migrations (version) VALUES (:version)"
                    ), {"version": "001_purchase_creation"})
    except SQLAlchemyError:
        raise RuntimeError("Unable to initialize database tables; check schema permissions and PostgreSQL availability.") from None
    logger.info("Database ready; purchase schema initialized.")

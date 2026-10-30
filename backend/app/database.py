"""Database initialization and request-scoped sessions."""

import logging
import time
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Base

logger = logging.getLogger("uvicorn.error")


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
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
                raise RuntimeError(
                    "Database unavailable after startup retries; check PostgreSQL "
                    "availability and credentials."
                ) from None
            logger.warning(
                "Database unavailable; retrying startup (%s/%s).",
                attempt, settings.startup_attempts,
            )
            time.sleep(settings.startup_interval_seconds)

    try:
        Base.metadata.create_all(engine)
    except SQLAlchemyError:
        raise RuntimeError(
            "Unable to initialize database tables; check schema permissions "
            "and PostgreSQL availability."
        ) from None
    logger.info("Database ready; purchase tables initialized.")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session

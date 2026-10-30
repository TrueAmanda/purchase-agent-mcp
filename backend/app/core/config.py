"""Environment configuration without external configuration dependencies."""

import os
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


@dataclass(frozen=True)
class Settings:
    database_url: URL
    startup_attempts: int
    startup_interval_seconds: float

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            attempts = int(os.getenv("DB_STARTUP_ATTEMPTS", "30"))
            interval = float(os.getenv("DB_STARTUP_INTERVAL_SECONDS", "2"))
            if attempts < 1 or not 0 <= interval <= 60:
                raise ValueError
            raw_url = os.getenv("DATABASE_URL", "")
            if raw_url:
                database_url = make_url(raw_url)
                if database_url.drivername != "postgresql+psycopg":
                    raise ValueError
            else:
                password = os.environ["POSTGRES_PASSWORD"]
                if not password:
                    raise ValueError
                database_url = URL.create(
                    "postgresql+psycopg",
                    username=os.getenv("POSTGRES_USER", "purchase_agent"),
                    password=password,
                    host=os.getenv("POSTGRES_HOST", "localhost"),
                    port=int(os.getenv("POSTGRES_PORT", "5432")),
                    database=os.getenv("POSTGRES_DB", "purchase_agent"),
                )
        except (KeyError, ValueError, ArgumentError):
            raise RuntimeError(
                "Invalid database configuration. Set POSTGRES_PASSWORD or a "
                "postgresql+psycopg DATABASE_URL and valid startup retry settings."
            ) from None
        return cls(database_url, attempts, interval)

"""FastAPI session dependency using the shared persistence infrastructure."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from purchase_storage.database import build_engine, initialize_database

__all__ = ["build_engine", "initialize_database", "get_session"]


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session

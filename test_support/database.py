"""Shared isolated SQL storage for MCP and backend integration tests."""

import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from purchase_storage import Base


@pytest.fixture
def isolated_db_engine(tmp_path):
    raw_url = os.getenv("TEST_DATABASE_URL")
    if not raw_url:
        engine = create_engine(
            f"sqlite:///{(tmp_path / 'isolated-test.sqlite').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 10}, hide_parameters=True,
        )

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        try:
            Base.metadata.create_all(engine)
            yield engine
        finally:
            engine.dispose()
        return

    url = make_url(raw_url)
    if url.drivername != "postgresql+psycopg" or url.database != "purchase_mcp_test":
        raise ValueError("TEST_DATABASE_URL must point to a postgresql+psycopg database named purchase_mcp_test.")
    schema = "integration_test_" + uuid4().hex
    assert re.fullmatch(r"integration_test_[a-f0-9]{32}", schema)
    admin = create_engine(url, hide_parameters=True)
    engine = None
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        engine = create_engine(
            url, hide_parameters=True,
            connect_args={"options": f"-c search_path={schema}", "connect_timeout": 3},
            execution_options={"schema_translate_map": {None: schema}},
        )
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()

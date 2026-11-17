"""Shared test fixtures.

By default the database-backed tests use a throwaway SQLite file. Set
``TEST_DATABASE_URL`` to run the same tests against a real Postgres instead:

    TEST_DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db" python -m pytest -q

The fixture deletes every row from the app tables before each test, so point it at
a scratch database - never at one holding data you want to keep. It deliberately
does not fall back to ``DATABASE_URL`` for that reason.
"""

import os

import pytest
from sqlalchemy import delete

from airoad.db import build_engine
from airoad.drivers import drivers_table, driver_tokens_table, metadata as driver_metadata
from airoad.ratelimit import attempts_table, metadata as rate_limit_metadata
from airoad.store import metadata as store_metadata, reports_table

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"


def shared_database_url() -> str | None:
    """The Postgres URL under test, if the run was pointed at one."""
    return os.environ.get(TEST_DATABASE_URL_ENV) or None


def reset_shared_database(url: str) -> None:
    """Create the tables if needed and empty them so each test starts clean."""
    engine = build_engine(url)
    try:
        store_metadata.create_all(engine)
        driver_metadata.create_all(engine)
        rate_limit_metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(delete(reports_table))
            connection.execute(delete(driver_tokens_table))
            connection.execute(delete(drivers_table))
            connection.execute(delete(attempts_table))
    finally:
        engine.dispose()


@pytest.fixture
def database_url(tmp_path):
    """SQLite file by default, ``TEST_DATABASE_URL`` when it is set."""
    shared = shared_database_url()
    if shared:
        reset_shared_database(shared)
        return shared
    return f"sqlite:///{tmp_path / 'test.db'}"


def pytest_report_header(config):
    shared = shared_database_url()
    if shared:
        # Never echo credentials; the driver and host are enough to confirm what ran.
        target = shared.split("@")[-1] if "@" in shared else shared
        return f"database backend: postgres ({target})"
    return "database backend: sqlite (set TEST_DATABASE_URL to use Postgres)"

"""Postgres-only proof that one instance holds one connection.

Skipped unless ``TEST_DATABASE_URL`` points at a scratch Postgres, which is what
the CI Postgres job and the README's Postgres command set. SQLite cannot show
this: it has no server sessions and pools differently, so a SQLite URL skips
here too rather than failing the pool-size assertion further down.

Use a scratch database - the fixtures empty its tables.
"""

import os

import pytest
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from airoad.db import get_engine
from airoad.drivers import create_driver_store
from airoad.ratelimit import create_limiter
from airoad.store import create_store

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def points_at_postgres(database_url: str) -> bool:
    """SQLite is a valid ``TEST_DATABASE_URL`` for the rest of the suite, not here."""
    try:
        return make_url(database_url).get_backend_name() in {"postgresql", "postgres"}
    except ArgumentError:
        return False


pytestmark = pytest.mark.skipif(
    not points_at_postgres(TEST_DATABASE_URL),
    reason=(
        "TEST_DATABASE_URL must point at a scratch Postgres; "
        "see 'Verifying against Postgres' in README.md"
    ),
)


def test_three_components_share_one_postgres_connection(database_url):
    store = create_store(database_url)
    limiter = create_limiter("30/minute", database_url)
    drivers = create_driver_store(database_url)

    engine = get_engine(database_url)
    # All three work through the one engine, whose pool cannot open a second
    # connection (size 1 and no overflow - a bigger pool would still reuse one
    # connection here and hide the extra capacity).
    assert store._engine is limiter._engine is drivers._engine is engine
    assert engine.pool.size() == 1
    assert engine.pool._max_overflow == 0

    connections = set()

    @event.listens_for(engine, "after_cursor_execute")
    def _record_connection(conn, cursor, statement, parameters, context, executemany):
        connections.add(id(conn.connection.dbapi_connection))

    try:
        # Every component, in turn, over that one connection.
        store.add_many("driver-1", [{"report_id": "r1"}])
        assert store.count("driver-1") == 1
        _, token = drivers.create_driver("Asha")
        assert drivers.find_driver(token) is not None
        assert limiter.check("1.2.3.4").allowed is True
    finally:
        event.remove(engine, "after_cursor_execute", _record_connection)

    assert len(connections) == 1

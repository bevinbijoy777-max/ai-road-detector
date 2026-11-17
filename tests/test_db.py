"""Engine tests: one connection per instance, reused.

Every Vercel function instance builds its own engine, so it must hold exactly one
Postgres connection and reuse it - a fresh connection per statement would both
exhaust Postgres and make a sync batch reconnect once per report. Nothing here
opens a Postgres socket: ``create_engine`` resolves the dialect and pool without
connecting, and reuse is asserted against a local SQLite engine instead.
"""

import threading

import pytest
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

from airoad.db import (
    POOL_RECYCLE_SECONDS,
    POOL_TIMEOUT_SECONDS,
    POSTGRES_POOL_SIZE,
    build_engine,
    disable_prepared_statements,
    get_engine,
)
from airoad.drivers import create_driver_store
from airoad.ratelimit import create_limiter
from airoad.store import SqlReportStore, create_store

#: Pooled and direct URLs in the shapes the providers hand out. No credentials,
#: and no connection is ever made.
POOLED_URLS = [
    "postgresql://user:pass@ep-cool-name-123456-pooler.us-east-2.aws.neon.tech/neondb",
    "postgresql+psycopg://user:pass@ep-cool-name-123456-pooler.us-east-2.aws.neon.tech/neondb",
    "postgresql://default:pass@ep-cool-name-123456-pooler.us-east-1.postgres.vercel-storage.com/verceldb",
    "postgresql://postgres.abcdefgh:pass@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    # Transaction mode on a host that does not (or no longer) says "pooler".
    "postgresql://postgres:pass@db.internal.example.com:6543/postgres",
]

DIRECT_URLS = [
    "postgresql://user:pass@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb",
    "postgresql+psycopg://user:pass@db.example.com:5432/airoad",
]


@pytest.mark.parametrize("url", POOLED_URLS + DIRECT_URLS)
def test_postgres_keeps_and_reuses_one_connection(url):
    """A pooled URL must pool client-side too, or each query reconnects."""
    engine = build_engine(url)
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == POSTGRES_POOL_SIZE == 1
        assert engine.pool._max_overflow == 0
        assert engine.pool._pre_ping is True
        assert engine.pool._timeout == POOL_TIMEOUT_SECONDS
        assert engine.pool._recycle == POOL_RECYCLE_SECONDS
    finally:
        engine.dispose()


@pytest.mark.parametrize("url", POOLED_URLS + DIRECT_URLS)
def test_n_instances_hold_n_connections_not_fifteen(url):
    """SQLAlchemy's default 5 + 10 overflow per instance is what exhausted Postgres."""
    engines = [build_engine(url) for _ in range(20)]
    try:
        assert sum(engine.pool.size() for engine in engines) == 20
    finally:
        for engine in engines:
            engine.dispose()


def test_sequential_checkouts_reuse_the_same_dbapi_connection(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'reuse.db'}")
    try:
        with engine.connect() as first:
            first.exec_driver_sql("select 1")
            first_raw = first.connection.dbapi_connection
        with engine.connect() as second:
            second.exec_driver_sql("select 1")
            assert second.connection.dbapi_connection is first_raw
    finally:
        engine.dispose()


def test_sync_batch_opens_one_connection_for_every_report(tmp_path):
    """The motivation: add_many loops per report, and must not reconnect per report."""
    engine = build_engine(f"sqlite:///{tmp_path / 'batch.db'}")
    connects = []
    event.listen(engine, "connect", lambda *args: connects.append(1))
    try:
        store = SqlReportStore(engine)
        outcome = store.add_many(
            "driver-1", [{"report_id": f"r{i}"} for i in range(5)]
        )

        assert outcome.stored == 5
        assert store.count("driver-1") == 5
        assert len(connects) == 1
    finally:
        engine.dispose()


def test_sqlite_fallback_keeps_sharing_connections_between_threads(tmp_path):
    """The dev/test database must stay usable from TestClient's worker thread."""
    engine = build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("select 1").scalar_one() == 1
    finally:
        engine.dispose()


# --- prepared statements ---------------------------------------------------


@pytest.mark.parametrize("url", POOLED_URLS + DIRECT_URLS)
def test_every_postgres_engine_disables_prepared_statements(url):
    """A prepared statement cannot outlive the session it was created in."""
    engine = build_engine(url)
    try:
        assert event.contains(engine, "connect", disable_prepared_statements)
    finally:
        engine.dispose()


def test_disable_prepared_statements_clears_the_psycopg_threshold():
    class Psycopg3Connection:
        prepare_threshold = 5

    connection = Psycopg3Connection()
    disable_prepared_statements(connection, None)
    assert connection.prepare_threshold is None


def test_disable_prepared_statements_ignores_other_dbapis():
    """psycopg2 and non-Postgres drivers have no such attribute - do not raise."""

    class OtherConnection:
        pass

    connection = OtherConnection()
    disable_prepared_statements(connection, None)
    assert not hasattr(connection, "prepare_threshold")


# --- one engine per DATABASE_URL --------------------------------------------


def test_components_share_one_cached_engine_per_url(tmp_path):
    """Three components, one connection: they must not each pool their own."""
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    store = create_store(url)
    limiter = create_limiter("30/minute", url)
    drivers = create_driver_store(url)

    shared = get_engine(url)
    assert store._engine is shared
    assert limiter._engine is shared
    assert drivers._engine is shared
    # Cached per URL, not globally.
    assert get_engine(f"sqlite:///{tmp_path / 'other.db'}") is not shared


def test_concurrent_callers_all_get_the_one_engine(database_url):
    """A cold-start race must not leave an instance holding a second engine."""
    engines = []
    threads = [
        threading.Thread(target=lambda: engines.append(get_engine(database_url)))
        for _ in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(engines) == len(threads)
    assert len({id(engine) for engine in engines}) == 1

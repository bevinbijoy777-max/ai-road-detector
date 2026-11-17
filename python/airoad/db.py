"""Database plumbing shared by the report, driver and rate-limit tables.

``DATABASE_URL`` selects the engine: Postgres in production, a local SQLite file
for development and tests. The length constants live here so the table definitions
and the request validation cannot drift apart - a value that is too long is
rejected with a 422 before Postgres ever sees it.

Serverless deployments are connection-hungry: every function instance builds its
own engine, and SQLAlchemy's default pool would let each one hold up to fifteen
Postgres connections. :func:`build_engine` keeps the per-instance footprint to a
single connection, held open and reused for the instance's lifetime.

Every Postgres connection also has psycopg 3's server-side prepared statements
switched off: they exist only inside one Postgres session, and a transaction-mode
pooler (Supabase's shared pooler, for instance) hands each transaction a
different session.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

#: Connections one instance may hold open. One, reused: a sync batch writes all
#: of its reports over the same connection instead of reconnecting per report,
#: and N instances still mean N connections.
POSTGRES_POOL_SIZE = 1

#: How long a request waits for the instance's one connection before failing,
#: rather than queueing until the function times out.
POOL_TIMEOUT_SECONDS = 10

#: Drop a connection this long after it was created: long enough to be reused
#: across many requests, short enough to outlive no idle-timeout middlebox.
POOL_RECYCLE_SECONDS = 300

DEFAULT_SQLITE_URL = "sqlite:///reports.db"

#: Longest accepted report id (matches ``reports.report_id``).
MAX_REPORT_ID_LENGTH = 64

#: Longest driver id (matches ``drivers.driver_id`` and ``reports.driver_id``).
MAX_DRIVER_ID_LENGTH = 32


class StorageNotConfigured(RuntimeError):
    """Raised when the deployment has no durable database configured."""


def disable_prepared_statements(dbapi_connection, connection_record=None) -> None:
    """Keep psycopg 3 from preparing statements on the server.

    psycopg prepares a statement after its fifth execution on a *connection*,
    and the prepared statement only exists in that connection's Postgres
    session. A transaction-mode pooler gives each transaction a different
    session, so the statement is either missing or already taken on the next
    one. psycopg's own docs and Supabase's both prescribe ``prepare_threshold =
    None``. Only psycopg 3 has the attribute, so this is a no-op elsewhere.
    """
    if hasattr(dbapi_connection, "prepare_threshold"):
        dbapi_connection.prepare_threshold = None


def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            # TestClient and worker threads share the connection.
            connect_args={"check_same_thread": False},
        )

        # pysqlite's legacy transaction handling commits implicitly around DDL
        # and SAVEPOINT statements, which silently defeats a rollback and makes a
        # skipped duplicate look committed. Turn it off and emit BEGIN ourselves,
        # as SQLAlchemy's SQLite docs prescribe for savepoints to work.
        @event.listens_for(engine, "connect")
        def _disable_pysqlite_begin(dbapi_connection, connection_record) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _emit_begin(connection) -> None:
            connection.exec_driver_sql("BEGIN")

        return engine

    # Postgres, with or without a pooler in front: exactly one connection per
    # instance, kept and reused. A sync batch therefore costs one connect for the
    # whole batch rather than one per report, while the cap still keeps N
    # instances at N connections.
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=POSTGRES_POOL_SIZE,
        max_overflow=0,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
    )

    # Applies to every connection the pool opens, not just the first. Whether a
    # pooler sits in front is a URL detail we would only be guessing at, and the
    # plan cache it costs is not worth a wrong guess on the deployment that has
    # one.
    event.listen(engine, "connect", disable_prepared_statements)

    return engine


def resolve_database_url(database_url: Optional[str] = None) -> str:
    """Explicit URL, else ``DATABASE_URL``, else a local SQLite file.

    A serverless filesystem is read-only apart from an ephemeral /tmp, so on
    Vercel a local file would silently lose data; that is a configuration error.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if url is None:
        if os.environ.get("VERCEL"):
            raise StorageNotConfigured(
                "DATABASE_URL is not set. Add a Postgres connection string "
                "(Vercel Postgres, Neon, Supabase, ...) to the project's "
                "environment variables so reports and rate limits are shared."
            )
        url = DEFAULT_SQLITE_URL
    return url

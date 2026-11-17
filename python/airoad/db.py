"""Database plumbing shared by the report, driver and rate-limit tables.

``DATABASE_URL`` selects the engine: Postgres in production, a local SQLite file
for development and tests. The length constants live here so the table definitions
and the request validation cannot drift apart - a value that is too long is
rejected with a 422 before Postgres ever sees it.

Serverless deployments are connection-hungry: every function instance builds its
own engine, and SQLAlchemy's default pool would let each one hold up to fifteen
Postgres connections. :func:`build_engine` keeps the per-instance footprint to a
single connection, held open and reused for the instance's lifetime, whether
``DATABASE_URL`` reaches Postgres directly or through a pooler.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

#: Connections one instance may hold open. One, reused: a sync batch writes all
#: of its reports over the same connection instead of reconnecting per report,
#: and N instances still mean N connections for the pooler to multiplex.
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


def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            pool_pre_ping=True,
            # TestClient and worker threads share the connection.
            connect_args={"check_same_thread": False},
        )

    # Postgres, with or without a pooler in front: exactly one connection per
    # instance, kept and reused. A sync batch therefore costs one connect for the
    # whole batch rather than one per report, while the cap still keeps N
    # instances at N connections - whether Postgres or the pooler holds them.
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=POSTGRES_POOL_SIZE,
        max_overflow=0,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
    )


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

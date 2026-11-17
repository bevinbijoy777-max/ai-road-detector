"""Database plumbing shared by the report, driver and rate-limit tables.

``DATABASE_URL`` selects the engine: Postgres in production, a local SQLite file
for development and tests. The length constants live here so the table definitions
and the request validation cannot drift apart - a value that is too long is
rejected with a 422 before Postgres ever sees it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DEFAULT_SQLITE_URL = "sqlite:///reports.db"

#: Longest accepted report id (matches ``reports.report_id``).
MAX_REPORT_ID_LENGTH = 64

#: Longest driver id (matches ``drivers.driver_id`` and ``reports.driver_id``).
MAX_DRIVER_ID_LENGTH = 32


class StorageNotConfigured(RuntimeError):
    """Raised when the deployment has no durable database configured."""


def build_engine(database_url: str) -> Engine:
    options: Dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        # TestClient and worker threads share the connection.
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **options)


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

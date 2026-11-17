"""Durable storage for damage reports synced from the phone.

Reports must outlive a single process: on Vercel every invocation may land on a
fresh instance with its own memory, so anything kept in a module-level list is
lost when that instance is recycled.

Storage is selected by ``DATABASE_URL`` and goes through SQLAlchemy, so the same
code runs on Postgres in production (Vercel Postgres, Neon, Supabase, ...) and on
SQLite for local development and tests.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

DEFAULT_SQLITE_URL = "sqlite:///reports.db"

metadata = MetaData()

reports_table = Table(
    "reports",
    metadata,
    # The client's report id is the primary key, so re-uploading a queued report
    # after a retry is ignored instead of duplicated.
    Column("report_id", String(64), primary_key=True),
    Column("synced", Boolean, nullable=False),
    # JSON as text keeps the schema identical on every dialect.
    Column("payload", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)


class StorageNotConfigured(RuntimeError):
    """Raised when the deployment has no durable database configured."""


@dataclass(frozen=True)
class SyncOutcome:
    received: int
    stored: int
    skipped: int
    total: int


class ReportStore(ABC):
    """Persistence contract for synced reports."""

    @abstractmethod
    def add_many(self, reports: Sequence[Dict[str, Any]]) -> SyncOutcome:
        """Store reports, ignoring ids that are already present."""

    @abstractmethod
    def list_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recently received reports, newest first."""

    @abstractmethod
    def count(self) -> int:
        """Total number of stored reports."""


class SqlReportStore(ReportStore):
    """SQL-backed store; creates its table on first use."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    def add_many(self, reports: Sequence[Dict[str, Any]]) -> SyncOutcome:
        stored = 0
        skipped = 0
        for report in reports:
            record = {**report, "synced": True}
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        insert(reports_table).values(
                            report_id=str(record["report_id"]),
                            synced=True,
                            payload=json.dumps(record),
                            received_at=datetime.now(timezone.utc),
                        )
                    )
                stored += 1
            except IntegrityError:
                # Already synced by an earlier retry - not an error.
                skipped += 1
        return SyncOutcome(
            received=len(reports),
            stored=stored,
            skipped=skipped,
            total=self.count(),
        )

    def list_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
        statement = (
            select(reports_table.c.payload)
            .order_by(reports_table.c.received_at.desc())
            .limit(limit)
        )
        with self._engine.connect() as connection:
            return [json.loads(row.payload) for row in connection.execute(statement)]

    def count(self) -> int:
        statement = select(func.count()).select_from(reports_table)
        with self._engine.connect() as connection:
            return int(connection.execute(statement).scalar_one())


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
                "environment variables so synced reports survive."
            )
        url = DEFAULT_SQLITE_URL
    return url


def create_store(database_url: Optional[str] = None) -> ReportStore:
    """Build a store from an explicit URL or the environment."""
    return SqlReportStore(build_engine(resolve_database_url(database_url)))


@lru_cache(maxsize=1)
def get_store() -> ReportStore:
    """Process-wide store, created once per instance."""
    return create_store()

"""Durable storage for damage reports synced from the phone.

Reports must outlive a single process: on Vercel every invocation may land on a
fresh instance with its own memory, so anything kept in a module-level list is
lost when that instance is recycled.

Every report belongs to a driver, and every read is scoped to one driver, so no
driver can see another's reports.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Sequence

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from airoad.db import MAX_DRIVER_ID_LENGTH, MAX_REPORT_ID_LENGTH, get_engine

metadata = MetaData()

reports_table = Table(
    "reports",
    metadata,
    # Unique per driver, so two drivers can reuse an id and one driver's retry is
    # still recognised as a duplicate.
    Column("report_id", String(MAX_REPORT_ID_LENGTH), primary_key=True),
    Column("driver_id", String(MAX_DRIVER_ID_LENGTH), primary_key=True),
    Column("synced", Boolean, nullable=False),
    # JSON as text keeps the schema identical on every dialect.
    Column("payload", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class SyncOutcome:
    received: int
    stored: int
    skipped: int
    total: int


class ReportStore(ABC):
    """Persistence contract for synced reports, scoped to one driver."""

    @abstractmethod
    def add_many(
        self, driver_id: str, reports: Sequence[Dict[str, Any]]
    ) -> SyncOutcome:
        """Store reports for ``driver_id``, ignoring ids already present."""

    @abstractmethod
    def list_reports(self, driver_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """The driver's most recently received reports, newest first."""

    @abstractmethod
    def count(self, driver_id: str) -> int:
        """How many reports ``driver_id`` has stored."""


class SqlReportStore(ReportStore):
    """SQL-backed store; creates its table on first use."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    def add_many(
        self, driver_id: str, reports: Sequence[Dict[str, Any]]
    ) -> SyncOutcome:
        """Store a whole batch in one transaction, skipping ids already stored."""
        if not reports:
            return SyncOutcome(
                received=0, stored=0, skipped=0, total=self.count(driver_id)
            )

        stored = 0
        skipped = 0
        # One transaction for the batch: an upload lands completely or not at
        # all, and the batch costs its instance's single connection one checkout.
        with self._engine.begin() as connection:
            for report in reports:
                record = {**report, "synced": True}
                try:
                    # Each row gets its own SAVEPOINT. On Postgres a primary-key
                    # collision aborts the whole transaction unless it is undone
                    # to a savepoint, so without this one duplicate id would fail
                    # the entire batch instead of being skipped.
                    with connection.begin_nested():
                        connection.execute(
                            insert(reports_table).values(
                                report_id=str(record["report_id"]),
                                driver_id=driver_id,
                                synced=True,
                                payload=json.dumps(record),
                                received_at=datetime.now(timezone.utc),
                            )
                        )
                    stored += 1
                except IntegrityError:
                    # Already synced by an earlier retry - not an error.
                    skipped += 1
            total = self._count(connection, driver_id)
        return SyncOutcome(
            received=len(reports), stored=stored, skipped=skipped, total=total
        )

    def list_reports(self, driver_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        statement = (
            select(reports_table.c.payload)
            .where(reports_table.c.driver_id == driver_id)
            .order_by(reports_table.c.received_at.desc())
            .limit(limit)
        )
        with self._engine.connect() as connection:
            return [json.loads(row.payload) for row in connection.execute(statement)]

    def count(self, driver_id: str) -> int:
        with self._engine.connect() as connection:
            return self._count(connection, driver_id)

    @staticmethod
    def _count(connection: Connection, driver_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(reports_table)
            .where(reports_table.c.driver_id == driver_id)
        )
        return int(connection.execute(statement).scalar_one())


def create_store(database_url=None) -> ReportStore:
    """Build a store from an explicit URL or the environment.

    Uses the process-wide engine for that URL, so the rate limiter and driver
    store share its connection rather than opening their own.
    """
    return SqlReportStore(get_engine(database_url))


@lru_cache(maxsize=1)
def get_store() -> ReportStore:
    """Process-wide store, created once per instance."""
    return create_store()

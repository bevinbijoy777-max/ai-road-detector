"""Per-driver identity and tokens.

Each driver authenticates with their own bearer token, so reports can be scoped to
the driver who captured them. Only a SHA-256 hash of each token is stored, so a
database leak does not hand out working credentials.

A driver can hold several tokens at once, which is what makes rotation possible:
issue a second token, ship the phone build that uses it, then revoke the first.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine

from airoad.db import MAX_DRIVER_ID_LENGTH, build_engine, resolve_database_url

MAX_DRIVER_NAME_LENGTH = 80

#: SHA-256 rendered as hex.
MAX_TOKEN_HASH_LENGTH = 64

metadata = MetaData()

drivers_table = Table(
    "drivers",
    metadata,
    Column("driver_id", String(MAX_DRIVER_ID_LENGTH), primary_key=True),
    Column("name", String(MAX_DRIVER_NAME_LENGTH), nullable=False),
    Column("created_at", Float, nullable=False),
)

driver_tokens_table = Table(
    "driver_tokens",
    metadata,
    Column("token_hash", String(MAX_TOKEN_HASH_LENGTH), primary_key=True),
    Column("driver_id", String(MAX_DRIVER_ID_LENGTH), nullable=False),
    Column("created_at", Float, nullable=False),
    Index("ix_driver_tokens_driver", "driver_id"),
)


@dataclass(frozen=True)
class Driver:
    driver_id: str
    name: str


def hash_token(token: str) -> str:
    """Hash a bearer token for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DriverStore(ABC):
    """Driver records and their tokens."""

    @abstractmethod
    def create_driver(self, name: str) -> tuple[Driver, str]:
        """Create a driver and return it with its first token."""

    @abstractmethod
    def add_token(self, driver_id: str) -> str:
        """Issue another token for an existing driver."""

    @abstractmethod
    def find_driver(self, token: str) -> Optional[Driver]:
        """The driver owning ``token``, or ``None`` if it is unknown or revoked."""

    @abstractmethod
    def get_driver(self, driver_id: str) -> Optional[Driver]:
        """Look a driver up by id."""

    @abstractmethod
    def list_drivers(self) -> List[Dict[str, Any]]:
        """Every driver with the number of active tokens."""

    @abstractmethod
    def revoke_token(self, token: str) -> bool:
        """Delete one token; ``True`` when it existed."""


class SqlDriverStore(DriverStore):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    def create_driver(self, name: str) -> tuple[Driver, str]:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("driver name must not be empty")
        if len(cleaned) > MAX_DRIVER_NAME_LENGTH:
            raise ValueError(
                f"driver name must be at most {MAX_DRIVER_NAME_LENGTH} characters"
            )

        driver = Driver(driver_id=secrets.token_hex(8), name=cleaned)
        token = self._new_token()
        now = time.time()
        with self._engine.begin() as connection:
            connection.execute(
                insert(drivers_table).values(
                    driver_id=driver.driver_id, name=driver.name, created_at=now
                )
            )
            connection.execute(
                insert(driver_tokens_table).values(
                    token_hash=hash_token(token), driver_id=driver.driver_id, created_at=now
                )
            )
        return driver, token

    def add_token(self, driver_id: str) -> str:
        if self.get_driver(driver_id) is None:
            raise KeyError(driver_id)
        token = self._new_token()
        with self._engine.begin() as connection:
            connection.execute(
                insert(driver_tokens_table).values(
                    token_hash=hash_token(token),
                    driver_id=driver_id,
                    created_at=time.time(),
                )
            )
        return token

    def find_driver(self, token: str) -> Optional[Driver]:
        if not token:
            return None
        statement = (
            select(drivers_table.c.driver_id, drivers_table.c.name)
            .select_from(
                drivers_table.join(
                    driver_tokens_table,
                    driver_tokens_table.c.driver_id == drivers_table.c.driver_id,
                )
            )
            .where(driver_tokens_table.c.token_hash == hash_token(token))
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        return Driver(row.driver_id, row.name) if row else None

    def get_driver(self, driver_id: str) -> Optional[Driver]:
        statement = select(drivers_table.c.driver_id, drivers_table.c.name).where(
            drivers_table.c.driver_id == driver_id
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).first()
        return Driver(row.driver_id, row.name) if row else None

    def list_drivers(self) -> List[Dict[str, Any]]:
        statement = (
            select(
                drivers_table.c.driver_id,
                drivers_table.c.name,
                func.count(driver_tokens_table.c.token_hash).label("tokens"),
            )
            .select_from(
                drivers_table.outerjoin(
                    driver_tokens_table,
                    driver_tokens_table.c.driver_id == drivers_table.c.driver_id,
                )
            )
            .group_by(drivers_table.c.driver_id, drivers_table.c.name)
            .order_by(drivers_table.c.name)
        )
        with self._engine.connect() as connection:
            return [dict(row._mapping) for row in connection.execute(statement)]

    def revoke_token(self, token: str) -> bool:
        if not token:
            return False
        with self._engine.begin() as connection:
            result = connection.execute(
                delete(driver_tokens_table).where(
                    driver_tokens_table.c.token_hash == hash_token(token)
                )
            )
        return bool(result.rowcount)

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)


def create_driver_store(database_url=None) -> DriverStore:
    return SqlDriverStore(build_engine(resolve_database_url(database_url)))


@lru_cache(maxsize=1)
def get_driver_store() -> DriverStore:
    """Process-wide driver store, created once per instance."""
    return create_driver_store()

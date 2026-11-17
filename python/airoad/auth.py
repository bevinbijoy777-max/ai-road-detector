"""Per-driver authentication for the report sync endpoints.

Each driver sends their own bearer token, created with the admin CLI:

    python -m airoad.admin create-driver "Asha"

Only a hash of the token is stored. The returned :class:`Driver` is what the
endpoints use to scope every write and read, so one driver cannot see another's
reports.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from airoad.db import StorageNotConfigured
from airoad.drivers import Driver, DriverStore, get_driver_store

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def driver_store_dependency() -> DriverStore:
    """Injection point so tests can supply a throwaway database."""
    try:
        return get_driver_store()
    except StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def require_driver(
    authorization: Optional[str] = Header(None),
    drivers: DriverStore = Depends(driver_store_dependency),
) -> Driver:
    """Resolve the calling driver from ``Authorization: Bearer <token>``."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers=_WWW_AUTHENTICATE,
        )

    driver = drivers.find_driver(token)
    if driver is None:
        raise HTTPException(
            status_code=401,
            detail="unknown or revoked token",
            headers=_WWW_AUTHENTICATE,
        )
    return driver

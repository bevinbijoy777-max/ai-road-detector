"""Authentication for the report sync endpoints.

Synced reports carry GPS coordinates and damage observations, so neither
``POST /reports`` nor ``GET /reports`` is public. Both require a shared bearer
token read from the ``SYNC_API_TOKEN`` environment variable.

If the variable is unset the endpoints refuse to serve (503) rather than falling
back to open access: a missing secret is a configuration error, not permission.

Generate a token with:

    python -c "import secrets; print(secrets.token_urlsafe(32))"

and set it as ``SYNC_API_TOKEN`` in the deployment's environment variables.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

SYNC_TOKEN_ENV = "SYNC_API_TOKEN"

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def require_sync_token(authorization: Optional[str] = Header(None)) -> None:
    """Allow the request only with the configured bearer token."""
    expected = os.environ.get(SYNC_TOKEN_ENV)
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{SYNC_TOKEN_ENV} is not set, so the report endpoints are closed. "
                "Set it to a long random value and redeploy."
            ),
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers=_WWW_AUTHENTICATE,
        )

    # Constant-time comparison so the token cannot be guessed byte by byte.
    if not secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=401,
            detail="invalid token",
            headers=_WWW_AUTHENTICATE,
        )

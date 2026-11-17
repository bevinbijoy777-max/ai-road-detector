"""Authentication for the report sync endpoints.

Synced reports carry GPS coordinates and damage observations, so neither
``POST /reports`` nor ``GET /reports`` is public. Both require a shared bearer
token from the ``SYNC_API_TOKEN`` environment variable.

``SYNC_API_TOKEN`` accepts a comma- (or newline-) separated list so a token can
be rotated without breaking the phone's sync:

1. Deploy with the old and new token: ``SYNC_API_TOKEN=old-token,new-token``.
2. Ship the phone build that uses the new token (both work meanwhile).
3. Drop the old token: ``SYNC_API_TOKEN=new-token``.

If no token is configured the endpoints refuse to serve (503) rather than falling
back to open access: a missing secret is a configuration error, not permission.

Generate a token with:

    python -c "import secrets; print(secrets.token_urlsafe(32))"
"""

from __future__ import annotations

import os
import secrets
from typing import Optional, Tuple

from fastapi import Header, HTTPException

SYNC_TOKEN_ENV = "SYNC_API_TOKEN"

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def configured_tokens() -> Tuple[str, ...]:
    """Tokens the server currently accepts, in configured order."""
    raw = os.environ.get(SYNC_TOKEN_ENV) or ""
    return tuple(
        token.strip()
        for token in raw.replace("\n", ",").split(",")
        if token.strip()
    )


def require_sync_token(authorization: Optional[str] = Header(None)) -> None:
    """Allow the request only with one of the configured bearer tokens."""
    expected_tokens = configured_tokens()
    if not expected_tokens:
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

    # Constant-time comparison, and every configured token is compared so the
    # timing does not reveal which one matched.
    supplied = token.encode("utf-8")
    matched = False
    for candidate in expected_tokens:
        matched |= secrets.compare_digest(supplied, candidate.encode("utf-8"))

    if not matched:
        raise HTTPException(
            status_code=401,
            detail="invalid token",
            headers=_WWW_AUTHENTICATE,
        )

"""Bearer token guard tests."""

import pytest
from fastapi import HTTPException

from airoad.auth import SYNC_TOKEN_ENV, require_sync_token

TOKEN = "s3cret-token"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, TOKEN)


def test_accepts_the_configured_token():
    assert require_sync_token(f"Bearer {TOKEN}") is None


def test_scheme_is_case_insensitive():
    assert require_sync_token(f"bearer {TOKEN}") is None


def test_rejects_a_missing_header():
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token(None)
    assert excinfo.value.status_code == 401
    assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    "header", ["", "Bearer", "Bearer ", TOKEN, f"Basic {TOKEN}", f"Bearer {TOKEN}x"]
)
def test_rejects_malformed_or_wrong_credentials(header):
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token(header)
    assert excinfo.value.status_code == 401


def test_closes_the_endpoint_when_the_token_is_not_configured(monkeypatch):
    monkeypatch.delenv(SYNC_TOKEN_ENV, raising=False)
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token(f"Bearer {TOKEN}")
    assert excinfo.value.status_code == 503
    assert SYNC_TOKEN_ENV in excinfo.value.detail


def test_empty_token_setting_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "")
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token("Bearer ")
    assert excinfo.value.status_code == 503

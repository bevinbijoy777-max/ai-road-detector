"""Bearer token guard tests."""

import pytest
from fastapi import HTTPException

from airoad.auth import SYNC_TOKEN_ENV, configured_tokens, require_sync_token

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


# --- rotation ---------------------------------------------------------------


def test_both_tokens_work_during_a_rotation(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "old-token,new-token")
    assert require_sync_token("Bearer old-token") is None
    assert require_sync_token("Bearer new-token") is None


def test_only_the_new_token_works_after_the_rotation_finishes(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "new-token")
    assert require_sync_token("Bearer new-token") is None
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token("Bearer old-token")
    assert excinfo.value.status_code == 401


def test_whitespace_and_newlines_separate_tokens(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "  first ,\n second \n,third\n")
    assert configured_tokens() == ("first", "second", "third")
    for candidate in ("first", "second", "third"):
        assert require_sync_token(f"Bearer {candidate}") is None


def test_empty_entries_are_ignored(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "token,,   ,")
    assert configured_tokens() == ("token",)
    assert require_sync_token("Bearer token") is None


def test_a_list_of_only_separators_is_unconfigured(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, " , ,\n")
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token("Bearer anything")
    assert excinfo.value.status_code == 503


def test_unknown_tokens_are_still_rejected_with_a_list(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "old-token,new-token")
    with pytest.raises(HTTPException) as excinfo:
        require_sync_token("Bearer something-else")
    assert excinfo.value.status_code == 401


def test_tokens_are_not_matched_by_prefix(monkeypatch):
    monkeypatch.setenv(SYNC_TOKEN_ENV, "abcdef,ghijkl")
    for candidate in ("abc", "abcdefgh", "ghij"):
        with pytest.raises(HTTPException):
            require_sync_token(f"Bearer {candidate}")

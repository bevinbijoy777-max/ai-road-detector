"""Per-driver authentication tests."""

import pytest
from fastapi import HTTPException

from airoad.auth import driver_store_dependency, require_driver
from airoad.drivers import create_driver_store, get_driver_store


@pytest.fixture
def drivers(database_url):
    return create_driver_store(database_url)


def test_a_valid_token_resolves_its_driver(drivers):
    driver, token = drivers.create_driver("Asha")
    assert require_driver(f"Bearer {token}", drivers) == driver


def test_the_scheme_is_case_insensitive(drivers):
    driver, token = drivers.create_driver("Asha")
    assert require_driver(f"bearer {token}", drivers) == driver


def test_two_drivers_resolve_to_different_identities(drivers):
    asha, asha_token = drivers.create_driver("Asha")
    ravi, ravi_token = drivers.create_driver("Ravi")

    assert require_driver(f"Bearer {asha_token}", drivers) == asha
    assert require_driver(f"Bearer {ravi_token}", drivers) == ravi


def test_rejects_a_missing_header(drivers):
    with pytest.raises(HTTPException) as excinfo:
        require_driver(None, drivers)
    assert excinfo.value.status_code == 401
    assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize(
    "header", ["", "Bearer", "Bearer ", "Basic abc", "some-token", "Bearer " + "x" * 40]
)
def test_rejects_malformed_or_unknown_credentials(drivers, header):
    drivers.create_driver("Asha")
    with pytest.raises(HTTPException) as excinfo:
        require_driver(header, drivers)
    assert excinfo.value.status_code == 401


def test_a_revoked_token_stops_working(drivers):
    driver, token = drivers.create_driver("Asha")
    replacement = drivers.add_token(driver.driver_id)
    drivers.revoke_token(token)

    with pytest.raises(HTTPException):
        require_driver(f"Bearer {token}", drivers)
    assert require_driver(f"Bearer {replacement}", drivers) == driver


def test_the_driver_store_returns_503_without_a_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_driver_store.cache_clear()
    try:
        with pytest.raises(HTTPException) as excinfo:
            driver_store_dependency()
        assert excinfo.value.status_code == 503
        assert "DATABASE_URL" in excinfo.value.detail
    finally:
        get_driver_store.cache_clear()

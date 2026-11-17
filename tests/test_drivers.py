"""Driver and token store tests."""

import pytest
from sqlalchemy import select

from airoad.db import MAX_DRIVER_ID_LENGTH, build_engine
from airoad.drivers import (
    MAX_DRIVER_NAME_LENGTH,
    SqlDriverStore,
    create_driver_store,
    driver_tokens_table,
    hash_token,
)


@pytest.fixture
def drivers(database_url):
    return create_driver_store(database_url)


def stored_token_hashes(database_url) -> list[str]:
    engine = build_engine(database_url)
    with engine.connect() as connection:
        return [
            row.token_hash
            for row in connection.execute(select(driver_tokens_table.c.token_hash))
        ]


def test_create_driver_returns_an_id_and_a_token(drivers):
    driver, token = drivers.create_driver("Asha")
    assert driver.name == "Asha"
    assert driver.driver_id
    assert len(driver.driver_id) <= MAX_DRIVER_ID_LENGTH
    assert len(token) >= 32


def test_the_token_is_found_again_after_a_restart(database_url):
    create_driver_store(database_url).create_driver("Ravi")

    fresh = create_driver_store(database_url)
    assert [row["name"] for row in fresh.list_drivers()] == ["Ravi"]


def test_tokens_are_stored_only_as_hashes(drivers, database_url):
    _, token = drivers.create_driver("Asha")
    hashes = stored_token_hashes(database_url)
    assert token not in hashes
    assert hash_token(token) in hashes


def test_find_driver_resolves_a_valid_token(drivers):
    driver, token = drivers.create_driver("Asha")
    assert drivers.find_driver(token) == driver


@pytest.mark.parametrize("token", ["", "not-a-token"])
def test_unknown_tokens_do_not_resolve(drivers, token):
    assert drivers.find_driver(token) is None


def test_each_driver_gets_a_distinct_token(drivers):
    _, first = drivers.create_driver("Asha")
    _, second = drivers.create_driver("Ravi")
    assert first != second


def test_a_driver_can_hold_two_tokens_for_rotation(drivers):
    driver, old_token = drivers.create_driver("Asha")
    new_token = drivers.add_token(driver.driver_id)

    assert drivers.find_driver(old_token) == driver
    assert drivers.find_driver(new_token) == driver
    assert drivers.list_drivers()[0]["tokens"] == 2


def test_revoking_one_token_leaves_the_other_working(drivers):
    driver, old_token = drivers.create_driver("Asha")
    new_token = drivers.add_token(driver.driver_id)

    assert drivers.revoke_token(old_token) is True
    assert drivers.find_driver(old_token) is None
    assert drivers.find_driver(new_token) == driver


def test_revoking_an_unknown_token_reports_it(drivers):
    drivers.create_driver("Asha")
    assert drivers.revoke_token("nope") is False
    assert drivers.revoke_token("") is False


def test_add_token_requires_an_existing_driver(drivers):
    with pytest.raises(KeyError):
        drivers.add_token("missing-driver")


def test_driver_names_are_trimmed(drivers):
    driver, _ = drivers.create_driver("  Asha  ")
    assert driver.name == "Asha"


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_names_are_rejected(drivers, name):
    with pytest.raises(ValueError):
        drivers.create_driver(name)


def test_overlong_names_are_rejected(drivers):
    with pytest.raises(ValueError):
        drivers.create_driver("x" * (MAX_DRIVER_NAME_LENGTH + 1))


def test_list_drivers_is_empty_at_first(drivers):
    assert drivers.list_drivers() == []


def test_list_drivers_counts_tokens_per_driver(drivers):
    asha, _ = drivers.create_driver("Asha")
    drivers.create_driver("Ravi")
    drivers.add_token(asha.driver_id)

    rows = {row["name"]: row["tokens"] for row in drivers.list_drivers()}
    assert rows == {"Asha": 2, "Ravi": 1}


def test_get_driver_by_id(drivers):
    driver, _ = drivers.create_driver("Asha")
    assert drivers.get_driver(driver.driver_id) == driver
    assert drivers.get_driver("missing") is None


def test_store_type_is_the_sql_implementation(drivers):
    assert isinstance(drivers, SqlDriverStore)

"""Report store tests.

The store is exercised through SQLite by default, which runs the same SQLAlchemy
code path as the Postgres deployment; set ``TEST_DATABASE_URL`` to run it on
Postgres. Duplicate handling relies on a primary-key violation rather than
dialect-specific SQL.
"""

import pytest

from airoad.db import DEFAULT_SQLITE_URL, StorageNotConfigured
from airoad.store import SqlReportStore, create_store

DRIVER = "driver-1"
OTHER_DRIVER = "driver-2"


def test_reports_survive_a_new_store_instance(database_url):
    first = create_store(database_url)
    outcome = first.add_many(
        DRIVER, [{"report_id": "r1", "synced": False, "notes": "deep pothole"}]
    )
    assert (outcome.received, outcome.stored, outcome.skipped, outcome.total) == (1, 1, 0, 1)

    # A second store on the same URL stands in for a fresh serverless instance.
    second = create_store(database_url)
    assert second.count(DRIVER) == 1
    stored = second.list_reports(DRIVER)
    assert stored[0]["report_id"] == "r1"
    assert stored[0]["notes"] == "deep pothole"
    # The server has it, so it is synced regardless of what the phone sent.
    assert stored[0]["synced"] is True


def test_duplicate_report_ids_are_not_stored_twice(database_url):
    store = create_store(database_url)
    store.add_many(DRIVER, [{"report_id": "dup"}])

    outcome = store.add_many(DRIVER, [{"report_id": "dup"}, {"report_id": "fresh"}])
    assert outcome.received == 2
    assert outcome.stored == 1
    assert outcome.skipped == 1
    assert outcome.total == 2
    assert store.count(DRIVER) == 2


def test_reports_are_scoped_to_their_driver(database_url):
    store = create_store(database_url)
    store.add_many(DRIVER, [{"report_id": "mine"}])
    store.add_many(OTHER_DRIVER, [{"report_id": "theirs"}])

    assert store.count(DRIVER) == 1
    assert store.count(OTHER_DRIVER) == 1
    assert [report["report_id"] for report in store.list_reports(DRIVER)] == ["mine"]
    assert [report["report_id"] for report in store.list_reports(OTHER_DRIVER)] == ["theirs"]


def test_two_drivers_may_use_the_same_report_id(database_url):
    store = create_store(database_url)
    store.add_many(DRIVER, [{"report_id": "shared-id"}])

    outcome = store.add_many(OTHER_DRIVER, [{"report_id": "shared-id"}])
    assert outcome.stored == 1
    assert outcome.skipped == 0
    assert store.count(DRIVER) == 1
    assert store.count(OTHER_DRIVER) == 1


def test_an_unknown_driver_has_no_reports(database_url):
    store = create_store(database_url)
    store.add_many(DRIVER, [{"report_id": "r1"}])

    assert store.count("nobody") == 0
    assert store.list_reports("nobody") == []


def test_empty_batch_is_a_no_op(database_url):
    store = create_store(database_url)
    outcome = store.add_many(DRIVER, [])
    assert (outcome.received, outcome.stored, outcome.skipped, outcome.total) == (0, 0, 0, 0)


def test_list_returns_newest_first_and_respects_the_limit(database_url):
    store = create_store(database_url)
    store.add_many(DRIVER, [{"report_id": f"r{i}"} for i in range(5)])

    assert store.count(DRIVER) == 5
    newest = store.list_reports(DRIVER, limit=2)
    assert len(newest) == 2
    assert {report["report_id"] for report in newest} <= {"r3", "r4"}


def test_extra_fields_round_trip(database_url):
    store = create_store(database_url)
    store.add_many(
        DRIVER,
        [
            {
                "report_id": "rich",
                "predictions": [{"defect_type": "pothole", "confidence": 0.9}],
                "location": {"latitude": 12.9716, "longitude": 77.5946},
            }
        ],
    )
    stored = store.list_reports(DRIVER)[0]
    assert stored["predictions"][0]["defect_type"] == "pothole"
    assert stored["location"]["latitude"] == 12.9716


def test_vercel_without_a_database_url_fails_loudly(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(StorageNotConfigured):
        create_store()


def test_local_default_falls_back_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.chdir(tmp_path)  # keep reports.db out of the repo

    store = create_store()
    assert isinstance(store, SqlReportStore)
    assert (tmp_path / "reports.db").exists()
    assert DEFAULT_SQLITE_URL.endswith("reports.db")


def test_explicit_url_wins_over_the_environment(monkeypatch, database_url):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///somewhere-else.db")
    store = create_store(database_url)
    assert store.count(DRIVER) == 0

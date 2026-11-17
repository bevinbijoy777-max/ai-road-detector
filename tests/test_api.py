"""FastAPI endpoint tests."""

import io
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from airoad.api import app, limiter_dependency, store_dependency
from airoad.auth import driver_store_dependency
from airoad.db import MAX_REPORT_ID_LENGTH
from airoad.drivers import create_driver_store, get_driver_store
from airoad.limits import MAX_UPLOAD_BYTES
from airoad.ratelimit import create_limiter, get_limiter
from airoad.store import create_store, get_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def configured(database_url):
    """A driver with a token, a clean database and a roomy limit per test."""
    store = create_store(database_url)
    drivers = create_driver_store(database_url)
    driver, token = drivers.create_driver("Test Driver")

    app.dependency_overrides[store_dependency] = lambda: store
    app.dependency_overrides[driver_store_dependency] = lambda: drivers
    app.dependency_overrides[limiter_dependency] = lambda: create_limiter(
        "1000/minute", database_url
    )
    yield SimpleNamespace(store=store, drivers=drivers, driver=driver, token=token)
    app.dependency_overrides.clear()


@pytest.fixture
def auth(configured):
    return {"Authorization": f"Bearer {configured.token}"}


def png_bytes(shade: int = 20) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 40, 3), shade, dtype=np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


def detect(headers=None):
    return client.post(
        "/detect", files={"image": ("road.png", png_bytes(), "image/png")}, headers=headers
    )


# --- public endpoints -------------------------------------------------------


def test_health_reports_the_offline_model_and_storage(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": "on-device-heuristic-v1",
        "offline_detection": True,
        "durable_report_storage": False,
    }


def test_health_reports_durable_storage_when_configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    assert client.get("/health").json()["durable_report_storage"] is True


def test_limits_publishes_the_upload_cap():
    body = client.get("/limits").json()
    assert body["max_upload_bytes"] == MAX_UPLOAD_BYTES
    assert body["max_upload_mb"] == 4.5
    assert "413" in body["note"]


def test_detect_returns_a_detection_result():
    response = client.post(
        "/detect",
        files={"image": ("road.png", png_bytes(), "image/png")},
        data={"latitude": "12.9716", "longitude": "77.5946"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["image_path"] == "road.png"  # client filename, not a server temp path
    assert body["ran_offline"] is True
    assert body["model_name"] == "on-device-heuristic-v1"
    assert body["location"]["latitude"] == pytest.approx(12.9716)
    assert body["predictions"]


def test_detect_without_coordinates_leaves_location_null():
    response = client.post(
        "/detect", files={"image": ("road.png", png_bytes(), "image/png")}
    )
    assert response.status_code == 200
    assert response.json()["location"] is None


def test_detect_rejects_a_file_that_is_not_an_image():
    response = client.post(
        "/detect", files={"image": ("road.png", b"not an image", "image/png")}
    )
    assert response.status_code == 400
    assert "could not read image" in response.json()["detail"]


def test_detect_rejects_an_empty_upload():
    response = client.post("/detect", files={"image": ("road.png", b"", "image/png")})
    assert response.status_code == 400


def test_detect_rejects_an_oversized_photo_with_413():
    oversized = b"\0" * (MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/detect", files={"image": ("huge.png", oversized, "image/png")}
    )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["error"] == "image too large"
    assert detail["max_upload_bytes"] == MAX_UPLOAD_BYTES
    assert detail["size_bytes"] > MAX_UPLOAD_BYTES
    assert "downscale" in detail["message"]


def test_detect_accepts_a_photo_at_exactly_the_limit():
    # One byte over is rejected; one byte under must reach decoding, which then
    # fails cleanly as an unreadable image.
    not_an_image = b"\0" * (MAX_UPLOAD_BYTES - 1)
    response = client.post(
        "/detect", files={"image": ("big.png", not_an_image, "image/png")}
    )
    assert response.status_code == 400


def test_detect_requires_both_coordinates():
    response = client.post(
        "/detect",
        files={"image": ("road.png", png_bytes(), "image/png")},
        data={"latitude": "12.9716"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("lat,lng", [("999", "0"), ("0", "-999")])
def test_detect_rejects_out_of_range_coordinates(lat, lng):
    response = client.post(
        "/detect",
        files={"image": ("road.png", png_bytes(), "image/png")},
        data={"latitude": lat, "longitude": lng},
    )
    assert response.status_code == 422


# --- per-driver authentication ---------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_report_routes_reject_a_missing_token(method):
    request = getattr(client, method)
    response = (
        request("/reports", json=[{"report_id": "x"}]) if method == "post" else request("/reports")
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["get", "post"])
def test_report_routes_reject_an_unknown_token(method):
    headers = {"Authorization": "Bearer not-a-real-token"}
    request = getattr(client, method)
    response = (
        request("/reports", json=[{"report_id": "x"}], headers=headers)
        if method == "post"
        else request("/reports", headers=headers)
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "unknown or revoked token"


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Basic token", "token"])
def test_report_routes_require_a_bearer_scheme(header):
    response = client.get("/reports", headers={"Authorization": header})
    assert response.status_code == 401


def test_a_revoked_token_stops_working(configured):
    replacement = configured.drivers.add_token(configured.driver.driver_id)
    configured.drivers.revoke_token(configured.token)

    assert client.get("/reports", headers={"Authorization": f"Bearer {configured.token}"}).status_code == 401
    assert client.get("/reports", headers={"Authorization": f"Bearer {replacement}"}).status_code == 200


def test_report_routes_return_503_without_a_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.pop(driver_store_dependency, None)
    app.dependency_overrides.pop(store_dependency, None)
    get_driver_store.cache_clear()
    get_store.cache_clear()
    try:
        response = client.get("/reports")
        assert response.status_code == 503
        assert "DATABASE_URL" in response.json()["detail"]
    finally:
        get_driver_store.cache_clear()
        get_store.cache_clear()


# --- report ids -------------------------------------------------------------


def test_a_report_id_at_the_column_length_is_accepted(auth):
    report_id = "r" * MAX_REPORT_ID_LENGTH
    response = client.post("/reports", json=[{"report_id": report_id}], headers=auth)
    assert response.status_code == 200
    assert response.json()["stored"] == 1


def test_an_overlong_report_id_returns_422_not_500(auth):
    """Postgres rejects a value wider than the column; validate before storing."""
    report_id = "r" * (MAX_REPORT_ID_LENGTH + 1)
    response = client.post("/reports", json=[{"report_id": report_id}], headers=auth)
    assert response.status_code == 422
    assert "report_id" in str(response.json()["detail"])


def test_reports_require_a_report_id(auth):
    response = client.post("/reports", json=[{"notes": "anonymous"}], headers=auth)
    assert response.status_code == 422


# --- storage ----------------------------------------------------------------


def test_reports_are_persisted_and_listed(auth, configured):
    reports = [
        {"report_id": "a", "synced": False, "notes": "pothole"},
        {"report_id": "b", "synced": False},
    ]
    response = client.post("/reports", json=reports, headers=auth)
    assert response.status_code == 200
    assert response.json() == {
        "accepted": 2,
        "stored": 2,
        "skipped": 0,
        "total_synced": 2,
    }

    listing = client.get("/reports", headers=auth).json()
    assert listing["driver_id"] == configured.driver.driver_id
    assert listing["total_synced"] == 2
    assert {report["report_id"] for report in listing["reports"]} == {"a", "b"}
    assert all(report["synced"] is True for report in listing["reports"])


def test_one_driver_cannot_see_another_drivers_reports(auth, configured):
    client.post(
        "/reports",
        json=[{"report_id": "asha-1", "latitude": 12.97, "longitude": 77.59}],
        headers=auth,
    )

    ravi, ravi_token = configured.drivers.create_driver("Ravi")
    ravi_auth = {"Authorization": f"Bearer {ravi_token}"}

    listing = client.get("/reports", headers=ravi_auth).json()
    assert listing["driver_id"] == ravi.driver_id
    assert listing["reports"] == []
    assert listing["total_synced"] == 0

    # The original driver still sees their own report.
    assert client.get("/reports", headers=auth).json()["total_synced"] == 1


def test_sync_counts_are_per_driver(auth, configured):
    ravi, ravi_token = configured.drivers.create_driver("Ravi")
    ravi_auth = {"Authorization": f"Bearer {ravi_token}"}

    client.post("/reports", json=[{"report_id": "mine"}], headers=auth)
    response = client.post("/reports", json=[{"report_id": "mine"}], headers=ravi_auth)

    # Same report id, different driver: stored, not treated as a duplicate.
    assert response.json() == {
        "accepted": 1,
        "stored": 1,
        "skipped": 0,
        "total_synced": 1,
    }


def test_resending_a_report_is_idempotent(auth):
    payload = [{"report_id": "retry-me"}]
    assert client.post("/reports", json=payload, headers=auth).json()["stored"] == 1

    second = client.post("/reports", json=payload, headers=auth).json()
    assert second == {"accepted": 1, "stored": 0, "skipped": 1, "total_synced": 1}


def test_reports_reach_a_fresh_store(database_url, auth):
    """A second process reading the same database still sees the reports."""
    created = client.post("/reports", json=[{"report_id": "durable"}], headers=auth).json()
    assert created["stored"] == 1

    other_instance = create_store(database_url)
    driver_id = client.get("/reports", headers=auth).json()["driver_id"]
    assert other_instance.count(driver_id) == 1
    assert other_instance.list_reports(driver_id)[0]["report_id"] == "durable"


def test_reports_listing_respects_the_limit(auth):
    client.post("/reports", json=[{"report_id": f"r{i}"} for i in range(5)], headers=auth)
    listing = client.get("/reports", params={"limit": 2}, headers=auth).json()
    assert len(listing["reports"]) == 2
    assert listing["total_synced"] == 5


def test_reports_listing_validates_the_limit(auth):
    assert client.get("/reports", params={"limit": 0}, headers=auth).status_code == 422
    assert client.get("/reports", params={"limit": 10_000}, headers=auth).status_code == 422


# --- rate limiting on /detect ------------------------------------------------


@pytest.fixture
def strict_limiter(database_url):
    """Allow two detect calls per window, shared through a database."""
    limiter = create_limiter("2/minute", database_url)
    app.dependency_overrides[limiter_dependency] = lambda: limiter
    return limiter


def test_detect_returns_429_after_the_limit(strict_limiter):
    assert detect().status_code == 200
    assert detect().status_code == 200

    blocked = detect()
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["error"] == "rate limit exceeded"
    assert detail["limit"] == 2
    assert detail["window_seconds"] == 60.0
    assert detail["retry_after_seconds"] >= 1
    assert int(blocked.headers["retry-after"]) >= 1


def test_rate_limit_is_per_client(monkeypatch, strict_limiter):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    first = {"X-Forwarded-For": "1.1.1.1"}
    assert detect(first).status_code == 200
    assert detect(first).status_code == 200
    assert detect(first).status_code == 429

    # A different client still has its own allowance.
    assert detect({"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_rate_limit_does_not_apply_to_reports(strict_limiter, auth):
    for _ in range(3):
        detect()
    # Exhausting detection does not lock the authenticated sync endpoint.
    assert client.post("/reports", json=[{"report_id": "a"}], headers=auth).status_code == 200
    assert client.get("/reports", headers=auth).status_code == 200


def test_detect_closed_when_the_limit_is_misconfigured(monkeypatch):
    monkeypatch.setenv("DETECT_RATE_LIMIT", "banana")
    app.dependency_overrides.pop(limiter_dependency, None)
    get_limiter.cache_clear()
    try:
        response = detect()
        assert response.status_code == 503
        assert "DETECT_RATE_LIMIT" in response.json()["detail"]
    finally:
        get_limiter.cache_clear()


def test_detect_needs_a_database_on_vercel(monkeypatch):
    """Without shared storage the limit cannot hold, so /detect refuses to run."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.pop(limiter_dependency, None)
    get_limiter.cache_clear()
    try:
        response = detect()
        assert response.status_code == 503
        assert "DATABASE_URL" in response.json()["detail"]
    finally:
        get_limiter.cache_clear()

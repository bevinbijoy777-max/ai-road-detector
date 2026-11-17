"""FastAPI endpoint tests."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from airoad.api import app, limiter_dependency, store_dependency
from airoad.auth import SYNC_TOKEN_ENV
from airoad.limits import MAX_UPLOAD_BYTES
from airoad.ratelimit import SlidingWindowLimiter
from airoad.store import create_store

SYNC_TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {SYNC_TOKEN}"}

client = TestClient(app)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    """A configured token, a throwaway database and a roomy limit per test."""
    monkeypatch.setenv(SYNC_TOKEN_ENV, SYNC_TOKEN)
    store = create_store(f"sqlite:///{tmp_path / 'reports.db'}")
    app.dependency_overrides[store_dependency] = lambda: store
    app.dependency_overrides[limiter_dependency] = lambda: SlidingWindowLimiter(
        limit=1000, window_seconds=60
    )
    yield store
    app.dependency_overrides.clear()


def png_bytes(shade: int = 20) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 40, 3), shade, dtype=np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


def detect(headers=None):
    return client.post(
        "/detect", files={"image": ("road.png", png_bytes(), "image/png")}, headers=headers
    )


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


# --- authentication on the report routes -------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_report_routes_reject_a_missing_token(method):
    request = getattr(client, method)
    if method == "post":
        response = request("/reports", json=[{"report_id": "x"}])
    else:
        response = request("/reports")
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["get", "post"])
def test_report_routes_reject_a_wrong_token(method):
    request = getattr(client, method)
    headers = {"Authorization": "Bearer not-the-token"}
    if method == "post":
        response = request("/reports", json=[{"report_id": "x"}], headers=headers)
    else:
        response = request("/reports", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic test-token-123", "test-token-123"],
)
def test_report_routes_require_a_bearer_scheme(header):
    response = client.get("/reports", headers={"Authorization": header})
    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "post"])
def test_report_routes_are_closed_when_no_token_is_configured(monkeypatch, method):
    monkeypatch.delenv(SYNC_TOKEN_ENV, raising=False)
    request = getattr(client, method)
    if method == "post":
        response = request("/reports", json=[{"report_id": "x"}], headers=AUTH)
    else:
        response = request("/reports", headers=AUTH)
    assert response.status_code == 503
    assert SYNC_TOKEN_ENV in response.json()["detail"]


def test_token_is_not_required_for_detection_or_health():
    assert client.get("/health").status_code == 200
    assert client.get("/limits").status_code == 200
    detected = client.post("/detect", files={"image": ("road.png", png_bytes(), "image/png")})
    assert detected.status_code == 200


# --- storage ----------------------------------------------------------------


def test_reports_are_persisted_and_listed():
    reports = [
        {"report_id": "a", "synced": False, "notes": "pothole"},
        {"report_id": "b", "synced": False},
    ]
    response = client.post("/reports", json=reports, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "accepted": 2,
        "stored": 2,
        "skipped": 0,
        "total_synced": 2,
    }

    listing = client.get("/reports", headers=AUTH).json()
    assert listing["total_synced"] == 2
    assert {report["report_id"] for report in listing["reports"]} == {"a", "b"}
    assert all(report["synced"] is True for report in listing["reports"])


def test_resending_a_report_is_idempotent():
    payload = [{"report_id": "retry-me"}]
    assert client.post("/reports", json=payload, headers=AUTH).json()["stored"] == 1

    second = client.post("/reports", json=payload, headers=AUTH).json()
    assert second == {"accepted": 1, "stored": 0, "skipped": 1, "total_synced": 1}


def test_reports_require_a_report_id():
    response = client.post("/reports", json=[{"notes": "anonymous"}], headers=AUTH)
    assert response.status_code == 422


def test_reports_reach_a_fresh_store(tmp_path):
    """A second process reading the same database still sees the reports."""
    client.post("/reports", json=[{"report_id": "durable"}], headers=AUTH)

    other_instance = create_store(f"sqlite:///{tmp_path / 'reports.db'}")
    assert other_instance.count() == 1
    assert other_instance.list_reports()[0]["report_id"] == "durable"


def test_reports_listing_respects_the_limit():
    client.post(
        "/reports", json=[{"report_id": f"r{i}"} for i in range(5)], headers=AUTH
    )
    listing = client.get("/reports", params={"limit": 2}, headers=AUTH).json()
    assert len(listing["reports"]) == 2
    assert listing["total_synced"] == 5


def test_reports_listing_validates_the_limit():
    assert client.get("/reports", params={"limit": 0}, headers=AUTH).status_code == 422
    assert (
        client.get("/reports", params={"limit": 10_000}, headers=AUTH).status_code == 422
    )


def test_reports_return_503_when_storage_is_not_configured(monkeypatch):
    from airoad.store import get_store

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.clear()  # exercise the real dependency
    get_store.cache_clear()
    try:
        response = client.get("/reports", headers=AUTH)
        assert response.status_code == 503
        assert "DATABASE_URL" in response.json()["detail"]
    finally:
        get_store.cache_clear()


# --- rate limiting on /detect ------------------------------------------------


@pytest.fixture
def strict_limiter():
    """Allow two detect calls per window."""
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
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


def test_rate_limit_does_not_apply_to_reports(strict_limiter):
    for _ in range(3):
        detect()
    # Exhausting detection does not lock the authenticated sync endpoint.
    assert client.post("/reports", json=[{"report_id": "a"}], headers=AUTH).status_code == 200
    assert client.get("/reports", headers=AUTH).status_code == 200


def test_detect_closed_when_the_limit_is_misconfigured(monkeypatch):
    from airoad.ratelimit import get_limiter

    monkeypatch.setenv("DETECT_RATE_LIMIT", "banana")
    app.dependency_overrides.pop(limiter_dependency, None)
    get_limiter.cache_clear()
    try:
        response = detect()
        assert response.status_code == 503
        assert "DETECT_RATE_LIMIT" in response.json()["detail"]
    finally:
        get_limiter.cache_clear()

"""FastAPI endpoint tests."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from airoad.api import app, store_dependency
from airoad.limits import MAX_UPLOAD_BYTES
from airoad.store import create_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_store(tmp_path):
    """Give every test its own database instead of the process-wide one."""
    store = create_store(f"sqlite:///{tmp_path / 'reports.db'}")
    app.dependency_overrides[store_dependency] = lambda: store
    yield store
    app.dependency_overrides.clear()


def png_bytes(shade: int = 20) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 40, 3), shade, dtype=np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


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
    # A single-byte-over-limit file is rejected; one byte under must get through
    # to decoding, which then fails cleanly as an unreadable image.
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


def test_reports_are_persisted_and_listed():
    reports = [
        {"report_id": "a", "synced": False, "notes": "pothole"},
        {"report_id": "b", "synced": False},
    ]
    response = client.post("/reports", json=reports)
    assert response.status_code == 200
    assert response.json() == {
        "accepted": 2,
        "stored": 2,
        "skipped": 0,
        "total_synced": 2,
    }

    listing = client.get("/reports").json()
    assert listing["total_synced"] == 2
    assert {report["report_id"] for report in listing["reports"]} == {"a", "b"}
    assert all(report["synced"] is True for report in listing["reports"])


def test_resending_a_report_is_idempotent():
    payload = [{"report_id": "retry-me"}]
    assert client.post("/reports", json=payload).json()["stored"] == 1

    second = client.post("/reports", json=payload).json()
    assert second == {"accepted": 1, "stored": 0, "skipped": 1, "total_synced": 1}


def test_reports_require_a_report_id():
    response = client.post("/reports", json=[{"notes": "anonymous"}])
    assert response.status_code == 422


def test_reports_reach_a_fresh_store(temp_store, tmp_path):
    """A second process reading the same database still sees the reports."""
    client.post("/reports", json=[{"report_id": "durable"}])

    other_instance = create_store(f"sqlite:///{tmp_path / 'reports.db'}")
    assert other_instance.count() == 1
    assert other_instance.list_reports()[0]["report_id"] == "durable"


def test_reports_listing_respects_the_limit():
    client.post("/reports", json=[{"report_id": f"r{i}"} for i in range(5)])
    listing = client.get("/reports", params={"limit": 2}).json()
    assert len(listing["reports"]) == 2
    assert listing["total_synced"] == 5


def test_reports_listing_validates_the_limit():
    assert client.get("/reports", params={"limit": 0}).status_code == 422
    assert client.get("/reports", params={"limit": 10_000}).status_code == 422


def test_reports_return_503_when_storage_is_not_configured(monkeypatch):
    from airoad.store import get_store

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.clear()  # exercise the real dependency
    get_store.cache_clear()
    try:
        response = client.get("/reports")
        assert response.status_code == 503
        assert "DATABASE_URL" in response.json()["detail"]
    finally:
        get_store.cache_clear()

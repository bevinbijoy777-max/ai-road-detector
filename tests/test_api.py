"""FastAPI endpoint tests."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from airoad.api import app

client = TestClient(app)


def png_bytes(shade: int = 20) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 40, 3), shade, dtype=np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_health_reports_the_offline_model():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "model": "on-device-heuristic-v1",
        "offline_detection": True,
    }


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
    assert body["predictions"][0]["defect_type"] in {"pothole", "road_crack", "surface_wear", "none"}


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


def test_reports_endpoint_acknowledges_queued_reports():
    reports = [{"report_id": "a", "synced": False}, {"report_id": "b", "synced": False}]
    response = client.post("/reports", json=reports)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 2
    assert body["total_synced"] >= 2

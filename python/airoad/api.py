"""FastAPI sync API.

Two jobs:

* ``POST /detect`` re-runs the on-device detector on a server (useful for testing
  and for browsers/clients without a local model).
* ``POST /reports`` is the endpoint the Android ``ReportSyncWorker`` uploads queued
  offline reports to once the phone is back online.

Run it from the ``python/`` directory:

    uvicorn airoad.api:app --host 0.0.0.0 --port 8000

Inference itself never needs the network; this service only exists so phones can
sync reports later.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from airoad.detector import OfflineDetectorFacade
from airoad.models import GeoLocation

app = FastAPI(
    title="AI Road Detector",
    version="0.1.0",
    description="Sync and detection API for pothole / road-crack reports.",
)

_detector = OfflineDetectorFacade()

# In-memory acknowledgement log for synced reports. Swap for a real database
# before running this anywhere that needs to survive a restart.
_synced_reports: List[dict] = []


def _resolve_location(
    latitude: Optional[float], longitude: Optional[float]
) -> Optional[GeoLocation]:
    """Build a validated location from the optional multipart form fields."""
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        raise HTTPException(
            status_code=422, detail="latitude and longitude must be sent together"
        )
    location = GeoLocation(latitude=latitude, longitude=longitude)
    if not location.is_valid():
        raise HTTPException(
            status_code=422, detail="latitude must be -90..90 and longitude -180..180"
        )
    return location


@app.get("/health")
def health() -> dict:
    """Liveness probe that also reports which model the service would use."""
    return {"status": "ok", "model": _detector.model_name, "offline_detection": True}


@app.post("/detect")
async def detect(
    image: UploadFile = File(...),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
) -> dict:
    """Detect road defects in an uploaded image."""
    location = _resolve_location(latitude, longitude)
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty image upload")

    suffix = Path(image.filename or "upload.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(raw)
        temp_path = Path(handle.name)

    try:
        result = _detector.run(str(temp_path), location)
    except OSError as exc:  # unreadable file, or not a decodable image
        raise HTTPException(
            status_code=400, detail=f"could not read image: {exc}"
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    payload = result.to_dict()
    # Report the client's filename rather than the server's temp path.
    payload["image_path"] = image.filename or temp_path.name
    return payload


@app.post("/reports")
def sync_reports(reports: List[dict]) -> dict:
    """Accept reports the phone queued while offline."""
    _synced_reports.extend(reports)
    return {"accepted": len(reports), "total_synced": len(_synced_reports)}

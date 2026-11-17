"""FastAPI sync API.

Two jobs:

* ``POST /detect`` re-runs the on-device detector on a server (useful for testing
  and for clients without a local model).
* ``POST /reports`` is the endpoint the Android ``ReportSyncWorker`` uploads queued
  offline reports to once the phone is back online. Reports are persisted through
  :mod:`airoad.store`, so they survive the instance that handled the upload.

Run it locally:

    uvicorn airoad.api:app --host 0.0.0.0 --port 8000

Inference itself never needs the network; this service only exists so phones can
sync reports later.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from airoad.detector import OfflineDetectorFacade
from airoad.limits import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, format_size
from airoad.models import GeoLocation
from airoad.store import ReportStore, StorageNotConfigured, get_store

app = FastAPI(
    title="AI Road Detector",
    version="0.2.0",
    description="Sync and detection API for pothole / road-crack reports.",
)

_detector = OfflineDetectorFacade()


class SyncedReport(BaseModel):
    """A report the phone queued while offline.

    ``report_id`` is required because it is the storage key; any other field the
    client sends (predictions, location, notes, ...) is stored verbatim.
    """

    model_config = {"extra": "allow"}

    report_id: str = Field(min_length=1)


def store_dependency() -> ReportStore:
    """Injection point so tests can supply a throwaway database.

    A deployment without a usable database answers 503 rather than failing every
    request with a stack trace.
    """
    try:
        return get_store()
    except StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _too_large(size_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={
            "error": "image too large",
            "size_bytes": size_bytes,
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "message": (
                f"image is {format_size(size_bytes)}, over the "
                f"{format_size(MAX_UPLOAD_BYTES)} upload limit - downscale it first"
            ),
        },
    )


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
    """Liveness probe that also reports the active model and storage backend."""
    return {
        "status": "ok",
        "model": _detector.model_name,
        "offline_detection": True,
        "durable_report_storage": bool(os.environ.get("DATABASE_URL")),
    }


@app.get("/limits")
def upload_limits() -> dict:
    """Upload limits a client should check before sending a photo."""
    return {
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_upload_mb": round(MAX_UPLOAD_MB, 2),
        "note": (
            "Vercel rejects request bodies above this at the edge with a 413 "
            "before the function runs, so downscale larger photos on the device."
        ),
    }


@app.post("/detect")
async def detect(
    image: UploadFile = File(...),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
) -> dict:
    """Detect road defects in an uploaded image."""
    location = _resolve_location(latitude, longitude)

    # Starlette knows the size before the body is buffered when it can parse it.
    declared_size = getattr(image, "size", None)
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        raise _too_large(declared_size)

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty image upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise _too_large(len(raw))

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
def sync_reports(
    reports: List[SyncedReport],
    store: ReportStore = Depends(store_dependency),
) -> dict:
    """Accept reports the phone queued while offline."""
    outcome = store.add_many([report.model_dump() for report in reports])
    return {
        "accepted": outcome.received,
        "stored": outcome.stored,
        "skipped": outcome.skipped,
        "total_synced": outcome.total,
    }


@app.get("/reports")
def list_reports(
    limit: int = Query(50, ge=1, le=500),
    store: ReportStore = Depends(store_dependency),
) -> dict:
    """Reports already synced, newest first."""
    return {"reports": store.list_reports(limit), "total_synced": store.count()}

"""Domain models for road-damage detection (OOP: encapsulation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4


class DefectType(str, Enum):
    POTHOLE = "pothole"
    ROAD_CRACK = "road_crack"
    SURFACE_WEAR = "surface_wear"
    NONE = "none"

    @property
    def display_name(self) -> str:
        return {
            DefectType.POTHOLE: "Pothole",
            DefectType.ROAD_CRACK: "Road Crack",
            DefectType.SURFACE_WEAR: "Surface Wear",
            DefectType.NONE: "No Defect",
        }[self]


@dataclass(frozen=True)
class GeoLocation:
    latitude: float
    longitude: float
    accuracy_meters: Optional[float] = None

    def as_maps_query(self) -> str:
        return f"{self.latitude:.6f},{self.longitude:.6f}"

    def is_valid(self) -> bool:
        return -90.0 <= self.latitude <= 90.0 and -180.0 <= self.longitude <= 180.0


@dataclass(frozen=True)
class DefectPrediction:
    defect_type: DefectType
    confidence: float
    bounding_box: Optional[tuple[float, float, float, float]] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def confidence_percent(self) -> int:
        return int(round(self.confidence * 100))


@dataclass
class DetectionResult:
    image_path: str
    predictions: List[DefectPrediction]
    location: Optional[GeoLocation]
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_name: str = "on-device-heuristic-v1"
    ran_offline: bool = True

    @property
    def primary(self) -> DefectPrediction:
        if not self.predictions:
            return DefectPrediction(DefectType.NONE, 0.0)
        return max(self.predictions, key=lambda item: item.confidence)

    def to_dict(self) -> dict:
        return {
            "image_path": self.image_path,
            "model_name": self.model_name,
            "ran_offline": self.ran_offline,
            "detected_at": self.detected_at.isoformat(),
            "location": None
            if self.location is None
            else {
                "latitude": self.location.latitude,
                "longitude": self.location.longitude,
                "accuracy_meters": self.location.accuracy_meters,
            },
            "predictions": [
                {
                    "defect_type": pred.defect_type.value,
                    "confidence": pred.confidence,
                    "bounding_box": pred.bounding_box,
                }
                for pred in self.predictions
            ],
        }


@dataclass
class DamageReport:
    result: DetectionResult
    report_id: str = field(default_factory=lambda: str(uuid4()))
    synced: bool = False
    notes: str = ""

    def mark_synced(self) -> None:
        self.synced = True

    def to_dict(self) -> dict:
        payload = self.result.to_dict()
        payload["report_id"] = self.report_id
        payload["synced"] = self.synced
        payload["notes"] = self.notes
        return payload

"""On-device detectors. Inheritance + polymorphism; no cloud required at inference time."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

from airoad.models import DefectPrediction, DefectType, DetectionResult, GeoLocation


class BaseDetector(ABC):
    """Contract every on-device model must implement."""

    model_name: str = "base"

    @abstractmethod
    def detect(self, image_path: str, location: Optional[GeoLocation] = None) -> DetectionResult:
        raise NotImplementedError


class HeuristicDetector(BaseDetector):
    """
    Lightweight, CPU-only detector optimized for phones.

    Dark irregular patches tend to be potholes; elongated high-contrast
    edges tend to be cracks. This is a demo-grade model that runs fully
    offline. Replace `analyze()` with a TFLite Interpreter for production.
    """

    model_name = "on-device-heuristic-v1"

    def __init__(self, input_size: int = 224) -> None:
        self._input_size = input_size

    def detect(self, image_path: str, location: Optional[GeoLocation] = None) -> DetectionResult:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(image_path)
        predictions = self.analyze(path)
        return DetectionResult(
            image_path=str(path),
            predictions=predictions,
            location=location,
            model_name=self.model_name,
            ran_offline=True,
        )

    def analyze(self, path: Path) -> List[DefectPrediction]:
        image = Image.open(path).convert("RGB").resize((self._input_size, self._input_size))
        pixels = np.asarray(image, dtype=np.float32) / 255.0
        gray = pixels.mean(axis=2)

        darkness = float(np.mean(gray < 0.28))
        local = gray[1:, 1:] - gray[:-1, :-1]
        edge_energy = float(np.mean(np.abs(local)))
        elongation = float(np.std(gray, axis=1).mean() / (np.std(gray, axis=0).mean() + 1e-6))

        pothole_score = min(1.0, darkness * 2.4 + (0.15 if edge_energy > 0.08 else 0.0))
        crack_score = min(1.0, edge_energy * 3.5 + (0.2 if elongation > 1.15 else 0.0))
        wear_score = min(1.0, float(np.std(gray)) * 2.2)

        ranked = [
            DefectPrediction(DefectType.POTHOLE, round(pothole_score, 3)),
            DefectPrediction(DefectType.ROAD_CRACK, round(crack_score, 3)),
            DefectPrediction(DefectType.SURFACE_WEAR, round(wear_score, 3)),
        ]
        ranked.sort(key=lambda item: item.confidence, reverse=True)
        if ranked[0].confidence < 0.22:
            return [DefectPrediction(DefectType.NONE, 0.81)]
        return ranked[:2]


class TFLiteDetector(BaseDetector):
    """Placeholder for a quantized TFLite model shipped with the Android app."""

    model_name = "road-defect-int8-tflite"

    def __init__(self, model_path: str) -> None:
        self._model_path = Path(model_path)
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"TFLite model not found at {model_path}. Use HeuristicDetector offline."
            )

    def detect(self, image_path: str, location: Optional[GeoLocation] = None) -> DetectionResult:
        # Production path: load Interpreter, run int8 inference, decode boxes.
        # Kept explicit so students can plug in a real .tflite file later.
        raise RuntimeError("Wire TensorFlow Lite Interpreter here after exporting the model.")


class OfflineDetectorFacade:
    """Picks an on-device model and never requires the network for inference."""

    def __init__(self, preferred: Optional[BaseDetector] = None) -> None:
        self._detector = preferred or HeuristicDetector()

    def run(self, image_path: str, location: Optional[GeoLocation] = None) -> DetectionResult:
        return self._detector.detect(image_path, location)

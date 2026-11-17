"""On-device detector tests."""

import numpy as np
import pytest
from PIL import Image

from airoad.detector import (
    BaseDetector,
    HeuristicDetector,
    OfflineDetectorFacade,
    TFLiteDetector,
)
from airoad.models import DefectPrediction, DefectType, DetectionResult, GeoLocation


def write_image(path, shade: int = 20) -> str:
    Image.fromarray(np.full((48, 48, 3), shade, dtype=np.uint8)).save(path)
    return str(path)


def test_dark_patch_is_detected_as_a_pothole(tmp_path):
    result = HeuristicDetector().detect(write_image(tmp_path / "dark.png"), GeoLocation(12.97, 77.59))
    assert result.ran_offline is True
    assert result.model_name == "on-device-heuristic-v1"
    assert result.primary.defect_type is DefectType.POTHOLE
    assert result.primary.confidence >= 0.8
    assert result.location == GeoLocation(12.97, 77.59)


def test_bright_uniform_image_reports_no_defect(tmp_path):
    # A flat bright surface has no dark patches and no edges.
    result = HeuristicDetector().detect(write_image(tmp_path / "bright.png", shade=220))
    assert result.primary.defect_type is DefectType.NONE
    assert result.primary.confidence > 0.5


def test_missing_image_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        HeuristicDetector().detect(str(tmp_path / "nope.jpg"))


def test_non_image_file_raises_os_error(tmp_path):
    bogus = tmp_path / "fake.png"
    bogus.write_text("this is not an image")
    with pytest.raises(OSError):
        HeuristicDetector().detect(str(bogus))


def test_tflite_detector_requires_an_existing_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        TFLiteDetector(str(tmp_path / "missing.tflite"))


def test_tflite_detector_reports_a_clear_placeholder_error(tmp_path):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    with pytest.raises(RuntimeError):
        TFLiteDetector(str(model)).detect("road.jpg")


def test_base_detector_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BaseDetector()


def test_facade_defaults_to_the_heuristic_model():
    assert OfflineDetectorFacade().model_name == HeuristicDetector.model_name


def test_facade_accepts_a_custom_detector():
    class FixedDetector(BaseDetector):
        model_name = "fixed-model"

        def detect(self, image_path, location=None):
            return DetectionResult(
                image_path=image_path,
                predictions=[DefectPrediction(DefectType.ROAD_CRACK, 0.42)],
                location=location,
                model_name=self.model_name,
            )

    facade = OfflineDetectorFacade(preferred=FixedDetector())
    assert facade.model_name == "fixed-model"
    result = facade.run("road.jpg", GeoLocation(1.0, 2.0))
    assert result.model_name == "fixed-model"
    assert result.primary.defect_type is DefectType.ROAD_CRACK

"""Domain model tests."""

import pytest

from airoad.models import (
    DamageReport,
    DefectPrediction,
    DefectType,
    DetectionResult,
    GeoLocation,
)


def test_every_defect_type_has_a_display_name():
    names = {defect.display_name for defect in DefectType}
    assert names == {"Pothole", "Road Crack", "Surface Wear", "No Defect"}


@pytest.mark.parametrize("lat,lng", [(-90.0, 0.0), (90.0, 0.0), (0.0, -180.0), (0.0, 180.0), (12.9, 77.5)])
def test_geo_location_accepts_in_range_coordinates(lat, lng):
    assert GeoLocation(lat, lng).is_valid()


@pytest.mark.parametrize("lat,lng", [(90.1, 0.0), (-91.0, 0.0), (0.0, 180.5), (0.0, -181.0)])
def test_geo_location_rejects_out_of_range_coordinates(lat, lng):
    assert not GeoLocation(lat, lng).is_valid()


def test_geo_location_maps_query_is_six_decimals():
    assert GeoLocation(12.9716, 77.5946).as_maps_query() == "12.971600,77.594600"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_must_be_a_probability(confidence):
    with pytest.raises(ValueError):
        DefectPrediction(DefectType.POTHOLE, confidence)


def test_confidence_percent_rounds_to_int():
    assert DefectPrediction(DefectType.POTHOLE, 0.876).confidence_percent == 88


def test_primary_picks_the_highest_confidence():
    result = DetectionResult(
        image_path="road.jpg",
        predictions=[
            DefectPrediction(DefectType.SURFACE_WEAR, 0.3),
            DefectPrediction(DefectType.ROAD_CRACK, 0.7),
        ],
        location=None,
    )
    assert result.primary.defect_type is DefectType.ROAD_CRACK
    assert result.primary.confidence == 0.7


def test_primary_without_predictions_is_no_defect():
    result = DetectionResult(image_path="road.jpg", predictions=[], location=None)
    assert result.primary.defect_type is DefectType.NONE
    assert result.primary.confidence == 0.0


def test_detection_result_serialises_location_and_predictions():
    result = DetectionResult(
        image_path="road.jpg",
        predictions=[DefectPrediction(DefectType.POTHOLE, 0.5, (1.0, 2.0, 3.0, 4.0))],
        location=GeoLocation(12.97, 77.59, accuracy_meters=5.0),
        model_name="demo",
    )
    payload = result.to_dict()
    assert payload["location"] == {
        "latitude": 12.97,
        "longitude": 77.59,
        "accuracy_meters": 5.0,
    }
    assert payload["predictions"][0]["defect_type"] == "pothole"
    assert payload["predictions"][0]["bounding_box"] == (1.0, 2.0, 3.0, 4.0)
    assert payload["model_name"] == "demo"
    assert payload["ran_offline"] is True


def test_detection_result_serialises_missing_location_as_null():
    payload = DetectionResult(image_path="road.jpg", predictions=[], location=None).to_dict()
    assert payload["location"] is None


def test_damage_report_starts_unsynced_and_can_be_marked_synced():
    report = DamageReport(
        result=DetectionResult(image_path="road.jpg", predictions=[], location=None)
    )
    assert report.synced is False
    assert report.report_id
    report.mark_synced()
    assert report.synced is True
    assert report.to_dict()["synced"] is True

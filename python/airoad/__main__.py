"""Command-line entry point: ``python -m airoad IMAGE [--lat LAT --lng LNG] [--json]``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from airoad.detector import OfflineDetectorFacade
from airoad.limits import (
    MAX_UPLOAD_BYTES,
    UploadTooLargeError,
    check_upload_file,
    format_size,
)
from airoad.models import DetectionResult, GeoLocation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airoad",
        description="Detect potholes and road cracks on-device. Never needs the network.",
    )
    parser.add_argument("image", help="path to a road image")
    parser.add_argument("--lat", type=float, default=None, help="GPS latitude of the capture")
    parser.add_argument("--lng", type=float, default=None, help="GPS longitude of the capture")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the full detection result as JSON",
    )
    parser.add_argument(
        "--check-upload",
        action="store_true",
        dest="check_upload",
        help=(
            "only check whether the image fits the "
            f"{format_size(MAX_UPLOAD_BYTES)} upload limit (exit 3 if it does not)"
        ),
    )
    return parser


def format_human(result: DetectionResult) -> str:
    lines = [
        f"Image:    {result.image_path}",
        f"Model:    {result.model_name} (offline: {'yes' if result.ran_offline else 'no'})",
        f"Location: {result.location.as_maps_query() if result.location else 'not provided'}",
    ]
    primary = result.primary
    lines.append(
        f"Primary:  {primary.defect_type.display_name} ({primary.confidence_percent}%)"
    )
    for prediction in result.predictions:
        lines.append(
            f"  - {prediction.defect_type.display_name}: "
            f"{prediction.confidence_percent}%"
        )
    return "\n".join(lines)


def _check_upload(image: str) -> int:
    """Report whether a photo fits the upload limit, without detecting anything."""
    try:
        size = check_upload_file(image)
    except FileNotFoundError:
        print(f"error: no such image: {image}", file=sys.stderr)
        return 2
    except UploadTooLargeError as exc:
        print(f"too large: {exc}", file=sys.stderr)
        return 3
    print(
        f"{image}: {format_size(size)} - fits the "
        f"{format_size(MAX_UPLOAD_BYTES)} upload limit"
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check_upload:
        return _check_upload(args.image)

    if (args.lat is None) != (args.lng is None):
        parser.error("--lat and --lng must be provided together")

    location = None
    if args.lat is not None:
        location = GeoLocation(latitude=args.lat, longitude=args.lng)
        if not location.is_valid():
            parser.error("--lat must be -90..90 and --lng must be -180..180")

    try:
        result = OfflineDetectorFacade().run(args.image, location)
    except FileNotFoundError:
        print(f"error: no such image: {args.image}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not read image {args.image}: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

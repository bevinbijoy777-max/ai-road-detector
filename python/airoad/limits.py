"""Upload size limits.

Vercel Functions reject a request body over 4.5 MB at the edge, before the
function is invoked, so the API never gets a chance to explain why a large photo
failed. Clients therefore need to check the size themselves - either against
``GET /limits`` before uploading, or by downscaling anything above the limit.

The helpers here are shared by the API and the CLI so both agree on the numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

#: Vercel Functions request body cap.
MAX_UPLOAD_BYTES = 4_500_000
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES / 1_000_000

PathLike = Union[str, Path]


class UploadTooLargeError(ValueError):
    """Raised when a photo is bigger than the upload limit."""

    def __init__(self, size_bytes: int, limit_bytes: int = MAX_UPLOAD_BYTES) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"image is {format_size(size_bytes)}, over the "
            f"{format_size(limit_bytes)} upload limit - downscale it first"
        )


def format_size(size_bytes: int) -> str:
    """Human-readable size using the same units as the limit."""
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    if size_bytes >= 1_000:
        return f"{size_bytes / 1_000:.0f} kB"
    return f"{size_bytes} bytes"


def check_upload_size(size_bytes: int, limit_bytes: int = MAX_UPLOAD_BYTES) -> int:
    """Return ``size_bytes`` unchanged, or raise :class:`UploadTooLargeError`."""
    if size_bytes > limit_bytes:
        raise UploadTooLargeError(size_bytes, limit_bytes)
    return size_bytes


def check_upload_file(path: PathLike, limit_bytes: int = MAX_UPLOAD_BYTES) -> int:
    """Check an image file's size on disk before uploading it."""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))
    return check_upload_size(file_path.stat().st_size, limit_bytes)

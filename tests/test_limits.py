"""Upload size limit tests."""

import pytest

from airoad.limits import (
    MAX_UPLOAD_BYTES,
    UploadTooLargeError,
    check_upload_file,
    check_upload_size,
    format_size,
)


def test_limit_matches_the_vercel_body_cap():
    assert MAX_UPLOAD_BYTES == 4_500_000


@pytest.mark.parametrize("size", [0, 1, MAX_UPLOAD_BYTES - 1, MAX_UPLOAD_BYTES])
def test_sizes_at_or_below_the_limit_pass(size):
    assert check_upload_size(size) == size


def test_one_byte_over_the_limit_is_rejected():
    with pytest.raises(UploadTooLargeError) as excinfo:
        check_upload_size(MAX_UPLOAD_BYTES + 1)
    assert excinfo.value.size_bytes == MAX_UPLOAD_BYTES + 1
    assert excinfo.value.limit_bytes == MAX_UPLOAD_BYTES
    assert "downscale" in str(excinfo.value)


def test_custom_limits_are_honoured():
    assert check_upload_size(10, limit_bytes=10) == 10
    with pytest.raises(UploadTooLargeError):
        check_upload_size(11, limit_bytes=10)


@pytest.mark.parametrize(
    "size,expected",
    [(0, "0 bytes"), (999, "999 bytes"), (1_500, "2 kB"), (4_500_000, "4.5 MB"), (12_000_000, "12.0 MB")],
)
def test_format_size(size, expected):
    assert format_size(size) == expected


def test_check_upload_file_reads_the_size_on_disk(tmp_path):
    small = tmp_path / "small.jpg"
    small.write_bytes(b"\0" * 1024)
    assert check_upload_file(small) == 1024

    big = tmp_path / "big.jpg"
    big.write_bytes(b"\0" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(UploadTooLargeError):
        check_upload_file(big)


def test_check_upload_file_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_upload_file(tmp_path / "nope.jpg")

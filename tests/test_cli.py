"""CLI tests for ``python -m airoad``."""

import json

import numpy as np
import pytest
from PIL import Image

from airoad.__main__ import main


def write_image(tmp_path, shade: int = 20) -> str:
    path = tmp_path / "road.png"
    Image.fromarray(np.full((40, 40, 3), shade, dtype=np.uint8)).save(path)
    return str(path)


def test_cli_prints_json(tmp_path, capsys):
    exit_code = main([write_image(tmp_path), "--lat", "12.9716", "--lng", "77.5946", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ran_offline"] is True
    assert payload["location"]["latitude"] == pytest.approx(12.9716)
    assert payload["predictions"]


def test_cli_prints_a_human_summary(tmp_path, capsys):
    exit_code = main([write_image(tmp_path)])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Primary:" in output
    assert "Location: not provided" in output


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.png")]) == 2
    assert "no such image" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--lat", "12.0"],                       # half a coordinate pair
        ["--lat", "999", "--lng", "0"],          # out of range
        ["--lat", "0", "--lng", "-999"],
    ],
)
def test_cli_rejects_bad_coordinates(tmp_path, args):
    with pytest.raises(SystemExit) as excinfo:
        main([write_image(tmp_path), *args])
    assert excinfo.value.code == 2

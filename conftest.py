"""Test bootstrap: make the ``python/`` source root importable from the repo root."""

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "python"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

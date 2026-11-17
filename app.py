"""Vercel entrypoint.

Vercel's FastAPI preset looks for a module-level ``app`` at a recognised
entrypoint (``app.py``, ``index.py``, ``main.py``, ...) in the project root. The
real application lives in ``python/airoad/api.py``, so this file puts that
source root on the import path and re-exports ``app``.

Local development and the container/uvicorn setup do not need this file:

    uvicorn airoad.api:app --port 8000
"""

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "python"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from airoad.api import app  # noqa: E402  (path setup must run first)

__all__ = ["app"]

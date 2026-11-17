"""The Vercel entrypoint must expose the same FastAPI app without the package installed."""

from fastapi import FastAPI

import app as vercel_entrypoint
from airoad.api import app as package_app


def test_root_app_module_reexports_the_fastapi_app():
    assert isinstance(vercel_entrypoint.app, FastAPI)
    assert vercel_entrypoint.app is package_app


def test_entrypoint_exposes_the_public_routes():
    paths = {route.path for route in vercel_entrypoint.app.routes}
    assert {"/health", "/detect", "/reports"} <= paths

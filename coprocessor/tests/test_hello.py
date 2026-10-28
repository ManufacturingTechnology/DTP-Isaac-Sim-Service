"""Smoke test: the installed co-processor imports, builds its FastAPI app, and answers its version route.

A starting point for the co-processor's test suite (`make test` here or at the repo root).
"""
import asyncio

import amt.dtp.iscp
from amt.dtp.iscp.coprocessor import app


def test_app_builds():
    assert app.title.startswith("AMT Digital Testbed Platform (DTP) Isaac Sim Co-Processor")


def test_version_route_returns_package_version():
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/application/version")
    result = route.endpoint()
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    assert result == amt.dtp.iscp.__version__

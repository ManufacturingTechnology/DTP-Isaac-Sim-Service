"""ApiHost (ADR-0024): the shared server host under CoreApi and KinematicsApi: start/stop on its own thread, the
documentation routes, no routes of its own, error handling, and the bridge to the main loop."""
import asyncio
import threading

import pytest
import requests
from fastapi import APIRouter

from amt.dtp.ise.lib.control.core.server import CoreApi
from amt.dtp.ise.lib.control.host import ApiHost
from amt.dtp.ise.lib.control.motion.server import KinematicsApi

from .conftest import free_port


def test_start_stop_docs_and_routes():
    host = ApiHost("Probe", description="A test host", port=free_port(), host="127.0.0.1")
    router = APIRouter(prefix="/api/probe")

    @router.get("/thread")
    async def thread():
        return {"thread": threading.current_thread().name}

    @router.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    host.add_router(router)
    host.start()
    try:
        base = f"http://127.0.0.1:{host.port}"
        assert host.is_running and host.server_loop is not None
        assert requests.get(f"{base}/api/probe/thread").json()["thread"] == host.server_thread.name != \
            threading.current_thread().name
        for path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
            assert requests.get(base + path).status_code == 200
        assert requests.get(f"{base}/api/core/info").status_code == 404  # no routes of its own
        answer = requests.get(f"{base}/api/probe/boom")
        assert answer.status_code == 500 and "boom" in answer.json()["detail"]
        with pytest.raises(RuntimeError):
            host.add_router(APIRouter())  # too late: the app is built
        with pytest.raises(RuntimeError):
            host.start()  # already running
    finally:
        host.stop()
    assert not host.is_running
    host.stop()  # idempotent


def test_the_main_loop_bridge():
    async def scenario():
        main = asyncio.get_running_loop()
        host = ApiHost("Bridge", port=free_port(), host="127.0.0.1")
        router = APIRouter(prefix="/api/bridge")

        async def on_main():
            return asyncio.get_running_loop() is main and threading.current_thread() is threading.main_thread()

        @router.get("/main")
        async def call_main():
            return {"on_main": await host.run_coro_on_main_loop(on_main())}

        host.add_router(router)
        host.start()  # captures this (the main) loop
        try:
            assert host.main_loop is main
            answer = await asyncio.to_thread(requests.get, f"http://127.0.0.1:{host.port}/api/bridge/main")
            return answer.json()
        finally:
            host.stop()

    assert asyncio.run(scenario()) == {"on_main": True}


def test_the_layering():
    assert issubclass(CoreApi, ApiHost) and issubclass(KinematicsApi, ApiHost) and not issubclass(KinematicsApi, CoreApi)
    core = CoreApi("Core", port=free_port())
    assert core.layers == ["CoreApi"]  # ApiHost is below the machine-control layers, not one of them

"""The CoreApi base: info, assets, layering, and running code on the starting thread's loop."""
import asyncio
import hashlib
import threading

import pytest
import requests
from fastapi import APIRouter

from amt.dtp.ise.lib.control.core.client import CoreApiError, CoreClient
from amt.dtp.ise.lib.control.core.server import CoreApi

from .conftest import free_port


@pytest.fixture
def asset_files(tmp_path):
    urdf = tmp_path / "AMT MR-A1.urdf"
    urdf.write_text("<robot name='mr_a1'/>", encoding="utf-8")
    xrdf = tmp_path / "robot.xrdf"
    xrdf.write_text("format: xrdf\n", encoding="utf-8")
    return urdf, xrdf


@pytest.fixture
def api(asset_files):
    urdf, xrdf = asset_files
    api = CoreApi("Robot Cell", "A test cell", "2.0.0", port=free_port(), host="127.0.0.1", assets=[urdf, xrdf])
    api.start()
    yield api
    api.stop()


def test_info(api):
    info = CoreClient(f"http://127.0.0.1:{api.port}").info()
    assert (info.title, info.description, info.version) == ("Robot Cell", "A test cell", "2.0.0")
    assert info.layers == ["CoreApi"]


def test_assets_list_and_download(api, asset_files, tmp_path):
    urdf, xrdf = asset_files
    client = CoreClient(f"http://127.0.0.1:{api.port}")
    assets = {a.name: a for a in client.assets()}
    assert set(assets) == {"AMT MR-A1.urdf", "robot.xrdf"}
    assert assets["AMT MR-A1.urdf"].sha256 == hashlib.sha256(urdf.read_bytes()).hexdigest()
    assert assets["AMT MR-A1.urdf"].size == urdf.stat().st_size
    assert assets["AMT MR-A1.urdf"].media_type == "application/xml"
    assert assets["robot.xrdf"].media_type == "application/yaml"

    assert client.asset_bytes("AMT MR-A1.urdf") == urdf.read_bytes()  # name with a space
    out = tmp_path / "downloads"
    out.mkdir()
    path = client.download_asset("robot.xrdf", out)
    assert path == out / "robot.xrdf" and path.read_bytes() == xrdf.read_bytes()
    response = requests.get(f"http://127.0.0.1:{api.port}/api/core/assets/download/robot.xrdf")
    assert 'filename="robot.xrdf"' in response.headers["content-disposition"]

    with pytest.raises(CoreApiError) as info:
        client.asset_bytes("missing.urdf")
    assert info.value.status_code == 404 and "robot.xrdf" in str(info.value)


def test_base_endpoints_are_tagged_core(api):
    paths = requests.get(f"http://127.0.0.1:{api.port}/api/openapi.json").json()["paths"]
    for path in ("/api/core/info", "/api/core/assets/list", "/api/core/assets/download/{name}"):
        assert paths[path]["get"]["tags"] == ["Core"], path


def test_asset_hash_follows_file_changes(api, asset_files):
    urdf, _ = asset_files
    client = CoreClient(f"http://127.0.0.1:{api.port}")
    before = {a.name: a.sha256 for a in client.assets()}["AMT MR-A1.urdf"]
    urdf.write_text("<robot name='changed'/>", encoding="utf-8")
    after = {a.name: a.sha256 for a in client.assets()}["AMT MR-A1.urdf"]
    assert before != after


def test_invalid_assets(tmp_path):
    a = tmp_path / "a" / "same.urdf"
    b = tmp_path / "b" / "same.urdf"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text("x")
    with pytest.raises(ValueError, match="two assets are named 'same.urdf'"):
        CoreApi("X", assets=[a, b])
    with pytest.raises(ValueError, match="is not a file"):
        CoreApi("X", assets=[tmp_path / "missing.urdf"])
    # A mapping renames.
    assert set(CoreApi("X", assets={"a.urdf": a, "b.urdf": b}).assets) == {"a.urdf", "b.urdf"}


class EchoApi(CoreApi):
    """A minimal layered API: adds a route that runs code on the starting thread's loop."""

    def __init__(self, **kwargs):
        super().__init__("Echo", **kwargs)
        router = APIRouter(prefix="/api/echo", tags=["Echo"])

        @router.get("/thread")
        async def thread():
            async def on_main():
                return threading.current_thread().name
            return {"thread": await self.run_coro_on_main_loop(on_main())}

        self.add_router(router)

    def api_description(self) -> str:
        return super().api_description() + "\n\n**Echo** layer."


def test_layering_and_main_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    api = EchoApi(port=free_port(), host="127.0.0.1")
    try:
        api.start()
        assert api.main_loop is loop
        result = {}

        def call():
            result["response"] = requests.get(f"http://127.0.0.1:{api.port}/api/echo/thread", timeout=5).json()

        caller = threading.Thread(target=call)
        caller.start()
        # The "simulation" thread (this one) runs its loop, so the coroutine runs here.
        while caller.is_alive():
            loop.run_until_complete(asyncio.sleep(0.01))
        assert result["response"] == {"thread": threading.current_thread().name}

        info = CoreClient(f"http://127.0.0.1:{api.port}").info()
        assert info.layers == ["CoreApi", "EchoApi"]
        assert "**Echo** layer." in api.app.description
        with pytest.raises(RuntimeError, match="before the app is built"):
            api.add_router(APIRouter())
    finally:
        api.stop()
        asyncio.set_event_loop(None)
        loop.close()

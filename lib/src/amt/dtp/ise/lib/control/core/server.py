"""CoreApi: the base every machine-control API builds on (e.g. the segment API).

A CoreApi is an :class:`~..host.ApiHost` (a FastAPI app served by uvicorn on its own thread and event loop, so
requests never block the simulation, with a bridge to the loop of the thread that started it, Isaac Sim's main loop)
that composes the machine-control API layers and self-describes:

- ``GET /api/core/info``: the API's title, description, version, and layers.
- ``GET /api/core/assets/list`` and ``GET /api/core/assets/download/{name}``: files given to the
  constructor (e.g. URDF/XRDF robot descriptions), for clients to download.

APIs are layered by subclassing: a subclass adds its own routes with :meth:`add_router`
(e.g. SegmentApi adds ``/api/segment/...``) and can extend the API description and the stop
sequence. Requires the ``server`` extra (fastapi, uvicorn).
"""
import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from ..host import API_PREFIX, ApiHost
from .models import AssetInfo, CoreInfo


CORE_PREFIX = f"{API_PREFIX}/core"

# Media types mimetypes doesn't know.
_MEDIA_TYPES = {
    ".urdf": "application/xml",
    ".xrdf": "application/yaml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".usd": "application/octet-stream",
    ".usda": "text/plain",
    ".usdc": "application/octet-stream",
    ".usdz": "model/vnd.usdz+zip",
    ".stl": "model/stl",
}

AssetsArg = Optional[Union[Mapping[str, Union[str, os.PathLike]], Sequence[Union[str, os.PathLike]]]]


def _media_type(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class CoreApi(ApiHost):
    """The base machine-control API.

    :param title: The API's title (shown in the API docs).
    :param description: Markdown shown at the top of the API docs.
    :param version: The version set by the API's author.
    :param port: The TCP port to serve on.
    :param host: The interface to bind; "0.0.0.0" serves on every interface.
    :param assets: Files clients can download: a ``{name: path}`` mapping, or a sequence of paths
        (each served under its file name). Every file must exist.
    """

    def __init__(
        self,
        title: str,
        description: str = "",
        version: str = "0.1.0",
        port: int = 8020,
        host: str = "0.0.0.0",
        assets: AssetsArg = None,
    ):
        super().__init__(title, description, version, port, host)
        self._assets: Dict[str, Path] = self._normalize_assets(assets)
        self._asset_hashes: Dict[str, Tuple[int, int, str]] = {}

        self.add_router(self._base_router())

    @staticmethod
    def _normalize_assets(assets: AssetsArg) -> Dict[str, Path]:
        if assets is None:
            return {}
        if isinstance(assets, Mapping):
            items = [(str(name), Path(path)) for name, path in assets.items()]
        elif isinstance(assets, Sequence) and not isinstance(assets, (str, bytes)):
            items = [(Path(path).name, Path(path)) for path in assets]
        else:
            raise TypeError("assets must be a Mapping[str, path] or a Sequence[path]")
        result: Dict[str, Path] = {}
        problems: List[str] = []
        for name, path in items:
            if not name or "/" in name or "\\" in name:
                problems.append(f"asset name {name!r} must be a non-empty file name (no '/' or '\\')")
            elif name in result:
                problems.append(f"two assets are named {name!r} ({result[name]} and {path}); pass a {{name: path}} mapping")
            elif not path.is_file():
                problems.append(f"asset {name!r}: {path} is not a file")
            else:
                result[name] = path.resolve()
        if problems:
            raise ValueError("invalid assets: " + "; ".join(problems))
        return result

    # ---- properties -------------------------------------------------------------------------

    @property
    def assets(self) -> Dict[str, Path]:
        """The served files, by name."""
        return dict(self._assets)

    @property
    def layers(self) -> List[str]:
        """The API layers of this API, base first (e.g. ``["CoreApi", "SegmentApi"]``)."""
        return [c.__name__ for c in reversed(type(self).__mro__) if isinstance(c, type) and issubclass(c, CoreApi)]

    # ---- building -----------------------------------------------------------------------------

    def api_description(self) -> str:
        """Markdown shown at the top of the API docs. Subclasses extend it."""
        lines = [self._description, ""] if self._description else []
        lines.append(f"**API layers**: {', '.join(self.layers)}.")
        if self._assets:
            lines.append("")
            lines.append(f"**Assets**: {', '.join(f'[{n}]({CORE_PREFIX}/assets/download/{n})' for n in self._assets)}.")
        return "\n".join(lines)

    def _base_router(self) -> APIRouter:
        router = APIRouter(prefix=CORE_PREFIX, tags=["Core"])

        @router.get("/info", summary="Get API Info", response_model=CoreInfo)
        async def info():
            return CoreInfo(
                title=self._title, description=self._description, version=self._version,
                layers=self.layers,
            )

        @router.get(
            "/assets/list",
            summary="List Assets",
            description="The files this API serves (e.g. robot descriptions).",
            response_model=List[AssetInfo],
        )
        async def assets_list():
            return [self._asset_info(name) for name in self._assets]

        @router.get(
            "/assets/download/{name}",
            summary="Download Asset",
            response_class=FileResponse,
            responses={200: {"content": {"application/octet-stream": {}}}, 404: {"description": "No asset with this name."}},
        )
        async def assets_download(name: str):
            path = self._assets.get(name)
            if path is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"no asset named {name!r}; assets: {sorted(self._assets)}",
                )
            if not path.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"asset {name!r} no longer exists")
            return FileResponse(path, media_type=_media_type(path), filename=name)

        return router

    def _asset_info(self, name: str) -> AssetInfo:
        path = self._assets[name]
        stat = path.stat()
        cached = self._asset_hashes.get(name)
        if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            cached = (stat.st_mtime_ns, stat.st_size, digest.hexdigest())
            self._asset_hashes[name] = cached
        return AssetInfo(name=name, size=stat.st_size, media_type=_media_type(path), sha256=cached[2])

    # ---- lifecycle ------------------------------------------------------------------------------

    def _started_message(self) -> str:
        return f"{super()._started_message()} ({', '.join(self.layers)})"

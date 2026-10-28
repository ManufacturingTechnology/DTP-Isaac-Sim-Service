"""CoreClient: the base of the control API clients (see :mod:`.server`).

Only depends on requests and pydantic (not on the server's fastapi/uvicorn).
"""
from pathlib import Path
from typing import Any, List, Optional, Union

import requests

from .models import AssetInfo, CoreInfo


API_PREFIX = "/api"


class CoreApiError(Exception):
    """A control API returned an error. ``code`` is the error code when the API sent one (e.g.
    ``OUT_OF_RANGE``, ``HALTED``), else None; ``detail`` is the response's ``detail``."""

    def __init__(self, status_code: int, detail: Any, method: str = "", url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.code = detail.get("code") if isinstance(detail, dict) else None
        if isinstance(detail, dict) and "message" in detail:  # a coded error: {code, message, ...}
            text = f"{detail.get('code')}: {detail['message']}" if detail.get("code") else str(detail["message"])
        elif isinstance(detail, list):  # FastAPI validation errors
            lines = []
            for error in detail:
                loc = ".".join(str(p) for p in error.get("loc", []))
                lines.append(f"  {loc}: {error.get('msg')}")
            text = "invalid request:\n" + "\n".join(lines)
        else:
            text = str(detail)
        super().__init__(f"{method} {url} -> {status_code}: {text}".strip())


class HttpClient:
    """The HTTP plumbing every client shares (a session, ``/api`` URLs, coded errors as :class:`CoreApiError`),
    with no routes of its own: the base of :class:`CoreClient` (machine-control APIs) and of the kinematics
    service's client.

    :param base_url: The API's address, e.g. ``http://isaac-host:8020``.
    :param timeout: Timeout in seconds for ordinary requests.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8020", timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    @property
    def base_url(self) -> str:
        return self._base_url

    def _url(self, path: str) -> str:
        return f"{self._base_url}{API_PREFIX}{path}"

    def _raise_for(self, response: requests.Response, method: str, url: str) -> None:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            response.close()
            raise CoreApiError(response.status_code, detail, method, url)

    def _request(self, method: str, path: str, timeout: Optional[float] = None, **kwargs: Any) -> requests.Response:
        url = self._url(path)
        response = self._session.request(method, url, timeout=timeout or self._timeout, **kwargs)
        self._raise_for(response, method, url)
        return response

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class CoreClient(HttpClient):
    """Client for a machine-control API's base endpoints (the CoreApi layer): info and assets.

    :param base_url: The API's address, e.g. ``http://isaac-host:8020``.
    :param timeout: Timeout in seconds for ordinary requests.
    """

    # ---- API ------------------------------------------------------------------------------------

    def info(self) -> CoreInfo:
        """The API's title, description, version, and layers."""
        return CoreInfo.model_validate(self._request("GET", "/core/info").json())

    def assets(self) -> List[AssetInfo]:
        """The files the API serves."""
        return [AssetInfo.model_validate(a) for a in self._request("GET", "/core/assets/list").json()]

    def asset_bytes(self, name: str) -> bytes:
        """The contents of an asset."""
        return self._request("GET", f"/core/assets/download/{requests.utils.quote(name)}").content

    def download_asset(self, name: str, destination: Union[str, Path, None] = None) -> Path:
        """Download an asset to ``destination`` (a file path, or a directory to save it in under its
        name; the current directory by default). Returns the file's path."""
        destination = Path(destination) if destination is not None else Path.cwd()
        if destination.is_dir():
            destination = destination / name
        url = self._url(f"/core/assets/download/{requests.utils.quote(name)}")
        with self._session.get(url, stream=True, timeout=self._timeout) as response:
            self._raise_for(response, "GET", url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(destination, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return destination

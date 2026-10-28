"""ApiHost: a FastAPI app served by uvicorn on its own thread and event loop (ADR-0024).

The generic part of hosting a web API from inside Isaac Sim (or any process): routers (:meth:`ApiHost.add_router`),
the app with its documentation (``/api/docs``, ``/api/redoc``, ``/api/openapi.json``), an error handler that logs
unhandled exceptions, start/stop on a daemon thread with its own asyncio loop, a helper to schedule work on the
server's loop, and an **optional bridge to the main loop**: :meth:`ApiHost.start` captures the event loop of the
calling thread (Isaac Sim's main loop when started from the extension or a behavior script), and
:meth:`ApiHost.run_coro_on_main_loop` runs a coroutine there from a request handler (for anything that touches Isaac
Sim state). It has **no routes of its own** and knows nothing about machine control:

- :class:`~.core.server.CoreApi` (the machine-control core: identity, assets, layers, ``/api/core``; ADR-0007) and so
  the segment API and the RobotAPI build on it;
- the scene-free kinematics service (:class:`~.motion.server.KinematicsApi`) builds on it directly and never uses the
  bridge (it touches no Isaac state, so it handles requests on its own thread, one at a time).

Requires the ``server`` extra (fastapi, uvicorn).
"""
import asyncio
import textwrap
import threading
import time
import warnings
from typing import Any, Callable, Coroutine, List, Optional

import uvicorn
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse

from ..logging import _logger


API_PREFIX = "/api"


class ApiHost:
    """Hosts a FastAPI app on its own thread.

    :param title: The API's title (shown in the API docs).
    :param description: Markdown shown at the top of the API docs (see :meth:`api_description`).
    :param version: The version set by the API's author.
    :param port: The TCP port to serve on.
    :param host: The interface to bind; "0.0.0.0" serves on every interface.
    """

    def __init__(self, title: str, description: str = "", version: str = "0.1.0", port: int = 8020,
                 host: str = "0.0.0.0"):
        if not isinstance(title, str) or not title.strip():
            raise TypeError("title must be a non-empty str")
        if not isinstance(description, str):
            raise TypeError("description must be a str")
        if not isinstance(version, str) or not version.strip():
            raise TypeError("version must be a non-empty str")
        if not isinstance(port, int):
            raise TypeError("port must be an int")
        if not isinstance(host, str):
            raise TypeError("host must be a str")
        self._title = title
        self._description = description
        self._version = version
        self._port = port
        self._host = host
        self._routers: List[APIRouter] = []
        self._app: Optional[FastAPI] = None
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    # ---- properties -------------------------------------------------------------------------

    @property
    def title(self) -> str:
        return self._title

    @property
    def description(self) -> str:
        return self._description

    @property
    def version(self) -> str:
        return self._version

    @property
    def port(self) -> int:
        return self._port

    @property
    def host(self) -> str:
        return self._host

    @property
    def app(self) -> FastAPI:
        """The FastAPI app (built on first access; add routers before that)."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def server_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """The event loop the API runs on (on its own thread), while running."""
        return self._loop

    @property
    def main_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """The event loop of the thread that called :meth:`start` (Isaac Sim's main loop when started from the
        extension or a behavior script), if it had one."""
        return self._main_loop

    @property
    def server_thread(self) -> Optional[threading.Thread]:
        """The thread the API runs on, while running."""
        return self._thread

    # ---- building -----------------------------------------------------------------------------

    def add_router(self, router: APIRouter) -> None:
        """Add routes (under the router's own prefix; use ``/api/...``). Must be called before the
        app is built (i.e. before :meth:`start` or the first access of :attr:`app`)."""
        if self._app is not None:
            raise RuntimeError("routers must be added before the app is built (before start())")
        self._routers.append(router)

    def api_description(self) -> str:
        """Markdown shown at the top of the API docs. Subclasses extend it."""
        return self._description

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title=self._title,
            version=self._version,
            description=textwrap.dedent(self.api_description()).strip(),
            docs_url=f"{API_PREFIX}/docs",
            redoc_url=f"{API_PREFIX}/redoc",
            openapi_url=f"{API_PREFIX}/openapi.json",
            # One schema per model (named like the class), rather than separate "-Input"/"-Output"
            # variants for models used in both requests and responses.
            separate_input_output_schemas=False,
        )

        @app.exception_handler(Exception)
        async def _unhandled_exception_handler(request: Request, ex: Exception):
            _logger.error(f"Unhandled exception on {request.method} {request.url}", exception=ex)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Internal server error: {ex}"},
            )

        for router in self._routers:
            app.include_router(router)
        return app

    # ---- lifecycle ------------------------------------------------------------------------------

    def _on_starting(self) -> None:
        """Called by :meth:`start` on the starting thread before the server thread starts. Subclasses extend it."""

    def _capture_main_loop(self) -> None:
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    self._main_loop = asyncio.get_event_loop()
            except RuntimeError:
                self._main_loop = None

    def _started_message(self) -> str:
        return f"API '{self._title}' v{self._version} serving on port {self._port}"

    def start(self, timeout: float = 10.0) -> None:
        """Start serving on a background thread. Blocks until the server accepts connections;
        raises RuntimeError if it can't start (e.g. the port is in use)."""
        if self.is_running:
            raise RuntimeError(f"the API '{self._title}' is already running")
        app = self.app
        self._capture_main_loop()
        self._on_starting()

        config = uvicorn.Config(app, host=self._host, port=self._port, log_config=None, timeout_graceful_shutdown=2)
        server = uvicorn.Server(config)
        self._server = server
        loop_ready = threading.Event()
        failure: List[BaseException] = []

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop_ready.set()
            try:
                loop.run_until_complete(server.serve())
            except BaseException as ex:  # uvicorn exits with SystemExit when it can't bind
                failure.append(ex)
                if isinstance(ex, Exception):
                    _logger.error(f"API '{self._title}' stopped with an error", exception=ex)
                else:
                    _logger.error(f"API '{self._title}' stopped: {type(ex).__name__}: {ex}")
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        self._thread = threading.Thread(target=_run, daemon=True, name=f"API: {self._title}")
        self._thread.start()
        loop_ready.wait(timeout)

        deadline = time.monotonic() + timeout
        while not server.started:
            if failure or not self._thread.is_alive():
                self._thread.join(1.0)
                self._thread = None
                self._loop = None
                self._server = None
                raise RuntimeError(f"the API '{self._title}' could not start on {self._host}:{self._port} (is the port already in use?)")
            if time.monotonic() > deadline:
                raise RuntimeError(f"the API '{self._title}' did not start within {timeout} s")
            time.sleep(0.01)
        _logger.info(self._started_message())

    def stop(self, timeout: float = 5.0) -> None:
        """Stop serving."""
        server, thread = self._server, self._thread
        if server is None:
            return
        self._on_stopping()
        server.should_exit = True
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout)
        self._server = None
        self._thread = None
        self._loop = None

    def _on_stopping(self) -> None:
        """Called by :meth:`stop` before the server shuts down (e.g. to close open streams).
        Subclasses extend it."""

    # ---- threading helpers ------------------------------------------------------------------------

    def call_soon_on_server_loop(self, callback: Callable[..., Any], *args: Any) -> bool:
        """Schedule ``callback(*args)`` on the API's event loop from any thread. Returns False if
        the API isn't running."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(callback, *args)
            return True
        except RuntimeError:
            return False  # loop closed between the check and the call (stopping)

    async def run_coro_on_main_loop(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """From a request handler, run ``coro`` on the main loop (the loop of the thread that called :meth:`start`,
        e.g. Isaac Sim's) and wait for its result. Use it for code that must run on the simulation's thread."""
        if not asyncio.iscoroutine(coro):
            raise TypeError("coro must be a coroutine")
        if self._main_loop is None:
            coro.close()
            raise RuntimeError("the API was started from a thread without an event loop")
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._main_loop))

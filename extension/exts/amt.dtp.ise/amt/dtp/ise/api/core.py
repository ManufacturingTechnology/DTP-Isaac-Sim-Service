import os
import pathlib
from typing import List, Optional, Callable, Dict, Coroutine, Any
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import asyncio
import threading
import logging
import textwrap
import sys
from datetime import datetime, timezone
import inspect
import traceback

from omni.services.core.routers import ServiceAPIRouter
import omni.services.core.main
import uvicorn.loops

from ..__version__ import __version__
from ..lib.control.host import ApiHost
from ..lib.logging import _logger, LogEntry, LogLevels
#from ..service.logging import _logger, LogEntry, LogLevels


class _AmtDtpUvicornApiLogHandler(logging.Handler):

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        match record.levelname:
            case "CRITICAL":
                level = LogLevels.Critical
            case "ERROR":
                level = LogLevels.Error
            case "WARNING":
                level = LogLevels.Warning
            case "INFO":
                level = LogLevels.Trace
            case "DEBUG":
                level = LogLevels.Trace
            case "NOTSET":
                level = LogLevels.Trace
            case _:
                level = LogLevels.Trace
        timestamp = datetime.now().astimezone(timezone.utc)
        frames = inspect.stack()
        frames.pop(0) # Remove the emit() function
        frames.pop(0) # Remove the handler() function
        # Remove all frames from the logging.Logger class
        while True:
            try:
                sender = frames[0].frame.f_locals["self"]
                if isinstance(sender, logging.Logger):
                    frames.pop(0) # Remove logger frame
                else:
                    break
            except Exception as ex:
                break
        _logger._log(LogEntry(timestamp, level, msg, frames))


def _configure_uvicorn_logging_to_callback():
    handler = _AmtDtpUvicornApiLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear() # avoid duplicates
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False # keep it from also going to root handlers


class ApiController(ApiHost):
    """The ISE API (port 8012): the extension's own routes (``api/routers.py``) under ``/api``, the static docs site at
    ``/``, uvicorn's logs forwarded to the extension's logger. It is an
    :class:`~..lib.control.host.ApiHost` (ADR-0024): the server runs on its own thread with its own loop, and handlers
    run Isaac-touching code on Isaac Sim's main loop with :meth:`run_coro_on_isaac_loop` (an alias of
    :meth:`~..lib.control.host.ApiHost.run_coro_on_main_loop`). A singleton."""
    _singleton: Optional["ApiController"] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True
        super().__init__("AMT Digital Testbed Platform (DTP) Isaac Sim Extension (ISE)", version=__version__, port=8012,
                         host="0.0.0.0")
        self._root_router = ServiceAPIRouter()

    @property
    def root_router(self) -> ServiceAPIRouter:
        return self._root_router

    @property
    def thread_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """The server's loop (on its own thread)."""
        return self.server_loop

    def add_router(self, router: ServiceAPIRouter):
        self._root_router.include_router(router)

    def _build_app(self) -> FastAPI:
        app = super()._build_app()
        app.include_router(self._root_router, prefix="/api")
        _docs_dir = pathlib.Path(__file__).parent.parent.parent.parent.parent / "docs"
        if _docs_dir.exists():
            app.mount("/", StaticFiles(directory=_docs_dir, html=True), name="site")
        return app

    def start(self, timeout: float = 10.0):
        """Start serving (as before, a failure to start is logged and the extension carries on)."""
        _configure_uvicorn_logging_to_callback()
        try:
            super().start(timeout)
        except RuntimeError as ex:
            _logger.error("Error starting api server", exception=ex)

    def stop(self, timeout: float = 5.0):
        super().stop(timeout)
        ApiController._singleton = None
        ApiController._singleton_intialized = False

    async def run_coro_on_isaac_loop(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run ``coro`` on Isaac Sim's main loop and wait for its result (an alias of ``run_coro_on_main_loop``)."""
        return await self.run_coro_on_main_loop(coro)

"""KinematicsService: the kinematics service (ADR-0010), started and stopped with the extension.

Serves a :class:`~..lib.control.motion.server.KinematicsApi` with the ``cumotion`` backend (solvers and planners) on
its own port (``kinematics_service_port`` in the extension configuration, default 8014; Swagger at ``/api/docs``):
clients upload a robot's URDF and XRDF (optionally with end-effector fragments), get a solver, and call it (FK, IK,
Jacobians, trajectory sampling including planned moves, tool frames, self-collision, and a world of obstacles they
send). **It never touches Isaac Sim's state**: no stage, timeline, physics, ``omni.usd`` context, or Isaac's event
loop, so it works with no stage open and no simulation playing (only the process hosts it). Requests are handled on
the API's own thread, one at a time (ADR-0010); every cuMotion call holds the process-wide
``CUMOTION_LOCK``. Solvers live in memory until they are deleted or the service stops (their files are deleted with
them).

If the service can't start (e.g. the port is in use), the error is logged and shown in the extension's error window;
the extension carries on without the service.
"""
import asyncio
from typing import Any, Optional

from ..__version__ import __version__
from ..lib.control.motion.server import DEFAULT_PORT, KinematicsApi
from ..lib.logging import _logger
from .config import ConfigurationService
from .core import Service


class KinematicsService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._api: Optional[KinematicsApi] = None
        self._error_window: Optional[Any] = None  # a MessageWindow, shown if the service can't start

    @property
    def api(self) -> Optional[KinematicsApi]:
        """The running kinematics API, or None (not started, or it couldn't start)."""
        return self._api

    def _service_start(self) -> None:
        if self._api is not None:
            raise Exception("_api is not None. Can not start KinematicsService.")
        try:
            port = ConfigurationService().configuration.kinematics_service_port or DEFAULT_PORT
        except Exception as ex:
            _logger.error("Could not read the kinematics service port; using the default", exception=ex)
            port = DEFAULT_PORT
        try:
            from ..robot.kinematics import cumotion_solver_factory  # imports cuMotion
            from ..robot.planning import cumotion_planner_factory
            api = KinematicsApi(
                {"cumotion": cumotion_solver_factory},
                planner_factories={"cumotion": cumotion_planner_factory},
                title="AMT DTP Kinematics Service",
                description="Robot kinematics and motion planning over a REST API, independent of Isaac Sim's scene: "
                            "upload a robot's URDF and XRDF, then call its solver; send obstacles to plan around.",
                version=__version__,
                port=port,
            )
            api.start()
        except Exception as ex:
            _logger.error(f"The kinematics service could not start on port {port}; the extension continues without it",
                          exception=ex)
            self._show_error(port, ex)
            return
        self._api = api

    def _show_error(self, port: int, ex: Exception) -> None:
        """Show the error window on the next update (the extension may still be starting its UI). The UI modules are
        imported here, so the service itself imports nothing from Omniverse (the offline probe checks that)."""
        async def show():
            try:
                import omni.kit.app
                from ..ui.core import MessageWindow
                await omni.kit.app.get_app().next_update_async()
                self._error_window = MessageWindow(
                    "Error Report",
                    f"The kinematics service could not start on port {port}, so it is not available.\n"
                    f"If the port is in use, change kinematics_service_port in the extension configuration and "
                    f"restart the extension.\n\n{ex}",
                    ex,
                ).show()
            except Exception as window_ex:
                _logger.error("Could not show the kinematics service error window", exception=window_ex)
        try:
            asyncio.ensure_future(show())
        except RuntimeError as loop_ex:  # no event loop (outside Isaac Sim, e.g. an offline harness)
            _logger.error("Could not schedule the kinematics service error window", exception=loop_ex)

    def _service_stop(self) -> None:
        if self._error_window is not None:
            self._error_window.hide()
            self._error_window = None
        if self._api is not None:
            try:
                self._api.stop()  # also deletes every solver and its files
            finally:
                self._api = None

        KinematicsService._singleton = None
        KinematicsService._singleton_intialized = False

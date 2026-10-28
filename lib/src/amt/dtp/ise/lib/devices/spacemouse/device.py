"""`SpaceMouse`: read a 6-DOF SpaceMouse (or any HID multi-axis controller) from any Python program.

    from amt.dtp.ise.lib.devices.spacemouse import SpaceMouse

    with SpaceMouse() as mouse:          # first device found; reconnects if it's unplugged
        state = mouse.state()            # latest snapshot: translation, rotation, buttons
        print(state.translation, state.rotation, state.buttons)

A reader thread blocks on the device and merges every report into one persistent six-axis state (devices such as the
SpaceMouse Compact send translation and rotation in separate reports, and send nothing while the cap is at rest).
`state()` only copies that snapshot, so it never blocks: safe to call every frame from an application's main loop.
`on_change` callbacks run on the reader thread.
"""
import threading
import time
from typing import Callable, List, Optional, Union

from ...logging import _logger
from .backend import Backend, DeviceDisconnected, OpenDevice, SpaceMouseUnavailable, default_backend
from .hid import ROTATION_USAGES, TRANSLATION_USAGES, ReportValues, normalize
from .models import DeviceInfo, SpaceMouseState
from .tested import is_tested

DeviceSelector = Union[DeviceInfo, str, Callable[[DeviceInfo], bool], None]
Listener = Callable[[SpaceMouseState], None]


class SpaceMouse:
    """A SpaceMouse read on a background thread. See the module docstring for an example.

    Args:
        device: which device to use: None (the first one found), a `DeviceInfo` or device path, a substring of the
            product name (case-insensitive), or a predicate on `DeviceInfo`.
        on_change: called with the new state whenever an axis or button changes (on the reader thread).
        rescan_interval: seconds between looks for a device while none is connected.
        backend: the OS layer; the default is the one for this platform.
    """

    def __init__(self, device: DeviceSelector = None, *, on_change: Optional[Listener] = None,
                 rescan_interval: float = 2.0, backend: Optional[Backend] = None, poll_timeout: float = 0.1):
        self._selector = device
        self._rescan_interval = rescan_interval
        self._poll_timeout = poll_timeout
        self._backend = backend
        self._listeners: List[Listener] = [on_change] if on_change else []
        self._lock = threading.Lock()
        self._state = SpaceMouseState()
        self._translation = [0.0, 0.0, 0.0]
        self._rotation = [0.0, 0.0, 0.0]
        self._buttons: List[int] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._open: Optional[OpenDevice] = None
        self._last_problem = ""

    # ------------------------------------------------------------------ public API

    @staticmethod
    def list_devices(backend: Optional[Backend] = None) -> List[DeviceInfo]:
        """Every connected multi-axis controller."""
        return (backend or default_backend()).enumerate()

    def start(self) -> "SpaceMouse":
        """Start reading (idempotent). Raises SpaceMouseUnavailable if this platform isn't supported."""
        if self._thread is not None and self._thread.is_alive():
            return self
        if self._backend is None:
            self._backend = default_backend()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="SpaceMouse reader", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        """Stop reading and close the device; the state returns to idle and disconnected."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._close("stopped")

    def __enter__(self) -> "SpaceMouse":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def state(self) -> SpaceMouseState:
        """The latest state (idle and `connected=False` while no device is open)."""
        with self._lock:
            return self._state

    @property
    def connected(self) -> bool:
        return self.state().connected

    @property
    def device(self) -> Optional[DeviceInfo]:
        return self.state().device

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ------------------------------------------------------------------ reader thread

    def _select(self, candidates: List[DeviceInfo]) -> Optional[DeviceInfo]:
        selector = self._selector
        for info in candidates:
            if selector is None:
                return info
            if isinstance(selector, DeviceInfo) and info.path == selector.path:
                return info
            if isinstance(selector, str) and (info.path == selector or selector.lower() in info.name.lower()):
                return info
            if callable(selector) and not isinstance(selector, (str, DeviceInfo)) and selector(info):
                return info
        return None

    def _problem(self, message: str) -> None:
        """Log a problem once until it changes (the reader retries every few seconds)."""
        if message != self._last_problem:
            self._last_problem = message
            _logger.warning(f"SpaceMouse: {message}")

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._open is None and not self._connect():
                self._stop.wait(self._rescan_interval)
                continue
            try:
                report = self._open.read(self._poll_timeout)
            except DeviceDisconnected as ex:
                self._close(f"disconnected ({ex})")
                continue
            except Exception as ex:  # keep the thread alive whatever a backend does
                self._close(f"read failed ({ex!r})")
                continue
            if report is None:
                continue
            try:
                values = self._open.decode(report)
            except Exception as ex:
                self._problem(f"couldn't decode a report ({ex!r})")
                continue
            if values is not None:
                self._apply(values)

    def _connect(self) -> bool:
        try:
            candidates = self._backend.enumerate()
        except Exception as ex:
            self._problem(f"device enumeration failed ({ex!r})")
            return False
        info = self._select(candidates)
        if info is None:
            return False
        try:
            self._open = self._backend.open(info)
        except SpaceMouseUnavailable as ex:
            self._problem(str(ex))
            return False
        self._last_problem = ""
        try:
            environment = "; ".join(f"{k}: {v}" for k, v in self._backend.environment().items())
        except Exception:
            environment = ""
        status = "tested" if is_tested(info) else "untested model, expected to work (please report the results)"
        _logger.info(f"SpaceMouse: opened {info.describe()} [{status}]" + (f"; {environment}" if environment else ""))
        with self._lock:
            self._translation, self._rotation, self._buttons = [0.0] * 3, [0.0] * 3, []
            self._state = SpaceMouseState(connected=True, device=info, timestamp=time.monotonic())
        self._notify(self._state)
        return True

    def _close(self, reason: str) -> None:
        device, self._open = self._open, None
        if device is None:
            return
        try:
            device.close()
        except Exception:
            pass
        _logger.info(f"SpaceMouse: {device.info.describe()} {reason}")
        with self._lock:
            self._translation, self._rotation, self._buttons = [0.0] * 3, [0.0] * 3, []
            self._state = SpaceMouseState()
        self._notify(self._state)

    def _apply(self, values: ReportValues) -> None:
        with self._lock:
            before = (tuple(self._translation), tuple(self._rotation), tuple(self._buttons))
            for usage, raw in values.axes.items():
                low, high = values.axis_ranges.get(usage, (-1, 1))
                if usage in TRANSLATION_USAGES:
                    self._translation[TRANSLATION_USAGES.index(usage)] = normalize(raw, low, high)
                elif usage in ROTATION_USAGES:
                    self._rotation[ROTATION_USAGES.index(usage)] = normalize(raw, low, high)
            if values.buttons is not None:
                self._buttons = sorted(values.buttons)
            after = (tuple(self._translation), tuple(self._rotation), tuple(self._buttons))
            self._state = SpaceMouseState(translation=after[0], rotation=after[1], buttons=list(after[2]),
                                          timestamp=time.monotonic(), connected=True, device=self._state.device)
            state, changed = self._state, after != before
        if changed:
            self._notify(state)

    def _notify(self, state: SpaceMouseState) -> None:
        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception as ex:
                self._problem(f"an on_change listener raised {ex!r}")

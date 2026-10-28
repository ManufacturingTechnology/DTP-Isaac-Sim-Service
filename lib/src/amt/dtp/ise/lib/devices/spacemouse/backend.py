"""The per-OS layer: find multi-axis controllers, open one, read and decode its input reports."""
import sys
from typing import Dict, List, Optional, Protocol

from .hid import ReportValues
from .models import DeviceInfo


class SpaceMouseUnavailable(RuntimeError):
    """The platform isn't supported, or the device can't be opened (on Linux usually a missing udev rule)."""


class DeviceDisconnected(OSError):
    """The device went away (unplugged, or a read failed)."""


class OpenDevice(Protocol):
    info: DeviceInfo

    def read(self, timeout: float) -> Optional[bytes]:
        """The next input report, or None if none arrived within `timeout` seconds. Raises DeviceDisconnected."""

    def decode(self, report: bytes) -> Optional[ReportValues]:
        """What the report carried, or None if it isn't an input report this device describes."""

    def layout_summary(self) -> List[str]:
        """Human-readable lines describing the axes and buttons, for diagnostics."""

    def close(self) -> None:
        ...


class Backend(Protocol):
    name: str

    def enumerate(self) -> List[DeviceInfo]:
        """Every connected HID multi-axis controller (Generic Desktop / Multi-axis Controller)."""

    def open(self, info: DeviceInfo) -> OpenDevice:
        ...

    def environment(self) -> Dict[str, str]:
        """Facts worth recording in a test report (e.g. whether 3Dconnexion's software is running)."""


def default_backend() -> Backend:
    """The backend for this OS. Raises SpaceMouseUnavailable on other platforms."""
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform.startswith("linux"):
        from .linux import LinuxBackend
        return LinuxBackend()
    raise SpaceMouseUnavailable(f"SpaceMouse support isn't implemented for platform '{sys.platform}' "
                                f"(Windows and Linux are)")

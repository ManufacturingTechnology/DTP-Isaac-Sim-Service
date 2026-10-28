"""Read a 6-DOF SpaceMouse (or any HID multi-axis controller) with no third-party dependency.

    from amt.dtp.ise.lib.devices.spacemouse import SpaceMouse

    with SpaceMouse() as mouse:
        print(mouse.state())

Windows (SetupAPI + hid.dll) and Linux (hidraw; needs a udev rule) are supported; 3Dconnexion's driver is not
required. Diagnostics: `python -m amt.dtp.ise.lib.devices.spacemouse --list` or `--dump SECONDS`. Written clean-room
from the USB HID specification and the OS documentation (see each module's docstring), Apache-2.0.
"""
from .backend import Backend, DeviceDisconnected, SpaceMouseUnavailable, default_backend
from .device import SpaceMouse
from .models import DeviceInfo, SpaceMouseState
from .shaping import shape_axis
from .tested import TESTED_DEVICES, DeviceTestRecord, is_tested

__all__ = [
    "Backend", "DeviceDisconnected", "DeviceInfo", "SpaceMouse", "SpaceMouseState", "SpaceMouseUnavailable",
    "TESTED_DEVICES", "DeviceTestRecord", "default_backend", "is_tested", "shape_axis",
]

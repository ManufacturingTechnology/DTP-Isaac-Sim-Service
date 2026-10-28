"""Which devices have been tested, on which OS, with which driver.

Decoding is generic (from each device's own HID report descriptor), so every HID multi-axis controller is expected to
work; this table records what has actually been checked. Add a row for every test, including failures, with the output
of `python -m amt.dtp.ise.lib.devices.spacemouse --dump 20` (it prints every field below). On Windows, `driver` is the
driver bound to the device's HID interface: 3Dconnexion's `3dxhid` when 3DxWare is installed, or "" for Windows'
generic HID driver; `software` says what 3Dconnexion software was running.
"""
from dataclasses import dataclass
from typing import List

from .models import DeviceInfo


@dataclass(frozen=True)
class DeviceTestRecord:
    model: str
    vendor_id: int
    product_id: int
    version: int
    os: str  # e.g. "Windows 11 (10.0.26300)", "Ubuntu 24.04"
    driver: str  # bound driver; "" = Windows' generic HID driver
    software: str  # 3Dconnexion software installed/running on Windows, spacenavd on Linux
    date: str  # ISO date
    method: str  # what read the device: "probe" (the standalone probe script) or "SpaceMouse class"
    result: str  # what worked, what didn't


TESTED_DEVICES: List[DeviceTestRecord] = [
    DeviceTestRecord("SpaceMouse Compact", 0x256F, 0xC635, 0x0437, "Windows 11 Enterprise (10.0.26300)", "3dxhid 1.6.6",
                     "3DxWare 10.9.10 and 3DxWinCore 17.9.10 installed and running", "2026-10-27", "probe",
                     "all six axes and both buttons decoded through the HID API while 3DxWare kept reading the device"),
    DeviceTestRecord("SpaceMouse Compact", 0x256F, 0xC635, 0x0437, "Windows 11 (10.0.26200)", "",
                     "none installed", "2026-10-27", "probe",
                     "all six axes (each reached the full ±350 logical range), both buttons, both together; "
                     "a six-axis update about every 16 ms"),
]


def records_for(info: DeviceInfo) -> List[DeviceTestRecord]:
    return [r for r in TESTED_DEVICES if (r.vendor_id, r.product_id) == (info.vendor_id, info.product_id)]


def is_tested(info: DeviceInfo) -> bool:
    return bool(records_for(info))

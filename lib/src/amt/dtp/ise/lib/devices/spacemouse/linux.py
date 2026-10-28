"""Linux backend: sysfs to find hidraw devices and their report descriptors, `/dev/hidrawN` to read input reports.

Written from the Linux kernel's hidraw documentation (Documentation/hid/hidraw.rst: one report per read(); the first
byte is the report number when the device uses numbered reports; the raw report descriptor is also available) and
sysfs layout (`/sys/class/hidraw/hidrawN/device/report_descriptor`, `uevent` with `HID_ID` and `HID_NAME`).
https://docs.kernel.org/hid/hidraw.html ; the parsing and decoding are tested offline; ❓ not yet run against a
device on Linux hardware.

Reading `/dev/hidrawN` needs permission: by default only root can. A udev rule grants it, e.g.
`/etc/udev/rules.d/99-spacemouse.rules` with
`KERNEL=="hidraw*", ATTRS{idVendor}=="256f", MODE="0660", TAG+="uaccess"` (then `sudo udevadm control --reload` and
replug the device). The user docs carry the exact steps.
"""
import errno
import os
import select
from typing import Dict, List, Optional

from .backend import DeviceDisconnected, SpaceMouseUnavailable
from .hid import AXIS_NAMES, ReportLayout, ReportValues, decode_report, is_multi_axis_controller, \
    parse_report_descriptor
from .models import DeviceInfo


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _uevent(path: str) -> Dict[str, str]:
    result = {}
    for line in _read_text(path).splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


class LinuxDevice:
    def __init__(self, info: DeviceInfo, layout: ReportLayout):
        self.info = info
        self._layout = layout
        try:
            self._fd: Optional[int] = os.open(info.path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError as ex:
            raise SpaceMouseUnavailable(
                f"No permission to read {info.path} ({info.describe()}). Add a udev rule granting access to the "
                f"device (see the installation docs), then replug it.") from ex
        except OSError as ex:
            raise SpaceMouseUnavailable(f"Couldn't open {info.path}: {ex}") from ex

    def read(self, timeout: float) -> Optional[bytes]:
        if self._fd is None:
            raise DeviceDisconnected("device is closed")
        try:
            ready, _, _ = select.select([self._fd], [], [], max(0.0, timeout))
            if not ready:
                return None
            data = os.read(self._fd, 4096)
        except BlockingIOError:
            return None
        except OSError as ex:
            raise DeviceDisconnected(str(ex)) from ex
        if not data:
            raise DeviceDisconnected("end of file")  # the device node went away
        return data

    def decode(self, report: bytes) -> Optional[ReportValues]:
        return decode_report(self._layout, report)

    def layout_summary(self) -> List[str]:
        lines = ["report IDs: " + ("yes" if self._layout.uses_report_ids else "no")]
        for report_id, fields in sorted(self._layout.fields.items()):
            for f in fields:
                name = AXIS_NAMES.get(f.usage, hex(f.usage)) if f.usage_page == 0x01 else \
                    (f"button {f.usage}" if f.usage_page == 0x09 else f"page {f.usage_page:#04x} usage {f.usage:#04x}")
                lines.append(f"report {report_id}: {name}, bits {f.bit_offset}+{f.bit_size}, logical "
                             f"[{f.logical_min}, {f.logical_max}]")
        return lines

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as ex:
                if ex.errno != errno.EBADF:
                    raise
            self._fd = None


class LinuxBackend:
    name = "linux"

    def __init__(self, sysfs_root: str = "/sys/class/hidraw", dev_root: str = "/dev"):
        self._sysfs_root = sysfs_root
        self._dev_root = dev_root
        self._layouts: Dict[str, ReportLayout] = {}

    def enumerate(self) -> List[DeviceInfo]:
        try:
            names = sorted(os.listdir(self._sysfs_root))
        except OSError:
            return []
        devices = []
        for name in names:
            device_dir = os.path.join(self._sysfs_root, name, "device")
            try:
                with open(os.path.join(device_dir, "report_descriptor"), "rb") as f:
                    layout = parse_report_descriptor(f.read())
            except (OSError, ValueError):
                continue
            if layout.application is None or not is_multi_axis_controller(*layout.application):
                continue
            uevent = _uevent(os.path.join(device_dir, "uevent"))
            vendor_id = product_id = 0
            parts = uevent.get("HID_ID", "").split(":")  # bus:vendor:product, hex
            if len(parts) == 3:
                vendor_id, product_id = int(parts[1], 16), int(parts[2], 16)
            usb_device = os.path.join(device_dir, "..", "..")  # hid device -> USB interface -> USB device
            version_text = _read_text(os.path.join(usb_device, "bcdDevice"))
            driver = ""
            try:
                driver = os.path.basename(os.readlink(os.path.join(device_dir, "driver")))
            except OSError:
                pass
            path = os.path.join(self._dev_root, name)
            self._layouts[path] = layout
            devices.append(DeviceInfo(
                name=_read_text(os.path.join(usb_device, "product")) or uevent.get("HID_NAME", name),
                manufacturer=_read_text(os.path.join(usb_device, "manufacturer")),
                vendor_id=vendor_id, product_id=product_id,
                version=int(version_text, 16) if version_text else 0,
                path=path, platform="linux", driver=driver))
        return devices

    def open(self, info: DeviceInfo) -> LinuxDevice:
        layout = self._layouts.get(info.path)
        if layout is None:
            self.enumerate()
            layout = self._layouts.get(info.path)
        if layout is None:
            raise SpaceMouseUnavailable(f"{info.path} is not a connected multi-axis controller")
        return LinuxDevice(info, layout)

    def environment(self) -> Dict[str, str]:
        running = []
        try:
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    comm = _read_text(f"/proc/{pid}/comm")
                    if comm == "spacenavd":
                        running.append(comm)
        except OSError:
            pass
        return {"spacenavd running": "yes" if running else "no"}

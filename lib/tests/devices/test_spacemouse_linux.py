"""The Linux backend against a fake sysfs tree and a FIFO in place of /dev/hidrawN (Linux only)."""
import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux backend uses select on FIFOs")

from amt.dtp.ise.lib.devices.spacemouse import DeviceDisconnected, SpaceMouse, SpaceMouseUnavailable  # noqa: E402
from amt.dtp.ise.lib.devices.spacemouse.linux import LinuxBackend  # noqa: E402

from . import hid_descriptors as d  # noqa: E402


def fake_hidraw(root, name, descriptor, hid_id, hid_name, product="", bcd="", driver="hid-generic"):
    """sysfs: <root>/sys/<name>/device -> <root>/devices/usb/intf/hid (report_descriptor, uevent, driver), with the
    USB device two levels up holding product and bcdDevice. /dev: <root>/dev/<name> as a FIFO."""
    usb = root / "devices" / f"usb-{name}"
    hid = usb / "intf" / "hid"
    hid.mkdir(parents=True)
    (hid / "report_descriptor").write_bytes(descriptor)
    (hid / "uevent").write_text(f"DRIVER={driver}\nHID_ID={hid_id}\nHID_NAME={hid_name}\n")
    drivers = root / "drivers" / driver
    drivers.mkdir(parents=True, exist_ok=True)
    os.symlink(drivers, hid / "driver")
    if product:
        (usb / "product").write_text(product + "\n")
    if bcd:
        (usb / "bcdDevice").write_text(bcd + "\n")
    (root / "sys" / name).mkdir(parents=True)
    os.symlink(hid, root / "sys" / name / "device")
    (root / "dev").mkdir(exist_ok=True)
    os.mkfifo(root / "dev" / name)


def keyboard_descriptor():
    return b"".join([d.usage_page(0x01), d.usage(0x06), d.collection(d.APPLICATION), d.report_size(8),
                     d.report_count(1), d.usage(0x30), d.input_(d.DATA_VAR_ABS), d.end_collection()])


@pytest.fixture
def tree(tmp_path):
    fake_hidraw(tmp_path, "hidraw0", keyboard_descriptor(), "0003:0000046D:0000C52B", "Logitech USB Receiver")
    fake_hidraw(tmp_path, "hidraw1", d.compact_like(), "0003:0000256F:0000C635", "3Dconnexion SpaceMouse Compact",
                product="SpaceMouse Compact", bcd="0437")
    return tmp_path


def backend_for(tree):
    return LinuxBackend(sysfs_root=str(tree / "sys"), dev_root=str(tree / "dev"))


def test_enumerates_only_multi_axis_controllers(tree):
    devices = backend_for(tree).enumerate()
    assert len(devices) == 1
    info = devices[0]
    assert (info.name, info.vendor_id, info.product_id, info.version) == ("SpaceMouse Compact", 0x256F, 0xC635, 0x0437)
    assert (info.platform, info.driver, info.path) == ("linux", "hid-generic", str(tree / "dev" / "hidraw1"))


def test_reads_and_decodes_reports(tree):
    backend = backend_for(tree)
    info = backend.enumerate()[0]
    writer_ready = threading.Event()

    def writer():
        fd = os.open(info.path, os.O_WRONLY)  # blocks until the reader has opened the FIFO
        writer_ready.set()
        for hexstr in ("01 e4 ff 00 00 00 00", "03 02 00 00 00 00 00"):
            os.write(fd, bytes.fromhex(hexstr))
            time.sleep(0.05)
        os.close(fd)

    threading.Thread(target=writer, daemon=True).start()
    device = backend.open(info)
    try:
        assert writer_ready.wait(2)
        first = device.read(1.0)
        assert device.decode(first).axes == {0x30: -28, 0x31: 0, 0x32: 0}
        assert device.decode(device.read(1.0)).buttons == {2}
        with pytest.raises(DeviceDisconnected):  # the writer closed: end of file, as when a device is unplugged
            while True:
                device.read(1.0)
        assert any("X, bits 0+16, logical [-350, 350]" in line for line in device.layout_summary())
    finally:
        device.close()


def test_spacemouse_class_on_linux_backend(tree):
    backend = backend_for(tree)
    info = backend.enumerate()[0]

    def writer():
        fd = os.open(info.path, os.O_WRONLY)
        os.write(fd, bytes.fromhex("02 00 00 5e 01 00 00"))
        time.sleep(0.5)
        os.close(fd)

    threading.Thread(target=writer, daemon=True).start()
    with SpaceMouse(backend=backend, rescan_interval=0.05, poll_timeout=0.02) as mouse:
        end = time.monotonic() + 3
        while time.monotonic() < end and mouse.state().rotation[1] != 1.0:
            time.sleep(0.01)
        assert mouse.state().rotation == (0.0, 1.0, 0.0) and mouse.device.name == "SpaceMouse Compact"


def test_permission_error_names_the_udev_rule(tree, monkeypatch):
    backend = backend_for(tree)
    info = backend.enumerate()[0]

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(SpaceMouseUnavailable, match="udev rule"):
        backend.open(info)

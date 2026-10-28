"""The SpaceMouse class against a scripted backend: merging, callbacks, hot-plug, selection, stop."""
import queue
import threading
import time

import pytest

from amt.dtp.ise.lib.devices.spacemouse import DeviceDisconnected, DeviceInfo, SpaceMouse, SpaceMouseState, \
    SpaceMouseUnavailable, is_tested
from amt.dtp.ise.lib.devices.spacemouse import __main__ as cli
from amt.dtp.ise.lib.devices.spacemouse.hid import decode_report, parse_report_descriptor

from . import hid_descriptors as d

COMPACT = DeviceInfo(name="SpaceMouse Compact", manufacturer="3Dconnexion", vendor_id=0x256F, product_id=0xC635,
                     version=0x0437, path="fake0", platform="windows", driver="")
OTHER = DeviceInfo(name="Some Multi-axis Thing", vendor_id=0x1234, product_id=0x0001, path="fake1", platform="linux")


class FakeDevice:
    def __init__(self, info, reports: "queue.Queue"):
        self.info = info
        self.reports = reports
        self.layout = parse_report_descriptor(d.compact_like())
        self.closed = False

    def read(self, timeout):
        try:
            item = self.reports.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def decode(self, report):
        return decode_report(self.layout, report)

    def layout_summary(self):
        return ["fake"]

    def close(self):
        self.closed = True


class FakeBackend:
    name = "fake"

    def __init__(self, devices=(COMPACT,)):
        self.devices = list(devices)
        self.reports = queue.Queue()
        self.opened = []
        self.fail_open = None

    def enumerate(self):
        return list(self.devices)

    def open(self, info):
        if self.fail_open:
            raise self.fail_open
        device = FakeDevice(info, self.reports)
        self.opened.append(device)
        return device

    def environment(self):
        return {"fake software": "no"}


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make(backend, **kwargs):
    return SpaceMouse(backend=backend, rescan_interval=0.05, poll_timeout=0.02, **kwargs)


def test_merges_translation_rotation_and_buttons():
    backend = FakeBackend()
    with make(backend) as mouse:
        assert wait_for(lambda: mouse.connected)
        assert mouse.device == COMPACT and mouse.state().is_idle
        backend.reports.put(bytes.fromhex("01 e4 ff 00 00 5e 01"))  # X -28, Z +350
        backend.reports.put(bytes.fromhex("02 00 00 a2 fe 00 00"))  # Ry -350
        backend.reports.put(bytes.fromhex("03 03 00 00 00 00 00"))  # buttons 1 and 2
        assert wait_for(lambda: mouse.state().buttons == [1, 2])
        state = mouse.state()
        assert state.translation == pytest.approx((-28 / 350, 0.0, 1.0))
        assert state.rotation == pytest.approx((0.0, -1.0, 0.0))  # rotation kept while buttons changed
        assert state.button_mask == 3 and not state.is_idle


def test_on_change_only_when_something_changes():
    backend = FakeBackend()
    seen = []
    with make(backend, on_change=seen.append) as mouse:
        assert wait_for(lambda: mouse.connected)
        for _ in range(3):
            backend.reports.put(bytes.fromhex("01 0a 00 00 00 00 00"))
        backend.reports.put(bytes.fromhex("01 00 00 00 00 00 00"))
        assert wait_for(lambda: len(seen) >= 3)
        time.sleep(0.1)
    connected = [s for s in seen if s.connected]
    assert [s.translation[0] for s in connected] == pytest.approx([0.0, 10 / 350, 0.0])  # connect, move, rest
    assert seen[-1].connected is False  # stop reports disconnected


def test_disconnect_zeroes_state_and_reconnects():
    backend = FakeBackend()
    with make(backend) as mouse:
        assert wait_for(lambda: mouse.connected)
        backend.reports.put(bytes.fromhex("01 5e 01 00 00 00 00"))
        assert wait_for(lambda: mouse.state().translation[0] == 1.0)
        backend.devices = []
        backend.reports.put(DeviceDisconnected("unplugged"))
        assert wait_for(lambda: not mouse.connected)
        assert mouse.state().is_idle and mouse.device is None and backend.opened[0].closed
        backend.devices = [COMPACT]
        assert wait_for(lambda: mouse.connected)
        assert len(backend.opened) == 2 and mouse.state().translation == (0.0, 0.0, 0.0)


def test_selection():
    backend = FakeBackend(devices=[OTHER, COMPACT])
    with make(backend, device="compact") as mouse:
        assert wait_for(lambda: mouse.connected) and mouse.device == COMPACT
    with make(backend, device=lambda info: info.vendor_id == 0x1234) as mouse:
        assert wait_for(lambda: mouse.connected) and mouse.device == OTHER
    with make(backend, device="no such device") as mouse:
        time.sleep(0.2)
        assert not mouse.connected and mouse.running


def test_open_failure_retries_and_stop_is_prompt():
    backend = FakeBackend()
    backend.fail_open = SpaceMouseUnavailable("no permission")
    mouse = make(backend).start()
    time.sleep(0.2)
    assert not mouse.connected
    backend.fail_open = None
    assert wait_for(lambda: mouse.connected)
    started = time.monotonic()
    mouse.stop()
    assert time.monotonic() - started < 1.0 and not mouse.running and not mouse.connected


def test_listener_exception_does_not_stop_reading():
    backend = FakeBackend()

    def bad(state):
        raise RuntimeError("listener bug")

    with make(backend, on_change=bad) as mouse:
        assert wait_for(lambda: mouse.connected)
        backend.reports.put(bytes.fromhex("01 0a 00 00 00 00 00"))
        assert wait_for(lambda: mouse.state().translation[0] > 0)


def test_state_is_json_round_trippable():
    state = SpaceMouseState(translation=(0.5, 0, -1), rotation=(0, 0.25, 0), buttons=[2], timestamp=1.5,
                            connected=True, device=COMPACT)
    assert SpaceMouseState.model_validate_json(state.model_dump_json()) == state
    assert state.button_mask == 2


def test_tested_registry_and_describe():
    assert is_tested(COMPACT) and not is_tested(OTHER)
    assert COMPACT.describe() == "SpaceMouse Compact (256F:C635, rev 0437, driver: Windows generic HID)"


def test_cli_list(monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_backend", lambda: FakeBackend(devices=[COMPACT]))
    assert cli.main(["--list"]) == 0
    assert "SpaceMouse Compact (256F:C635" in capsys.readouterr().out
    monkeypatch.setattr(cli, "default_backend", lambda: FakeBackend(devices=[]))
    assert cli.main(["--list"]) == 1


def test_cli_dump(monkeypatch, capsys):
    backend = FakeBackend(devices=[COMPACT])
    monkeypatch.setattr(cli, "default_backend", lambda: backend)

    def feed():
        time.sleep(0.15)
        backend.reports.put(bytes.fromhex("01 5e 01 00 00 00 00"))
        backend.reports.put(bytes.fromhex("03 01 00 00 00 00 00"))

    threading.Thread(target=feed, daemon=True).start()
    assert cli.main(["--dump", "0.6"]) == 0
    out = capsys.readouterr().out
    assert "fake software: no" in out and "X [+0.00, +1.00]" in out and "Buttons seen: [1]" in out

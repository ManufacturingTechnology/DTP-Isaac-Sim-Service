"""SpaceMouse diagnostics: list devices, or dump a device's layout and live state for a test report.

    python -m amt.dtp.ise.lib.devices.spacemouse --list
    python -m amt.dtp.ise.lib.devices.spacemouse --dump 20     # move the cap and press the buttons

The dump prints everything a `TESTED_DEVICES` row needs: the device, its driver, the OS, what 3Dconnexion software
(or spacenavd) is running, the decoded layout, the ranges reached, the buttons seen, and the report rate.
"""
import argparse
import platform
import sys
import threading
import time
from typing import List, Optional

from .backend import SpaceMouseUnavailable, default_backend
from .device import SpaceMouse
from .models import SpaceMouseState
from .tested import records_for


def _list() -> int:
    backend = default_backend()
    devices = backend.enumerate()
    if not devices:
        print("No multi-axis controller found.")
        return 1
    for info in devices:
        tested = records_for(info)
        print(f"{info.describe()}\n  path: {info.path}\n  tested: "
              + ("; ".join(f"{r.os}, driver {r.driver or 'generic'} ({r.date})" for r in tested) if tested else "no"))
    return 0


def _dump(seconds: float) -> int:
    backend = default_backend()
    devices = backend.enumerate()
    if not devices:
        print("No multi-axis controller found.")
        return 1
    info = devices[0]
    print(f"OS: {platform.platform()}; Python {platform.python_version()}")
    for key, value in backend.environment().items():
        print(f"{key}: {value}")
    print(f"Device: {info.describe()}")
    opened = backend.open(info)
    try:
        for line in opened.layout_summary():
            print(f"  {line}")
    finally:
        opened.close()

    states: List[SpaceMouseState] = []
    lock = threading.Lock()

    def record(state: SpaceMouseState) -> None:
        with lock:
            states.append(state)

    print(f"Reading for {seconds:g} s: move the cap in every direction and press each button.")
    mouse = SpaceMouse(info, on_change=record, backend=backend)
    with mouse:
        end = time.monotonic() + seconds
        last_print = 0.0
        while time.monotonic() < end:
            time.sleep(0.05)
            state = mouse.state()
            if time.monotonic() - last_print >= 0.25 and not state.is_idle:
                last_print = time.monotonic()
                t, r = state.translation, state.rotation
                print(f"  T ({t[0]:+.2f} {t[1]:+.2f} {t[2]:+.2f})  R ({r[0]:+.2f} {r[1]:+.2f} {r[2]:+.2f})  "
                      f"buttons {state.buttons}")
    with lock:
        moving = [s for s in states if s.connected]
    if not any(not s.is_idle for s in moving):
        print("No input received (the device sends reports only while the cap moves or a button changes).")
        return 1
    ranges = []
    for name, index, attr in (("X", 0, "translation"), ("Y", 1, "translation"), ("Z", 2, "translation"),
                              ("Rx", 0, "rotation"), ("Ry", 1, "rotation"), ("Rz", 2, "rotation")):
        values = [getattr(s, attr)[index] for s in moving]
        ranges.append(f"{name} [{min(values):+.2f}, {max(values):+.2f}]")
    buttons = sorted({b for s in moving for b in s.buttons})
    stamps = sorted(s.timestamp for s in moving)
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b > a)
    median = f"{gaps[len(gaps) // 2] * 1000:.1f} ms" if gaps else "n/a"
    print(f"Changes: {len(moving)}; median interval between changes: {median}")
    print("Ranges reached (normalized): " + ", ".join(ranges))
    print(f"Buttons seen: {buttons or 'none'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m amt.dtp.ise.lib.devices.spacemouse",
                                     description="SpaceMouse diagnostics (HID multi-axis controllers).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list connected multi-axis controllers")
    group.add_argument("--dump", type=float, metavar="SECONDS", help="print the layout and live state for SECONDS")
    args = parser.parse_args(argv)
    try:
        return _list() if args.list else _dump(args.dump)
    except SpaceMouseUnavailable as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

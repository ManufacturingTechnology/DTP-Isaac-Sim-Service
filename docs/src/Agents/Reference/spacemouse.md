---
title: 'SpaceMouse (as built)'
audience: agent
---

# SpaceMouse (as built)

> **Abstract.** The native SpaceMouse support: a clean-room, dependency-free HID reader in the lib
> (`lib/src/amt/dtp/ise/lib/devices/spacemouse/`) usable from any Python program, and the extension's camera
> navigation backend on top of it (`extension/exts/amt.dtp.ise/amt/dtp/ise/service/spacemouse.py`). Windows reads
> through SetupAPI and hid.dll, Linux through sysfs and hidraw. ADR-0028. User docs: Getting Started › Install ›
> "SpaceMouse" and "Using a SpaceMouse in your own application". Verify against the code; may lag.

## Lib: `amt.dtp.ise.lib.devices.spacemouse`

| Module | What |
|---|---|
| `hid` | Pure HID: report-descriptor parser (`parse_report_descriptor` → `ReportLayout` of `Field`s by report ID), `decode_report` (axes by usage, buttons), `normalize` (logical range → [-1, 1]), `extract` (little-endian bit fields). Used by the Linux backend; Windows parses descriptors itself |
| `models` | `DeviceInfo` (name, manufacturer, VID/PID, version, path, platform, bound `driver`) and `SpaceMouseState` (`translation` X/Y/Z, `rotation` Rx/Ry/Rz in [-1, 1], `buttons`, `timestamp`, `connected`, `device`; pydantic, so it serializes for network use) |
| `backend` | The `Backend` / `OpenDevice` protocols, `SpaceMouseUnavailable`, `DeviceDisconnected`, `default_backend()` (by `sys.platform`) |
| `windows` | `WindowsBackend`: enumerate HID interfaces (SetupAPI), keep those whose top-level collection is Generic Desktop / Multi-axis Controller (`HidP_GetCaps`), record the bound driver (`SPDRP_SERVICE`: `3dxhid` with 3DxWare, empty for Windows' generic driver); open shared and overlapped; decode with `HidP_GetUsageValue` (sign-extended) and `HidP_GetUsages`; `environment()` lists running 3Dconnexion processes (Toolhelp32) |
| `linux` | `LinuxBackend(sysfs_root, dev_root)`: `/sys/class/hidraw/*/device/report_descriptor` parsed with `hid`, `uevent` `HID_ID`/`HID_NAME`, USB `product`/`bcdDevice` two levels up, the `driver` link; reads `/dev/hidrawN` with `select`; a permission error names the udev rule; `environment()` reports spacenavd |
| `device` | `SpaceMouse`: the class users add to their apps (below) |
| `shaping` | `shape_axis(value, deadzone, expo)`, per axis in isolation (moved here from the extension) |
| `tested` | `TESTED_DEVICES` (`DeviceTestRecord`: model, IDs, version, OS, bound driver, 3Dconnexion software, date, method, result), `is_tested`, `records_for` |
| `__main__` | `python -m amt.dtp.ise.lib.devices.spacemouse --list` / `--dump SECONDS`: everything a test record needs |

**`SpaceMouse`** (`device.py`): `start()`/`stop()` or a `with` block; `state()` copies the latest snapshot (never
blocks); `on_change` / `add_listener` callbacks run on the reader thread (exceptions are logged, not fatal); `device=`
selects by `DeviceInfo`, path, name substring, or predicate. The reader thread enumerates every `rescan_interval`
until a device opens, then blocks on reads with `poll_timeout` (so `stop()` returns within about that long). Every
report updates one persistent six-axis state: the SpaceMouse Compact sends translation (report 1), rotation (report 2)
and buttons (report 3) separately and **sends nothing at rest** (a final all-zero pair), so "no report" means
"unchanged". On a read error the device is closed, the state returns to idle and disconnected (a lost device can't
leave the camera drifting), and discovery resumes. Logging goes through the lib's `_logger` (open, close, problems
once each); nothing is logged per report.

**Axes are in the device's HID frame**, normalized per axis from the declared logical range (Compact: ±350; the full
range is reached at full deflection, V on hardware). Applications map axes to their own meaning.

## Extension: `service/spacemouse.py`

- `SPACEMOUSE_BACKEND = "native"` (default) or `"srl"`. Each `_AxisSource.poll()` returns a `_NavInput` (move right/
  up/backward, look up/left, roll clockwise, button mask); **each backend owns its axis mapping and signs**, and
  `tick()` only shapes and applies them.
- `_NativeAxisSource` starts a lib `SpaceMouse` (background thread; `tick()` reads the snapshot on Isaac's main loop)
  and maps the HID frame: move right = −X, up = Z, backward = −Y, look up = −Rx, look left = Rz, roll clockwise = Ry.
  The first mapping followed the cap's direction under an assumed frame (+X right, +Y toward the user, +Z down,
  rotations right-handed) and every axis was backwards on hardware (user test, SpaceMouse Compact, Windows,
  2026-10-28), so all six signs were inverted. V (all six reported backwards; the inverted default not yet re-tested).
- `SPACEMOUSE_FLIP_AXES = False`: when True, `tick()` negates all six shaped axes (buttons untouched) for whichever
  backend is selected, restoring the uninverted native behavior. A plain constant on purpose, not yet tied to
  configuration; intended to become a user setting later. `SPACEMOUSE_COMPARE` logs each backend's mapping before the
  flip.
- `_SrlAxisSource` keeps the srl mapping exactly as verified before, including the deliberately un-negated pitch
  (marked TEMPORARY in the code at the user's request). srl is enabled and imported only when this backend is
  selected, because enabling it makes Kit pip-install hidapi.
- `SPACEMOUSE_COMPARE = True` runs both backends and logs their `_NavInput`s side by side about twice a second while
  the cap moves: the clean-room way to check the native signs against srl's.
- **No window, no device:** `_service_start()` asks `omni.appwindow` for Isaac's OS window first; headless or
  streaming-only (none), it logs and starts nothing, since there's no viewport for the user to fly.
- `SPACEMOUSE_FOCUS_ONLY = True` (a constant, not yet configuration): `tick()` ignores the device (motion and buttons)
  unless Isaac Sim is the application the user is working in, at the **OS level, whole application** (not Kit's focused
  panel). `_ApplicationFocus`: on Windows the foreground window's owning process is ours (`GetForegroundWindow` +
  `GetWindowThreadProcessId`; a NULL foreground window, which happens during activation changes, reads as unfocused,
  and the call then returns 0 without writing the PID: V by probe); elsewhere `carb.windowing`
  `is_window_focused` on the main window (main window only, unverified on Linux ❓). A failing check logs once and
  reports focused. The lib reader keeps running while unfocused. Why it's needed: our raw HID reads bypass 3DxWare's
  routing, so without it Isaac moved while other apps had focus (user report, 2026-10-28).
- `_service_stop()` closes the sources (stops the reader thread). Buttons: 1 frames the selection, 2 resets the view.

## Verification so far (2026-10-27)

- **Windows, SpaceMouse Compact, 3DxWare running:** the class enumerates the device (driver `3dxhid`), opens it,
  reports the environment, and closes within one poll; Windows' decoding of reports captured earlier matches the
  probe's values exactly. V (live run; no cap movement during the run).
- **Driver not required:** the same device on a laptop without 3Dconnexion software, read by the probe (the same HID
  calls), binds to Windows' generic HID driver and works fully. V (probe, not yet this class).
- **Linux:** parser, decoding, the backend (fake sysfs and a FIFO) and the class are unit-tested; no hardware run ❓.
- **The extension backend:** run in Isaac Sim by the user (2026-10-28); it drives the camera, with every axis
  backwards under the first mapping (now inverted, see above). Buttons not yet reported ❓.
- Tests: `lib/tests/devices/` (descriptors built item by item from HID 1.11, the captured Compact reports, a scripted
  backend for the class, the Linux backend, the CLI).

## Removing srl (after the user verifies the native backend)

Delete `_SrlAxisSource`, `_import_srl`, `SPACEMOUSE_COMPARE` and the `_BACKENDS["srl"]` entry; `install-spacemouse.*`;
the install page's "Previous method" note; the srl, hidapi and spacenavd rows in `extension/THIRD-PARTY-NOTICES.md`;
the `srl.spacemouse` entry in `extension/.gitignore`; and the user's todo about the fork.

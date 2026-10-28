import ctypes
import math
import os
import sys
from typing import Optional, Tuple, List, Callable, NamedTuple

import pxr
import pxr.Gf
import pxr.Sdf
import pxr.Usd
import pxr.UsdGeom
import isaacsim.core.experimental.utils.stage as stage_utils
# NOTE: set_camera_view has no isaacsim.core.experimental.* equivalent in Isaac Sim 6.0,
# so the (deprecated-but-present) viewports utility is retained intentionally.
# It is only used for the "reset view" button now; the per-frame navigation path
# authors the camera transform directly (see _author_camera_transform).
import isaacsim.core.utils.viewports
import omni.usd
import omni.kit.viewport
import omni.kit.viewport.window
import omni.kit.viewport.utility
import omni.kit.app
import carb.events
import time  # For wall-clock fallback if needed

from ..lib.devices.spacemouse import SpaceMouse, SpaceMouseState, SpaceMouseUnavailable, shape_axis
from ..lib.logging import _logger
from .core import Service
from .subscription import SubscriptionService, SubscriptionSet

# The srl.spacemouse extension (the "srl" backend) is enabled and imported only when that backend is used: enabling
# it makes Kit pip-install hidapi at run time.
_srl_module = None


def _import_srl():
    """The srl.spacemouse extension module, or None if it isn't installed."""
    global _srl_module
    if _srl_module is None:
        try:
            ext_manager = omni.kit.app.get_app().get_extension_manager()
            ext_manager.set_extension_enabled_immediate("srl.spacemouse", True)
            from srl.spacemouse import spacemouse_extension
            _srl_module = spacemouse_extension
        except Exception:
            _srl_module = False
    return _srl_module or None


# ---------------------------------------------------------------------------
# Camera navigation configuration
# ---------------------------------------------------------------------------

# Which backend supplies the device input.
#   "native" -> this extension's own reader (amt.dtp.ise.lib.devices.spacemouse): reads the device's HID reports
#               directly on Windows and Linux, no third-party code, 3Dconnexion's driver not required (ADR-0028).
#   "srl"    -> the external srl.spacemouse extension (install-spacemouse.*). Kept until the native backend is
#               verified, then removed.
SPACEMOUSE_BACKEND = "native"
#SPACEMOUSE_BACKEND = "srl"

# Debug aid for checking the native backend's axis mapping: when True and srl.spacemouse is installed, both backends
# run and the camera inputs each one produces are logged side by side about twice a second while the cap moves (the
# selected backend still drives the camera). Comparing behavior is fine for the clean-room implementation; reading
# srl's code is not.
SPACEMOUSE_COMPARE = False

# Invert all six navigation axes (buttons unaffected), for any backend. Not tied to configuration yet; meant to become
# a user setting later. False: each backend's own mapping (for native, as checked on hardware 2026-10-28).
SPACEMOUSE_FLIP_AXES = False

# Only navigate while Isaac Sim is the application the user is working in (OS-level focus, see _ApplicationFocus), so
# a SpaceMouse used in another application side by side doesn't also move Isaac's camera. Not tied to configuration
# yet; meant to become a user setting later.
SPACEMOUSE_FOCUS_ONLY = True

# How the camera's orientation is allowed to evolve.
#   "level" -> yaw about world up, pitch about the camera's own right axis, and
#              roll actively removed every frame so the horizon stays flat.
#   "free"  -> full 6-DOF; roll from the device is preserved and accumulates.
ORIENTATION_MODE = "level"
#ORIENTATION_MODE = "free"

GLOBAL_SPEED = 1.0
TRANSLATION_SPEED = 3.0 * GLOBAL_SPEED     # world units per second at full deflection
ROTATION_SPEED = 45.0 * GLOBAL_SPEED # 90.0 * GLOBAL_SPEED       # degrees per second at full deflection
DEADZONE = 0.04             # per-axis; applied to each axis in isolation
EXPO = 0.35                 # 0.0 = linear response, 1.0 = fully cubic
MAX_DT = 1.0 / 15.0         # clamp so a frame hitch cannot launch the camera
PITCH_LIMIT_DEG = 89.0      # only meaningful when ORIENTATION_MODE == "level"

# Local axes of a USD camera. Note these are NOT the Z-up world axes: a
# UsdGeom.Camera looks down its own -Z with +Y up and +X right.
CAM_RIGHT = pxr.Gf.Vec3d(1, 0, 0)
CAM_UP = pxr.Gf.Vec3d(0, 1, 0)
CAM_BACKWARD = pxr.Gf.Vec3d(0, 0, 1)
CAM_FORWARD = pxr.Gf.Vec3d(0, 0, -1)

# Xform op types we can fill in place from a rotation+translation pose. Anything
# outside this set (pivots, euler rotations, ...) falls back to a matrix op.
_WRITABLE_OP_TYPES = {
    pxr.UsdGeom.XformOp.TypeTranslate,
    pxr.UsdGeom.XformOp.TypeOrient,
    pxr.UsdGeom.XformOp.TypeScale,
}

# Values must be authored at the precision the op was declared with, otherwise
# the Set is rejected.
_VEC3_BY_PRECISION = {
    pxr.UsdGeom.XformOp.PrecisionFloat: pxr.Gf.Vec3f,
    pxr.UsdGeom.XformOp.PrecisionDouble: pxr.Gf.Vec3d,
    pxr.UsdGeom.XformOp.PrecisionHalf: pxr.Gf.Vec3h,
}
_QUAT_BY_PRECISION = {
    pxr.UsdGeom.XformOp.PrecisionFloat: pxr.Gf.Quatf,
    pxr.UsdGeom.XformOp.PrecisionDouble: pxr.Gf.Quatd,
    pxr.UsdGeom.XformOp.PrecisionHalf: pxr.Gf.Quath,
}


def _shape_axis(value: float) -> float:
    """Deadzone and expo for one axis, in isolation (each axis is shaped without reference to the others, so moving
    one axis never rescales the rest)."""
    return shape_axis(value, DEADZONE, EXPO)


def _app_window():
    """Isaac Sim's top-level OS window (a carb.windowing Window), or None when running without one (headless,
    streaming-only)."""
    try:
        import omni.appwindow
        app_window = omni.appwindow.get_default_app_window()
        return app_window.get_window() if app_window is not None else None
    except Exception:
        return None


class _ApplicationFocus:
    """Whether Isaac Sim is the application the user is working in, at the OS level (not which Kit panel has focus).

    Windows: the foreground window, the one the user is currently working with, belongs to this process
    (GetForegroundWindow + GetWindowThreadProcessId, compared to our own process ID; any Isaac Sim window counts). Per
    Microsoft's docs the foreground window can be NULL while activation is changing, and then
    GetWindowThreadProcessId returns 0 without writing the process ID (also seen in a probe), which reads as "not
    focused".
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid

    Elsewhere: carb.windowing's is_window_focused on Isaac Sim's main window, i.e. that one OS window has input focus
    (unverified on Linux). A separate OS window Kit might open is not counted there.

    If the check itself fails, it logs once and reports focused, so a broken check never disables the device.
    """

    def __init__(self, window):
        self._window = window
        self._warned = False
        try:
            self._is_foreground = self._create_check()
        except Exception as ex:
            self._is_foreground = self._failing_check(ex)

    @staticmethod
    def _failing_check(ex: Exception) -> Callable[[], bool]:
        def check() -> bool:
            raise ex
        return check

    def _create_check(self) -> Callable[[], bool]:
        if sys.platform == "win32":
            from ctypes import wintypes as wt
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wt.HWND
            user32.GetForegroundWindow.argtypes = []
            user32.GetWindowThreadProcessId.restype = wt.DWORD
            user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
            own_pid = os.getpid()

            def is_foreground() -> bool:
                hwnd = user32.GetForegroundWindow()
                if not hwnd:
                    return False
                pid = wt.DWORD(0)
                if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
                    return False
                return pid.value == own_pid

            return is_foreground
        import carb.windowing
        windowing = carb.windowing.acquire_windowing_interface()
        return lambda: bool(windowing.is_window_focused(self._window))

    def focused(self) -> bool:
        try:
            return self._is_foreground()
        except Exception as ex:
            if not self._warned:
                self._warned = True
                carb.log_warn(f"SpaceMouse: can't tell whether Isaac Sim has focus ({ex}); navigating regardless.")
            return True


class _NavInput(NamedTuple):
    """Camera navigation intents in [-1, 1], unshaped, plus the button mask (1 = left/button 1, 2 = right/button 2,
    3 = both). Each backend maps its own device axes to these; everything downstream is backend-agnostic."""
    move_right: float
    move_up: float
    move_backward: float
    look_up: float
    look_left: float
    roll_cw: float
    buttons: int


class _AxisSource:
    """Supplies camera navigation input from a 6-DOF device.

    `poll()` returns a `_NavInput` (per-axis values in [-1, 1], independent of one another) or None. Each backend
    decides which of its device axes means which camera motion, and with which sign; everything downstream is
    backend-agnostic, so a backend can be swapped by flipping SPACEMOUSE_BACKEND.
    """

    name = "none"

    def available(self) -> bool:
        raise NotImplementedError

    def poll(self) -> Optional[_NavInput]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _NativeAxisSource(_AxisSource):
    """The extension's own reader: a lib `SpaceMouse` on a background thread; `poll()` only copies its snapshot, so
    nothing blocks Isaac Sim's main loop.

    Axis mapping, from the HID axes (X, Y, Z, Rx, Ry, Rz; usages 0x30-0x35) under the device frame assumed here:
    +X = cap pushed right, +Y = cap pulled toward the user, +Z = cap pushed down; rotations right-handed about those
    axes (the HID specification doesn't state the frame). Mapping each axis to the camera motion in the cap's
    direction made every axis backwards on hardware (SpaceMouse Compact, Windows, 2026-10-28), so all six signs below
    are inverted from that; SPACEMOUSE_FLIP_AXES restores the uninverted behavior.
    """

    name = "native"

    def __init__(self):
        self._mouse: Optional[SpaceMouse] = None
        try:
            self._mouse = SpaceMouse().start()
        except SpaceMouseUnavailable as ex:
            carb.log_warn(f"SpaceMouse: {ex}")

    def available(self) -> bool:
        return self._mouse is not None

    def state(self) -> Optional[SpaceMouseState]:
        return self._mouse.state() if self._mouse is not None else None

    def poll(self) -> Optional[_NavInput]:
        state = self.state()
        if state is None or not state.connected:
            return None
        x, y, z = state.translation
        rx, ry, rz = state.rotation
        # Each comment names the cap motion the uninverted sign followed.
        return _NavInput(
            move_right=-x,        # cap pushed right
            move_up=z,            # cap lifted (HID +Z is down)
            move_backward=-y,     # cap pulled toward the user
            look_up=-rx,          # cap tilted back (front edge up): +Rx turns +Y toward +Z
            look_left=rz,         # cap twisted counterclockwise seen from above: -Rz (+Z points down)
            roll_cw=ry,           # cap rolled clockwise as the user sees it: -Ry (+Y points at the user)
            buttons=state.button_mask,
        )

    def close(self) -> None:
        if self._mouse is not None:
            self._mouse.stop()
            self._mouse = None


class _SrlAxisSource(_AxisSource):
    """Axis source backed by the vendored srl.spacemouse extension.

    Everything srl-specific is quarantined here: device discovery (which works
    by driving that extension's own UI widgets) and reading its device thread.
    """

    name = "srl"

    def __init__(self):
        self._device_id: Optional[int] = None
        self._device_id_test: Optional[int] = None
        self._next_device_id_check_time: Optional[float] = None

    def available(self) -> bool:
        return _import_srl() is not None

    def poll(self) -> Optional[_NavInput]:
        srl = _import_srl()
        if srl is None:
            return None

        spacemouse_extension = srl.get_global_spacemouse_extension()
        if spacemouse_extension is None:
            return None

        # By default, the spacemouse is disabled in the extension. Enable it.
        engage_model = spacemouse_extension._models["Engage"]
        combo_box = engage_model[1]
        combo_box_model = combo_box.model
        combo_box_item_count = len(combo_box_model.get_item_children())
        combo_box_item_value_model = combo_box_model.get_item_value_model()

        if self._device_id is None:
            self._advance_discovery(engage_model, combo_box_item_count, combo_box_item_value_model)
            return None

        spacemouse = srl.get_global_spacemouse()
        if spacemouse is None:
            # Device was connected but isn't available anymore. Need to check for devices again.
            self._device_id = None  # Force to check again
            self._next_device_id_check_time = time.time() - 1.0  # Check current device first.
            return None

        # Read the device thread's raw state directly instead of calling
        # get_controller_state(). That accessor pipes the reading through
        # SpaceMouseFilter, which applies a softmax that suppresses the weaker
        # axes and then renormalizes the whole 3-vector against a shared budget
        # of 1.0 -- so two rotation axes at full deflection each come out at
        # 0.707, and a small axis alongside a large one is crushed further. The
        # values held in _control are the untouched per-axis HID readings.
        control = getattr(spacemouse, "_control", None)
        if control is None:
            # The device thread hasn't produced its first reading yet.
            return None

        # xyz is (x, y, z); rpy is (roll, pitch, yaw); both already in [-1, 1]. The signs below were verified
        # against hardware for this backend; roll was verified inverted and is flipped.
        raw_pos, raw_rot = list(control.xyz), list(control.rpy)
        # ---------------------------------------------------------------
        # TEMPORARY -- MUST BE CHANGED BACK
        # Pitch tested as inverted on hardware and the correct sign is
        #     look_up = -raw_rot[1]
        # It is deliberately left un-negated for now at the user's request.
        # Restore the negation above when the temporary need has passed.
        # ---------------------------------------------------------------
        return _NavInput(
            move_right=-raw_pos[0],       # device x
            move_up=-raw_pos[2],          # device z
            move_backward=raw_pos[1],     # device y
            look_up=raw_rot[1],           # device pitch  <- TEMPORARY, see above
            look_left=raw_rot[2],         # device yaw
            roll_cw=raw_rot[0],           # device roll
            buttons=int(control.buttons),
        )

    def _advance_discovery(self, engage_model, combo_box_item_count, combo_box_item_value_model) -> None:
        """Step the 'which combobox entry is the real device' state machine."""
        if self._next_device_id_check_time is not None:
            if time.time() < self._next_device_id_check_time:
                return  # Wait until it is time
            self._next_device_id_check_time = None

            # Check whether this is the device
            if _import_srl().get_global_spacemouse() is not None:
                # Found the device
                self._device_id = self._device_id_test  # This is the device
                return

        # Figure out which item in the combobox corresponds to the spacemouse device
        if engage_model[0].get_value_as_bool():
            engage_model[0].set_value(False)
            return

        if self._device_id_test is None:
            self._device_id_test = -1
        self._device_id_test += 1
        if self._device_id_test >= combo_box_item_count:
            self._device_id_test = None
            return

        # Set the selected device combobox item
        combo_box_item_value_model.set_value(self._device_id_test)
        # Set "Engage" checkbox to true
        engage_model[0].set_value(True)
        # Need to wait now for isaac to handle the device change
        self._next_device_id_check_time = time.time() + 1.0  # Wait a second


_BACKENDS = {"native": _NativeAxisSource, "srl": _SrlAxisSource}


def _create_axis_source(name: str) -> Optional[_AxisSource]:
    backend = _BACKENDS.get(name)
    if backend is None:
        carb.log_warn(f"Unknown SPACEMOUSE_BACKEND '{name}'; spacemouse navigation disabled.")
        return None
    source = backend()
    if source.available():
        return source
    source.close()
    return None


class SpacemouseService(Service):
    _singleton: Optional[Service] = None
    _singleton_intialized = False

    def __new__(cls, *args, **kwargs):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    @staticmethod
    def extension_is_imported() -> bool:
        """Whether a SpaceMouse backend is running."""
        return SpacemouseService._singleton is not None and SpacemouseService._singleton._axis_source is not None

    def __init__(self):
        if self._singleton_intialized:
            return
        self._singleton_intialized = True

        self._sub: Optional[SubscriptionSet] = None
        self._axis_source: Optional[_AxisSource] = None
        self._compare_source: Optional[_AxisSource] = None
        self._last_compare_log = 0.0
        self._last_tick_time: Optional[float] = None
        self._focus: Optional[_ApplicationFocus] = None

    def _service_start(self):
        # Without an application window there's no viewport for the user to fly, so don't read the device at all.
        window = _app_window()
        if window is None:
            _logger.info("SpaceMouse: no application window (headless or streaming-only); navigation not started.")
            return
        self._focus = _ApplicationFocus(window) if SPACEMOUSE_FOCUS_ONLY else None
        self._axis_source = _create_axis_source(SPACEMOUSE_BACKEND)
        if SPACEMOUSE_COMPARE and self._axis_source is not None:
            other = "srl" if SPACEMOUSE_BACKEND == "native" else "native"
            self._compare_source = _create_axis_source(other)
        if self._axis_source is not None:
            self._sub = SubscriptionService().create_subscription_set(SpacemouseService.__name__)
            self._sub.on_timeline_current_time_ticked_permanent = self.tick
            self._sub.enable()
            self._last_tick_time = None

    def _service_stop(self):
        if self._sub is not None:
            self._sub.destroy()

        for source in (self._axis_source, self._compare_source):
            if source is not None:
                source.close()
        self._compare_source = None
        self._axis_source = None
        self._focus = None
        self._last_tick_time = None
        self._singleton = None
        self._singleton_intialized = False

    # def get_current_camera_eye_and_rotation(self):
    #     """
    #     Returns:
    #         eye: Gf.Vec3d          # current world position
    #         rot_quat_xyzw: list    # current rotation as [x, y, z, w]
    #     """
    #     stage = isaacsim.core.utils.stage.get_current_stage()
    #     cam_prim = None
    #     for path in ["/OmniverseKit_Persp", "/World/OmniverseKit_Persp", "/Cameras/OmniverseKit_Persp"]:
    #         cam_prim = stage.GetPrimAtPath(path)
    #         if cam_prim and cam_prim.IsA(pxr.UsdGeom.Camera):
    #             break
    #     if not cam_prim:
    #         raise RuntimeError("Perspective camera prim not found!")

    #     # This is the exact method you wanted — always reflects the real current state
    #     world_matrix: pxr.Gf.Matrix4d = omni.usd.get_world_transform_matrix(cam_prim)
    #     eye = world_matrix.ExtractTranslation()                    # Gf.Vec3d
    #     rotation = world_matrix.ExtractRotation()                  # Gf.Rotation → quaternion WXYZ

    #     quat_wxyz = rotation.GetQuat()                             # Gf.Quatd (WXYZ)
    #     rot_xyzw = [
    #         quat_wxyz.GetImaginary()[0],
    #         quat_wxyz.GetImaginary()[1],
    #         quat_wxyz.GetImaginary()[2],
    #         quat_wxyz.GetReal(),
    #     ]

    #     q_curr_xyzw = pxr.Gf.Quatd(rot_xyzw[3], pxr.Gf.Vec3d(*rot_xyzw[:3]))
    #     return cam_prim, eye, q_curr_xyzw

    def get_current_camera_eye_and_target(self) -> Tuple[pxr.UsdGeom.Camera, pxr.Gf.Vec3d, pxr.Gf.Quatd, pxr.Gf.Vec3d]:
        """
        Get the current perspective camera prim, position, orientation, and target direction.
        """
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        cam_prim: pxr.UsdGeom.Camera = None
        for path in ["/OmniverseKit_Persp", "/World/OmniverseKit_Persp", "/Cameras/OmniverseKit_Persp"]:
            try:
                # This can fail when opening a new file
                cam_prim: pxr.UsdGeom.Camera = stage.GetPrimAtPath(path)
            except Exception as ex:
                pass
            if cam_prim and cam_prim.IsA(pxr.UsdGeom.Camera):
                break
        if not cam_prim:
            return None, None, None, None

        # This is the exact method you wanted — always reflects the real current state
        world_matrix: pxr.Gf.Matrix4d = omni.usd.get_world_transform_matrix(cam_prim)
        eye: pxr.Gf.Vec3d = world_matrix.ExtractTranslation()                    # Gf.Vec3d
        rotation: pxr.Gf.Rotation = world_matrix.ExtractRotation()                  # Gf.Rotation → quaternion WXYZ
        quat_wxyz: pxr.Gf.Quatd = rotation.GetQuat()                             # Gf.Quatd (WXYZ)

        # Local forward for Omniverse viewport camera = -Z
        local_forward = pxr.Gf.Vec3d(0, 0, -1)

        # Rotate forward vector into world space
        world_forward: pxr.Gf.Vec3d = quat_wxyz.Transform(local_forward).GetNormalized()

        # Compute point in front
        target: pxr.Gf.Vec3d = eye + world_forward * 100.0

        return cam_prim, eye, quat_wxyz, target

    @staticmethod
    def _stage_up_axis(stage) -> pxr.Gf.Vec3d:
        up_axis = pxr.UsdGeom.GetStageUpAxis(stage) if stage else pxr.UsdGeom.Tokens.z
        if up_axis == pxr.UsdGeom.Tokens.x:
            return pxr.Gf.Vec3d(1, 0, 0)
        if up_axis == pxr.UsdGeom.Tokens.y:
            return pxr.Gf.Vec3d(0, 1, 0)
        return pxr.Gf.Vec3d(0, 0, 1)

    def _orient_free(self, q_old: pxr.Gf.Quatd, look_up_deg: float, look_left_deg: float,
                     roll_deg: float) -> pxr.Gf.Quatd:
        """Full 6-DOF orientation update. Roll is preserved and accumulates."""
        q_delta = pxr.Gf.Rotation(CAM_RIGHT, look_up_deg).GetQuat() \
            * pxr.Gf.Rotation(CAM_UP, look_left_deg).GetQuat() \
            * pxr.Gf.Rotation(CAM_BACKWARD, roll_deg).GetQuat()
        # These axes are expressed in the camera's own frame, so post-multiply.
        return (q_old * q_delta).GetNormalized()

    def _orient_level(self, q_old: pxr.Gf.Quatd, look_up_deg: float, look_left_deg: float,
                      stage) -> pxr.Gf.Quatd:
        """Level-horizon orientation update: yaw about world up, pitch about the
        camera's own right axis, roll removed."""
        world_up = self._stage_up_axis(stage)
        forward = q_old.Transform(CAM_FORWARD).GetNormalized()

        # Clamp the pitch so the view can never tip past vertical, which would
        # make the world-up yaw axis degenerate.
        elevation = math.degrees(math.asin(max(-1.0, min(1.0, pxr.Gf.Dot(forward, world_up)))))
        look_up_deg = max(-PITCH_LIMIT_DEG - elevation, min(PITCH_LIMIT_DEG - elevation, look_up_deg))

        # Both axes are world-space vectors, so these pre-multiply. Yawing about
        # world up (rather than the camera's tilted local up) is what keeps the
        # left/right rate constant instead of scaling by 1/cos(pitch).
        right = q_old.Transform(CAM_RIGHT).GetNormalized()
        q_new = pxr.Gf.Rotation(world_up, look_left_deg).GetQuat() \
            * pxr.Gf.Rotation(right, look_up_deg).GetQuat() \
            * q_old

        # Rebuild the basis from the resulting forward against world up so that
        # no roll can accumulate from the composition.
        forward_new = q_new.GetNormalized().Transform(CAM_FORWARD).GetNormalized()
        right_new = pxr.Gf.Cross(forward_new, world_up).GetNormalized()
        up_new = pxr.Gf.Cross(right_new, forward_new).GetNormalized()
        basis = pxr.Gf.Matrix4d(
            right_new[0], right_new[1], right_new[2], 0.0,
            up_new[0], up_new[1], up_new[2], 0.0,
            -forward_new[0], -forward_new[1], -forward_new[2], 0.0,
            0.0, 0.0, 0.0, 1.0,
        )
        return basis.ExtractRotation().GetQuat()

    @staticmethod
    def _edit_target_layer(stage, ops):
        """Pick the layer the camera's transform must be authored into.

        Kit keeps the viewport cameras' xform ops in the SESSION layer, which is
        stronger than the stage's normal edit target. Authoring into the default
        target therefore does nothing useful: the opinion lands but is shadowed,
        so the camera silently refuses to move (and ClearXformOpOrder fails
        outright, yielding an invalid op). This mirrors the rule in Kit's own
        TransformPrimCommand._set_value_with_precision.
        """
        if ops:
            attr_path = ops[0].GetAttr().GetPath()
            try:
                session_layer, property_spec = omni.usd.find_spec_on_session_or_its_sublayers(stage, attr_path)
                if property_spec:
                    return session_layer
            except Exception:
                session_layer = stage.GetSessionLayer()
                if session_layer is not None and session_layer.GetPropertyAtPath(attr_path) is not None:
                    return session_layer
        return stage.GetEditTarget().GetLayer()

    @staticmethod
    def _write_into_existing_ops(ops, local_matrix: pxr.Gf.Matrix4d) -> bool:
        """Write the pose into the camera's existing xform ops.

        Deliberately never restructures the op stack. Returns False if the stack
        uses ops we cannot fill exactly, so the caller can fall back.
        """
        if not ops:
            return False

        if len(ops) == 1 and ops[0].GetOpType() == pxr.UsdGeom.XformOp.TypeTransform:
            ops[0].Set(local_matrix)
            return True

        op_types = {op.GetOpType() for op in ops}
        if not op_types.issubset(_WRITABLE_OP_TYPES):
            return False

        translation = local_matrix.ExtractTranslation()
        quat = local_matrix.ExtractRotation().GetQuat()
        for op in ops:
            op_type = op.GetOpType()
            if op_type == pxr.UsdGeom.XformOp.TypeTranslate:
                op.Set(_VEC3_BY_PRECISION[op.GetPrecision()](translation))
            elif op_type == pxr.UsdGeom.XformOp.TypeOrient:
                op.Set(_QUAT_BY_PRECISION[op.GetPrecision()](quat))
            # Scale is left untouched: the pose we author is pure rotation plus
            # translation, so whatever scale the camera carries stays valid.
        return True

    @staticmethod
    def _ensure_center_of_interest(cam_prim) -> None:
        """Make sure the orbit pivot attribute exists.

        It is stored in camera-local space, so it rides along with the transform
        automatically and does not need rewriting every frame.
        """
        coi_attr = cam_prim.GetAttribute("omni:kit:centerOfInterest")
        if coi_attr and coi_attr.IsValid() and coi_attr.Get() is not None:
            return
        cam_prim.CreateAttribute(
            "omni:kit:centerOfInterest", pxr.Sdf.ValueTypeNames.Vector3d, True, pxr.Sdf.VariabilityUniform
        ).Set(pxr.Gf.Vec3d(0, 0, -10))

    def _author_camera_transform(self, cam_prim, position_world: pxr.Gf.Vec3d,
                                 q_world: pxr.Gf.Quatd) -> None:
        """Write the camera pose straight to USD.

        Replaces isaacsim.core.utils.viewports.set_camera_view, which rebuilds
        orientation from an eye/target look-at (discarding roll and any pose the
        2-DOF reconstruction cannot express) and issues four undoable Kit
        commands per call -- 240 undo entries per second at 60 fps.
        """
        xformable = pxr.UsdGeom.Xformable(cam_prim)
        if not xformable:
            return
        ops = xformable.GetOrderedXformOps()

        world_matrix = pxr.Gf.Matrix4d(1.0)
        world_matrix.SetRotate(pxr.Gf.Rotation(q_world))
        world_matrix.SetTranslateOnly(position_world)

        parent_to_world = xformable.ComputeParentToWorldTransform(pxr.Usd.TimeCode.Default())
        local_matrix = world_matrix * parent_to_world.GetInverse()

        stage = cam_prim.GetStage()
        layer = self._edit_target_layer(stage, ops)
        with pxr.Usd.EditContext(stage, stage.GetEditTargetForLocalLayer(layer)):
            if not self._write_into_existing_ops(ops, local_matrix):
                # Unusual (or empty) op stack. Collapsing it to a single matrix
                # op is safe here specifically because we are authoring into the
                # same layer that owns the existing ops.
                matrix_op = xformable.MakeMatrixXform()
                if not matrix_op:
                    carb.log_warn(f"Spacemouse: cannot author a transform op on {cam_prim.GetPath()}")
                    return
                matrix_op.Set(local_matrix)
            self._ensure_center_of_interest(cam_prim)

    def _log_comparison(self, nav: _NavInput) -> None:
        """SPACEMOUSE_COMPARE: log both backends' inputs side by side, about twice a second while the cap moves."""
        if self._compare_source is None or time.perf_counter() - self._last_compare_log < 0.5:
            return
        other = self._compare_source.poll()
        if not any(nav[:6]) and (other is None or not any(other[:6])):
            return
        self._last_compare_log = time.perf_counter()

        def fmt(n: Optional[_NavInput]) -> str:
            if n is None:
                return "no reading"
            axes = " ".join(f"{name}={value:+.2f}" for name, value in zip(_NavInput._fields[:6], n[:6]))
            return f"{axes} buttons={n.buttons}"

        _logger.info(f"SpaceMouse compare: {self._axis_source.name}: {fmt(nav)} | "
                     f"{self._compare_source.name}: {fmt(other)}")

    def tick(self, current_time: float, dt: float):

        source = self._axis_source
        if source is None:
            return

        # Camera navigation runs on wall-clock time, NOT the dt handed to us
        # here: that value comes from the timeline event payload, so it is
        # simulation time. It goes to zero while the timeline is stopped and
        # scales with the sim's time scale, neither of which should change how
        # fast the viewport camera flies.
        now = time.perf_counter()
        frame_dt = 0.0 if self._last_tick_time is None else now - self._last_tick_time
        self._last_tick_time = now
        frame_dt = max(0.0, min(frame_dt, MAX_DT))
        if frame_dt <= 0.0:
            return

        nav = source.poll()
        if nav is None:
            return
        # Another application has focus: ignore the device entirely (motion and buttons). The reader keeps running,
        # so input resumes from the current cap position as soon as Isaac Sim is focused again.
        if self._focus is not None and not self._focus.focused():
            return
        self._log_comparison(nav)
        raw_buttons = nav.buttons

        cam_prim, eye_old, q_old, _target_old = self.get_current_camera_eye_and_target()
        if cam_prim is None:
            # No Cam Available
            return

        # raw_buttons
        #   0: No Buttons
        #   1: Left Button
        #   2: Right Button
        #   3: Both Buttons
        if raw_buttons == 1:
            # Frame the current selection
            omni.kit.viewport.utility.frame_viewport_selection()
            return

        if raw_buttons == 2:
            eye = pxr.Gf.Vec3d(1.5, 1, 1)
            target = pxr.Gf.Vec3d(0, 0, 0)
            omni.usd.get_context().get_selection().clear_selected_prim_paths()
            isaacsim.core.utils.viewports.set_camera_view(eye=eye, target=target, camera_prim_path=str(cam_prim.GetPath()))
            omni.kit.viewport.utility.frame_viewport_selection()
            return

        # Shape every axis independently. Which device axis drives which motion, and its sign, is each backend's
        # decision (see its poll()); nothing here depends on the backend except the optional inversion.
        sign = -1.0 if SPACEMOUSE_FLIP_AXES else 1.0
        move_right = sign * _shape_axis(nav.move_right)
        move_up = sign * _shape_axis(nav.move_up)
        move_backward = sign * _shape_axis(nav.move_backward)
        look_up = sign * _shape_axis(nav.look_up)
        look_left = sign * _shape_axis(nav.look_left)
        roll_cw = sign * _shape_axis(nav.roll_cw)

        # If there is no input movement, don't bother calculating
        if not (move_right or move_up or move_backward or look_up or look_left or roll_cw):
            return

        # Calculate new rotation
        rotation_step = ROTATION_SPEED * frame_dt
        if ORIENTATION_MODE == "free":
            q_new = self._orient_free(
                q_old, look_up * rotation_step, look_left * rotation_step, roll_cw * rotation_step
            )
        else:
            q_new = self._orient_level(
                q_old, look_up * rotation_step, look_left * rotation_step, cam_prim.GetStage()
            )

        # Calculate new position, in the camera's frame after rotating
        translation_step = TRANSLATION_SPEED * frame_dt
        eye_new = eye_old \
            + q_new.Transform(CAM_RIGHT) * (move_right * translation_step) \
            + q_new.Transform(CAM_UP) * (move_up * translation_step) \
            + q_new.Transform(CAM_BACKWARD) * (move_backward * translation_step)

        self._author_camera_transform(cam_prim, eye_new, q_new)

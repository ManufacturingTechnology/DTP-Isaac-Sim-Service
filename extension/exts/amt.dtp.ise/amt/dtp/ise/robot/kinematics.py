"""``CuMotionKinematicsSolver``: the cuMotion backend of the ``KinematicsSolver`` interface.

Built from a robot's URDF and XRDF alone: no USD stage, no simulation, so it also runs offline with Isaac
Sim's Python (cuMotion from the ``isaacsim.robot_motion.cumotion`` extension's ``pip_prebundle``). Units
are SI (meters, radians), as cuMotion's; poses are in the robot's base frame (the URDF root link).

- **Frames:** the XRDF's frames plus frames added at runtime (``set_frames``: an XRDF ``add_frame``
  modifier and a ``tool_frames`` entry each; the robot description is reloaded, about 0.2 ms for a 6-axis
  robot). This is how ``ManipulatorRobotCuMotionActuator.set_tcp`` / ``set_flange_frame`` work.
- **IK:** ``cumotion.solve_ik`` solves one target at a time (cuMotion 1.1.0a2 has no batched IK without
  a collision world), so a batch is solved sequentially with continuity seeding (the base class); a seeded
  solve takes about 35 µs, FK about 1 µs (measured with the AMT MR-A1).
- **Joint types** come from the URDF (cuMotion doesn't expose them); ``continuous`` joints are revolute
  without position limits.
- **Jacobian:** cuMotion's analytic ``Kinematics.jacobian`` (linear rows, then angular velocity rows, in the base
  frame; it matches central differences of FK to 1e-9, by probe).
- **Self-collision:** a ``RobotWorldInspector`` without a world view: the XRDF's ``self_collision`` spheres and
  ``ignore`` pairs only. :meth:`CuMotionKinematicsSolver.robot_world_inspector` takes a world view: the extension
  point for collision-aware queries in process (the RobotAPI's planner, and the kinematics service's planner, whose
  world is the client's neutral obstacles, never the scene).
- **Concurrency:** an instance is not thread-safe (its ``ik_config`` template and frame reloads are Python-side
  state): callers serialize access per instance (the kinematics service and the RobotAPI hold a lock per
  solver). Also, every cuMotion call in this module holds the process-wide :data:`CUMOTION_LOCK`, so no two
  cuMotion calls overlap even if a binding released the GIL: cuMotion documents no thread safety, and a probe
  measured that its pybind11 calls hold the GIL anyway, so the lock costs no parallelism. It is
  held per call, never per batch, so the simulation thread waits for at most one call. Evidence:
  ``docs/src/Agents/Scratch/robots/supporting/2026-10-23_cumotion-thread-safety.md``.

Design: ``docs/src/Agents/Specs/robot-control.md`` ("KinematicsSolver", "The kinematics service").
"""
import copy
import math
import pathlib
import threading
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

import numpy as np
import yaml

try:  # In Isaac Sim: registers the DLL directory cumotion needs on Windows, and has the URDF normalizer.
    import isaacsim.robot_motion.cumotion as _isaac_cumotion
except ImportError:  # offline: the caller puts cumotion on the path (and its DLL directory)
    _isaac_cumotion = None
import cumotion

from ..lib.control.motion.kinematics import FrameChange, JointInfo, KinematicsError, KinematicsSolver
from ..lib.control.motion.registry import SolverSource


CUMOTION_LOCK = threading.RLock()
"""Held around every cuMotion call in this module (one call at a time, process-wide); see the module docstring."""


def _limit(value: float) -> Optional[float]:
    return float(value) if value is not None and math.isfinite(value) and value > 0 else None


class CuMotionKinematicsSolver(KinematicsSolver):
    """Forward and inverse kinematics with cuMotion, from URDF + XRDF files (or their text).

    :param urdf_path: The robot's URDF file (or pass ``urdf_text``).
    :param xrdf_path: The robot's XRDF file (or pass ``xrdf_text``). It must list ``tool_frames``; the
        first is the default tool frame (by convention ``tool0``, the flange face).
    :param tool_frame: The default tool frame, if not the XRDF's first.
    """

    backend = "cumotion"

    def __init__(
        self,
        urdf_path: Optional[str] = None,
        xrdf_path: Optional[str] = None,
        tool_frame: Optional[str] = None,
        urdf_text: Optional[str] = None,
        xrdf_text: Optional[str] = None,
    ):
        super().__init__()
        if (urdf_path is None) == (urdf_text is None) or (xrdf_path is None) == (xrdf_text is None):
            raise TypeError("pass exactly one of urdf_path / urdf_text and one of xrdf_path / xrdf_text")
        self._urdf_path = urdf_path
        self._xrdf_path = xrdf_path
        if urdf_text is None:
            urdf_text = pathlib.Path(urdf_path).read_text(encoding="utf-8")
        if xrdf_text is None:
            xrdf_text = pathlib.Path(xrdf_path).read_text(encoding="utf-8")
        if _isaac_cumotion is not None:
            # Adds the effort/velocity limit attributes urdfdom requires (as load_cumotion_robot() does).
            urdf_text = _isaac_cumotion.normalize_urdf_for_urdfdom(urdf_text)
        self._urdf_text: str = urdf_text
        self._xrdf: Dict[str, Any] = yaml.safe_load(xrdf_text)
        if not isinstance(self._xrdf, dict):
            raise ValueError(f"XRDF {xrdf_path or '(text)'} is not a valid XRDF (YAML mapping)")
        self._urdf_joint_types: Dict[str, str] = {
            j.get("name"): j.get("type") for j in ElementTree.fromstring(self._urdf_text).iter("joint")}
        self._added: Dict[str, Tuple[str, np.ndarray, np.ndarray]] = {}  # frames added at runtime (meters)
        self.ik_config = cumotion.IkConfig()  # the template for every solve (tolerances are set per solve)
        self.last_ik_results: Optional[Any] = None
        self._load()
        self._xrdf_frames = self.frame_names
        with CUMOTION_LOCK:
            tools = list(self._description.tool_frame_names())
        if not tools:
            raise ValueError(f"XRDF {xrdf_path or '(text)'} has no tool_frames; add a tool0 frame (an add_frame modifier) "
                             f"and list it in tool_frames (frames: {self._xrdf_frames})")
        if tool_frame is not None and tool_frame not in tools:
            raise ValueError(f"tool_frame '{tool_frame}' is not one of the XRDF tool_frames {tools}")
        self._default_tool_frame = tool_frame or tools[0]

    # ---- loading ------------------------------------------------------------------------------------

    def _load(self, added: Optional[Dict[str, Tuple[str, np.ndarray, np.ndarray]]] = None) -> None:
        added = self._added if added is None else added
        xrdf = copy.deepcopy(self._xrdf)
        if added:
            modifiers = list(xrdf.get("modifiers") or [])
            tool_frames = list(xrdf.get("tool_frames") or [])
            for name, (parent, position, orientation) in added.items():
                modifiers.append({"add_frame": {
                    "frame_name": name,
                    "parent_frame_name": parent,
                    "joint_name": f"{name}_joint",
                    "joint_type": "FIXED",
                    "fixed_transform": {
                        "position": [float(v) for v in position],
                        "orientation": {"w": float(orientation[0]), "xyz": [float(v) for v in orientation[1:]]},
                    },
                }})
                if name not in tool_frames:
                    tool_frames.append(name)
            xrdf["modifiers"] = modifiers
            xrdf["tool_frames"] = tool_frames
        xrdf_text = yaml.safe_dump(xrdf, sort_keys=False)
        with CUMOTION_LOCK:
            description = cumotion.load_robot_from_memory(xrdf_text, self._urdf_text)
            kinematics = description.kinematics()
            joints = []
            for i in range(description.num_cspace_coords()):
                name = description.cspace_coord_name(i)
                limits = kinematics.cspace_coord_limits(i)
                urdf_type = self._urdf_joint_types.get(name, "revolute")
                joints.append(JointInfo(
                    name=name,
                    type="prismatic" if urdf_type == "prismatic" else "revolute",
                    lower=-math.inf if urdf_type == "continuous" else float(limits.lower),
                    upper=math.inf if urdf_type == "continuous" else float(limits.upper),
                    velocity=_limit(kinematics.cspace_coord_velocity_limit(i)),
                    acceleration=_limit(kinematics.cspace_coord_acceleration_limit(i)),
                    jerk=_limit(kinematics.cspace_coord_jerk_limit(i)),
                ))
        self._description = description
        self._kinematics = kinematics
        self._joints = joints
        self._cumotion_robot = None
        self._inspector = None  # the self-collision inspector of this description, built on demand

    def _set_frames(self, changes: Dict[str, FrameChange]) -> None:
        added = dict(self._added)
        for name, change in changes.items():
            if name in self._xrdf_frames:
                raise KinematicsError("UNKNOWN_FRAME", f"'{name}' is a frame of the robot description; it can't be changed")
            if change is None:
                added.pop(name, None)
                continue
            parent, position, orientation = change
            position = np.asarray(position, dtype=np.float64).reshape(3)
            orientation = np.asarray(orientation, dtype=np.float64).reshape(4)
            norm = float(np.linalg.norm(orientation))
            if norm < 1e-9:
                raise ValueError("the frame's orientation must be a non-zero quaternion")
            added.pop(name, None)  # re-added last, after its (possibly new) parent
            added[name] = (parent, position, orientation / norm)
        # Parents must exist and come first: order by dependency.
        ordered: Dict[str, Tuple[str, np.ndarray, np.ndarray]] = {}
        pending = dict(added)
        while pending:
            ready = [n for n, (parent, _, _) in pending.items() if parent in self._xrdf_frames or parent in ordered]
            if not ready:
                raise KinematicsError("UNKNOWN_FRAME", f"frame parent(s) not found: "
                                      f"{sorted({p for p, _, _ in pending.values()})}; frames: {self._xrdf_frames + list(ordered)}")
            for name in ready:
                ordered[name] = pending.pop(name)
        try:
            self._load(ordered)
        except Exception:
            self._load()  # keep the previous description
            raise
        self._added = ordered

    # ---- description --------------------------------------------------------------------------------

    @property
    def joints(self) -> List[JointInfo]:
        return list(self._joints)

    @property
    def base_frame(self) -> str:
        with CUMOTION_LOCK:
            return self._kinematics.base_frame_name()

    @property
    def frame_names(self) -> List[str]:
        with CUMOTION_LOCK:
            return list(self._kinematics.frame_names())

    @property
    def tool_frames(self) -> List[str]:
        with CUMOTION_LOCK:
            tools = list(self._description.tool_frame_names())
        tools.remove(self._default_tool_frame)
        return [self._default_tool_frame, *tools]

    @property
    def added_frames(self) -> Dict[str, Tuple[str, np.ndarray, np.ndarray]]:
        """Frames added at runtime: ``{name: (parent, position (m), orientation wxyz)}``."""
        return {n: (p, pos.copy(), o.copy()) for n, (p, pos, o) in self._added.items()}

    @property
    def xrdf(self) -> Dict[str, Any]:
        """The XRDF as loaded (without the frames added at runtime)."""
        return copy.deepcopy(self._xrdf)

    @property
    def urdf_text(self) -> str:
        return self._urdf_text

    @property
    def robot_description(self) -> "cumotion.RobotDescription":
        """The current cuMotion robot description (replaced whenever the frames change)."""
        return self._description

    @property
    def kinematics(self) -> "cumotion.Kinematics":
        """The current cuMotion kinematics (replaced whenever the frames change)."""
        return self._kinematics

    @property
    def cumotion_robot(self) -> Any:
        """An ``isaacsim.robot_motion.cumotion.CumotionRobot`` for cuMotion's planners and controllers (only
        inside Isaac Sim); rebuild anything made from it after the frames change."""
        if _isaac_cumotion is None:
            raise RuntimeError("CumotionRobot needs Isaac Sim's isaacsim.robot_motion.cumotion extension")
        if self._cumotion_robot is None:
            directory = pathlib.Path(self._xrdf_path).parent if self._xrdf_path else pathlib.Path.cwd()
            with CUMOTION_LOCK:
                self._cumotion_robot = _isaac_cumotion.CumotionRobot(
                    directory=directory, robot_description=self._description, kinematics=self._kinematics,
                    controlled_joint_names=[j.name for j in self._joints])
        return self._cumotion_robot

    def robot_world_inspector(self, world_view: Any = None) -> Any:
        """A new cuMotion ``RobotWorldInspector`` for the current description and ``world_view`` (None: no world,
        self-collision only). The extension point for collision-aware queries in process (the upper layer); build
        a new one after the frames change. The scene-free kinematics service never passes a world."""
        with CUMOTION_LOCK:
            if world_view is None:
                return cumotion.create_robot_world_inspector(self._description)
            return cumotion.create_robot_world_inspector(self._description, world_view)

    def _self_inspector(self) -> Any:
        if self._inspector is None:
            self._inspector = self.robot_world_inspector()
        return self._inspector

    # ---- kinematics ---------------------------------------------------------------------------------------

    def _fk(self, q: np.ndarray, frame: str) -> Tuple[np.ndarray, np.ndarray]:
        positions = np.empty((q.shape[0], 3))
        orientations = np.empty((q.shape[0], 4))
        for i, row in enumerate(q):
            with CUMOTION_LOCK:
                pose = self._kinematics.pose(row, frame)
                translation = pose.translation
                r = pose.rotation
                orientations[i] = (r.w(), r.x(), r.y(), r.z())
            positions[i] = np.asarray(translation, dtype=np.float64).reshape(3)
        return positions, orientations

    def _jacobian(self, q: np.ndarray, frame: str) -> np.ndarray:
        result = np.empty((q.shape[0], 6, q.shape[1]))
        for i, row in enumerate(q):
            with CUMOTION_LOCK:
                result[i] = np.asarray(self._kinematics.jacobian(row, frame), dtype=np.float64)
        return result

    def _self_collision(self, q: np.ndarray) -> List[List[Tuple[str, str]]]:
        inspector = self._self_inspector()
        result = []
        for row in q:
            with CUMOTION_LOCK:
                pairs = inspector.frames_in_self_collision(np.ascontiguousarray(row, dtype=np.float64))
            result.append([(str(a), str(b)) for a, b in pairs])
        return result

    def _collision_spheres(self, q: np.ndarray) -> List[Tuple[str, np.ndarray, float]]:
        inspector = self._self_inspector()
        with CUMOTION_LOCK:
            count = inspector.num_self_collision_spheres()
            frames = [inspector.self_collision_sphere_frame_name(i) for i in range(count)]
            centers = inspector.self_collision_sphere_positions(np.ascontiguousarray(q, dtype=np.float64))
            radii = inspector.self_collision_sphere_radii()
        return [(f, np.asarray(c, dtype=np.float64).reshape(3), float(r)) for f, c, r in zip(frames, centers, radii)]

    def solve_ik_native(self, target: "cumotion.Pose3", frame: str, seed: Optional[np.ndarray]) -> Any:
        """One cuMotion IK solve with :attr:`ik_config` as it is (its tolerances included): the cuMotion
        ``IkResults``, also kept in :attr:`last_ik_results`."""
        self.ik_config.cspace_seeds = [np.asarray(seed, dtype=np.float64)] if seed is not None else []
        with CUMOTION_LOCK:
            results = cumotion.solve_ik(self._kinematics, target, frame, self.ik_config)
        self.last_ik_results = results
        return results

    def _ik_one(self, position, orientation, frame, seed, position_tolerance, orientation_tolerance):
        config = self.ik_config
        saved = config.position_tolerance, config.orientation_tolerance
        # cuMotion's orientation tolerance is per axis; the base class checks the rotation angle, which
        # can be up to ~1.22 x the per-axis error, so ask cuMotion for a little more.
        config.position_tolerance = float(position_tolerance)
        config.orientation_tolerance = float(orientation_tolerance) * 0.8
        try:
            w, x, y, z = (float(v) for v in orientation)
            target = cumotion.Pose3(rotation=cumotion.Rotation3(w, x, y, z), translation=np.asarray(position, dtype=np.float64))
            results = self.solve_ik_native(target, frame, seed)
        finally:
            config.position_tolerance, config.orientation_tolerance = saved
        q = np.asarray(results.cspace_position, dtype=np.float64).reshape(-1)
        if q.shape[0] != len(self._joints) or np.isnan(q).any():
            return False, None
        return bool(results.success), q


def cumotion_solver_factory(source: SolverSource) -> CuMotionKinematicsSolver:
    """The kinematics service's ``cumotion`` backend: a solver from the uploaded files (a ``SolverSource``)."""
    return CuMotionKinematicsSolver(urdf_path=str(source.urdf_path), xrdf_path=str(source.xrdf_path),
                                    tool_frame=source.tool_frame)

"""The ``KinematicsSolver`` interface: batched forward and inverse kinematics for one robot (one
end-effector combination, ADR-0012), independent of any backend (cuMotion, cuRobo, ...).

**Units are SI** (radians, meters, rad/s, m/s); conversion to the wire's degrees happens at the API
boundary. Orientations are ``[w, x, y, z]`` arrays here (the named models live at the boundary).

A backend implements :meth:`KinematicsSolver._fk` and :meth:`KinematicsSolver._ik_one`; the base class
provides batching, continuity seeding, unwrapping, and backend-independent error measurement. A backend
with a truly batched IK overrides :meth:`KinematicsSolver.ik`. Optional hooks: ``_set_frames`` (runtime tool
frames), ``_jacobian`` (analytic; the default is numerical), ``_self_collision`` / ``_collision_spheres`` (the
robot's own collision geometry, no world); :attr:`KinematicsSolver.capabilities` lists what a solver has.
A solver is not thread-safe: callers serialize access to one instance (the kinematics service and the
RobotAPI hold a lock per solver). Design:``docs/src/Agents/Specs/robot-control.md``
("KinematicsSolver").
"""
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Tuple

import numpy as np


JointType = Literal["revolute", "prismatic"]
FrameChange = Optional[Tuple[str, np.ndarray, np.ndarray]]  # (parent frame, position (3,) m, orientation (4,) wxyz)

DEFAULT_POSITION_TOLERANCE = 1e-3  # m (cuMotion's IkConfig default)
DEFAULT_ORIENTATION_TOLERANCE = math.radians(0.5)  # rad


class KinematicsError(Exception):
    """A kinematics request can't be served: ``code`` is a robot error code (``UNKNOWN_FRAME``,
    ``UNKNOWN_JOINT``, ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message


@dataclass(frozen=True)
class JointInfo:
    """One c-space joint. Limits in SI units; ``None`` (or an infinite position limit) means unknown."""
    name: str
    type: JointType
    lower: float = -math.inf
    upper: float = math.inf
    velocity: Optional[float] = None
    acceleration: Optional[float] = None
    jerk: Optional[float] = None


@dataclass
class IkSolution:
    """The result of a batch IK: per target ``success``, the joint positions ``q`` (``(N, n)``; the best
    attempt where it failed, or NaN if there was none), and the measured ``position_error`` (m) and
    ``orientation_error`` (rad, the rotation angle between target and solution)."""
    success: np.ndarray
    q: np.ndarray
    position_error: np.ndarray
    orientation_error: np.ndarray

    def __len__(self) -> int:
        return int(self.success.shape[0])

    @property
    def all_success(self) -> bool:
        return bool(np.all(self.success))

    def first_failure(self) -> Optional[int]:
        failed = np.flatnonzero(~self.success)
        return int(failed[0]) if failed.size else None


# A URDF velocity limit at or above this (rad/s or m/s) is a placeholder, not a limit: cuMotion's URDF
# normalizer injects exactly 1000 when one is missing (isaacsim.robot_motion.cumotion urdf_normalize.
# DEFAULT_VELOCITY), and exported URDFs carry such values (the AMT MR-A1's says 1e6).
PLACEHOLDER_VELOCITY = 1000.0


def to_wire(joint: JointInfo, value: float) -> float:
    """SI (rad, m) to wire units (degrees for revolute joints, meters for prismatic)."""
    return math.degrees(value) if joint.type == "revolute" else float(value)


def from_wire(joint: JointInfo, value: float) -> float:
    """Wire units (degrees for revolute joints, meters for prismatic) to SI (rad, m)."""
    return math.radians(value) if joint.type == "revolute" else float(value)


def plausible_limit(value: Optional[float], placeholder: float = math.inf) -> Optional[float]:
    """``value`` if it is a usable limit (finite, positive, below ``placeholder``), else None."""
    if value is None or not math.isfinite(value) or value <= 0 or value >= placeholder:
        return None
    return float(value)


def quaternion_angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The rotation angle (rad) between wxyz quaternions ``a`` and ``b`` (``(4,)`` or ``(N, 4)``)."""
    a = np.atleast_2d(a)
    b = np.atleast_2d(b)
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    dot = np.clip(np.abs(np.sum(a * b, axis=1)), 0.0, 1.0)
    return 2.0 * np.arccos(dot)


def _quat_conjugate(q: np.ndarray) -> np.ndarray:
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def _quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton products of wxyz quaternions ``(N, 4)``."""
    w1, x1, y1, z1 = a.T
    w2, x2, y2, z2 = b.T
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
                     w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                     w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], axis=1)


def _rotation_vector(q: np.ndarray) -> np.ndarray:
    """Rotation vectors (axis × angle, rad) of wxyz quaternions ``(N, 4)``, along the shorter arc."""
    q = q * np.where(q[:, :1] < 0, -1.0, 1.0)
    v = q[:, 1:]
    s = np.linalg.norm(v, axis=1)
    angle = 2.0 * np.arctan2(s, q[:, 0])
    scale = np.where(s > 1e-12, angle / np.where(s > 1e-12, s, 1.0), 2.0)
    return v * scale[:, None]


class KinematicsSolver(ABC):
    """Forward and inverse kinematics for one robot, in SI units.

    Subclasses provide the description (:attr:`joints`, :attr:`base_frame`, :attr:`frame_names`,
    :attr:`tool_frames`), :meth:`_fk` and :meth:`_ik_one`, and optionally :meth:`_set_frames`.
    """

    backend: str = "unknown"

    def __init__(self) -> None:
        self._revision = 0

    # ---- description ------------------------------------------------------------------------------

    @property
    @abstractmethod
    def joints(self) -> List[JointInfo]:
        """The c-space joints, in solver order."""

    @property
    @abstractmethod
    def base_frame(self) -> str:
        """The frame every pose is expressed in (the robot base)."""

    @property
    @abstractmethod
    def frame_names(self) -> List[str]:
        """Every frame FK can be computed for (links and added frames)."""

    @property
    @abstractmethod
    def tool_frames(self) -> List[str]:
        """The tool frames; the first is the default."""

    @property
    def joint_names(self) -> List[str]:
        return [j.name for j in self.joints]

    @property
    def num_joints(self) -> int:
        return len(self.joints)

    @property
    def default_tool_frame(self) -> str:
        return self.tool_frames[0]

    @property
    def revision(self) -> int:
        """Increments whenever the frames change (see :meth:`set_frames`)."""
        return self._revision

    def check_frame(self, frame: Optional[str]) -> str:
        """``frame`` (the default tool frame if None), or raise ``UNKNOWN_FRAME``."""
        if frame is None:
            return self.default_tool_frame
        if frame not in self.frame_names:
            raise KinematicsError("UNKNOWN_FRAME", f"unknown frame '{frame}'; frames: {self.frame_names}")
        return frame

    def joint_index(self, name: str) -> int:
        try:
            return self.joint_names.index(name)
        except ValueError:
            raise KinematicsError("UNKNOWN_JOINT", f"unknown joint '{name}'; joints: {self.joint_names}") from None

    # ---- frames (TCP definitions) ----------------------------------------------------------------------

    def set_frames(self, changes: Mapping[str, FrameChange]) -> None:
        """Add, move (``name: (parent, position, wxyz)``) or remove (``name: None``) fixed frames, all at
        once; added frames become tool frames. Rolled back if the backend rejects the result."""
        self._set_frames(dict(changes))
        self._revision += 1

    def set_frame(self, name: str, parent: str, position: Sequence[float], orientation: Optional[Sequence[float]] = None) -> None:
        self.set_frames({name: (parent, np.asarray(position, dtype=np.float64),
                                np.asarray(orientation if orientation is not None else (1.0, 0.0, 0.0, 0.0), dtype=np.float64))})

    def remove_frame(self, name: str) -> None:
        self.set_frames({name: None})

    def _set_frames(self, changes: Dict[str, FrameChange]) -> None:
        raise NotImplementedError(f"the {self.backend} solver can't change frames")

    # ---- capabilities ---------------------------------------------------------------------------------

    @property
    def capabilities(self) -> List[str]:
        """What this solver can do: ``fk``, ``ik``, ``jacobian`` (always; numerical unless the backend has
        an analytic one), ``frames`` (``set_frames``), ``self_collision`` (and its collision spheres)."""
        result = ["fk", "ik", "jacobian"]
        cls = type(self)
        if cls._set_frames is not KinematicsSolver._set_frames:
            result.append("frames")
        if cls._self_collision is not KinematicsSolver._self_collision:
            result.append("self_collision")
        return result

    def _unsupported(self, what: str) -> KinematicsError:
        return KinematicsError("UNSUPPORTED", f"the {self.backend} solver doesn't support {what}")

    # ---- backend ------------------------------------------------------------------------------------

    @abstractmethod
    def _fk(self, q: np.ndarray, frame: str) -> Tuple[np.ndarray, np.ndarray]:
        """Poses of ``frame`` for joint positions ``q`` ``(N, n)``: positions ``(N, 3)``, wxyz ``(N, 4)``."""

    @abstractmethod
    def _ik_one(self, position: np.ndarray, orientation: np.ndarray, frame: str, seed: Optional[np.ndarray],
                position_tolerance: float, orientation_tolerance: float) -> Tuple[bool, Optional[np.ndarray]]:
        """Solve one target: ``(success, q)``; ``q`` may be a best attempt when unsuccessful, or None."""

    # ---- API ------------------------------------------------------------------------------------------

    def as_joint_array(self, q: Sequence) -> np.ndarray:
        q = np.asarray(q, dtype=np.float64)
        if q.ndim == 1:
            q = q.reshape(1, -1)
        if q.ndim != 2 or q.shape[1] != self.num_joints:
            raise ValueError(f"joint positions must have {self.num_joints} columns ({self.joint_names}), got shape {q.shape}")
        return q

    def fk(self, q: Sequence, frame: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Forward kinematics, batched: positions ``(N, 3)`` and wxyz orientations ``(N, 4)`` of ``frame``
        (default: the default tool frame) in :attr:`base_frame`."""
        frame = self.check_frame(frame)
        return self._fk(self.as_joint_array(q), frame)

    def ik(
        self,
        positions: Sequence,
        orientations: Sequence,
        frame: Optional[str] = None,
        seed: Optional[Sequence[float]] = None,
        sequential: bool = True,
        position_tolerance: float = DEFAULT_POSITION_TOLERANCE,
        orientation_tolerance: float = DEFAULT_ORIENTATION_TOLERANCE,
    ) -> IkSolution:
        """Inverse kinematics for ``N`` targets (positions ``(N, 3)`` m, wxyz ``(N, 4)``) of ``frame``.

        With ``sequential`` (continuity seeding), target *i* is seeded with the solution of target *i−1*
        (the first with ``seed``), so the solutions stay on one IK branch; otherwise every target is
        seeded with ``seed``. Revolute solutions are unwrapped to the 2π-equivalent nearest the seed.
        ``success`` requires the backend's success and the measured errors within the tolerances."""
        frame = self.check_frame(frame)
        positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
        orientations = np.asarray(orientations, dtype=np.float64).reshape(-1, 4)
        if positions.shape[0] != orientations.shape[0]:
            raise ValueError("positions and orientations must have the same length")
        orientations = orientations / np.linalg.norm(orientations, axis=1, keepdims=True)
        n, count = self.num_joints, positions.shape[0]
        first_seed = None if seed is None else self.as_joint_array(seed)[0]
        success = np.zeros(count, dtype=bool)
        q = np.full((count, n), np.nan)
        current = first_seed
        for i in range(count):
            start = current if sequential else first_seed
            ok, solution = self._ik_one(positions[i], orientations[i], frame, start, position_tolerance, orientation_tolerance)
            if solution is None:
                continue
            solution = np.asarray(solution, dtype=np.float64).reshape(-1)
            if start is not None:
                solution = self.unwrap(solution, start)
            q[i] = solution
            success[i] = bool(ok)
            if ok:
                current = solution
        position_error = np.full(count, np.inf)
        orientation_error = np.full(count, np.inf)
        solved = ~np.isnan(q).any(axis=1)
        if solved.any():
            p, o = self._fk(q[solved], frame)
            position_error[solved] = np.linalg.norm(p - positions[solved], axis=1)
            orientation_error[solved] = quaternion_angle(o, orientations[solved])
        success &= (position_error <= position_tolerance * (1 + 1e-6)) & (orientation_error <= orientation_tolerance * (1 + 1e-6))
        return IkSolution(success, q, position_error, orientation_error)

    def jacobian(self, q: Sequence, frame: Optional[str] = None) -> np.ndarray:
        """Geometric Jacobians ``(N, 6, n)`` of ``frame`` (default: the default tool frame) in
        :attr:`base_frame`, SI: rows 0–2 map joint velocities to the frame origin's linear velocity (m/s),
        rows 3–5 to its angular velocity (rad/s), both in base coordinates."""
        frame = self.check_frame(frame)
        return self._jacobian(self.as_joint_array(q), frame)

    def _jacobian(self, q: np.ndarray, frame: str) -> np.ndarray:
        """Central differences of :meth:`_fk` (a backend with an analytic Jacobian overrides this)."""
        h = 1e-6
        count, n = q.shape
        result = np.zeros((count, 6, n))
        for j in range(n):
            step = np.zeros(n)
            step[j] = h
            p_plus, o_plus = self._fk(q + step, frame)
            p_minus, o_minus = self._fk(q - step, frame)
            result[:, :3, j] = (p_plus - p_minus) / (2 * h)
            result[:, 3:, j] = _rotation_vector(_quat_multiply(o_plus, _quat_conjugate(o_minus))) / (2 * h)
        return result

    def self_collision(self, q: Sequence) -> List[List[Tuple[str, str]]]:
        """For each configuration (``(N, n)``, SI), the pairs of frames whose collision spheres intersect
        (the robot's own geometry only, no world); an empty list means collision-free. Raises
        ``UNSUPPORTED`` if the backend can't check self-collision."""
        return self._self_collision(self.as_joint_array(q))

    def _self_collision(self, q: np.ndarray) -> List[List[Tuple[str, str]]]:
        raise self._unsupported("self-collision checks")

    def collision_spheres(self, q: Sequence) -> List[Tuple[str, np.ndarray, float]]:
        """The self-collision spheres at one configuration ``(n,)`` (SI): ``(frame, center (3,) m in the base
        frame, radius m)`` each. Raises ``UNSUPPORTED`` if the backend has none."""
        return self._collision_spheres(self.as_joint_array(q)[0])

    def _collision_spheres(self, q: np.ndarray) -> List[Tuple[str, np.ndarray, float]]:
        raise self._unsupported("collision spheres")

    def unwrap(self, q: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """For each revolute joint, the 2π-equivalent of ``q`` nearest ``reference`` that is within the
        joint's limits (unchanged if none is)."""
        result = np.array(q, dtype=np.float64)
        for i, joint in enumerate(self.joints):
            if joint.type != "revolute":
                continue
            turns = round((reference[i] - result[i]) / (2 * math.pi))
            if turns:
                candidate = result[i] + 2 * math.pi * turns
                if joint.lower - 1e-9 <= candidate <= joint.upper + 1e-9:
                    result[i] = candidate
        return result

    # ---- wire units (degrees for revolute joints, meters for prismatic ones) ------------------------------

    def joints_from_wire(self, values: Mapping[str, float], base: Optional[np.ndarray] = None) -> np.ndarray:
        """Joint positions ``(n,)`` in SI units from ``{joint name: degrees or meters}``. Every joint is
        required unless ``base`` (SI) supplies the missing ones. Unknown names raise ``UNKNOWN_JOINT``."""
        names = self.joint_names
        unknown = [name for name in values if name not in names]
        if unknown:
            raise KinematicsError("UNKNOWN_JOINT", f"unknown joint(s) {unknown}; joints: {names}")
        missing = [name for name in names if name not in values]
        if missing and base is None:
            raise KinematicsError("UNKNOWN_JOINT", f"joint(s) {missing} missing; every joint is required: {names}")
        q = np.array(base, dtype=np.float64) if base is not None else np.zeros(len(names))
        for i, joint in enumerate(self.joints):
            if joint.name in values:
                q[i] = from_wire(joint, values[joint.name])
        return q

    def joints_to_wire(self, q: Sequence[float]) -> Dict[str, float]:
        """``{joint name: degrees or meters}`` from SI joint positions ``(n,)``."""
        return {joint.name: to_wire(joint, float(v)) for joint, v in zip(self.joints, np.asarray(q).reshape(-1))}

    def limit_violation(self, q: np.ndarray, tolerance: float = 1e-9) -> Optional[Tuple[int, int]]:
        """The first ``(row, joint index)`` of ``q`` ``(N, n)`` outside the position limits, or None."""
        q = self.as_joint_array(q)
        lower = np.array([j.lower for j in self.joints])
        upper = np.array([j.upper for j in self.joints])
        bad = (q < lower - tolerance) | (q > upper + tolerance)
        if not bad.any():
            return None
        row, joint = np.argwhere(bad)[0]
        return int(row), int(joint)

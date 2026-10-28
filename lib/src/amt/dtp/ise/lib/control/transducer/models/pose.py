"""Named pose models (ADR-0014): ``Vector3``, ``Quaternion``, ``Pose``, ``FramedPose``.

Libraries disagree on quaternion order (USD ``Gf.Quatd``, Isaac Sim, cuMotion, and Isaac Lab use
w, x, y, z; Warp, SciPy, and ROS use x, y, z, w), so poses are never bare arrays: every component is a
named field, and the converters say what they produce (``to_wxyz``, ``to_xyzw``, ``to_gf``).

Units: positions in meters (stage units; they coincide while ``metersPerUnit`` is 1). Rotation vectors
(``from_rotvec``, UR's axis-angle convention) are in radians; everything else is unitless.
"""
import math
from typing import Any, Sequence, Tuple

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _finite(value: Any, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not {value}")
    return value


def _vector(values: Any, length: int, what: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.shape != (length,):
        raise ValueError(f"{what} needs {length} values, got {array.shape[0]}")
    return array


class Vector3(BaseModel):
    """A 3D vector, e.g. a position in meters."""
    model_config = ConfigDict(extra="forbid")
    x: float = Field(title="X")
    y: float = Field(title="Y")
    z: float = Field(title="Z")

    @model_validator(mode="after")
    def _check_finite(self) -> "Vector3":
        for name in ("x", "y", "z"):
            _finite(getattr(self, name), name)
        return self

    def to_numpy(self) -> np.ndarray:
        """``[x, y, z]`` as a float64 array."""
        return np.array([self.x, self.y, self.z], dtype=np.float64)

    @classmethod
    def from_numpy(cls, values: Any) -> "Vector3":
        """From any 3-element sequence or array ``[x, y, z]``."""
        x, y, z = _vector(values, 3, "a Vector3")
        return cls(x=float(x), y=float(y), z=float(z))


class Quaternion(BaseModel):
    """A rotation as a unit quaternion with named components (w is the real part). Validated and
    normalized: any non-zero, finite quaternion is scaled to unit length; zero is rejected."""
    model_config = ConfigDict(extra="forbid")
    w: float = Field(title="W", description="The real (scalar) part.")
    x: float = Field(title="X")
    y: float = Field(title="Y")
    z: float = Field(title="Z")

    @model_validator(mode="after")
    def _normalize(self) -> "Quaternion":
        values = [_finite(getattr(self, name), name) for name in ("w", "x", "y", "z")]
        norm = math.sqrt(sum(v * v for v in values))
        if norm < 1e-9:
            raise ValueError("a quaternion must not be zero (it has no rotation); the identity is w=1, x=y=z=0")
        if abs(norm - 1.0) > 1e-15:
            self.w, self.x, self.y, self.z = (v / norm for v in values)
        return self

    # ---- construction ---------------------------------------------------------------------------

    @classmethod
    def identity(cls) -> "Quaternion":
        return cls(w=1.0, x=0.0, y=0.0, z=0.0)

    @classmethod
    def from_wxyz(cls, values: Any) -> "Quaternion":
        """From ``[w, x, y, z]`` (USD, Isaac Sim, cuMotion, Isaac Lab order)."""
        w, x, y, z = _vector(values, 4, "a wxyz quaternion")
        return cls(w=float(w), x=float(x), y=float(y), z=float(z))

    @classmethod
    def from_xyzw(cls, values: Any) -> "Quaternion":
        """From ``[x, y, z, w]`` (Warp, SciPy, ROS order)."""
        x, y, z, w = _vector(values, 4, "an xyzw quaternion")
        return cls(w=float(w), x=float(x), y=float(y), z=float(z))

    @classmethod
    def from_axis_angle(cls, axis: Any, angle: float) -> "Quaternion":
        """A rotation of ``angle`` radians about ``axis`` (any non-zero length)."""
        axis = _vector(axis, 3, "an axis")
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            raise ValueError("the rotation axis must not be zero")
        s = math.sin(angle / 2.0) / norm
        return cls(w=math.cos(angle / 2.0), x=float(axis[0] * s), y=float(axis[1] * s), z=float(axis[2] * s))

    @classmethod
    def from_rotvec(cls, rotvec: Any) -> "Quaternion":
        """From a rotation vector (axis × angle in radians; Universal Robots' pose orientation)."""
        rotvec = _vector(rotvec, 3, "a rotation vector")
        angle = float(np.linalg.norm(rotvec))
        if angle < 1e-12:
            return cls.identity()
        return cls.from_axis_angle(rotvec, angle)

    @classmethod
    def from_matrix(cls, matrix: Any) -> "Quaternion":
        """From a 3×3 rotation matrix (column vectors: ``p_parent = R @ p_child``)."""
        m = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
        trace = m[0, 0] + m[1, 1] + m[2, 2]
        if trace > 0:
            s = 2.0 * math.sqrt(trace + 1.0)
            w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
        elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
            s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
            w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
        elif m[1, 1] > m[2, 2]:
            s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
            w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
        else:
            s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
            w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
        return cls(w=w, x=x, y=y, z=z)

    @classmethod
    def from_gf(cls, quat: Any) -> "Quaternion":
        """From a USD ``Gf.Quatd`` / ``Gf.Quatf`` (real part first)."""
        imaginary = quat.GetImaginary()
        return cls(w=float(quat.GetReal()), x=float(imaginary[0]), y=float(imaginary[1]), z=float(imaginary[2]))

    # ---- conversion -------------------------------------------------------------------------------

    def to_wxyz(self) -> np.ndarray:
        """``[w, x, y, z]`` (float64): for USD, Isaac Sim, cuMotion, Isaac Lab."""
        return np.array([self.w, self.x, self.y, self.z], dtype=np.float64)

    def to_xyzw(self) -> np.ndarray:
        """``[x, y, z, w]`` (float64): for Warp, SciPy, ROS."""
        return np.array([self.x, self.y, self.z, self.w], dtype=np.float64)

    def to_rotvec(self) -> np.ndarray:
        """The rotation vector (axis × angle in radians, angle in [0, π])."""
        w, v = self.w, np.array([self.x, self.y, self.z], dtype=np.float64)
        if w < 0:
            w, v = -w, -v
        sin_half = float(np.linalg.norm(v))
        if sin_half < 1e-12:
            return np.zeros(3)
        angle = 2.0 * math.atan2(sin_half, w)
        return v / sin_half * angle

    def to_matrix(self) -> np.ndarray:
        """The 3×3 rotation matrix (column vectors: ``p_parent = R @ p_child``)."""
        w, x, y, z = self.w, self.x, self.y, self.z
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ], dtype=np.float64)

    def to_gf(self) -> Any:
        """A USD ``Gf.Quatd`` (needs ``pxr``)."""
        from pxr import Gf  # noqa: PLC0415 (pxr is optional: only inside Isaac Sim or with USD installed)
        return Gf.Quatd(self.w, self.x, self.y, self.z)

    # ---- algebra ----------------------------------------------------------------------------------

    def conjugate(self) -> "Quaternion":
        """The inverse rotation."""
        return Quaternion(w=self.w, x=-self.x, y=-self.y, z=-self.z)

    inverse = conjugate

    def multiply(self, other: "Quaternion") -> "Quaternion":
        """``self * other``: first rotate by ``other``, then by ``self``."""
        w1, x1, y1, z1 = self.w, self.x, self.y, self.z
        w2, x2, y2, z2 = other.w, other.x, other.y, other.z
        return Quaternion(
            w=w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            x=w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            y=w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            z=w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        )

    def __mul__(self, other: "Quaternion") -> "Quaternion":
        if not isinstance(other, Quaternion):
            return NotImplemented
        return self.multiply(other)

    def rotate(self, vector: Any) -> np.ndarray:
        """Rotate a 3D vector (a sequence, array, or :class:`Vector3`)."""
        v = vector.to_numpy() if isinstance(vector, Vector3) else _vector(vector, 3, "a vector")
        u = np.array([self.x, self.y, self.z], dtype=np.float64)
        return v + 2.0 * np.cross(u, np.cross(u, v) + self.w * v)

    def angle_to(self, other: "Quaternion") -> float:
        """The rotation angle (radians, in [0, π]) between this orientation and ``other``."""
        dot = abs(self.w * other.w + self.x * other.x + self.y * other.y + self.z * other.z)
        return 2.0 * math.acos(min(1.0, dot))


class Pose(BaseModel):
    """A position and an orientation: where a frame is, relative to a reference frame."""
    model_config = ConfigDict(extra="forbid")
    position: Vector3 = Field(title="Position", description="In meters.")
    orientation: Quaternion = Field(
        title="Orientation",
        description="A quaternion with named components (w is the real part); normalized when validated.",
    )

    @classmethod
    def from_numpy(cls, position: Any, orientation_wxyz: Any) -> "Pose":
        """From a position ``[x, y, z]`` and an orientation ``[w, x, y, z]``."""
        return cls(position=Vector3.from_numpy(position), orientation=Quaternion.from_wxyz(orientation_wxyz))

    @classmethod
    def identity(cls) -> "Pose":
        return cls(position=Vector3(x=0.0, y=0.0, z=0.0), orientation=Quaternion.identity())

    def to_numpy(self) -> Tuple[np.ndarray, np.ndarray]:
        """``(position [x, y, z], orientation [w, x, y, z])`` as float64 arrays."""
        return self.position.to_numpy(), self.orientation.to_wxyz()

    def compose(self, other: "Pose") -> "Pose":
        """``self * other``: ``other`` (expressed in this pose's frame) expressed in this pose's reference."""
        position = self.position.to_numpy() + self.orientation.rotate(other.position)
        return Pose(position=Vector3.from_numpy(position), orientation=self.orientation.multiply(other.orientation))

    def __mul__(self, other: "Pose") -> "Pose":
        if not isinstance(other, Pose):
            return NotImplemented
        return self.compose(other)

    def inverse(self) -> "Pose":
        conjugate = self.orientation.conjugate()
        return Pose(position=Vector3.from_numpy(-conjugate.rotate(self.position)), orientation=conjugate)

    def transform_point(self, point: Any) -> np.ndarray:
        """A point given in this pose's frame, expressed in its reference frame."""
        return self.position.to_numpy() + self.orientation.rotate(point)


class FramedPose(Pose):
    """A pose together with the name of the frame it is expressed in (like ROS ``PoseStamped``'s
    ``frame_id``), e.g. the robot's base frame."""
    frame: str = Field(title="Frame", description="The reference frame the pose is expressed in.")


def poses_to_arrays(poses: Sequence[Pose]) -> Tuple[np.ndarray, np.ndarray]:
    """Positions ``(N, 3)`` and wxyz orientations ``(N, 4)`` of a sequence of poses."""
    positions = np.array([[p.position.x, p.position.y, p.position.z] for p in poses], dtype=np.float64).reshape(-1, 3)
    orientations = np.array([[p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z] for p in poses],
                            dtype=np.float64).reshape(-1, 4)
    return positions, orientations

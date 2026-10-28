"""The ``MotionPlanner`` interface: collision-aware path planning for one robot (one end-effector combination),
used in process by the RobotAPI (obstacles from the scene) and by the kinematics service (library-neutral obstacles a
client sends, ADR-0010; never Isaac Sim's scene).

A planner *uses* a :class:`~.kinematics.KinematicsSolver` (the same robot description) and holds a **world**:
obstacle shapes (:class:`CuboidObstacle`, :class:`SphereObstacle`, :class:`CapsuleObstacle`), given in the
world frame (with the robot base's world pose) or in the robot base frame. The RobotAPI's ``plan`` move calls
:meth:`MotionPlanner.plan` for a geometric path (joint waypoints), times it like a chain of ``ptp`` moves, and
checks **every sample** with :meth:`MotionPlanner.check_path` before anything is queued.

A backend implements :meth:`MotionPlanner._set_world`, :meth:`MotionPlanner._plan`, and
:meth:`MotionPlanner._collisions`; the cuMotion one is ``amt.dtp.ise.robot.planning.CuMotionMotionPlanner``
(extension), whose world can be read from the USD stage's colliders. Units are SI (m, rad).

Pure Python (numpy, pydantic). Design: ``docs/src/Agents/Specs/robot-control.md`` ("The MotionPlanner").
"""
import threading
from abc import ABC, abstractmethod
from typing import Annotated, List, Literal, Optional, Sequence, Tuple, Union

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..transducer.models.pose import Pose, Vector3
from .kinematics import KinematicsSolver


class PlanningError(Exception):
    """A plan can't be made: ``code`` is ``PLAN_FAILED``, ``START_IN_COLLISION``, ``GOAL_IN_COLLISION``,
    ``PATH_IN_COLLISION``, or ``IK_UNREACHABLE``."""

    def __init__(self, code: str, message: str, step: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message
        self.step = step


# ---- obstacles -----------------------------------------------------------------------------------------

class ObstacleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(title="Name", description="Unique within the world (e.g. the USD prim path).")
    pose: Pose = Field(title="Pose", description="The shape's center and orientation (meters).")
    enabled: bool = Field(True, title="Enabled")


class CuboidObstacle(ObstacleBase):
    """A box centered on its pose."""
    type: Literal["cuboid"] = Field("cuboid", title="Type")
    size: Vector3 = Field(title="Size", description="Side lengths along the pose's x, y, z axes (meters).")


class SphereObstacle(ObstacleBase):
    type: Literal["sphere"] = Field("sphere", title="Type")
    radius: float = Field(gt=0, title="Radius", description="Meters.")


class CapsuleObstacle(ObstacleBase):
    """A capsule along the pose's z axis: a cylinder of ``height`` with hemispherical caps."""
    type: Literal["capsule"] = Field("capsule", title="Type")
    radius: float = Field(gt=0, title="Radius", description="Meters.")
    height: float = Field(ge=0, title="Height", description="The cylinder's length, without the caps (meters).")


class CylinderObstacle(ObstacleBase):
    """A cylinder along the pose's z axis. Backends without cylinders may cover it conservatively (cuMotion: a
    capsule of the same radius and height, which also covers the caps' extra length)."""
    type: Literal["cylinder"] = Field("cylinder", title="Type")
    radius: float = Field(gt=0, title="Radius", description="Meters.")
    height: float = Field(gt=0, title="Height", description="Meters.")


class MeshObstacle(ObstacleBase):
    """A triangle mesh in the pose's frame (meters). Backends without meshes approximate it (cuMotion: collision
    spheres generated from the surface)."""
    type: Literal["mesh"] = Field("mesh", title="Type")
    vertices: List[Vector3] = Field(min_length=3, title="Vertices", description="Meters, in the pose's frame.")
    triangles: List[Tuple[int, int, int]] = Field(min_length=1, title="Triangles",
                                                  description="Vertex indices, counterclockwise seen from outside.")

    @model_validator(mode="after")
    def _indices(self) -> "MeshObstacle":
        n = len(self.vertices)
        if any(not 0 <= i < n for t in self.triangles for i in t):
            raise ValueError(f"a triangle refers to a vertex index outside 0..{n - 1}")
        return self


Obstacle = Annotated[Union[CuboidObstacle, SphereObstacle, CapsuleObstacle, CylinderObstacle, MeshObstacle],
                     Field(discriminator="type")]
"""A library-neutral obstacle: the primitives of ROS ``shape_msgs/SolidPrimitive`` (box, sphere, cylinder) plus a
capsule (cuMotion, cuRobo) and a triangle mesh (``shape_msgs/Mesh``), each with a pose."""


class PlanningWorld(BaseModel):
    """A planner's world: obstacles and the frame they are given in."""
    model_config = ConfigDict(extra="forbid")
    reference: Literal["world", "base"] = Field(
        "world", title="Reference", description="world: poses in the world frame (needs the robot base's world pose); "
                                                "base: poses in the robot base frame.")
    obstacles: List[Obstacle] = Field(default_factory=list, title="Obstacles")
    base_pose: Optional[Pose] = Field(
        None, title="Base Pose", description="With reference world: the robot base's pose in the world frame.")

    @model_validator(mode="after")
    def _base_pose_for_world(self) -> "PlanningWorld":
        if self.reference == "world" and self.base_pose is None and self.obstacles:
            raise ValueError("obstacles given in the world frame need the robot's base_pose (or use reference base)")
        names = [o.name for o in self.obstacles]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"obstacle names must be unique: {duplicates}")
        return self


def obstacles_in_base(obstacles: Sequence[ObstacleBase], base_pose: Optional[Pose]) -> List[ObstacleBase]:
    """The obstacles with their poses re-expressed in the robot base frame (``base_pose``: the base in the
    world; None: they already are)."""
    if base_pose is None:
        return list(obstacles)
    inverse = base_pose.inverse()
    return [o.model_copy(update={"pose": inverse.compose(o.pose)}) for o in obstacles]


# ---- the planner -----------------------------------------------------------------------------------------

class MotionPlanner(ABC):
    """Collision-aware planning for one robot, in SI units, against a world of obstacles.

    Thread-safe: every public method holds :attr:`lock` (the RobotAPI also holds the robot's solver lock while
    planning, since a planner shares its solver)."""

    backend: str = "unknown"

    def __init__(self, solver: KinematicsSolver):
        if not isinstance(solver, KinematicsSolver):
            raise TypeError("solver must be a KinematicsSolver")
        self._solver = solver
        self.lock = threading.RLock()
        self._obstacles: List[ObstacleBase] = []
        self._base_pose: Optional[Pose] = None
        self.revision = 0

    @property
    def solver(self) -> KinematicsSolver:
        return self._solver

    @property
    def obstacles(self) -> List[ObstacleBase]:
        """The obstacles as last set (in their own reference)."""
        with self.lock:
            return list(self._obstacles)

    @property
    def base_pose(self) -> Optional[Pose]:
        return self._base_pose

    def set_obstacles(self, obstacles: Sequence[ObstacleBase], base_pose: Optional[Pose] = None) -> None:
        """Replace the world: ``obstacles`` in the world frame with the robot base at ``base_pose``, or in the
        base frame if ``base_pose`` is None. Names must be unique."""
        names = [o.name for o in obstacles]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"obstacle names must be unique: {duplicates}")
        with self.lock:
            self._set_world(obstacles_in_base(obstacles, base_pose))
            self._obstacles = list(obstacles)
            self._base_pose = base_pose
            self.revision += 1

    def set_world(self, world: PlanningWorld, base_pose: Optional[Pose] = None) -> None:
        """Replace the world with ``world``'s obstacles (``base_pose``: the base in the world frame; default the
        world's own ``base_pose``)."""
        base_pose = base_pose if base_pose is not None else world.base_pose
        if world.reference == "world" and base_pose is None and world.obstacles:
            raise ValueError("a world-referenced planning world needs the robot base's world pose")
        self.set_obstacles(world.obstacles, base_pose if world.reference == "world" else None)

    def plan(self, start: Sequence[float], goal: Optional[Sequence[float]] = None,
             target: Optional[Tuple[np.ndarray, np.ndarray]] = None, frame: Optional[str] = None,
             attempt: int = 0) -> np.ndarray:
        """A collision-free path from ``start`` (SI joints) to the joint ``goal`` or to the tool-frame ``target``
        (``(position (3,), wxyz (4,))`` in the base frame; ``frame``: the tool frame, default the solver's): the
        waypoints ``(M, n)``, the first being ``start`` and the last the goal. ``attempt`` > 0 asks for a
        different plan (e.g. another random seed). Raises :class:`PlanningError`."""
        if (goal is None) == (target is None):
            raise ValueError("give exactly one of goal and target")
        start = np.asarray(start, dtype=np.float64).reshape(-1)
        with self.lock:
            bad = self._collisions(start.reshape(1, -1))
            if bad[0]:
                raise PlanningError("START_IN_COLLISION", f"the start configuration is in collision ({bad[0]})", step=0)
            if goal is not None:
                goal = np.asarray(goal, dtype=np.float64).reshape(-1)
                bad = self._collisions(goal.reshape(1, -1))
                if bad[0]:
                    raise PlanningError("GOAL_IN_COLLISION", f"the goal configuration is in collision ({bad[0]})")
            frame = self._solver.check_frame(frame)
            path = np.asarray(self._plan(start, goal, target, frame, attempt), dtype=np.float64)
        if path.ndim != 2 or path.shape[0] < 2 or path.shape[1] != self._solver.num_joints:
            raise PlanningError("PLAN_FAILED", f"the {self.backend} planner returned an invalid path of shape {path.shape}")
        path[0] = start
        return path

    def collisions(self, q: Sequence) -> List[str]:
        """For each configuration ``(N, n)``: an empty string if collision-free, else what collides (obstacles
        and/or self-collision)."""
        with self.lock:
            return self._collisions(self._solver.as_joint_array(q))

    def check_path(self, q: Sequence) -> Optional[Tuple[int, str]]:
        """The first configuration of ``q`` ``(N, n)`` in collision, as ``(index, what)``, or None."""
        for index, what in enumerate(self.collisions(q)):
            if what:
                return index, what
        return None

    @property
    def description(self) -> dict:
        """What the planner knows, for ``GET /api/robot/info``."""
        with self.lock:
            return {"backend": self.backend, "obstacles": [o.name for o in self._obstacles], "revision": self.revision}

    # ---- backend ------------------------------------------------------------------------------------

    @abstractmethod
    def _set_world(self, obstacles: List[ObstacleBase]) -> None:
        """Replace the backend's world with ``obstacles`` (poses in the robot base frame)."""

    @abstractmethod
    def _plan(self, start: np.ndarray, goal: Optional[np.ndarray], target: Optional[Tuple[np.ndarray, np.ndarray]],
              frame: str, attempt: int) -> np.ndarray:
        """Waypoints ``(M, n)`` from ``start`` to ``goal`` (joints) or ``target`` (a pose of ``frame``); raise
        :class:`PlanningError` (``PLAN_FAILED``) if none was found."""

    @abstractmethod
    def _collisions(self, q: np.ndarray) -> List[str]:
        """See :meth:`collisions` (``q`` is ``(N, n)``)."""

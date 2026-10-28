"""``CuMotionMotionPlanner``: the cuMotion backend of the ``MotionPlanner`` interface, and
``stage_obstacles``: library-neutral obstacles read from a USD stage's colliders.

- **Planner:** cuMotion's graph-based ``MotionPlanner`` (RRT variants; ``plan_to_cspace_target``, and JtRRT for
  ``plan_to_pose_target``), built from the solver's robot description and a cuMotion ``World`` whose obstacles are
  the neutral shapes (in the robot base frame, as cuMotion plans relative to the base). Each attempt uses another
  random ``seed``. Probe (UR10, a 15 cm cube): plans in ≈ 1–6 ms, and its own straight segments can graze
  an obstacle by under a millimeter, which is why the RobotAPI checks every timed sample (and re-plans).
- **Collisions:** a ``RobotWorldInspector`` with the world view: the XRDF's ``world_collision`` spheres against the
  obstacles (``in_collision_with_obstacle``) and its ``self_collision`` spheres (``frames_in_self_collision``).
- **Shapes:** cuboid → ``CUBOID`` (side lengths), sphere → ``SPHERE``, capsule → ``CAPSULE`` (along z), cylinder → the
  capsule of the same radius and height (conservative: it also covers the caps), mesh → spheres from
  ``cumotion.generate_collision_spheres`` (as Isaac Sim's own ``CumotionWorldInterface.add_triangulated_meshes``).
- **Concurrency:** every cuMotion call holds :data:`~.kinematics.CUMOTION_LOCK`; the probe found that ``plan_to_*``
  **releases the GIL** (a Python thread kept running during planning), so the lock is what keeps cuMotion calls from
  overlapping; a plan holds it for its few milliseconds.
- **USD:** :func:`stage_obstacles` reads ``UsdGeom`` Cube, Sphere, Capsule, Cylinder, and Mesh prims that have
  ``UsdPhysics.CollisionAPI`` (or every gprim under the given roots), in the world frame and meters
  (``metersPerUnit``), skipping prims under ``exclude`` (e.g. the robot itself). Read the stage on the simulation
  thread (e.g. in ``on_play``) and pass the result to ``planner.set_obstacles(obstacles, base_pose)``.

Design: ``docs/src/Agents/Specs/robot-control.md`` ("The MotionPlanner").
"""
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import numpy as np

import cumotion

from ..lib.control.motion.planning import (
    CapsuleObstacle,
    CuboidObstacle,
    CylinderObstacle,
    MeshObstacle,
    MotionPlanner,
    ObstacleBase,
    PlanningError,
    SphereObstacle,
)
from ..lib.control.transducer.models.pose import Pose, Quaternion, Vector3
from .kinematics import CUMOTION_LOCK, CuMotionKinematicsSolver


def _pose3(pose: Pose) -> "cumotion.Pose3":
    p, o = pose.to_numpy()
    return cumotion.Pose3(rotation=cumotion.Rotation3(*[float(v) for v in o]), translation=np.asarray(p, dtype=np.float64))


class CuMotionMotionPlanner(MotionPlanner):
    """Collision-aware planning with cuMotion for a :class:`CuMotionKinematicsSolver`.

    :param solver: The robot's solver (its current description is used; the planner rebuilds after frame changes).
    :param tool_frame: The planner's tool frame (default: the solver's default tool frame).
    :param config: cuMotion ``MotionPlannerConfig`` parameters to set, e.g. ``{"max_iterations": 20000}``.
    :param mesh_max_overshoot: ``generate_collision_spheres``' ``max_overshoot`` (m) for mesh obstacles.
    """

    backend = "cumotion"

    def __init__(self, solver: CuMotionKinematicsSolver, tool_frame: Optional[str] = None,
                 config: Optional[dict] = None, mesh_max_overshoot: float = 0.02):
        super().__init__(solver)
        self._tool_frame = tool_frame
        self._config = dict(config or {})
        self._overshoot = float(mesh_max_overshoot)
        self._base_obstacles: List[ObstacleBase] = []
        self._built_for: Optional[Tuple[int, int]] = None  # (solver revision, world revision)
        self._world = self._view = self._inspector = self._planner_config = None
        self._world_revision = 0

    # ---- the cuMotion world -----------------------------------------------------------------------------

    def _set_world(self, obstacles: List[ObstacleBase]) -> None:
        self._base_obstacles = [o for o in obstacles if o.enabled]
        self._world_revision += 1
        self._built_for = None

    def _add(self, world: Any, obstacle: ObstacleBase) -> None:
        Type, Attribute = cumotion.Obstacle.Type, cumotion.Obstacle.Attribute
        if isinstance(obstacle, CuboidObstacle):
            shape = cumotion.create_obstacle(Type.CUBOID)
            shape.set_attribute(Attribute.SIDE_LENGTHS, obstacle.size.to_numpy())
        elif isinstance(obstacle, SphereObstacle):
            shape = cumotion.create_obstacle(Type.SPHERE)
            shape.set_attribute(Attribute.RADIUS, float(obstacle.radius))
        elif isinstance(obstacle, (CapsuleObstacle, CylinderObstacle)):
            shape = cumotion.create_obstacle(Type.CAPSULE)
            shape.set_attribute(Attribute.RADIUS, float(obstacle.radius))
            shape.set_attribute(Attribute.HEIGHT, float(obstacle.height))
        elif isinstance(obstacle, MeshObstacle):
            vertices = [np.array([v.x, v.y, v.z], dtype=np.float64) for v in obstacle.vertices]
            low, high = np.min(vertices, axis=0), np.max(vertices, axis=0)
            if (high - low).min() < 1e-4:
                # cuMotion's sphere generation needs a volume (probe: a flat plate fails its grid check): cover a flat
                # mesh with a thin box over its bounds instead.
                size = np.maximum(high - low, 2e-3)
                center = obstacle.pose.transform_point((low + high) / 2)
                box = CuboidObstacle(name=obstacle.name, pose=Pose(position=Vector3.from_numpy(center),
                                                                   orientation=obstacle.pose.orientation),
                                     size=Vector3.from_numpy(size))
                self._add(world, box)
                return
            triangles = [np.array(t, dtype=np.int32) for t in obstacle.triangles]
            spheres = cumotion.generate_collision_spheres(vertices, triangles, max_overshoot=self._overshoot)
            for sphere in spheres:
                ball = cumotion.create_obstacle(Type.SPHERE)
                ball.set_attribute(Attribute.RADIUS, float(sphere.radius))
                center = obstacle.pose.transform_point(np.asarray(sphere.center, dtype=np.float64))
                world.add_obstacle(ball, cumotion.Pose3(rotation=cumotion.Rotation3(1.0, 0.0, 0.0, 0.0),
                                                        translation=np.asarray(center, dtype=np.float64)))
            return
        else:
            raise ValueError(f"unsupported obstacle type {type(obstacle).__name__}")
        world.add_obstacle(shape, _pose3(obstacle.pose))

    def _build(self) -> None:
        key = (self.solver.revision, self._world_revision)
        if self._built_for == key:
            return
        solver: CuMotionKinematicsSolver = self.solver  # type: ignore[assignment]
        frame = solver.check_frame(self._tool_frame)
        with CUMOTION_LOCK:
            world = cumotion.create_world()
            for obstacle in self._base_obstacles:
                self._add(world, obstacle)
            view = world.add_world_view()
            inspector = solver.robot_world_inspector(view)
            config = cumotion.create_default_motion_planner_config(solver.robot_description, frame, view)
            for name, value in self._config.items():
                config.set_param(name, cumotion.MotionPlannerConfig.ParamValue(value))
        self._world, self._view, self._inspector, self._planner_config = world, view, inspector, config
        self._planner_frame = frame
        self._built_for = key

    # ---- planning ------------------------------------------------------------------------------------------

    def _plan(self, start: np.ndarray, goal: Optional[np.ndarray], target: Optional[Tuple[np.ndarray, np.ndarray]],
              frame: str, attempt: int) -> np.ndarray:
        if frame != (self._tool_frame or self.solver.default_tool_frame):
            self._tool_frame = frame
            self._built_for = None
        self._build()
        with CUMOTION_LOCK:
            self._planner_config.set_param("seed", cumotion.MotionPlannerConfig.ParamValue(123456 + 7919 * attempt))
            planner = cumotion.create_motion_planner(self._planner_config)
            if goal is not None:
                result = planner.plan_to_cspace_target(np.ascontiguousarray(start), np.ascontiguousarray(goal))
            else:
                w, x, y, z = (float(v) for v in target[1])
                pose = cumotion.Pose3(rotation=cumotion.Rotation3(w, x, y, z), translation=np.asarray(target[0], dtype=np.float64))
                result = planner.plan_to_pose_target(np.ascontiguousarray(start), pose)
            found = bool(result.path_found)
            path = [np.asarray(q, dtype=np.float64).reshape(-1) for q in result.path] if found else []
        if not found:
            raise PlanningError("PLAN_FAILED", f"cuMotion found no collision-free path (attempt {attempt + 1})")
        return np.vstack(path)

    def _collisions(self, q: np.ndarray) -> List[str]:
        self._build()
        result = []
        for row in q:
            row = np.ascontiguousarray(row, dtype=np.float64)
            with CUMOTION_LOCK:
                world = bool(self._inspector.in_collision_with_obstacle(row)) if self._base_obstacles else False
                pairs = self._inspector.frames_in_self_collision(row)
            parts = (["obstacle"] if world else []) + [f"self {a}-{b}" for a, b in pairs]
            result.append(", ".join(parts))
        return result

    def min_distance(self, q: Sequence[float]) -> float:
        """The signed distance (m) between the robot's world-collision spheres and the obstacles at ``q``."""
        with self.lock:
            self._build()
            with CUMOTION_LOCK:
                return float(self._inspector.min_distance_to_obstacle(np.ascontiguousarray(q, dtype=np.float64)))


def cumotion_planner_factory(solver: CuMotionKinematicsSolver) -> CuMotionMotionPlanner:
    """The kinematics service's ``cumotion`` planner backend (an empty world until one is set through the API)."""
    return CuMotionMotionPlanner(solver)


# ---- obstacles from a USD stage --------------------------------------------------------------------------------

def stage_obstacles(stage: Any, roots: Optional[Iterable[str]] = None, exclude: Iterable[str] = (),
                    colliders_only: bool = True, time: Any = None) -> List[ObstacleBase]:
    """Library-neutral obstacles from a USD stage, in the world frame and meters: ``UsdGeom`` Cube, Sphere, Capsule,
    Cylinder, and Mesh prims under ``roots`` (default: the whole stage), with ``UsdPhysics.CollisionAPI`` unless
    ``colliders_only`` is False, except those under any path in ``exclude`` (e.g. the robot and what it carries).
    Each obstacle is named by its prim path. Non-uniformly scaled spheres, capsules, and cylinders are covered by their
    largest scale (conservative)."""
    from pxr import Usd, UsdGeom, UsdPhysics  # noqa: PLC0415

    meters = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    cache = UsdGeom.XformCache(time if time is not None else Usd.TimeCode.Default())
    excluded = [str(p).rstrip("/") for p in exclude]
    starts = [stage.GetPrimAtPath(r) for r in roots] if roots else [stage.GetPseudoRoot()]
    result: List[ObstacleBase] = []
    for start in starts:
        if not start or not start.IsValid():
            continue
        for prim in Usd.PrimRange(start):
            path = str(prim.GetPath())
            if any(path == e or path.startswith(e + "/") for e in excluded):
                continue
            if not prim.IsA(UsdGeom.Gprim):
                continue
            if colliders_only and not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            imageable = UsdGeom.Imageable(prim)
            if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
                continue
            matrix = cache.GetLocalToWorldTransform(prim)
            obstacle = _obstacle_from_prim(prim, path, matrix, meters)
            if obstacle is not None:
                result.append(obstacle)
    return result


def _decompose(matrix: Any, meters: float) -> Tuple[Pose, np.ndarray]:
    """A USD world matrix (row vectors) → (pose in meters, scale)."""
    m = np.array(matrix, dtype=np.float64)
    rotation_scale = m[:3, :3].T  # columns are the scaled axes
    scale = np.linalg.norm(rotation_scale, axis=0)
    rotation = rotation_scale / np.where(scale > 0, scale, 1.0)
    if np.linalg.det(rotation) < 0:
        rotation[:, 0] *= -1
        scale[0] *= -1
    position = m[3, :3] * meters
    return Pose(position=Vector3.from_numpy(position), orientation=Quaternion.from_matrix(rotation)), np.abs(scale)


_AXIS_TO_Z = {"X": Quaternion.from_axis_angle([0, 1, 0], np.pi / 2), "Y": Quaternion.from_axis_angle([1, 0, 0], -np.pi / 2),
              "Z": Quaternion.identity()}


def _obstacle_from_prim(prim: Any, path: str, matrix: Any, meters: float) -> Optional[ObstacleBase]:
    from pxr import UsdGeom  # noqa: PLC0415
    pose, scale = _decompose(matrix, meters)
    s = scale * meters
    if prim.IsA(UsdGeom.Cube):
        size = float(UsdGeom.Cube(prim).GetSizeAttr().Get() or 2.0)
        return CuboidObstacle(name=path, pose=pose, size=Vector3.from_numpy(size * s))
    if prim.IsA(UsdGeom.Sphere):
        radius = float(UsdGeom.Sphere(prim).GetRadiusAttr().Get() or 1.0)
        return SphereObstacle(name=path, pose=pose, radius=radius * float(s.max()))
    if prim.IsA(UsdGeom.Capsule) or prim.IsA(UsdGeom.Cylinder):
        schema = UsdGeom.Capsule(prim) if prim.IsA(UsdGeom.Capsule) else UsdGeom.Cylinder(prim)
        axis = str(schema.GetAxisAttr().Get() or "Z")
        index = "XYZ".index(axis)
        radial = [i for i in range(3) if i != index]
        radius = float(schema.GetRadiusAttr().Get() or 1.0) * float(s[radial].max())
        height = float(schema.GetHeightAttr().Get() or 2.0) * float(s[index])
        aligned = pose.compose(Pose(position=Vector3(x=0.0, y=0.0, z=0.0), orientation=_AXIS_TO_Z[axis]))
        model = CapsuleObstacle if prim.IsA(UsdGeom.Capsule) else CylinderObstacle
        return model(name=path, pose=aligned, radius=radius, height=height) if height > 0 else None
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            return None
        vertices = [Vector3.from_numpy(np.asarray(p, dtype=np.float64) * scale * meters) for p in points]
        triangles, offset = [], 0
        for count in counts:  # fan triangulation of each polygon
            face = list(indices[offset:offset + count])
            offset += count
            triangles += [(int(face[0]), int(face[k]), int(face[k + 1])) for k in range(1, count - 1)]
        return MeshObstacle(name=path, pose=pose, vertices=vertices, triangles=triangles) if triangles else None
    return None

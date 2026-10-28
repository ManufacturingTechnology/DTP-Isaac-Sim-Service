"""A fake robot for the motion/RobotAPI tests (no Isaac Sim, no cuMotion): a Cartesian gantry (X, Y, Z,
prismatic) carrying a Z-Y-X wrist (Yaw, Pitch, Roll, revolute). Its IK is analytic, with two wrist branches
and a real singularity at Pitch = ±90°, so the jump check has something to catch.

Also a plain-Python automation component whose ``joints`` transducer has one position-driven field per
joint (like ``ArticulationTransducer``), and which tracks its targets perfectly."""
import math
from typing import Dict, List, Optional, Tuple, Type

import numpy as np
from pydantic import BaseModel, Field, create_model

from amt.dtp.ise.lib.control.component import (
    AutomationComponentBase,
    ModelFieldSpec,
    build_container_model,
    constrained_subclass,
    dynamic_model_name,
)
from amt.dtp.ise.lib.control.motion.kinematics import JointInfo, KinematicsSolver
from amt.dtp.ise.lib.control.transducer.models import (
    ArticulationActuatorModel,
    ArticulationSensorModel,
    PrismaticJointPositionModel,
    PrismaticJointSensorModel,
    Quaternion,
    RevoluteJointPositionModel,
    RevoluteJointSensorModel,
)

JOINTS = ["X", "Y", "Z", "Yaw", "Pitch", "Roll"]


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class FakeSolver(KinematicsSolver):
    backend = "fake"

    def __init__(self, velocity: Optional[float] = 2.0, linear_velocity: Optional[float] = 0.5,
                 acceleration: Optional[float] = 10.0, linear_acceleration: Optional[float] = 2.0):
        super().__init__()
        self._joints = [JointInfo(n, "prismatic", -1.0, 1.0, linear_velocity, linear_acceleration) for n in JOINTS[:3]]
        self._joints += [JointInfo("Yaw", "revolute", -2 * math.pi, 2 * math.pi, velocity, acceleration),
                         JointInfo("Pitch", "revolute", -math.pi / 2, math.pi / 2, velocity, acceleration),
                         JointInfo("Roll", "revolute", -2 * math.pi, 2 * math.pi, velocity, acceleration)]
        self._frames: Dict[str, Tuple[str, np.ndarray, np.ndarray]] = {}  # added frames: name -> (parent, pos, wxyz)
        self.ik_calls = 0

    @property
    def joints(self) -> List[JointInfo]:
        return list(self._joints)

    @property
    def base_frame(self) -> str:
        return "base"

    @property
    def frame_names(self) -> List[str]:
        return ["base", "wrist", "tool0", *self._frames]

    @property
    def tool_frames(self) -> List[str]:
        return ["tool0", *self._frames]

    def _set_frames(self, changes):
        frames = dict(self._frames)
        for name, change in changes.items():
            if change is None:
                frames.pop(name, None)
            else:
                parent, position, orientation = change
                if parent not in ["wrist", "tool0", *frames]:
                    raise ValueError(f"unknown parent {parent}")
                frames[name] = (parent, np.asarray(position, float), np.asarray(orientation, float))
        self._frames = frames

    def _frame_offset(self, frame: str) -> Tuple[np.ndarray, np.ndarray]:
        """The frame's pose relative to the wrist (tool0 = wrist)."""
        if frame in ("wrist", "tool0"):
            return np.zeros(3), np.eye(3)
        parent, position, orientation = self._frames[frame]
        p, r = self._frame_offset(parent)
        q = Quaternion.from_wxyz(orientation)
        return p + r @ position, r @ q.to_matrix()

    def _fk(self, q, frame):
        positions, orientations = [], []
        for row in q:
            x, y, z, yaw, pitch, roll = row
            r = _rz(yaw) @ _ry(pitch) @ _rx(roll)
            p = np.array([x, y, z])
            if frame == "base":
                positions.append(np.zeros(3)); orientations.append([1.0, 0, 0, 0]); continue
            offset_p, offset_r = self._frame_offset(frame)
            positions.append(p + r @ offset_p)
            orientations.append(Quaternion.from_matrix(r @ offset_r).to_wxyz())
        return np.array(positions), np.array(orientations)

    def _ik_one(self, position, orientation, frame, seed, position_tolerance, orientation_tolerance):
        self.ik_calls += 1
        offset_p, offset_r = self._frame_offset(frame)
        r_tool = Quaternion.from_wxyz(orientation).to_matrix()
        r = r_tool @ offset_r.T
        p = np.asarray(position) - r @ offset_p
        if np.any(np.abs(p) > 1.0):
            return False, None
        seed = np.zeros(6) if seed is None else np.asarray(seed)
        candidates = []
        sp = -r[2, 0]
        if abs(sp) > 1 - 1e-9:  # singular: yaw and roll are coupled; keep the seed's yaw
            pitch = math.copysign(math.pi / 2, sp)
            yaw = seed[3]
            # r = Rz(yaw) Ry(±90) Rx(roll): r[0,1] = cos(yaw) sin(±1) sin(roll) - sin(yaw) cos(roll)...
            m = _ry(pitch).T @ _rz(yaw).T @ r
            roll = math.atan2(m[2, 1], m[1, 1])
            candidates.append((yaw, pitch, roll))
        else:
            pitch = math.asin(sp)
            yaw = math.atan2(r[1, 0], r[0, 0])
            roll = math.atan2(r[2, 1], r[2, 2])
            candidates.append((yaw, pitch, roll))
            candidates.append((yaw + math.pi, math.pi - pitch, roll + math.pi))
        best = None
        for yaw, pitch, roll in candidates:
            angles = np.array([yaw, _wrap(pitch), roll])
            # nearest 2π-equivalent of yaw and roll to the seed
            for i in (0, 2):
                angles[i] = seed[3 + i] + _wrap(angles[i] - seed[3 + i])
            q = np.concatenate([p, angles])
            if not -math.pi / 2 - 1e-9 <= q[4] <= math.pi / 2 + 1e-9:
                continue  # the wrist can't reach this branch: the other one flips Yaw and Roll by 180 degrees
            cost = float(np.sum((q[3:] - seed[3:]) ** 2))
            if best is None or cost < best[0]:
                best = (cost, q)
        return (True, best[1]) if best is not None else (False, None)

    # Self-collision: one sphere at the base, one at the wrist (radius 0.1 m each); they collide when the wrist
    # comes within 0.2 m of the base origin.
    SPHERE_RADIUS = 0.1

    def _collision_spheres(self, q):
        wrist, _ = self._fk(q.reshape(1, -1), "wrist")
        return [("base", np.zeros(3), self.SPHERE_RADIUS), ("wrist", wrist[0], self.SPHERE_RADIUS)]

    def _self_collision(self, q):
        result = []
        for row in q:
            (_, c0, r0), (_, c1, r1) = self._collision_spheres(row)
            result.append([("base", "wrist")] if np.linalg.norm(c1 - c0) < r0 + r1 else [])
        return result


def joint_models(prefix: str = "Robot"):
    fields = {}
    for joint in JOINTS:
        prismatic = joint in ("X", "Y", "Z")
        limit = 1.0 if prismatic else 360.0
        model = constrained_subclass(dynamic_model_name(prefix, joint, "Commands"),
                                     PrismaticJointPositionModel if prismatic else RevoluteJointPositionModel,
                                     "position", ge=-limit, le=limit)
        fields[joint] = (Optional[model], None)
    commands = create_model(dynamic_model_name(prefix, "Commands"), __base__=ArticulationActuatorModel, **fields)
    actuals = create_model(dynamic_model_name(prefix, "Actuals"), __base__=ArticulationSensorModel, **{
        joint: (PrismaticJointSensorModel if joint in ("X", "Y", "Z") else RevoluteJointSensorModel, ...) for joint in JOINTS})
    return commands, actuals


class FakeRobot(AutomationComponentBase):
    """A robot component: transducer ``joints`` (position targets, degrees/meters) tracking perfectly."""

    def __init__(self, name: str = "Gantry Robot", lag: float = 0.0):
        self._name = name
        self.joint_commands, self.joint_actuals = joint_models()
        self._command_model = build_container_model("GantryRobotCommands", "Commands of the gantry robot", {
            "joints": ModelFieldSpec(self.joint_commands, "Joint position targets")})
        self._actual_model = build_container_model("GantryRobotActuals", "Actuals of the gantry robot", {
            "joints": ModelFieldSpec(self.joint_actuals, "Measured joints")})
        self.targets = {j: 0.0 for j in JOINTS}
        self.targets["Z"] = 0.5
        self.measured_offset = 0.0
        self.history: List[Dict[str, float]] = []

    name = property(lambda self: self._name)
    description = property(lambda self: "A fake gantry robot")
    version = property(lambda self: "0.1.0")
    actual_model = property(lambda self: self._actual_model)
    command_model = property(lambda self: self._command_model)

    def sensor_read(self) -> BaseModel:
        joint = dict(drive_mode="position", velocity=0.0, effort=0.0, forces=(0, 0, 0), torques=(0, 0, 0))
        values = {}
        for j in JOINTS:
            model = PrismaticJointSensorModel if j in ("X", "Y", "Z") else RevoluteJointSensorModel
            values[j] = model(position=self.targets[j] + self.measured_offset, **joint)
        return self._actual_model(joints=self.joint_actuals(**values))

    def actuator_read(self) -> BaseModel:
        fields = {j: typing_args(self.joint_commands.model_fields[j].annotation).model_construct(position=self.targets[j])
                  for j in JOINTS}
        return self._command_model(joints=self.joint_commands.model_construct(**fields))

    def actuator_write(self, value: BaseModel) -> None:
        joints = value.joints
        if joints is None:
            return
        for j in JOINTS:
            command = getattr(joints, j)
            if command is not None:
                self.targets[j] = command.position
        self.history.append(dict(self.targets))


def typing_args(annotation):
    import typing
    for arg in typing.get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return annotation


# ---- a fake motion planner (the upper layer) -----------------------------------------------------------------

from amt.dtp.ise.lib.control.motion.planning import MotionPlanner, PlanningError  # noqa: E402


class FakePlanner(MotionPlanner):
    """Plans for the gantry: a straight joint-space line if the tool point stays clear of the obstacles, else a
    detour over the top (Z raised to 0.95 m). Obstacles: spheres and boxes (axis-aligned in their pose's frame),
    checked against the tool0 point (with a 1 cm margin) and the gantry's own self-collision. ``careless`` returns
    the straight line even when it collides (for the planner-grazing fallback)."""
    backend = "fake"

    def __init__(self, solver, careless: bool = False):
        super().__init__(solver)
        self._world = []
        self.careless = careless
        self.plans = 0

    def _set_world(self, obstacles):
        self._world = [o for o in obstacles if o.enabled]

    def _point_hits(self, point):
        hits = []
        for o in self._world:
            local = o.pose.inverse().transform_point(point)
            if o.type == "sphere" and np.linalg.norm(local) < o.radius + 0.01:
                hits.append(o.name)
            elif o.type == "cuboid" and np.all(np.abs(local) < np.array([o.size.x, o.size.y, o.size.z]) / 2 + 0.01):
                hits.append(o.name)
        return hits

    def _collisions(self, q):
        positions, _ = self.solver.fk(q, "tool0")
        own = self.solver.self_collision(q)
        result = []
        for p, pairs in zip(positions, own):
            what = self._point_hits(p)
            result.append(", ".join([f"obstacle {n}" for n in what] + [f"self {a}-{b}" for a, b in pairs]))
        return result

    def _clear(self, a, b):
        return not any(self._collisions(np.array([a + s * (b - a) for s in np.linspace(0, 1, 60)])))

    def _plan(self, start, goal, target, frame, attempt):
        self.plans += 1
        if goal is None:
            solution = self.solver.ik(target[0].reshape(1, 3), target[1].reshape(1, 4), frame, seed=start)
            if not solution.all_success:
                raise PlanningError("IK_UNREACHABLE", "the target is unreachable")
            goal = solution.q[0]
        if self.careless or self._clear(start, goal):
            return np.vstack([start, goal])
        up_a, up_b = start.copy(), goal.copy()
        up_a[2] = up_b[2] = 0.95
        path = np.vstack([start, up_a, up_b, goal])
        if all(self._clear(a, b) for a, b in zip(path[:-1], path[1:])):
            return path
        raise PlanningError("PLAN_FAILED", "no path found")

"""The RobotAPI's compiler: robot commands → segment command frames (ADR-0011, compile at write).

- :class:`RobotBinding` ties a robot id to its :class:`~..motion.kinematics.KinematicsSolver` (one per
  end-effector combination, ADR-0012; the active one is :attr:`RobotBinding.solver`), its optional
  :class:`~..motion.planning.MotionPlanner`, and the segment component and articulation transducer whose joints
  it drives, with the effective limits, the jump-check parameters, and the execution tolerances.
- :func:`compile_commands` samples each move at the physics step (``set``, ``ptp``, ``lin``, ``circ``, ``plan``),
  solves IK (continuity-seeded), checks limits and joint jumps, blends consecutive moves with termination
  ``cnt``, and writes ``{component: {transducer: {field: {"position": v}}}}`` rows. Any failure raises
  :class:`RobotCompileError` and nothing is produced (all or nothing).
- :func:`predict_start`, :func:`read_joints`: the robot's commanded joints at the end of the queue (from
  the latest response frame plus the frames not yet reflected in it), and its commanded or measured joints
  in a response frame.

Pure Python (numpy, pydantic). Design: ``docs/src/Agents/Specs/robot-control.md`` ("The RobotAPI", "Execution
semantics", "The MotionPlanner").
"""
import copy
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
from pydantic import BaseModel

from ..component import id_problem, path_to_id
from ..motion.kinematics import (
    PLACEHOLDER_VELOCITY,
    KinematicsError,
    KinematicsSolver,
    from_wire,
    plausible_limit,
    to_wire,
)
from ..motion.planning import MotionPlanner, PlanningError
from ..motion.trajectory import (
    CartesianPath,
    DurationTooShort,
    InvalidCircle,
    TimeScaling,
    TooManySteps,
    blend_overlaps,
    cartesian_chain_samples,
    check_steps,
    joint_chain_samples,
    joint_time_scaling,
)
from ..transducer.models.pose import Pose
from .models import CircMove, CompiledCommand, LinMove, PlanMove, PtpMove, RobotError, RobotErrorCode, SetTcp


Tolerance = Union[float, Dict[str, float]]
BLEND_FACTORS = (1.0, 0.5, 0.0)  # overlaps tried, in turn, when a blended chain fails the jump check


class RobotCompileError(Exception):
    """A robot command can't be compiled; the whole batch is rejected. ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, command: Optional[int] = None, robot: Optional[str] = None,
                 step: Optional[int] = None, joint: Optional[str] = None, status: int = 422):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message
        self.command = command
        self.robot = robot
        self.step = step
        self.joint = joint
        self.status = status

    @property
    def error(self) -> RobotError:
        return RobotError(code=self.code, message=self.message, command=self.command, robot=self.robot,
                          step=self.step, joint=self.joint)

    @property
    def detail(self) -> Dict[str, Any]:
        """The body of the HTTP error: ``{code, message, command?, robot?, step?, joint?}``."""
        return self.error.model_dump(exclude_none=True)


def default_field(joint_name: str) -> str:
    """The command field of a joint, named the way ``ArticulationTransducer`` names it."""
    return joint_name if id_problem(joint_name) is None else path_to_id(joint_name)


@dataclass
class RobotBinding:
    """A robot served by a RobotApi.

    :param solver: Its kinematics solver (the active end-effector combination's, if there are several).
    :param component: The segment component id whose transducer drives the joints.
    :param transducer: The articulation transducer id in that component (position-driven joints).
    :param joint_fields: Solver joint name → command field (default: the joint name, as
        ``ArticulationTransducer`` names fields).
    :param velocity_limits: Overrides by joint name (deg/s or m/s); they win over the robot description.
    :param acceleration_limits: Overrides by joint name (deg/s² or m/s²).
    :param jerk_limits: Overrides by joint name (deg/s³ or m/s³); ``ptp`` and ``plan`` moves are jerk-limited
        when every moving joint has a jerk limit (from here or the robot description).
    :param jump_margin: The jump check allows ``velocity limit × physics step × jump_margin`` per step.
    :param max_step_revolute: Degrees per step for revolute joints without a usable velocity limit (the
        jump check's fallback, which also serves as their velocity limit for timing).
    :param max_step_prismatic: Meters per step, likewise for prismatic joints.
    :param tool_frame: The active tool frame (default: the solver's default tool frame).
    :param flange_frame: The frame a TCP set through the configuration is fixed to (default: the solver's
        default tool frame, e.g. ``tool0``).
    :param base_pose: Returns the robot base's pose in the world, for targets with ``reference="world"``
        (None: only base-referenced targets).
    :param path_tolerance: The largest |actual − commanded| per joint (deg or m; a number for every joint, or by
        joint name) while the robot's joints are commanded; beyond it the RobotAPI halts the segment
        (``PATH_TOLERANCE``). None: not monitored.
    :param goal_tolerance: Likewise after a motion: the actual joints must come within it of the last commanded
        joints within ``goal_time`` seconds (``GOAL_TOLERANCE``). None: not monitored.
    :param goal_time: Seconds allowed to settle within ``goal_tolerance``.
    :param start_tolerance: A compile for the robot while nothing queued moves it is refused (409
        ``START_TOLERANCE``) if its actual joints are farther than this from the commanded ones (e.g. after a
        collision). None: not checked.
    :param combinations: End-effector combinations by name, one solver each (built ahead of time, e.g. from merged
        XRDF fragments); ``solver`` is one of them (or is added as ``default``). All share the joints.
    :param combination: The active combination's name (default: ``solver``'s).
    :param planner: The motion planner for ``plan`` moves: one (used while its solver is active) or one per
        combination name.
    """
    solver: KinematicsSolver
    component: str
    transducer: str = "joints"
    joint_fields: Optional[Dict[str, str]] = None
    velocity_limits: Optional[Dict[str, float]] = None
    acceleration_limits: Optional[Dict[str, float]] = None
    jump_margin: float = 1.0
    max_step_revolute: float = 5.0
    max_step_prismatic: float = 0.01
    tool_frame: Optional[str] = None
    flange_frame: Optional[str] = None
    base_pose: Optional[Callable[[], Pose]] = None
    jerk_limits: Optional[Dict[str, float]] = None
    path_tolerance: Optional[Tolerance] = None
    goal_tolerance: Optional[Tolerance] = None
    goal_time: float = 1.0
    start_tolerance: Optional[Tolerance] = None
    combinations: Optional[Dict[str, KinematicsSolver]] = None
    combination: Optional[str] = None
    planner: Optional[Union[MotionPlanner, Dict[str, MotionPlanner]]] = None
    tcp_offset: Optional[Pose] = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.combinations is not None or self.combination is not None:
            combinations = dict(self.combinations or {})
            if self.combination is not None:
                if self.combination not in combinations:
                    raise ValueError(f"robot binding: unknown combination '{self.combination}'; combinations: "
                                     f"{sorted(combinations)}")
                self.solver = combinations[self.combination]
            else:
                name = next((n for n, s in combinations.items() if s is self.solver), None)
                if name is None:
                    name = "default"
                    if name in combinations:
                        raise ValueError("robot binding: the solver is not one of the combinations, and 'default' is taken")
                    combinations[name] = self.solver
                self.combination = name
            for name, solver in combinations.items():
                if not isinstance(solver, KinematicsSolver):
                    raise TypeError(f"robot binding: combination '{name}' is not a KinematicsSolver")
                if solver.joint_names != self.solver.joint_names:
                    raise ValueError(f"robot binding: combination '{name}' has joints {solver.joint_names}, not "
                                     f"{self.solver.joint_names}")
            self.combinations = combinations
        if not isinstance(self.solver, KinematicsSolver):
            raise TypeError("solver must be a KinematicsSolver")
        names = self.solver.joint_names
        fields = dict(self.joint_fields or {})
        unknown = [n for n in list(fields) + list(self.velocity_limits or {}) + list(self.acceleration_limits or {})
                   + list(self.jerk_limits or {}) + _tolerance_names(self.path_tolerance)
                   + _tolerance_names(self.goal_tolerance) + _tolerance_names(self.start_tolerance) if n not in names]
        if unknown:
            raise ValueError(f"robot binding: unknown joint name(s) {sorted(set(unknown))}; joints: {names}")
        self.joint_fields = {n: fields.get(n, default_field(n)) for n in names}
        for name, value in (list((self.velocity_limits or {}).items()) + list((self.acceleration_limits or {}).items())
                            + list((self.jerk_limits or {}).items())):
            if not (isinstance(value, (int, float)) and value > 0 and math.isfinite(value)):
                raise ValueError(f"robot binding: the limit of '{name}' must be a positive number, not {value!r}")
        for label in ("path_tolerance", "goal_tolerance", "start_tolerance"):
            spec = getattr(self, label)
            values = list(spec.values()) if isinstance(spec, dict) else ([] if spec is None else [spec])
            if any(not (isinstance(v, (int, float)) and v > 0 and math.isfinite(v)) for v in values):
                raise ValueError(f"robot binding: {label} values must be positive numbers")
        if not self.goal_time > 0:
            raise ValueError("goal_time must be positive")
        if not self.jump_margin > 0:
            raise ValueError("jump_margin must be positive")
        if not (self.max_step_revolute > 0 and self.max_step_prismatic > 0):
            raise ValueError("max_step_revolute and max_step_prismatic must be positive")
        if isinstance(self.planner, dict):
            unknown = sorted(set(self.planner) - set(self.combinations or {}))
            if unknown:
                raise ValueError(f"robot binding: planners for unknown combination(s) {unknown}")
        self.tool_frame = self.solver.check_frame(self.tool_frame)
        self.flange_frame = self.solver.check_frame(self.flange_frame) if self.flange_frame else self.solver.default_tool_frame

    # ---- combinations and the planner -----------------------------------------------------------------

    def select_combination(self, name: str) -> None:
        """Make ``name`` the active end-effector combination: its solver, its planner, and its default tool frame
        (its active TCP). A TCP offset set before is dropped (it belonged to the previous solver)."""
        if not self.combinations or name not in self.combinations:
            raise KinematicsError(RobotErrorCode.UNKNOWN_COMBINATION, f"unknown combination '{name}'; combinations: "
                                  f"{sorted(self.combinations or {})}")
        self.solver = self.combinations[name]
        self.combination = name
        self.tool_frame = self.solver.default_tool_frame
        self.flange_frame = self.solver.default_tool_frame
        self.tcp_offset = None

    def active_planner(self) -> Optional[MotionPlanner]:
        """The planner for the active combination (a single planner counts only while its solver is active)."""
        if isinstance(self.planner, dict):
            return self.planner.get(self.combination) if self.combination is not None else None
        if self.planner is not None and self.planner.solver is self.solver:
            return self.planner
        return None

    # ---- limits (SI) --------------------------------------------------------------------------------------

    def _fallback_step(self, i: int) -> float:
        joint = self.solver.joints[i]
        return math.radians(self.max_step_revolute) if joint.type == "revolute" else self.max_step_prismatic

    def velocity_limit(self, i: int) -> Tuple[Optional[float], str]:
        """The effective velocity limit of joint ``i`` (SI) and its source; None with ``fallback``."""
        joint = self.solver.joints[i]
        if self.velocity_limits and joint.name in self.velocity_limits:
            return from_wire(joint, self.velocity_limits[joint.name]), "override"
        limit = plausible_limit(joint.velocity, PLACEHOLDER_VELOCITY)
        if limit is not None:
            return limit, "urdf"
        return None, "fallback"

    def acceleration_limit(self, i: int) -> Optional[float]:
        joint = self.solver.joints[i]
        if self.acceleration_limits and joint.name in self.acceleration_limits:
            return from_wire(joint, self.acceleration_limits[joint.name])
        return plausible_limit(joint.acceleration)

    def jerk_limit(self, i: int) -> Optional[float]:
        joint = self.solver.joints[i]
        if self.jerk_limits and joint.name in self.jerk_limits:
            return from_wire(joint, self.jerk_limits[joint.name])
        return plausible_limit(joint.jerk)

    def max_steps(self, dt: float) -> np.ndarray:
        """The jump check's largest change per step (SI), per joint."""
        result = []
        for i in range(self.solver.num_joints):
            limit, _ = self.velocity_limit(i)
            result.append(limit * dt * self.jump_margin if limit is not None else self._fallback_step(i))
        return np.array(result)

    def timing_limits(self, dt: float, velocity_scaling: float = 1.0, acceleration_scaling: float = 1.0
                      ) -> Tuple[List[float], List[Optional[float]]]:
        """Velocity (SI/s; fallback joints: max step / dt) and acceleration limits for timing, scaled."""
        velocities, accelerations = [], []
        for i in range(self.solver.num_joints):
            limit, _ = self.velocity_limit(i)
            velocities.append((limit if limit is not None else self._fallback_step(i) / dt) * velocity_scaling)
            acceleration = self.acceleration_limit(i)
            accelerations.append(acceleration * acceleration_scaling if acceleration is not None else None)
        return velocities, accelerations

    def jerk_limits_si(self, acceleration_scaling: float = 1.0) -> List[Optional[float]]:
        """Jerk limits (SI) for timing, scaled like accelerations (None: unknown)."""
        return [(j * acceleration_scaling if j is not None else None)
                for j in (self.jerk_limit(i) for i in range(self.solver.num_joints))]

    def tolerance(self, which: str) -> Optional[np.ndarray]:
        """``path``, ``goal``, or ``start`` tolerance per joint (SI; inf where a dict leaves a joint out), or None."""
        spec = getattr(self, f"{which}_tolerance")
        if spec is None:
            return None
        result = []
        for joint in self.solver.joints:
            value = spec.get(joint.name) if isinstance(spec, dict) else spec
            result.append(math.inf if value is None else from_wire(joint, value))
        return np.array(result)

    def tolerance_wire(self, which: str) -> Optional[Dict[str, float]]:
        spec = getattr(self, f"{which}_tolerance")
        if spec is None:
            return None
        return {j.name: float(spec[j.name] if isinstance(spec, dict) else spec)
                for j in self.solver.joints if not isinstance(spec, dict) or j.name in spec}


def _tolerance_names(spec: Optional[Tolerance]) -> List[str]:
    return list(spec) if isinstance(spec, dict) else []


# ---- reading joints from frames ---------------------------------------------------------------------------

def _attr(value: Any, name: str) -> Any:
    return None if value is None else getattr(value, name, None)


def read_joints(binding: RobotBinding, response_frame: BaseModel, actual: bool = False) -> Optional[np.ndarray]:
    """The robot's commanded (or, with ``actual``, measured) joints in a segment response frame, SI;
    None if any is missing (e.g. the read failed, or a joint isn't position-driven)."""
    state = _attr(_attr(response_frame, "components"), binding.component)
    return read_component_joints(binding, _attr(state, "actuals" if actual else "commands"))


def read_component_joints(binding: RobotBinding, values: Any) -> Optional[np.ndarray]:
    """The robot's joints (SI) in a component's actuals or commands model; None if any is missing."""
    transducer = _attr(values, binding.transducer)
    result = []
    for joint in binding.solver.joints:
        value = _attr(_attr(transducer, binding.joint_fields[joint.name]), "position")
        if value is None:
            return None
        result.append(from_wire(joint, value))
    return np.array(result)


def frame_addresses(binding: RobotBinding, frame: Optional[BaseModel]) -> bool:
    """Whether a command frame sets any of the robot's joints."""
    transducer = _attr(_attr(_attr(frame, "components"), binding.component), binding.transducer)
    if transducer is None:
        return False
    return any(_attr(_attr(transducer, binding.joint_fields[j.name]), "position") is not None for j in binding.solver.joints)


def fold_commands(binding: RobotBinding, q: np.ndarray, frames: Sequence[BaseModel]) -> np.ndarray:
    """``q`` (SI) updated with each command frame's joint targets for this robot, in order."""
    q = np.array(q, dtype=np.float64)
    for frame in frames:
        transducer = _attr(_attr(_attr(frame, "components"), binding.component), binding.transducer)
        if transducer is None:
            continue
        for i, joint in enumerate(binding.solver.joints):
            value = _attr(_attr(transducer, binding.joint_fields[joint.name]), "position")
            if value is not None:
                q[i] = from_wire(joint, value)
    return q


def predict_start(binding: RobotBinding, latest: Optional[BaseModel], pending: Sequence[BaseModel], robot: str) -> np.ndarray:
    """The robot's commanded joints at the end of the queue (SI): the latest response frame's command
    readback with the frames not yet reflected in it folded on top. Raises ``NO_STATE`` (503)."""
    if latest is None:
        raise RobotCompileError(RobotErrorCode.NO_STATE, "no simulation step has run yet (is the simulation playing?)",
                                robot=robot, status=503)
    q = read_joints(binding, latest)
    if q is None:
        raise RobotCompileError(
            RobotErrorCode.NO_STATE,
            f"the commanded joints of robot '{robot}' can't be read from component '{binding.component}', transducer "
            f"'{binding.transducer}' (the read failed, or a joint isn't position-driven)", robot=robot, status=503)
    return fold_commands(binding, q, pending)


def check_start_state(binding: RobotBinding, latest: Optional[BaseModel], pending: Sequence[BaseModel], robot: str) -> None:
    """The start-state check (MoveIt's ``allowed_start_tolerance``): with a ``start_tolerance``, and nothing queued
    moving the robot, its actual joints must be within the tolerance of the commanded ones. Raises 409
    ``START_TOLERANCE``."""
    tolerance = binding.tolerance("start")
    if tolerance is None or latest is None or any(frame_addresses(binding, f) for f in pending):
        return
    commanded, actual = read_joints(binding, latest), read_joints(binding, latest, actual=True)
    if commanded is None or actual is None:
        return
    excess = np.abs(actual - commanded) - tolerance
    if (excess > 1e-12).any():
        i = int(np.argmax(excess))
        joint = binding.solver.joints[i]
        unit = "deg" if joint.type == "revolute" else "m"
        raise RobotCompileError(
            RobotErrorCode.START_TOLERANCE,
            f"robot '{robot}': {joint.name} is at {to_wire(joint, actual[i]):.4g} {unit}, {to_wire(joint, abs(actual[i] - commanded[i])):.4g} "
            f"{unit} from its commanded {to_wire(joint, commanded[i]):.4g} (start tolerance "
            f"{to_wire(joint, tolerance[i]):.4g} {unit}); the robot isn't where it was told to be (a collision?)",
            robot=robot, joint=joint.name, status=409)


# ---- compiling one move ------------------------------------------------------------------------------------

@dataclass
class CompiledBatch:
    """Segment command frames (as dicts, in the segment's wire format) and what each command took."""
    frames: List[Dict[str, Any]]
    commands: List[CompiledCommand]
    end: Dict[str, np.ndarray]


@dataclass
class PlannedMove:
    """What a ``plan`` move compiled to (for ``/api/robot/process/plan``)."""
    samples: np.ndarray
    waypoints: np.ndarray
    smoothed: bool
    attempts: int


def _target_in_base(binding: RobotBinding, move: Any, command: Optional[int], robot: str, pose: Optional[Pose] = None) -> Pose:
    pose = move.target if pose is None else pose
    if move.reference == "base":
        return pose
    if binding.base_pose is None:
        raise RobotCompileError(RobotErrorCode.REFERENCE_UNAVAILABLE,
                                f"robot '{robot}' has no base pose provider, so targets must use reference 'base'",
                                command=command, robot=robot)
    return binding.base_pose().inverse().compose(pose)


def _via_in_base(binding: RobotBinding, move: CircMove, command: Optional[int], robot: str) -> np.ndarray:
    if move.reference == "base":
        return move.via.to_numpy()
    if binding.base_pose is None:
        _target_in_base(binding, move, command, robot)  # raises
    return binding.base_pose().inverse().transform_point(move.via.to_numpy())


def _solve(binding: RobotBinding, positions: np.ndarray, orientations: np.ndarray, frame: str, seed: np.ndarray,
           command: Optional[int], robot: str) -> np.ndarray:
    solution = binding.solver.ik(positions, orientations, frame, seed=seed, sequential=True)
    failed = solution.first_failure()
    if failed is not None:
        target = positions[failed]
        raise RobotCompileError(
            RobotErrorCode.IK_UNREACHABLE,
            f"robot '{robot}': no inverse kinematics solution for {frame} at step {failed} (position "
            f"{np.round(target, 4).tolist()} m; position error {solution.position_error[failed]:.4g} m, orientation error "
            f"{math.degrees(solution.orientation_error[failed]):.3g} deg)", command=command, robot=robot, step=failed)
    return solution.q


def _check_goal_limits(binding: RobotBinding, goal: np.ndarray, command: Optional[int], robot: str) -> None:
    solver = binding.solver
    violation = solver.limit_violation(goal.reshape(1, -1))
    if violation is not None:
        joint = solver.joints[violation[1]]
        raise RobotCompileError(
            RobotErrorCode.JOINT_LIMIT,
            f"robot '{robot}': the goal of {joint.name} ({to_wire(joint, goal[violation[1]]):.4g}) is outside its limits "
            f"[{to_wire(joint, joint.lower):.4g}, {to_wire(joint, joint.upper):.4g}]", command=command, robot=robot,
            joint=joint.name)


def _check_samples(binding: RobotBinding, samples: np.ndarray, start: np.ndarray, dt: float, command: Optional[int],
                   robot: str) -> None:
    """Joint limits and the jump check (ADR-0013) on compiled samples."""
    solver = binding.solver
    violation = solver.limit_violation(samples)
    if violation is not None:
        step, i = violation
        joint = solver.joints[i]
        raise RobotCompileError(
            RobotErrorCode.JOINT_LIMIT,
            f"robot '{robot}': {joint.name} would reach {to_wire(joint, samples[step, i]):.4g} at step {step}, outside "
            f"its limits [{to_wire(joint, joint.lower):.4g}, {to_wire(joint, joint.upper):.4g}]",
            command=command, robot=robot, step=step, joint=joint.name)
    jump = check_steps(start, samples, binding.max_steps(dt))
    if jump is not None:
        joint = solver.joints[jump.joint]
        unit = "deg" if joint.type == "revolute" else "m"
        raise RobotCompileError(
            RobotErrorCode.JOINT_JUMP,
            f"robot '{robot}': {joint.name} would change by {to_wire(joint, jump.change):.4g} {unit} in one step at step "
            f"{jump.step} (at most {to_wire(joint, jump.allowed):.4g} {unit} per {dt:.4g} s step); a singularity or an IK "
            f"branch change on the path, or the move is too fast: lower its speed or use ptp",
            command=command, robot=robot, step=jump.step, joint=joint.name)


def _ptp_goal(binding: RobotBinding, move: PtpMove, start: np.ndarray, frame: str, command: Optional[int],
              robot: str) -> np.ndarray:
    if move.joints is not None:
        goal = binding.solver.joints_from_wire(move.joints, base=start)
    else:
        p, o = _target_in_base(binding, move, command, robot).to_numpy()
        goal = _solve(binding, p.reshape(1, 3), o.reshape(1, 4), frame, start, command, robot)[0]
    _check_goal_limits(binding, goal, command, robot)
    return goal


def _joint_scaling(binding: RobotBinding, delta: np.ndarray, dt: float, velocity_scaling: float,
                   acceleration_scaling: float, duration: Optional[float], max_steps: Optional[int]) -> TimeScaling:
    velocities, accelerations = binding.timing_limits(dt, velocity_scaling, acceleration_scaling)
    return joint_time_scaling(delta, velocities, accelerations, dt, duration, max_steps,
                              binding.jerk_limits_si(acceleration_scaling))


def _cartesian_path(binding: RobotBinding, move: Any, start_position: np.ndarray, start_orientation: np.ndarray,
                    command: Optional[int], robot: str) -> CartesianPath:
    p1, o1 = _target_in_base(binding, move, command, robot).to_numpy()
    if isinstance(move, CircMove):
        return CartesianPath.arc(start_position, start_orientation, _via_in_base(binding, move, command, robot), p1, o1)
    return CartesianPath.line(start_position, start_orientation, p1, o1)


def _cartesian_scaling(path: CartesianPath, move: Any, dt: float, max_steps: Optional[int]) -> TimeScaling:
    return path.time_scaling(
        move.velocity, move.acceleration, math.radians(move.angular_velocity),
        math.radians(move.angular_acceleration) if move.angular_acceleration is not None else None, dt, move.duration,
        max_steps, move.jerk, math.radians(move.angular_jerk) if move.angular_jerk is not None else None)


def _translate(ex: Exception, command: Optional[int], robot: str) -> RobotCompileError:
    if isinstance(ex, RobotCompileError):
        return ex
    if isinstance(ex, KinematicsError):
        return RobotCompileError(ex.code, f"robot '{robot}': {ex.message}", command=command, robot=robot)
    if isinstance(ex, PlanningError):
        return RobotCompileError(ex.code, f"robot '{robot}': {ex.message}", command=command, robot=robot, step=ex.step)
    if isinstance(ex, (DurationTooShort, TooManySteps, InvalidCircle)):
        return RobotCompileError(ex.error_code, f"robot '{robot}': {ex}", command=command, robot=robot)
    raise ex


def compile_chain(binding: RobotBinding, moves: Sequence[Any], start: np.ndarray, dt: float, robot: str,
                  commands: Optional[Sequence[int]] = None, max_steps: Optional[int] = None
                  ) -> Tuple[np.ndarray, List[int], List[bool]]:
    """Consecutive moves of one robot, each blended into the next (all ``ptp``, or all ``lin``/``circ``):
    joint samples ``(N, n)``, the step each move starts at, and whether each blend was kept. Overlaps follow
    each move's ``cnt``; if the blended chain fails the jump check, the overlaps are halved, then dropped."""
    commands = list(commands) if commands is not None else [None] * len(moves)
    solver = binding.solver
    try:
        frame = solver.check_frame(moves[0].tool_frame or binding.tool_frame)
        cnt = [m.cnt / 100.0 for m in moves[:-1]]
        if all(isinstance(m, PtpMove) for m in moves):
            goals, scalings, current = [], [], start
            for move, command in zip(moves, commands):
                goal = _ptp_goal(binding, move, current, frame, command, robot)
                scalings.append(_joint_scaling(binding, goal - current, dt, move.velocity_scaling, move.acceleration_scaling,
                                               move.duration, max_steps))
                goals.append(goal)
                current = goal

            def sample(overlaps: List[int]) -> Tuple[np.ndarray, List[int]]:
                return joint_chain_samples(start, goals, scalings, overlaps)
        else:
            p, o = solver.fk(start, frame)
            paths, scalings = [], []
            position, orientation = p[0], o[0]
            for move, command in zip(moves, commands):
                path = _cartesian_path(binding, move, position, orientation, command, robot)
                scalings.append(_cartesian_scaling(path, move, dt, max_steps))
                paths.append(path)
                position, orientation = path.end_position, path.end_orientation

            def sample(overlaps: List[int]) -> Tuple[np.ndarray, List[int]]:
                positions, orientations, first = cartesian_chain_samples(paths, scalings, overlaps)
                return _solve(binding, positions, orientations, frame, start, commands[0], robot), first
    except Exception as ex:  # noqa: BLE001 (translated or re-raised)
        raise _translate(ex, commands[0], robot) from None

    for factor in BLEND_FACTORS:
        overlaps = blend_overlaps(scalings, [c * factor for c in cnt])
        try:
            samples, starts = sample(overlaps)
            _check_samples(binding, samples, start, dt, commands[0], robot)
        except RobotCompileError as ex:
            if ex.code in (RobotErrorCode.JOINT_JUMP, RobotErrorCode.IK_UNREACHABLE) and any(overlaps) and factor > 0:
                continue
            if ex.step is not None:  # name the move (command) the step belongs to, and the step within it
                starts = starts_of(scalings, overlaps)
                segment = max(i for i, s in enumerate(starts) if s <= ex.step)
                ex.step -= starts[segment]
                ex.command = commands[segment]
            raise
        return samples, starts, [o > 0 for o in overlaps]
    raise AssertionError("unreachable")


def starts_of(scalings: Sequence[TimeScaling], overlaps: Sequence[int]) -> List[int]:
    starts = [0]
    for m in range(1, len(scalings)):
        starts.append(starts[-1] + scalings[m - 1].steps - overlaps[m - 1])
    return starts


def plan_move(binding: RobotBinding, move: PlanMove, start: np.ndarray, dt: float, command: Optional[int], robot: str,
              max_steps: Optional[int] = None) -> PlannedMove:
    """A ``plan`` move: plan (the robot's :class:`MotionPlanner`), time the waypoints as a chain of synchronized
    ``ptp`` segments (blended through the corners if ``smooth``), and check limits, joint jumps, and collisions at
    every step; a blended chain that collides falls back to stopping at each corner, and a plan that collides is
    re-planned (``attempts``). Raises :class:`RobotCompileError` (``NO_PLANNER``, ``PATH_IN_COLLISION``, ...)."""
    planner = binding.active_planner()
    if planner is None:
        raise RobotCompileError(RobotErrorCode.NO_PLANNER, f"robot '{robot}' has no motion planner"
                                f"{' for combination ' + repr(binding.combination) if binding.combination else ''}",
                                command=command, robot=robot)
    solver = binding.solver
    last: Optional[RobotCompileError] = None
    try:
        frame = solver.check_frame(move.tool_frame or binding.tool_frame)
        goal = target = None
        if move.joints is not None:
            goal = solver.joints_from_wire(move.joints, base=start)
            _check_goal_limits(binding, goal, command, robot)
        else:
            target = _target_in_base(binding, move, command, robot).to_numpy()
        for attempt in range(move.attempts):
            with planner.lock:
                waypoints = planner.plan(start, goal=goal, target=target, frame=frame, attempt=attempt)
            # drop repeated waypoints (zero-length segments)
            keep = [0] + [i for i in range(1, len(waypoints)) if np.abs(waypoints[i] - waypoints[i - 1]).max() > 1e-9]
            waypoints = waypoints[keep]
            if len(waypoints) < 2:
                waypoints = np.vstack([start, start])
            _check_goal_limits(binding, waypoints[-1], command, robot)
            scalings = [_joint_scaling(binding, b - a, dt, move.velocity_scaling, move.acceleration_scaling, None, max_steps)
                        for a, b in zip(waypoints[:-1], waypoints[1:])]
            for smooth in ((True, False) if move.smooth and len(scalings) > 1 else (False,)):
                overlaps = blend_overlaps(scalings, [1.0 if smooth else 0.0] * (len(scalings) - 1))
                samples, _ = joint_chain_samples(start, waypoints[1:], scalings, overlaps)
                try:
                    _check_samples(binding, samples, start, dt, command, robot)
                except RobotCompileError as ex:
                    last = ex
                    continue
                hit = planner.check_path(samples)
                if hit is None:
                    return PlannedMove(samples, waypoints, smooth and any(overlaps), attempt + 1)
                last = RobotCompileError(
                    RobotErrorCode.PATH_IN_COLLISION,
                    f"robot '{robot}': the timed path is in collision at step {hit[0]} ({hit[1]})", command=command,
                    robot=robot, step=hit[0])
    except Exception as ex:  # noqa: BLE001 (translated or re-raised)
        raise _translate(ex, command, robot) from None
    assert last is not None
    if last.code == RobotErrorCode.PATH_IN_COLLISION:
        last.message += f" in each of {move.attempts} plan(s)"
    raise last


def compile_move(binding: RobotBinding, move: Any, start: np.ndarray, dt: float, command: Optional[int], robot: str,
                 max_steps: Optional[int] = None) -> np.ndarray:
    """One robot's move as joint positions ``(N, n)`` (SI), one row per step, checked (ends at rest whatever its
    termination). ``max_steps`` caps the number of steps (``TOO_MANY_STEPS``; checked before sampling)."""
    if isinstance(move, PlanMove):
        return plan_move(binding, move, start, dt, command, robot, max_steps).samples
    if isinstance(move, SetTcp):
        try:
            frame = binding.solver.check_frame(move.tool_frame or binding.tool_frame)
            p, o = _target_in_base(binding, move, command, robot).to_numpy()
            samples = _solve(binding, p.reshape(1, 3), o.reshape(1, 4), frame, start, command, robot)
        except Exception as ex:  # noqa: BLE001
            raise _translate(ex, command, robot) from None
        _check_samples(binding, samples, start, dt, command, robot)
        return samples
    if not isinstance(move, (PtpMove, LinMove, CircMove)):
        raise TypeError(f"unknown move {type(move).__name__}")
    samples, _, _ = compile_chain(binding, [move], start, dt, robot, [command], max_steps)
    return samples


# ---- compiling commands ---------------------------------------------------------------------------------------

def _passthrough(components: Optional[BaseModel]) -> Dict[str, Any]:
    if components is None:
        return {}
    return components.model_dump(mode="json", exclude_unset=True)


def _blend_space(move: Any) -> Optional[str]:
    if isinstance(move, PtpMove):
        return "joint"
    if isinstance(move, (LinMove, CircMove)):
        return "cartesian"
    return None


def blends_into(command: Any, following: Any) -> Optional[str]:
    """The robot id if ``command`` blends into ``following`` (see :class:`~.models.TerminationMixin`): both move only
    that robot, ``command``'s move has termination cnt and no dwell, the moves blend in the same space (ptp→ptp,
    lin/circ→lin/circ), and they use the same tool frame; else None."""
    if following is None or len(command.robots) != 1 or len(following.robots) != 1 or command.dwell > 0:
        return None
    (robot, move), = command.robots.items()
    (other, next_move), = following.robots.items()
    if robot != other or getattr(move, "termination", "fine") != "cnt":
        return None
    space = _blend_space(move)
    if space is None or space != _blend_space(next_move) or move.tool_frame != next_move.tool_frame:
        return None
    return robot


def _check_conflict(binding: RobotBinding, passthrough: Dict[str, Any], index: int, robot: str) -> None:
    addressed = (passthrough.get(binding.component) or {}).get(binding.transducer) or {}
    overlap = sorted(set(addressed) & set(binding.joint_fields.values()))
    if overlap:
        raise RobotCompileError(
            RobotErrorCode.CONFLICT,
            f"the command moves robot '{robot}' and also sets {binding.component}.{binding.transducer} field(s) "
            f"{overlap} directly; send those in a separate command", command=index, robot=robot)


def _joint_row(binding: RobotBinding, components: Dict[str, Any], q: np.ndarray) -> None:
    transducer = components.setdefault(binding.component, {}).setdefault(binding.transducer, {})
    for joint, value in zip(binding.solver.joints, q):
        transducer[binding.joint_fields[joint.name]] = {"position": to_wire(joint, float(value))}


def compile_commands(
    commands: Sequence[Any],
    robots: Mapping[str, RobotBinding],
    start: Mapping[str, np.ndarray],
    dt: float,
) -> CompiledBatch:
    """Compile robot commands, in order, into segment command frames (dicts). ``start`` is each robot's
    commanded joints (SI) before the first command; each command starts where the previous one ended (or, when a
    command blends into the next, the next starts during its deceleration). Raises :class:`RobotCompileError`
    (all or nothing)."""
    if not (dt and dt > 0):
        raise RobotCompileError(RobotErrorCode.NO_STATE, "the physics step is unknown (no simulation step has run yet)",
                                status=503)
    current = {id: np.array(q, dtype=np.float64) for id, q in start.items()}
    frames: List[Dict[str, Any]] = []
    summaries: List[CompiledCommand] = []
    for index, command in enumerate(commands):
        for robot in command.robots:
            if robot not in robots:
                raise RobotCompileError(RobotErrorCode.UNKNOWN_ROBOT, f"unknown robot '{robot}'; robots: {sorted(robots)}",
                                        command=index, robot=robot)
            if robot not in current:
                raise RobotCompileError(RobotErrorCode.NO_STATE, f"the start state of robot '{robot}' is unknown",
                                        command=index, robot=robot, status=503)
    index = 0
    while index < len(commands):
        # A run of commands blending into each other (one robot), or a single command.
        run = [index]
        while run[-1] + 1 < len(commands) and blends_into(commands[run[-1]], commands[run[-1] + 1]) is not None:
            run.append(run[-1] + 1)
        if len(run) > 1:
            robot = next(iter(commands[index].robots))
            binding = robots[robot]
            moves = [next(iter(commands[i].robots.values())) for i in run]
            samples, starts, kept = compile_chain(binding, moves, current[robot], dt, robot, run)
            passthroughs = [_passthrough(commands[i].components) for i in run]
            for i, p in zip(run, passthroughs):
                _check_conflict(binding, p, i, robot)
            ends = starts[1:] + [len(samples)]
            for j, i in enumerate(run):
                first = len(frames)
                for k in range(starts[j], ends[j]):
                    components: Dict[str, Any] = copy.deepcopy(passthroughs[j]) if k == starts[j] else {}
                    _joint_row(binding, components, samples[k])
                    frame: Dict[str, Any] = {"frame_id": commands[i].frame_id, "components": components}
                    frames.append(frame)
                dwell_steps = math.ceil(commands[i].dwell / dt - 1e-9) if (j == len(run) - 1 and commands[i].dwell > 0) else 0
                for _ in range(dwell_steps):
                    frames.append({"frame_id": commands[i].frame_id})
                steps = len(frames) - first
                summaries.append(CompiledCommand(index=i, frame_id=commands[i].frame_id, first_step=first, steps=steps,
                                                 duration=steps * dt, blended=j < len(kept) and kept[j]))
            current[robot] = samples[-1]
            index = run[-1] + 1
            continue

        command = commands[index]
        moving: Dict[str, np.ndarray] = {}
        for robot, move in command.robots.items():
            moving[robot] = compile_move(robots[robot], move, current[robot], dt, index, robot)
        passthrough = _passthrough(command.components)
        for robot in moving:
            _check_conflict(robots[robot], passthrough, index, robot)
        motion_steps = max([len(s) for s in moving.values()], default=1)
        dwell_steps = math.ceil(command.dwell / dt - 1e-9) if command.dwell > 0 else 0
        first = len(frames)
        for k in range(motion_steps + dwell_steps):
            components = copy.deepcopy(passthrough) if k == 0 else {}
            for robot, samples in moving.items():
                if k < len(samples):
                    _joint_row(robots[robot], components, samples[k])
            frame = {"frame_id": command.frame_id}
            if components:
                frame["components"] = components
            frames.append(frame)
        for robot, samples in moving.items():
            current[robot] = samples[-1]
        steps = len(frames) - first
        summaries.append(CompiledCommand(index=index, frame_id=command.frame_id, first_step=first, steps=steps,
                                         duration=steps * dt))
        index += 1
    return CompiledBatch(frames=frames, commands=summaries, end=current)


# ---- stopping ------------------------------------------------------------------------------------------------

def deceleration_ramp(binding: RobotBinding, q: np.ndarray, velocity: np.ndarray, dt: float) -> Optional[np.ndarray]:
    """Joint samples ``(N, n)`` (SI) bringing a robot moving at ``velocity`` (SI/s) from ``q`` to rest, every joint
    decelerating uniformly and stopping together, as fast as the acceleration limits allow (joints without one: in
    0.1 s from their velocity limit). None if it's already at rest, or the ramp would leave the joint limits."""
    if not np.isfinite(velocity).all() or np.abs(velocity).max(initial=0.0) < 1e-9:
        return None
    _, accelerations = binding.timing_limits(dt)
    velocities, _ = binding.timing_limits(dt)
    times = []
    for v, a, vmax in zip(velocity, accelerations, velocities):
        a = a if a is not None else vmax / 0.1
        times.append(abs(v) / a if a > 0 else 0.0)
    steps = max(1, math.ceil(max(times) / dt - 1e-9))
    total = steps * dt
    t = np.arange(1, steps + 1, dtype=np.float64).reshape(-1, 1) * dt
    samples = q + velocity * t - velocity * t * t / (2.0 * total)
    if binding.solver.limit_violation(samples) is not None:
        return None
    return samples

"""RobotApi: TCP-level robot control on top of a segment (ADR-0010, ADR-0011).

A ``RobotApi`` is a :class:`~..segment.server.SegmentApi` (every ``/api/segment/...`` route stays) that adds
``/api/robot/...``: robot commands (``set``, ``ptp``, ``lin`` moves per robot, plus pass-through commands
for other components) are **compiled when written** into segment command frames, one per physics step,
and appended to the segment's queue: all or nothing, so what was validated is exactly what runs. It also
serves the kinematics service routes (``/api/kinematics/...``) for its robots, as read-only solvers (ids = robot ids).

Beyond ``set``, ``ptp``, and ``lin``: ``circ`` and ``plan`` moves (collision-aware, with the robot's
:class:`~..motion.planning.MotionPlanner`, whose world the behavior script builds from the stage), blending (``cnt``),
jerk-limited ``ptp``, compile from a given start (compile-then-merge), path/goal tolerance monitoring that halts the
segment, a start-state check, a robot clear that can decelerate, and end-effector combinations selected through the
configuration.

Typical use from a behavior script::

    def on_play(self):
        self._robot = AmtMrA1(prim=self.prim_path)
        solver = CuMotionKinematicsSolver(urdf_path, xrdf_path)
        self._api = RobotApi({"robot": self._robot}, robots={"arm": RobotBinding(solver, component="robot")},
                             port=8020)
        self._api.start()

    def on_physics(self, current_time, delta_time):
        self._api.step(physics_time_step=delta_time, timeline_time=current_time)

Requires the ``server`` extra. Design: ``docs/src/Agents/Specs/robot-control.md`` ("The RobotAPI").

Note: don't add ``from __future__ import annotations``: the route handlers are annotated with the
dynamically created per-API models, which FastAPI must see as classes.
"""
import asyncio
import collections
import threading
import typing
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

import numpy as np

from fastapi import APIRouter, Body, Header, HTTPException, Query, status
from pydantic import BaseModel, create_model

from ..component import ComponentDefinitionError
from ..core.server import API_PREFIX, AssetsArg
from ..motion.kinematics import KinematicsError, to_wire
from ..motion.registry import SolverRegistry
from ..motion.server import coded_error, joint_description, kinematics_router
from ..segment.codegen import RobotModels, generate_client_module
from ..segment.engine import (
    DEFAULT_BUFFER_SIZE,
    ComponentsArg,
    QueueChangedError,
    QueueClearedError,
    QueueFullError,
    SegmentEngine,
    SegmentEvent,
    SegmentHaltedError,
)
from ..segment.models import ErrorCode, SegmentError
from ..segment.server import ServerStoppedError, SegmentApi
from ..transducer.models.pose import FramedPose, Quaternion, Vector3
from .compiler import (
    RobotBinding,
    RobotCompileError,
    check_start_state,
    compile_commands,
    deceleration_ramp,
    fold_commands,
    frame_addresses,
    plan_move,
    predict_start,
    read_component_joints,
    read_joints,
)
from .models import (
    PlannerDescription,
    RobotApiInfo,
    RobotClearResponse,
    RobotCommandBase,
    RobotCommandResponse,
    COMPILE_PREVIEW_FIELDS,
    RobotCompileRequestBase,
    RobotCompileResponseBase,
    RobotPlanRequest,
    RobotPlanResponse,
    RobotConfig,
    RobotConfiguration,
    RobotDescription,
    RobotError,
    RobotErrorCode,
    RobotJointDescription,
    RobotResponseFrameBase,
    RobotState,
)


ROBOT_PREFIX = f"{API_PREFIX}/robot"
TCP_FRAME = "tcp"
COMPILE_ATTEMPTS = 3  # recompiles when another write changed the queue meanwhile


def build_robot_models(prefix: str, engine: SegmentEngine, preview_features: bool = False) -> RobotModels:
    """``{prefix}RobotCommand``, ``{prefix}RobotResponseFrame``, ``{prefix}RobotCompileResponse`` (with the compile
    answer's preview fields when ``preview_features``)."""
    models = engine.models
    command = create_model(f"{prefix}RobotCommand", __base__=RobotCommandBase[models.component_commands],
                           __doc__=RobotCommandBase.__doc__)
    response_frame = create_model(f"{prefix}RobotResponseFrame", __base__=RobotResponseFrameBase[models.response_frame],
                                  __doc__=RobotResponseFrameBase.__doc__)
    compile_response = create_model(f"{prefix}RobotCompileResponse", __base__=RobotCompileResponseBase[models.command_frame],
                                    __doc__=RobotCompileResponseBase.__doc__,
                                    **(COMPILE_PREVIEW_FIELDS if preview_features else {}))
    return RobotModels(command, response_frame, compile_response)


class RobotApi(SegmentApi):
    """The RobotAPI: a segment API plus robot commands compiled to its frames.

    :param components: As :class:`SegmentApi` (the components, or a :class:`SegmentEngine`).
    :param robots: ``{robot id: RobotBinding}``: each robot's solver and the component and articulation
        transducer whose joints it drives.
    :param preview_features: As :class:`SegmentApi`; here also compiling from a given start and ``dt``, and the
        compile answer's ``queue_revision`` and ``end`` (compile-then-merge, deferred).
    :param kinematics: Also serve the kinematics service routes (``/api/kinematics/...``) for the robots (read-only
        solvers: no create, delete, or configuration there).
    :param title, description, version, port, host, assets, model_prefix, max_queue_length, buffer_size,
        subscriber_queue_size: See :class:`SegmentApi`.
    """

    def __init__(
        self,
        components: Any,
        robots: Mapping[str, RobotBinding],
        title: Optional[str] = None,
        description: Optional[str] = None,
        version: str = "0.1.0",
        port: int = 8020,
        host: str = "0.0.0.0",
        assets: AssetsArg = None,
        model_prefix: Optional[str] = None,
        max_queue_length: int = 10_000,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        subscriber_queue_size: int = 256,
        kinematics: bool = True,
        preview_features: bool = False,
    ):
        if isinstance(components, SegmentEngine):
            engine = components
        else:
            engine = SegmentEngine(components, name=title, model_prefix=model_prefix,
                                   max_queue_length=max_queue_length, buffer_size=buffer_size)
        if not robots:
            raise ValueError("RobotApi needs at least one robot (robots={id: RobotBinding(...)})")
        self._robots: Dict[str, RobotBinding] = dict(robots)
        self._check_bindings(engine)
        self._registry = SolverRegistry.from_bindings(self._robots)  # one lock per robot, shared with the routes
        self._robot_models = build_robot_models(engine.model_prefix, engine, preview_features)
        self._kinematics = bool(kinematics)
        self._compile_request_model = create_model(
            f"{engine.model_prefix}RobotCompileRequest", __base__=RobotCompileRequestBase[engine.models.component_commands],
            __doc__=RobotCompileRequestBase.__doc__)
        self._render_cache: "collections.OrderedDict[Tuple[str, int], str]" = collections.OrderedDict()
        # Execution monitoring (simulation thread): each robot's last two commanded joints, and its settling state.
        self._history: Dict[str, "collections.deque"] = {id: collections.deque(maxlen=2) for id in self._robots}
        self._settling: Dict[str, Optional[float]] = {id: None for id in self._robots}  # seconds since its motion ended
        self._history_lock = threading.Lock()
        super().__init__(engine, title=title, description=description, version=version, port=port, host=host,
                         assets=assets, subscriber_queue_size=subscriber_queue_size, preview_features=preview_features)
        engine.add_monitor(self._monitor)
        self.add_router(self._robot_router())
        if self._kinematics:
            self.add_router(kinematics_router(self._registry))

    def _check_bindings(self, engine: SegmentEngine) -> None:
        problems: List[str] = []
        for id, binding in self._robots.items():
            if not isinstance(binding, RobotBinding):
                problems.append(f"robot '{id}': {binding!r} is not a RobotBinding")
                continue
            component = engine.components.get(binding.component)
            if component is None:
                problems.append(f"robot '{id}': no component '{binding.component}' in the segment (components: "
                                f"{sorted(engine.components)})")
                continue
            transducer_model = component.command_model.model_fields.get(binding.transducer)
            if transducer_model is None:
                problems.append(f"robot '{id}': component '{binding.component}' has no actuator '{binding.transducer}' "
                                f"(actuators: {component.actuator_ids})")
                continue
            commands = _model_type(transducer_model.annotation)
            for joint, field in binding.joint_fields.items():
                joint_field = commands.model_fields.get(field) if commands is not None else None
                joint_model = _model_type(joint_field.annotation) if joint_field is not None else None
                if joint_model is None or "position" not in joint_model.model_fields:
                    problems.append(f"robot '{id}': joint '{joint}' needs a position-driven field '{field}' in "
                                    f"{binding.component}.{binding.transducer}")
        if problems:
            raise ComponentDefinitionError("RobotApi", problems)

    # ---- properties ---------------------------------------------------------------------------

    @property
    def robots(self) -> Dict[str, RobotBinding]:
        return dict(self._robots)

    @property
    def robot_models(self) -> RobotModels:
        return self._robot_models

    @property
    def layers(self) -> List[str]:
        layers = super().layers
        return layers + ["KinematicsApi"] if self._kinematics else layers

    def _generate_client_source(self) -> str:
        engine = self._segment
        return generate_client_module(engine.model_prefix, self.title, self.description, self.version,
                                      engine.components, engine.models, engine.fingerprint, self._robot_models)

    # ---- compiling (thread-safe; usable from Python as well as through the API) -----------------------

    def _locks(self, ids: List[str]) -> List[threading.RLock]:
        return [self._registry.get(id).lock for id in sorted(ids)]

    def _compile(self, commands: List[Any], start: Optional[Mapping[str, Mapping[str, float]]] = None,
                 dt: Optional[float] = None) -> Tuple[Any, int]:
        """Compile against the end of the queue (or from ``start``, by robot id, in wire units):
        ``(CompiledBatch, queue_revision)``."""
        latest, pending, revision = self._segment.prediction_snapshot()
        robots = sorted({id for c in commands for id in c.robots} & set(self._robots))
        start = dict(start or {})
        unknown = sorted(set(start) - set(self._robots))
        if unknown:
            raise RobotCompileError(RobotErrorCode.UNKNOWN_ROBOT, f"start: unknown robot(s) {unknown}; robots: "
                                    f"{sorted(self._robots)}")
        dt = dt or self._segment.physics_time_step
        locks = self._locks(robots)
        for lock in locks:
            lock.acquire()
        try:
            initial = {}
            for id in robots:
                binding = self._robots[id]
                if id in start:
                    try:
                        initial[id] = binding.solver.joints_from_wire(start[id])
                    except KinematicsError as ex:
                        raise RobotCompileError(ex.code, f"start of robot '{id}': {ex.message}", robot=id) from None
                else:
                    check_start_state(binding, latest, pending, id)
                    initial[id] = predict_start(binding, latest, pending, id)
            return compile_commands(commands, self._robots, initial, dt or 0.0), revision
        finally:
            for lock in reversed(locks):
                lock.release()

    def _coerce_commands(self, commands: List[Any]) -> List[Any]:
        model = self._robot_models.command
        return [c if isinstance(c, model) else model.model_validate(c, from_attributes=True) for c in commands]

    def compile(self, commands: List[Any], start: Optional[Mapping[str, Mapping[str, float]]] = None,
                dt: Optional[float] = None) -> BaseModel:
        """Compile robot commands into segment command frames without queueing them, from the end of the queue. With
        ``preview_features`` (deferred), also from ``start`` (``{robot: {joint: deg or m}}``) and/or at ``dt``
        (compile-then-merge), and the answer has ``queue_revision`` and ``end``."""
        if not self._preview and (start is not None or dt is not None):
            raise RobotCompileError(RobotErrorCode.CONFLICT, "compiling from a start or at another dt is a deferred preview "
                                    "feature that this API doesn't serve", status=400)
        commands = self._coerce_commands(commands)
        batch, revision = self._compile(commands, start, dt)
        frames = [self._segment.coerce_frame(f) for f in batch.frames]
        if not self._preview:
            return self._robot_models.compile_response(frames=frames, commands=batch.commands)
        end = {id: self._robots[id].solver.joints_to_wire(q) for id, q in batch.end.items()}
        return self._robot_models.compile_response(frames=frames, commands=batch.commands, queue_revision=revision,
                                                   end=end)

    def plan(self, request: RobotPlanRequest) -> RobotPlanResponse:
        """Plan one robot's collision-free move (no queueing): the planner's waypoints and the timed, checked samples."""
        binding = self._robots.get(request.robot)
        if binding is None:
            raise RobotCompileError(RobotErrorCode.UNKNOWN_ROBOT, f"unknown robot '{request.robot}'; robots: "
                                    f"{sorted(self._robots)}", robot=request.robot)
        latest, pending, _ = self._segment.prediction_snapshot()
        dt = self._segment.physics_time_step
        if not dt:
            raise RobotCompileError(RobotErrorCode.NO_STATE, "the physics step is unknown (no simulation step has run yet)",
                                    status=503)
        with self._registry.get(request.robot).lock:
            if request.start is not None:
                try:
                    start = binding.solver.joints_from_wire(request.start)
                except KinematicsError as ex:
                    raise RobotCompileError(ex.code, ex.message, robot=request.robot) from None
            else:
                start = predict_start(binding, latest, pending, request.robot)
            planned = plan_move(binding, request.move, start, dt, None, request.robot)
            wire = binding.solver.joints_to_wire
            return RobotPlanResponse(
                robot=request.robot, waypoints=[wire(q) for q in planned.waypoints], steps=len(planned.samples),
                duration=len(planned.samples) * dt, smoothed=planned.smoothed, attempts=planned.attempts,
                samples=[wire(q) for q in planned.samples] if request.include_samples else None)

    def clear_robots(self, stop: str = "hold") -> RobotClearResponse:
        """Clear the segment's queue; with ``stop="decelerate"``, each moving robot gets a ramp to rest (at its
        acceleration limits, from its current commanded velocity) queued in the same atomic step. Releases a halt
        (not a pause)."""
        if stop not in ("hold", "decelerate"):
            raise RobotCompileError(RobotErrorCode.CONFLICT, f"unknown stop policy '{stop}' (hold, decelerate)")
        outcome: Dict[str, str] = {id: "hold" for id in self._robots}
        queued = [0]

        def ramps(latest: Optional[BaseModel], applying: List[BaseModel]) -> List[Dict[str, Any]]:
            if stop != "decelerate" or latest is None:
                return []
            dt = self._segment.physics_time_step or 0.0
            rows: Dict[int, Dict[str, Any]] = {}
            for id, binding in self._robots.items():
                q = read_joints(binding, latest)
                with self._history_lock:
                    history = list(self._history[id])
                if q is None or not dt or len(history) < 2:
                    continue
                velocity = (history[-1] - history[-2]) / dt
                if applying and frame_addresses(binding, applying[0]):  # a step is applying a frame right now
                    now = fold_commands(binding, q, applying)
                    velocity, q = (now - q) / dt, now
                samples = deceleration_ramp(binding, q, velocity, dt)
                if samples is None:
                    continue
                outcome[id] = "decelerate"
                for k, row in enumerate(samples):
                    components = rows.setdefault(k, {}).setdefault("components", {})
                    transducer = components.setdefault(binding.component, {}).setdefault(binding.transducer, {})
                    for joint, value in zip(binding.solver.joints, row):
                        transducer[binding.joint_fields[joint.name]] = {"position": to_wire(joint, float(value))}
            queued[0] = len(rows)
            return [rows[k] for k in sorted(rows)]

        removed = self._segment.clear(replacement=ramps)
        return RobotClearResponse(removed=len(removed), stop=stop, robots=outcome, steps=queued[0])

    # ---- execution monitoring (the simulation thread) -------------------------------------------------

    def _monitor(self, frame: Optional[BaseModel], readings: Mapping[str, Tuple[Any, Any]]) -> List[SegmentError]:
        """Path and goal tolerances (JTC's path_tolerance / goal_tolerance): while a frame commands a robot's
        joints, |actual − commanded| must stay within ``path_tolerance``; after its motion, the actual joints must
        come within ``goal_tolerance`` within ``goal_time``. A violation halts the segment."""
        errors: List[SegmentError] = []
        dt = self._segment.physics_time_step or 0.0
        for id, binding in self._robots.items():
            actuals, commands = readings.get(binding.component, (None, None))
            commanded = read_component_joints(binding, commands) if commands is not None else None
            if commanded is not None:
                with self._history_lock:
                    self._history[id].append(commanded)
            path, goal = binding.tolerance("path"), binding.tolerance("goal")
            if (path is None and goal is None) or commanded is None:
                continue
            actual = read_component_joints(binding, actuals) if actuals is not None else None
            if actual is None:
                continue
            deviation = np.abs(actual - commanded)
            if frame_addresses(binding, frame):
                self._settling[id] = 0.0
                if path is not None and (deviation > path + 1e-12).any():
                    errors.append(self._tolerance_error(id, binding, "PATH_TOLERANCE", deviation, path, "while moving"))
                    self._settling[id] = None
                continue
            elapsed = self._settling[id]
            if elapsed is None:
                continue
            if goal is None or not (deviation > goal + 1e-12).any():
                self._settling[id] = None  # settled
                continue
            elapsed += dt
            self._settling[id] = elapsed
            if elapsed > binding.goal_time + 1e-9:
                errors.append(self._tolerance_error(id, binding, "GOAL_TOLERANCE", deviation, goal,
                                                    f"{binding.goal_time:g} s after its motion"))
                self._settling[id] = None
        return errors

    def _tolerance_error(self, id: str, binding: RobotBinding, code: str, deviation: Any, tolerance: Any,
                         when: str) -> SegmentError:
        i = int(np.argmax(deviation - tolerance))
        joint = binding.solver.joints[i]
        unit = "deg" if joint.type == "revolute" else "m"
        return SegmentError(code=code, component=binding.component, transducer=binding.transducer,
                            message=f"robot '{id}': {joint.name} is {to_wire(joint, float(deviation[i])):.4g} {unit} from its "
                                    f"commanded position {when} (tolerance {to_wire(joint, float(tolerance[i])):.4g} {unit})")

    def write(self, commands: List[Any]) -> RobotCommandResponse:
        """Compile robot commands and append their frames to the segment's queue (all or nothing)."""
        commands = self._coerce_commands(commands)
        for attempt in range(COMPILE_ATTEMPTS):
            batch, revision = self._compile(commands)
            try:
                answer = self._segment.enqueue(batch.frames, expected_revision=revision)
            except QueueChangedError:
                continue
            return RobotCommandResponse(accepted=answer.accepted, queue_length=answer.queue_length, halted=answer.halted,
                                        commands=batch.commands)
        raise RobotCompileError(RobotErrorCode.QUEUE_CHANGED,
                                f"the queue changed during each of {COMPILE_ATTEMPTS} compiles; retry", status=409)

    def _submit(self, command: Any) -> Tuple[Any, Any]:
        commands = self._coerce_commands([command])
        for attempt in range(COMPILE_ATTEMPTS):
            batch, revision = self._compile(commands)
            try:
                return self._segment.submit_many(batch.frames, expected_revision=revision), batch
            except QueueChangedError:
                continue
        raise RobotCompileError(RobotErrorCode.QUEUE_CHANGED,
                                f"the queue changed during each of {COMPILE_ATTEMPTS} compiles; retry", status=409)

    # ---- robot state ------------------------------------------------------------------------------

    def robot_states(self, frame: Optional[BaseModel]) -> Dict[str, RobotState]:
        """Each robot's kinematic state in a segment response frame (FK of commanded and measured joints)."""
        states: Dict[str, RobotState] = {}
        for id, binding in self._robots.items():
            solver = binding.solver
            errors: List[RobotError] = []
            poses: Dict[str, Optional[FramedPose]] = {"target": None, "actual": None}
            with self._registry.get(id).lock:
                tool_frame = binding.tool_frame
                for key, actual in (("target", False), ("actual", True)):
                    q = read_joints(binding, frame, actual=actual) if frame is not None else None
                    if q is None:
                        errors.append(RobotError(code=RobotErrorCode.NO_STATE, robot=id,
                                                 message=f"the {'measured' if actual else 'commanded'} joints are not available"))
                        continue
                    try:
                        p, o = solver.fk(q, tool_frame)
                    except Exception as ex:  # report, don't fail the whole frame
                        errors.append(RobotError(code=RobotErrorCode.NO_STATE, robot=id, message=f"FK failed: {ex}"))
                        continue
                    poses[key] = FramedPose(position=Vector3.from_numpy(p[0]), orientation=Quaternion.from_wxyz(o[0]),
                                            frame=solver.base_frame)
            states[id] = RobotState(tool_frame=tool_frame, target_tcp_pose=poses["target"],
                                    actual_tcp_pose=poses["actual"], combination=binding.combination, errors=errors)
        return states

    def robot_frame(self, frame: BaseModel) -> BaseModel:
        """A segment response frame with each robot's kinematic state: a ``RobotResponseFrame``."""
        return self._robot_models.response_frame(frame=frame, robots=self.robot_states(frame))

    def _render(self, event: SegmentEvent) -> str:
        key = (self._segment.instance_id, event.sequence)
        cached = self._render_cache.get(key)
        if cached is None:
            cached = self.robot_frame(event.model).model_dump_json()
            self._render_cache[key] = cached
            while len(self._render_cache) > 64:
                self._render_cache.popitem(last=False)
        return cached

    # ---- configuration ------------------------------------------------------------------------------

    def config_read_robots(self) -> RobotConfiguration:
        return RobotConfiguration(robots={id: RobotConfig(combination=b.combination, tool_frame=b.tool_frame,
                                                          tcp_offset=b.tcp_offset)
                                          for id, b in self._robots.items()})

    def config_write_robots(self, value: RobotConfiguration) -> RobotConfiguration:
        """Apply robot configuration at once (queued frames are already joints, so they are unaffected)."""
        unknown = [id for id in value.robots if id not in self._robots]
        if unknown:
            raise RobotCompileError(RobotErrorCode.UNKNOWN_ROBOT, f"unknown robot(s) {unknown}; robots: {sorted(self._robots)}")
        for id, config in value.robots.items():
            binding = self._robots[id]
            with self._registry.get(id).lock:
                fields = config.model_fields_set
                try:
                    if "combination" in fields and config.combination is not None:
                        binding.select_combination(config.combination)
                    if "tcp_offset" in fields and config.tcp_offset is not None:
                        p, o = config.tcp_offset.to_numpy()
                        binding.solver.set_frames({TCP_FRAME: (binding.flange_frame, p, o)})
                        binding.tcp_offset = config.tcp_offset
                        if "tool_frame" not in fields or config.tool_frame is None:
                            binding.tool_frame = TCP_FRAME
                    if "tool_frame" in fields and config.tool_frame is not None:
                        binding.tool_frame = binding.solver.check_frame(config.tool_frame)
                except KinematicsError as ex:
                    raise RobotCompileError(ex.code, f"robot '{id}': {ex.message}", robot=id) from None
        return self.config_read_robots()

    # ---- description ------------------------------------------------------------------------------

    def robot_info(self) -> RobotApiInfo:
        dt = self._segment.physics_time_step
        robots = []
        for id, binding in self._robots.items():
            solver = binding.solver
            joints = []
            with self._registry.get(id).lock:
                steps = binding.max_steps(dt) if dt else None
                for i, joint in enumerate(solver.joints):
                    base = joint_description(joint).model_dump()
                    limit, source = binding.velocity_limit(i)
                    base["velocity_limit"] = to_wire(joint, limit) if limit is not None else (
                        to_wire(joint, binding._fallback_step(i) / dt) if dt else None)
                    acceleration = binding.acceleration_limit(i)
                    base["acceleration_limit"] = to_wire(joint, acceleration) if acceleration is not None else None
                    joints.append(RobotJointDescription(
                        **base, field=binding.joint_fields[joint.name], velocity_limit_source=source,
                        max_step=to_wire(joint, float(steps[i])) if steps is not None else None))
                robots.append(RobotDescription(
                    id=id, component=binding.component, transducer=binding.transducer, backend=solver.backend,
                    base_frame=solver.base_frame, joints=joints, frames=list(solver.frame_names),
                    tool_frames=list(solver.tool_frames), tool_frame=binding.tool_frame, tcp_offset=binding.tcp_offset,
                    jump_margin=binding.jump_margin, world_reference=binding.base_pose is not None,
                    combination=binding.combination, combinations=sorted(binding.combinations or {}),
                    planner=PlannerDescription(**planner.description) if (planner := binding.active_planner()) else None,
                    path_tolerance=binding.tolerance_wire("path"), goal_tolerance=binding.tolerance_wire("goal"),
                    goal_time=binding.goal_time, start_tolerance=binding.tolerance_wire("start")))
        return RobotApiInfo(robots=robots, physics_time_step=dt)

    def api_description(self) -> str:
        return super().api_description() + f"""

**RobotAPI** for {', '.join(f'`{id}`' for id in self._robots)}: robot commands (`set`, `ptp`, `lin`, `circ`, and `plan`
moves, blended with termination `cnt`, plus commands for other components) are compiled into segment frames when written,
all or nothing.
`POST {ROBOT_PREFIX}/process/write` (compile and queue), `POST {ROBOT_PREFIX}/process/compile` (frames only),
`POST {ROBOT_PREFIX}/process/execute`, `GET {ROBOT_PREFIX}/process/read`, `GET {ROBOT_PREFIX}/stream` (the segment's
frame and each robot's target and actual TCP pose in one message), `POST {ROBOT_PREFIX}/process/plan` (a planned move,
not queued), `DELETE {ROBOT_PREFIX}/process/clear?stop=decelerate` (clear, ramping moving robots to rest). Pause and
resume with the segment routes."""

    # ---- routes -------------------------------------------------------------------------------------

    def _robot_router(self) -> APIRouter:
        engine = self._segment
        RobotCommand_ = self._robot_models.command
        RobotResponseFrame_ = self._robot_models.response_frame
        RobotCompileResponse_ = self._robot_models.compile_response
        router = APIRouter(prefix=ROBOT_PREFIX, tags=["Robot"])
        compile_responses = {
            409: {"description": "The queue kept changing during compiles (code QUEUE_CHANGED)."},
            413: {"description": "The frames would exceed the maximum queue length."},
            422: {"description": "A command can't be compiled; nothing is queued. Coded detail: IK_UNREACHABLE, "
                                 "JOINT_JUMP, JOINT_LIMIT, DURATION_TOO_SHORT, UNKNOWN_ROBOT, UNKNOWN_FRAME, UNKNOWN_JOINT, "
                                 "REFERENCE_UNAVAILABLE, CONFLICT, INVALID_CIRCLE, NO_PLANNER, PLAN_FAILED, START_IN_COLLISION, "
                                 "GOAL_IN_COLLISION, PATH_IN_COLLISION (with command, robot, step, joint)."},
            503: {"description": "No simulation step has run yet, or a robot's joints can't be read (code NO_STATE)."},
        }

        def failure(ex: RobotCompileError) -> HTTPException:
            return HTTPException(status_code=ex.status, detail=ex.detail)

        @router.get("/info", summary="Get Robot Info", response_model=RobotApiInfo)
        def info():
            return self.robot_info()

        RobotCompileRequest_ = self._compile_request_model
        compile_description = ("Compile robot commands into segment command frames (one per physics step) without "
                               "queueing them, starting from the commanded joints at the end of the queue.")

        async def run_compile(commands: List[Any], start: Optional[Dict[str, Any]] = None, dt: Optional[float] = None):
            try:
                return await asyncio.to_thread(self.compile, commands, start, dt)
            except RobotCompileError as ex:
                raise failure(ex)

        if self._preview:  # deferred preview feature: compile-then-merge
            @router.post(
                "/process/compile",
                summary="Compile",
                description=compile_description + " Or send {commands, start, dt} to start robots from given joints and/or "
                            "use another step (compile-then-merge). The answer has the queue_revision the start was "
                            "predicted from and each robot's end joints. The frames can be merged with other machines' "
                            "frames and written to /api/segment/process/write?expected_revision=….",
                response_model=RobotCompileResponse_,
                responses={**compile_responses, 409: {"description": "START_TOLERANCE: a robot isn't where it was commanded."}},
            )
            async def compile_(body: Union[List[RobotCommand_], RobotCompileRequest_] = Body(
                    ..., description="The robot commands, in order; or {commands, start?, dt?}.")):
                if isinstance(body, list):
                    return await run_compile(body)
                return await run_compile(body.commands, body.start, body.dt)
        else:
            @router.post(
                "/process/compile",
                summary="Compile",
                description=compile_description,
                response_model=RobotCompileResponse_,
                responses={**compile_responses, 409: {"description": "START_TOLERANCE: a robot isn't where it was commanded."}},
            )
            async def compile_(body: List[RobotCommand_] = Body(..., description="The robot commands, in order.")):
                return await run_compile(body)

        @router.post(
            "/process/plan",
            summary="Plan",
            description="Plan one robot's collision-free move with its motion planner (obstacles from the simulation's "
                        "scene) without queueing it: the planner's waypoints, and the timed path checked for joint limits, "
                        "joint jumps, and collisions at every step (include_samples for the joints per step). To run it, "
                        "write a command with the same plan move.",
            response_model=RobotPlanResponse,
            responses=compile_responses,
        )
        async def plan(request: RobotPlanRequest = Body(...)):
            try:
                return await asyncio.to_thread(self.plan, request)
            except RobotCompileError as ex:
                raise failure(ex)

        @router.delete(
            "/process/clear",
            summary="Clear",
            description="Clear the segment's queue (like DELETE /api/segment/process/clear, which also releases a halt) "
                        "with a stop policy: hold (the default; the robots hold their last targets) or decelerate (each "
                        "moving robot gets a ramp to rest at its acceleration limits, queued in the same atomic step).",
            response_model=RobotClearResponse,
        )
        async def clear(stop: Literal["hold", "decelerate"] = Query("hold", description="hold or decelerate.")):
            try:
                return self.clear_robots(stop)
            except RobotCompileError as ex:
                raise failure(ex)

        @router.post(
            "/process/write",
            summary="Write",
            description="Compile robot commands into segment frames and append them to the segment's queue: all or "
                        "nothing (a command that can't be compiled rejects the batch, and nothing is queued).",
            response_model=RobotCommandResponse,
            responses=compile_responses,
        )
        async def write(commands: List[RobotCommand_] = Body(..., description="The robot commands, in order.")):
            try:
                return await asyncio.to_thread(self.write, commands)
            except RobotCompileError as ex:
                raise failure(ex)
            except QueueFullError as ex:
                raise HTTPException(status_code=413, detail=str(ex))

        @router.post(
            "/process/execute",
            summary="Execute",
            description="Compile one robot command, append its frames, and wait until its last frame has been applied; "
                        "returns that step's robot frame. On timeout, its frames still queued are removed.",
            response_model=RobotResponseFrame_,
            responses={**compile_responses,
                       409: {"description": "The segment is halted (code HALTED), the queue was cleared, or it kept "
                                            "changing (code QUEUE_CHANGED)."},
                       504: {"description": "Not applied within the timeout; the command's queued frames were removed."}},
        )
        async def execute(
            command: RobotCommand_ = Body(..., description="The robot command."),
            timeout: float = Query(60.0, gt=0, description="How long to wait for the command to finish, in seconds."),
        ):
            try:
                future, _ = await asyncio.to_thread(self._submit, command)
            except RobotCompileError as ex:
                raise failure(ex)
            except SegmentHaltedError as ex:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": ErrorCode.HALTED, "message": str(ex)})
            except QueueFullError as ex:
                raise HTTPException(status_code=413, detail=str(ex))
            try:
                frame = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout)
            except asyncio.TimeoutError:
                removed = engine.withdraw(future)
                if future.done() and not future.cancelled() and future.exception() is None:
                    frame = future.result()
                else:
                    future.cancel()
                    raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                                        detail=f"the command was not finished within {timeout} s"
                                               + ("; its remaining frames were removed from the queue" if removed else ""))
            except QueueClearedError as ex:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ex))
            except ServerStoppedError as ex:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(ex))
            return self.robot_frame(frame)

        @router.get(
            "/process/read",
            summary="Read",
            description="The latest step's segment response frame and each robot's target and actual TCP pose.",
            response_model=RobotResponseFrame_,
            responses={503: {"description": "No simulation step has run yet."}},
        )
        def read():
            latest = engine.latest
            if latest is None:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="no simulation step has run yet (is the simulation playing?)")
            return self.robot_frame(latest)

        @router.get(
            "/stream",
            summary="Stream",
            description="Like /api/segment/stream (same ids, filters, resuming, config/status/reset/heartbeat/error "
                        "events), but each `frame` event carries a robot frame: the segment's response frame and each "
                        "robot's target and actual TCP pose, in one message. For replayed frames the kinematic data is "
                        "computed at replay time (with the current tool frames).",
            responses={200: {"content": {"text/event-stream": {}}, "description": "A stream of events."},
                       404: {"description": "The resume point is no longer buffered (code OUT_OF_RANGE)."}},
        )
        async def stream(
            every: int = Query(1, ge=1, description="Only send every Nth frame."),
            executed_only: bool = Query(False, description="Only send frames of steps that applied a frame."),
            resume_after: Optional[int] = Query(None, ge=0, description="Resume after this sequence."),
            instance_id: Optional[str] = Query(None, description="The instance_id resume_after belongs to."),
            clamp: bool = Query(False, description="Resume from the oldest buffered event if the point is gone."),
            last_event_id: Optional[str] = Header(None, description="Set by EventSource when it reconnects."),
        ):
            return self._open_stream(every, executed_only, resume_after, instance_id, clamp, last_event_id,
                                     render=self._render)

        @router.get("/config/read", summary="Read Robot Configuration", response_model=RobotConfiguration)
        def config_read():
            return self.config_read_robots()

        @router.post(
            "/config/write",
            summary="Write Robot Configuration",
            description="Set robots' active tool frame and TCP (the 'tcp' frame relative to the flange tool frame, like "
                        "UR's set_tcp). Applied at once: frames already queued are joints, so they are unaffected.",
            response_model=RobotConfiguration,
        )
        def config_write(value: RobotConfiguration = Body(...)):
            try:
                return self.config_write_robots(value)
            except RobotCompileError as ex:
                raise failure(ex)

        return router


def _model_type(annotation: Any) -> Optional[type]:
    """The BaseModel class in an (Optional) annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _model_type(arg)
        if found is not None:
            return found
    return None

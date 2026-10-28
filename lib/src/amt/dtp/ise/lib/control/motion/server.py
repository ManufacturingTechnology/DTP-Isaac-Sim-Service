"""KinematicsApi: the kinematics service, a scene-free, MoveIt-like web API over uploaded robots (ADR-0010).

A client uploads a robot's URDF and XRDF (optionally with end-effector XRDF fragments, merged into one combination)
and gets a **solver** (a :class:`~.kinematics.KinematicsSolver`), then calls it: forward and inverse kinematics
(batched), reachability, Jacobians, trajectory sampling (a ``set``, ``ptp``, ``lin``, ``circ``, or ``plan`` move
sampled at a client-given ``dt``, exactly as the RobotAPI compiles it), a path check (joint limits and the jump
check), tool frames and a TCP offset, self-collision against the robot's own collision spheres, and, for backends
with a planner, a **world of library-neutral obstacles** set through the API (boxes, spheres, cylinders, capsules,
meshes with poses, like MoveIt's ``CollisionObject``) to plan around and check against. **Nothing comes from Isaac
Sim's scene:** obstacles taken from the USD stage stay in process (the RobotAPI). It knows nothing about segments,
the stage, the timeline, or physics, so any controller, even one driving a real robot, can use it without Isaac
Sim. It is **not** a machine-control API, so it is an :class:`~..host.ApiHost` (no ``/api/core`` routes), not a
``CoreApi`` (ADR-0024). In the extension it runs as ``KinematicsService`` on port 8014; a
:class:`~..robot.server.RobotApi` serves the same routes for its robots (read-only solvers, :func:`kinematics_router`).

**Threading (ADR-0010):** the server runs on its own thread, and the service handles requests
**one at a time**: every route is a coroutine that calls the solver inline on the server's event-loop thread (no
thread pool), so at most one request computes at a time (only network I/O, such as receiving an upload, overlaps).
It never uses the bridge to Isaac Sim's main loop. Each cuMotion call also holds the process-wide ``CUMOTION_LOCK``
shared with Isaac-thread users. (A RobotApi serves these routes from FastAPI's thread pool instead, so a long
kinematics request doesn't stall its streams.)

Routes under ``/api/kinematics``, tags "Solvers", "Kinematics", "Trajectories", "Tool Frames", "Collision", "World".
Wire units: degrees (revolute) and meters; solvers use radians. Requires the ``server`` extra; multipart
uploads also need ``python-multipart`` (bundled with Isaac Sim), JSON uploads don't. Design:
``docs/src/Agents/Specs/robot-control.md`` ("The kinematics service").
"""
import contextlib
import functools
import inspect
import math
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..host import API_PREFIX, ApiHost
from ..transducer.models.pose import Pose, Vector3, poses_to_arrays
from .kinematics import PLACEHOLDER_VELOCITY, KinematicsError, KinematicsSolver, JointInfo, plausible_limit, to_wire
from .models import (
    CollisionSphere,
    CollisionSpheres,
    CollisionSpheresRequest,
    FkRequest,
    FkResponse,
    IkRequest,
    IkResponse,
    IkResult,
    Jacobian,
    JacobianRequest,
    JacobianResponse,
    JointDescription,
    KinematicsDescription,
    KinematicsServiceInfo,
    ReachableResponse,
    RobotError,
    RobotErrorCode,
    SelfCollisionRequest,
    SelfCollisionResponse,
    SelfCollisionResult,
    ServiceLimits,
    SolverConfig,
    SolverCreateRequest,
    SolverDescription,
    SolverFrames,
    WorldCollisionRequest,
    WorldCollisionResponse,
    WorldCollisionResult,
    WorldDescription,
)
from .planning import PlanningWorld
from .registry import (
    DEFAULT_MAX_SOLVERS,
    DEFAULT_MAX_UPLOAD_BYTES,
    PlannerFactory,
    RegistryError,
    SolverEntry,
    SolverFactory,
    SolverRegistry,
)
from .trajectory import check_steps
from .trajectory_models import PathCheckRequest, PathCheckResponse, TrajectoryRequest, TrajectoryResponse


KINEMATICS_PREFIX = f"{API_PREFIX}/kinematics"
DEFAULT_PORT = 8014
DEFAULT_MAX_BATCH = 1_000_000  # None means no limit
DEFAULT_MAX_SAMPLES = 10_000_000
_UPLOAD_SLACK = 64 * 1024  # form fields and multipart boundaries on top of the two files
_MAX_FRAGMENTS = 1024


def coded_error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    """An HTTP error with a coded detail ``{code, message, ...}`` (as the segment API's)."""
    return HTTPException(status_code=status, detail={"code": code, "message": message, **{k: v for k, v in extra.items() if v is not None}})


def joint_description(joint: JointInfo) -> JointDescription:
    """A joint in wire units; placeholder velocity limits (≥ 1000 rad/s or m/s) are reported as null."""
    def wire(value: Optional[float]) -> Optional[float]:
        return None if value is None or not math.isfinite(value) else to_wire(joint, value)
    return JointDescription(
        name=joint.name, type=joint.type, unit="deg" if joint.type == "revolute" else "m",
        lower=wire(joint.lower), upper=wire(joint.upper),
        velocity_limit=wire(plausible_limit(joint.velocity, PLACEHOLDER_VELOCITY)),
        acceleration_limit=wire(plausible_limit(joint.acceleration)),
        jerk_limit=wire(plausible_limit(joint.jerk)),
    )


def kinematics_description(id: str, solver: KinematicsSolver) -> KinematicsDescription:
    return KinematicsDescription(
        id=id, backend=solver.backend, base_frame=solver.base_frame,
        joints=[joint_description(j) for j in solver.joints], frames=list(solver.frame_names),
        tool_frames=list(solver.tool_frames), default_tool_frame=solver.default_tool_frame, revision=solver.revision,
    )


def jacobian_to_wire(solver: KinematicsSolver, j: np.ndarray) -> Jacobian:
    """An SI Jacobian ``(6, n)`` in wire units: joint velocities in deg/s (revolute) or m/s (prismatic) to linear
    (m/s) and angular (deg/s) velocity."""
    scale = np.array([math.pi / 180 if joint.type == "revolute" else 1.0 for joint in solver.joints])
    linear = j[:3] * scale
    angular = j[3:] * scale * (180 / math.pi)
    singular = np.linalg.svd(j, compute_uv=False)
    return Jacobian(linear=linear.tolist(), angular=angular.tolist(), manipulability=float(np.prod(singular)))


def _multipart_available() -> bool:
    try:
        import python_multipart  # noqa: F401
        return True
    except ImportError:
        try:
            import multipart  # noqa: F401  (older python-multipart)
            return True
        except ImportError:
            return False


def _create_body_schema() -> Dict[str, Any]:
    json_schema = SolverCreateRequest.model_json_schema()
    multipart = {
        "type": "object",
        "required": ["urdf", "xrdf"],
        "properties": {
            "urdf": {"type": "string", "format": "binary", "description": "The URDF file."},
            "xrdf": {"type": "string", "format": "binary", "description": "The XRDF file (it must list tool_frames)."},
            "name": {"type": "string", "description": json_schema["properties"]["name"]["description"]},
            "tool_frame": {"type": "string", "description": json_schema["properties"]["tool_frame"]["description"]},
            "backend": {"type": "string", "description": json_schema["properties"]["backend"]["description"]},
            "fragment": {"type": "array", "items": {"type": "string", "format": "binary"},
                         "description": "End-effector XRDF fragment files (any number; with combination)."},
            "combination": {"type": "string", "description": "The combination (YAML or JSON text), with fragments."},
        },
    }
    return {"requestBody": {"required": True, "content": {
        "multipart/form-data": {"schema": multipart},
        "application/json": {"schema": json_schema},
    }}}


def _inline(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A coroutine handler that runs the synchronous handler ``fn`` inline on the server's event-loop thread (so a
    FastAPI app runs it there, one at a time, instead of in its thread pool). FastAPI reads the signature through
    ``__wrapped__``."""
    if inspect.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def handler(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)
    return handler


def kinematics_router(registry: SolverRegistry, max_batch: Optional[int] = DEFAULT_MAX_BATCH,
                      max_samples: Optional[int] = DEFAULT_MAX_SAMPLES, serial: bool = False) -> APIRouter:
    """The kinematics routes over ``registry``. Without factories (a RobotApi's read-only registry) the create
    and delete routes are left out. With ``serial`` (the kinematics service), every request is handled inline on
    the server's event-loop thread, one at a time; otherwise synchronous routes run in FastAPI's thread pool."""
    api_router = APIRouter(prefix=KINEMATICS_PREFIX)
    solver_path = "/solvers/{solver}"

    class _Router:  # api_router's decorators, wrapping handlers to run inline when serial
        def __getattr__(self, method: str) -> Callable[..., Any]:
            register = getattr(api_router, method)

            def decorator(*args: Any, **kwargs: Any) -> Callable[..., Any]:
                def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
                    register(*args, **kwargs)(_inline(fn) if serial else fn)
                    return fn
                return wrap
            return decorator
    router = _Router()

    def failure(ex: Exception, solver: Optional[str]) -> HTTPException:
        if isinstance(ex, RegistryError):
            return coded_error(ex.status, ex.code, ex.message, robot=ex.solver or solver)
        if isinstance(ex, KinematicsError):
            status = 422
            return coded_error(status, ex.code, ex.message, robot=solver)
        from ..robot.compiler import RobotCompileError
        if isinstance(ex, RobotCompileError):
            detail = ex.detail
            detail.pop("command", None)
            return HTTPException(status_code=ex.status, detail=detail)
        raise ex

    @contextlib.contextmanager
    def using(solver: str) -> Iterator[SolverEntry]:
        """The solver's entry with its lock held; errors become coded HTTP errors."""
        from ..robot.compiler import RobotCompileError
        try:
            with registry.use(solver) as entry:
                yield entry
        except (RegistryError, KinematicsError, RobotCompileError) as ex:
            raise failure(ex, solver) from None

    def check_batch(count: int, what: str) -> None:
        if max_batch is not None and count > max_batch:
            raise coded_error(413, RobotErrorCode.BATCH_TOO_LARGE, f"{count} {what} in one request; the maximum is {max_batch}")

    def joint_rows(entry: SolverEntry, rows: List[Dict[str, float]]) -> np.ndarray:
        solver = entry.solver
        return np.array([solver.joints_from_wire(values) for values in rows]).reshape(-1, solver.num_joints)

    # ---- Solvers ------------------------------------------------------------------------------------

    @router.get("/info", tags=["Solvers"], summary="Get Kinematics Service Info", response_model=KinematicsServiceInfo)
    def info():
        return KinematicsServiceInfo(
            backends=registry.backends, default_backend=registry.default_backend, creatable=registry.creatable,
            planning_backends=registry.planning_backends,
            solvers=len(registry), limits=ServiceLimits(max_solvers=registry.max_solvers, max_upload_bytes=registry.max_upload_bytes,
                                                        max_batch=max_batch, max_samples=max_samples))

    if registry.creatable:
        @router.post(
            "/solvers", tags=["Solvers"], summary="Create Solver", status_code=201, response_model=SolverDescription,
            description="Create a solver from a robot's URDF and XRDF: upload the two files as multipart/form-data "
                        "(fields urdf, xrdf, and optionally name, tool_frame, backend), or send their text as JSON. The "
                        "solver id is the name (unique), else generated. In memory only: it lives until deleted or the "
                        "service stops.",
            openapi_extra=_create_body_schema(),
            responses={409: {"description": "NAME_TAKEN or TOO_MANY_SOLVERS"}, 413: {"description": "UPLOAD_TOO_LARGE"},
                       415: {"description": "UNSUPPORTED_MEDIA_TYPE"}, 422: {"description": "INVALID_DESCRIPTION, ..."}},
        )
        async def create(request: Request):
            cap = None if registry.max_upload_bytes is None else 2 * registry.max_upload_bytes + _UPLOAD_SLACK
            too_large = coded_error(413, RobotErrorCode.UPLOAD_TOO_LARGE, f"the request is larger than {cap} bytes")
            length = request.headers.get("content-length")
            if cap is not None and length is not None and length.isdigit() and int(length) > cap:
                raise too_large
            body = bytearray()
            async for chunk in request.stream():
                body += chunk
                if cap is not None and len(body) > cap:
                    raise too_large
            request._body = bytes(body)  # Starlette re-reads a consumed body from here (form parsing)
            content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            filenames: Tuple[Optional[str], Optional[str]] = (None, None)
            if content_type == "application/json":
                try:
                    payload = SolverCreateRequest.model_validate_json(bytes(body))
                except ValidationError as ex:
                    raise RequestValidationError(jsonable_encoder(ex.errors(include_url=False), custom_encoder={ValueError: str})) from None
                urdf, xrdf = payload.urdf, payload.xrdf
                name, tool_frame, backend = payload.name, payload.tool_frame, payload.backend
                fragments, combination = payload.fragments, payload.combination
            elif content_type == "multipart/form-data":
                if not _multipart_available():
                    raise coded_error(415, RobotErrorCode.UNSUPPORTED_MEDIA_TYPE,
                                      "multipart uploads need the python-multipart package on the server; send JSON instead")
                form = await request.form()
                try:
                    values: Dict[str, Any] = {}
                    names: Dict[str, Optional[str]] = {}
                    for key in ("urdf", "xrdf", "name", "tool_frame", "backend", "combination"):
                        item = form.get(key)
                        if item is None or isinstance(item, str):
                            values[key] = item if item not in ("", None) else None
                        else:  # an uploaded file
                            values[key] = await item.read()
                            names[key] = item.filename
                    fragment_items = form.getlist("fragment")
                    if len(fragment_items) > _MAX_FRAGMENTS:
                        raise coded_error(413, RobotErrorCode.UPLOAD_TOO_LARGE, f"at most {_MAX_FRAGMENTS} fragments")
                    fragments = [item if isinstance(item, str) else await item.read() for item in fragment_items] or None
                    combination = values["combination"]
                    unknown = sorted(set(form.keys()) - {"urdf", "xrdf", "name", "tool_frame", "backend", "fragment",
                                                         "combination"})
                finally:
                    await form.close()
                if unknown:
                    raise coded_error(422, RobotErrorCode.INVALID_DESCRIPTION, f"unknown form field(s) {unknown}")
                missing = [k for k in ("urdf", "xrdf") if values.get(k) is None]
                if missing:
                    raise coded_error(422, RobotErrorCode.INVALID_DESCRIPTION, f"missing file(s) {missing}: upload urdf and xrdf")
                urdf, xrdf = values["urdf"], values["xrdf"]
                name, tool_frame, backend = values["name"], values["tool_frame"], values["backend"]
                for key in ("name", "tool_frame", "backend"):
                    if isinstance(values[key], bytes):
                        raise coded_error(422, RobotErrorCode.INVALID_DESCRIPTION, f"'{key}' must be a text field, not a file")
                filenames = (names.get("urdf"), names.get("xrdf"))
            else:
                raise coded_error(415, RobotErrorCode.UNSUPPORTED_MEDIA_TYPE,
                                  f"send multipart/form-data (files urdf, xrdf) or application/json, not '{content_type or 'nothing'}'")
            create = functools.partial(registry.create, urdf, xrdf, name=name, tool_frame=tool_frame, backend=backend,
                                       urdf_filename=filenames[0], xrdf_filename=filenames[1], fragments=fragments,
                                       combination=combination)
            try:
                entry = create() if serial else await run_in_threadpool(create)
            except RegistryError as ex:
                raise failure(ex, name) from None
            with entry.lock:
                return entry.describe()

    @router.get("/solvers", tags=["Solvers"], summary="List Solvers", response_model=List[SolverDescription])
    def list_solvers():
        result = []
        for entry in registry.entries():
            with entry.lock:
                if not entry.deleted:
                    result.append(entry.describe())
        return result

    @router.get(solver_path, tags=["Solvers"], summary="Get Solver", response_model=SolverDescription,
                responses={404: {"description": "UNKNOWN_SOLVER"}})
    def get_solver(solver: str):
        with using(solver) as entry:
            return entry.describe()

    if registry.creatable:
        @router.delete(solver_path, tags=["Solvers"], summary="Delete Solver", status_code=204,
                       response_class=Response, responses={404: {"description": "UNKNOWN_SOLVER"}})
        def delete_solver(solver: str):
            try:
                registry.delete(solver)
            except RegistryError as ex:
                raise failure(ex, solver) from None
            return Response(status_code=204)

    @router.get(solver_path + "/files/{name}", tags=["Solvers"], summary="Download Solver File", response_class=FileResponse,
                responses={200: {"content": {"application/octet-stream": {}}}, 404: {"description": "No such solver or file."}})
    def download_file(solver: str, name: str):
        with using(solver) as entry:
            path = entry.file_path(name)
            data = path.read_bytes()
        media = "application/xml" if name.lower().endswith(".urdf") else "application/yaml"
        return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---- Kinematics ---------------------------------------------------------------------------------------

    @router.post(solver_path + "/fk", tags=["Kinematics"], summary="Forward Kinematics", response_model=FkResponse,
                 description="Poses of a frame (default: the active tool frame) in the robot base frame, for a batch of joint "
                             "positions (by joint name, degrees or meters; every joint is required).")
    def fk(solver: str, request: FkRequest):
        check_batch(len(request.joints), "joint configurations")
        with using(solver) as entry:
            frame = entry.frame(request.frame)
            q = joint_rows(entry, request.joints)
            positions, orientations = entry.solver.fk(q, frame) if len(q) else (np.zeros((0, 3)), np.zeros((0, 4)))
            base = entry.solver.base_frame
        return FkResponse(solver=solver, frame=frame, reference_frame=base,
                          poses=[Pose.from_numpy(p, o) for p, o in zip(positions, orientations)])

    def solve(solver: str, request: IkRequest) -> Tuple[SolverEntry, str, Any]:
        check_batch(len(request.targets), "targets")
        with using(solver) as entry:
            frame = entry.frame(request.frame)
            seed = entry.solver.joints_from_wire(request.seed) if request.seed is not None else None
            if not request.targets:
                return entry, frame, None
            positions, orientations = poses_to_arrays(request.targets)
            solution = entry.solver.ik(positions, orientations, frame, seed=seed, sequential=request.sequential,
                                       position_tolerance=request.position_tolerance,
                                       orientation_tolerance=math.radians(request.orientation_tolerance))
            wire = [entry.solver.joints_to_wire(q) if not np.isnan(q).any() else None for q in solution.q]
        return entry, frame, (solution, wire)

    @router.post(solver_path + "/ik", tags=["Kinematics"], summary="Inverse Kinematics", response_model=IkResponse,
                 description="Joint positions (degrees or meters) that put a frame at each target pose (in the robot base frame). "
                             "With sequential (the default), each target is seeded with the previous solution, so the solutions "
                             "stay on one IK branch along a path. An unsolved target is not an HTTP error: its result has success "
                             "false and an IK_UNREACHABLE error.")
    def ik(solver: str, request: IkRequest):
        entry, frame, answer = solve(solver, request)
        if answer is None:
            return IkResponse(solver=solver, frame=frame, success=True, results=[])
        solution, wire = answer
        results = []
        for i in range(len(solution)):
            ok = bool(solution.success[i])
            attempted = wire[i] is not None
            results.append(IkResult(
                success=ok, joints=wire[i],
                position_error=float(solution.position_error[i]) if attempted else None,
                orientation_error=math.degrees(float(solution.orientation_error[i])) if attempted else None,
                error=None if ok else RobotError(
                    code=RobotErrorCode.IK_UNREACHABLE, robot=solver, step=i,
                    message=f"no inverse kinematics solution within {request.position_tolerance} m and "
                            f"{request.orientation_tolerance} deg for target {i}"),
            ))
        return IkResponse(solver=solver, frame=frame, success=solution.all_success, results=results)

    @router.post(solver_path + "/reachable", tags=["Kinematics"], summary="Reachability", response_model=ReachableResponse,
                 description="Whether each target pose has an IK solution within the tolerances (like UR's "
                             "get_inverse_kin_has_solution); the same request as ik.")
    def reachable(solver: str, request: IkRequest):
        entry, frame, answer = solve(solver, request)
        flags = [] if answer is None else [bool(s) for s in answer[0].success]
        return ReachableResponse(solver=solver, frame=frame, reachable=flags, all_reachable=all(flags))

    @router.post(solver_path + "/jacobian", tags=["Kinematics"], summary="Jacobian", response_model=JacobianResponse,
                 description="The geometric Jacobian of a frame's origin in the robot base frame at each configuration, in wire "
                             "units: joint velocities in deg/s (revolute) or m/s (prismatic) to linear velocity (m/s) and angular "
                             "velocity (deg/s). Also Yoshikawa's manipulability (of the SI Jacobian), near zero at singularities.")
    def jacobian(solver: str, request: JacobianRequest):
        check_batch(len(request.joints), "joint configurations")
        with using(solver) as entry:
            frame = entry.frame(request.frame)
            q = joint_rows(entry, request.joints)
            jacobians = entry.solver.jacobian(q, frame) if len(q) else np.zeros((0, 6, entry.solver.num_joints))
            result = [jacobian_to_wire(entry.solver, j) for j in jacobians]
            names, base = entry.solver.joint_names, entry.solver.base_frame
        return JacobianResponse(solver=solver, frame=frame, reference_frame=base, joint_names=names, jacobians=result)

    # ---- Trajectories -------------------------------------------------------------------------------------

    def jump_parameters(request: Any) -> Dict[str, float]:
        return {k: getattr(request, k) for k in ("jump_margin", "max_step_revolute", "max_step_prismatic")
                if k in request.model_fields_set}

    @router.post(solver_path + "/trajectories/sample", tags=["Trajectories"], summary="Sample a Move",
                 response_model=TrajectoryResponse,
                 description="Sample one move (set, ptp, lin, circ, or plan) from start at dt, exactly as the RobotAPI compiles a move: "
                             "trapezoidal timing (rounded up to whole steps), IK per sample (continuity-seeded), joint limits, "
                             "and the jump check (velocity limit × dt × jump_margin per step). Nothing is executed. A failure is a "
                             "422 with a coded detail (IK_UNREACHABLE, JOINT_LIMIT, JOINT_JUMP, DURATION_TOO_SHORT, TOO_MANY_STEPS).")
    def sample(solver: str, request: TrajectoryRequest):
        from ..robot.compiler import compile_move
        move = request.move
        if move.reference != "base":
            raise coded_error(422, RobotErrorCode.REFERENCE_UNAVAILABLE,
                              "the kinematics service has no scene: targets must use reference 'base'", robot=solver)
        with using(solver) as entry:
            binding = entry.limits_binding(request.velocity_limits, request.acceleration_limits, jump_parameters(request))
            start = entry.solver.joints_from_wire(request.start)
            frame = entry.solver.check_frame(move.tool_frame or binding.tool_frame)
            samples = compile_move(binding, move, start, request.dt, None, solver, max_steps=max_samples)
            joints = [entry.solver.joints_to_wire(q) for q in samples]
            poses = None
            if request.include_poses:
                positions, orientations = entry.solver.fk(samples, frame)
                poses = [Pose.from_numpy(p, o) for p, o in zip(positions, orientations)]
        return TrajectoryResponse(solver=solver, tool_frame=frame, dt=request.dt, steps=len(joints),
                                  duration=len(joints) * request.dt, joints=joints, poses=poses)

    @router.post(solver_path + "/trajectories/check", tags=["Trajectories"], summary="Check a Path",
                 response_model=PathCheckResponse,
                 description="Check a joint path (one configuration per step of dt) against the joint limits and the jump "
                             "check (ADR-0013). A failing path is not an HTTP error: ok is false and errors name the first "
                             "JOINT_LIMIT and the first JOINT_JUMP violation.")
    def check(solver: str, request: PathCheckRequest):
        check_batch(len(request.joints), "joint configurations")
        with using(solver) as entry:
            s = entry.solver
            binding = entry.limits_binding(request.velocity_limits, request.acceleration_limits, jump_parameters(request))
            q = joint_rows(entry, request.joints)
            errors: List[RobotError] = []
            if len(q):
                start = s.joints_from_wire(request.start) if request.start is not None else q[0]
                violation = s.limit_violation(q)
                if violation is not None:
                    step, i = violation
                    joint = s.joints[i]
                    errors.append(RobotError(
                        code=RobotErrorCode.JOINT_LIMIT, robot=solver, step=step, joint=joint.name,
                        message=f"{joint.name} is {to_wire(joint, q[step, i]):.4g} at step {step}, outside its limits "
                                f"[{to_wire(joint, joint.lower):.4g}, {to_wire(joint, joint.upper):.4g}]"))
                jump = check_steps(start, q, binding.max_steps(request.dt))
                if jump is not None:
                    joint = s.joints[jump.joint]
                    unit = "deg" if joint.type == "revolute" else "m"
                    errors.append(RobotError(
                        code=RobotErrorCode.JOINT_JUMP, robot=solver, step=jump.step, joint=joint.name,
                        message=f"{joint.name} changes by {to_wire(joint, jump.change):.4g} {unit} at step {jump.step} (at most "
                                f"{to_wire(joint, jump.allowed):.4g} {unit} per {request.dt:.4g} s step)"))
        return PathCheckResponse(solver=solver, ok=not errors, steps=len(request.joints), errors=errors)

    # ---- Tool Frames ---------------------------------------------------------------------------------------

    def frames_of(entry: SolverEntry) -> SolverFrames:
        s = entry.solver
        return SolverFrames(solver=entry.id, base_frame=s.base_frame, frames=list(s.frame_names), tool_frames=list(s.tool_frames),
                            default_tool_frame=s.default_tool_frame, tool_frame=entry.tool_frame,
                            flange_frame=entry.flange_frame, tcp_offset=entry.tcp_offset)

    @router.get(solver_path + "/frames", tags=["Tool Frames"], summary="List Frames", response_model=SolverFrames)
    def frames(solver: str):
        with using(solver) as entry:
            return frames_of(entry)

    @router.get(solver_path + "/config/read", tags=["Tool Frames"], summary="Read Solver Configuration",
                response_model=SolverConfig)
    def config_read(solver: str):
        with using(solver) as entry:
            return entry.config()

    @router.post(solver_path + "/config/write", tags=["Tool Frames"], summary="Write Solver Configuration",
                 response_model=SolverConfig,
                 description="Set the active tool frame (the default frame of every request) and/or the TCP offset: the 'tcp' "
                             "frame's pose relative to the flange frame (like UR's set_tcp; it becomes the active tool frame "
                             "unless tool_frame is given too), or clear it. Omitted fields are unchanged. A RobotAPI's robots "
                             "are read-only here (409 READ_ONLY).")
    def config_write(solver: str, request: SolverConfig):
        with using(solver) as entry:
            return entry.configure(request)

    # ---- Collision ---------------------------------------------------------------------------------------------

    @router.post(solver_path + "/collision/self", tags=["Collision"], summary="Self-Collision",
                 response_model=SelfCollisionResponse,
                 description="Check configurations (one, or a path) against the robot's own collision spheres from the XRDF "
                             "(self_collision geometry and ignore pairs). No world: obstacles are never considered. A robot "
                             "without spheres is always collision-free.")
    def self_collision(solver: str, request: SelfCollisionRequest):
        check_batch(len(request.joints), "joint configurations")
        with using(solver) as entry:
            q = joint_rows(entry, request.joints)
            pairs = entry.solver.self_collision(q) if len(q) else []
        results = [SelfCollisionResult(in_collision=bool(p), pairs=[list(pair) for pair in p]) for p in pairs]
        first = next((i for i, r in enumerate(results) if r.in_collision), None)
        return SelfCollisionResponse(solver=solver, collision_free=first is None, first_collision=first, results=results)

    @router.post(solver_path + "/collision/spheres", tags=["Collision"], summary="Collision Spheres",
                 response_model=CollisionSpheres,
                 description="The robot's self-collision spheres placed at a configuration, in the robot base frame.")
    def collision_spheres(solver: str, request: CollisionSpheresRequest):
        with using(solver) as entry:
            q = entry.solver.joints_from_wire(request.joints)
            spheres = entry.solver.collision_spheres(q)
            base = entry.solver.base_frame
        return CollisionSpheres(solver=solver, reference_frame=base, spheres=[
            CollisionSphere(frame=f, center=Vector3.from_numpy(c), radius=float(r)) for f, c, r in spheres])

    # ---- World ----------------------------------------------------------------------------------------------------

    def world_of(entry: SolverEntry) -> WorldDescription:
        planner = entry.planner
        if planner is None:
            raise RegistryError(RobotErrorCode.UNSUPPORTED, f"the {entry.backend} backend has no motion planner",
                                solver=entry.id)
        if entry.read_only:
            return WorldDescription(solver=entry.id, reference="world" if planner.base_pose is not None else "base",
                                    base_pose=planner.base_pose,
                                    obstacles=[o.model_dump(mode="json") for o in planner.obstacles],
                                    revision=planner.revision, backend=planner.backend)
        world = entry.world
        return WorldDescription(solver=entry.id, reference=world.reference if world else "base",
                                base_pose=world.base_pose if world else None,
                                obstacles=[o.model_dump(mode="json") for o in (world.obstacles if world else [])],
                                revision=planner.revision, backend=planner.backend)

    @router.get(solver_path + "/world", tags=["World"], summary="Read World", response_model=WorldDescription,
                description="The solver's obstacles: those set through PUT (a service solver), or a RobotAPI robot's scene "
                            "obstacles (read-only). 422 UNSUPPORTED if the backend has no planner.")
    def world_read(solver: str):
        with using(solver) as entry:
            return world_of(entry)

    @router.put(solver_path + "/world", tags=["World"], summary="Set World", response_model=WorldDescription,
                description="Replace the solver's obstacles: library-neutral shapes with poses (cuboid, sphere, cylinder, "
                            "capsule, mesh; meters), in the robot base frame (reference base) or a world frame with the "
                            "robot's base_pose. Plan moves (trajectories/sample) and collision/world use them. In memory, "
                            "per solver. A RobotAPI robot's world is its scene: 409 READ_ONLY.")
    def world_write(solver: str, request: PlanningWorld):
        check_batch(len(request.obstacles), "obstacles")
        check_batch(sum(len(getattr(o, "vertices", [])) for o in request.obstacles), "mesh vertices")
        with using(solver) as entry:
            entry.set_world(request)
            return world_of(entry)

    @router.delete(solver_path + "/world", tags=["World"], summary="Clear World", response_model=WorldDescription)
    def world_clear(solver: str):
        with using(solver) as entry:
            entry.set_world(None)
            return world_of(entry)

    @router.post(solver_path + "/collision/world", tags=["World"], summary="World Collision",
                 response_model=WorldCollisionResponse,
                 description="Check configurations (one, or a path) against the solver's obstacles and the robot's own "
                             "collision spheres (the XRDF's world_collision and self_collision geometry).")
    def world_collision(solver: str, request: WorldCollisionRequest):
        check_batch(len(request.joints), "joint configurations")
        with using(solver) as entry:
            planner = entry.planner
            if planner is None:
                raise RegistryError(RobotErrorCode.UNSUPPORTED, f"the {entry.backend} backend has no motion planner",
                                    solver=solver)
            q = joint_rows(entry, request.joints)
            details = planner.collisions(q) if len(q) else []
        results = [WorldCollisionResult(in_collision=bool(d), detail=d) for d in details]
        first = next((i for i, r in enumerate(results) if r.in_collision), None)
        return WorldCollisionResponse(solver=solver, collision_free=first is None, first_collision=first, results=results)

    return api_router


class KinematicsApi(ApiHost):
    """The kinematics service: solvers created from uploaded URDF + XRDF, called over the web, no scene.

    :param factories: ``{backend name: SolverFactory}`` (the first is the default), e.g.
        ``{"cumotion": cumotion_solver_factory}`` in the extension.
    :param planner_factories: ``{backend name: PlannerFactory}``: backends whose solvers can plan around obstacles set
        through the API (``/solvers/{solver}/world``), e.g. ``{"cumotion": cumotion_planner_factory}``.
    :param registry: A registry to serve instead (then ``factories``, ``max_solvers``, ``max_upload_bytes``, and
        ``temp_root`` are ignored).
    :param max_solvers, max_upload_bytes: Registry limits (see :class:`SolverRegistry`).
    :param max_batch: The most targets or configurations in one request.
    :param max_samples: The most steps of one sampled trajectory.

    Every limit can be None (no limit); the defaults are high.
    :param title, description, version, port, host: See :class:`~..host.ApiHost`.

    Requests are handled one at a time on the server's own thread (see the module docstring). Stopping the API deletes
    every solver and its files (in memory only).
    """

    def __init__(
        self,
        factories: Optional[Mapping[str, SolverFactory]] = None,
        registry: Optional[SolverRegistry] = None,
        title: str = "Kinematics Service",
        description: str = "",
        version: str = "0.1.0",
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
        max_solvers: Optional[int] = DEFAULT_MAX_SOLVERS,
        max_upload_bytes: Optional[int] = DEFAULT_MAX_UPLOAD_BYTES,
        max_batch: Optional[int] = DEFAULT_MAX_BATCH,
        max_samples: Optional[int] = DEFAULT_MAX_SAMPLES,
        temp_root: Optional[str] = None,
        planner_factories: Optional[Mapping[str, PlannerFactory]] = None,
    ):
        super().__init__(title, description, version, port, host)
        for limit in (max_batch, max_samples):
            if not (limit is None or (isinstance(limit, int) and limit > 0)):
                raise ValueError("max_batch and max_samples must be positive ints or None")
        self._registry = registry if registry is not None else SolverRegistry(
            factories, max_solvers=max_solvers, max_upload_bytes=max_upload_bytes, temp_root=temp_root,
            planner_factories=planner_factories)
        self.max_batch = max_batch
        self.max_samples = max_samples
        self.add_router(kinematics_router(self._registry, max_batch, max_samples, serial=True))

    @property
    def registry(self) -> SolverRegistry:
        return self._registry

    def stop(self, timeout: float = 5.0) -> None:
        """Stop serving and delete every solver (and its files)."""
        super().stop(timeout)
        self._registry.clear()

    def api_description(self) -> str:
        backends = ", ".join(f"`{b}`" for b in self._registry.backends) or "none"
        planning = ", ".join(f"`{b}`" for b in self._registry.planning_backends) or "none"
        intro = f"{self.description}\n\n" if self.description else ""
        return intro + f"""**Kinematics service** (backends: {backends}; planning: {planning}): create a **solver** from a robot's
URDF and XRDF (`POST {KINEMATICS_PREFIX}/solvers`, multipart or JSON; optionally with end-effector XRDF fragments and a
combination), then call it by id: forward and inverse kinematics (batched), reachability, Jacobians, trajectory sampling
(`set`, `ptp`, `lin`, `circ`, and `plan` at your `dt`), a path check, tool frames and a TCP offset, self-collision, and
a **world** of obstacles you send (boxes, spheres, cylinders, capsules, meshes with poses) to plan around and check
against. **Independent of Isaac Sim's scene**: nothing here reads the stage. Requests are handled one at a time.
Solvers live in memory until deleted or the service stops. Joint values in degrees (revolute) or meters (prismatic),
keyed by joint name; poses in the robot base frame unless a world reference is given; quaternions with named
components."""

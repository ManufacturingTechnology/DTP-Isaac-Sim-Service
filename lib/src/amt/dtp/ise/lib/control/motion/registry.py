"""The kinematics service's solver registry: solvers by id, each with its lock and configuration.

- A **service registry** (``SolverRegistry(factories={...})``) creates solvers from uploaded robot descriptions
  (URDF + XRDF) through a backend's :data:`SolverFactory`, stores the files in a temporary directory per
  solver, and deletes them with the solver (or on :meth:`SolverRegistry.clear`, when the service stops).
  In memory only.
- A **read-only registry** (:meth:`SolverRegistry.from_bindings`) wraps a RobotApi's robots: their ids are the
  solver ids, they can't be created, deleted, or configured here, and their active tool frame is the robot's.

**Concurrency:** every entry has an ``RLock``; :meth:`SolverRegistry.use` holds it for the whole request, so
requests on one solver are serialized (a solver has Python-side state: tool frames, the IK configuration),
while requests on different solvers can run in parallel threads where the server allows it (a RobotApi's thread
pool; the kinematics service handles one request at a time, ADR-0010). The RobotApi's compiler takes the same lock.
The cuMotion backend adds a process-wide lock around each native call (``amt.dtp.ise.robot.kinematics``).
Evidence and rationale: ``docs/src/Agents/Specs/robot-control.md`` ("Concurrency"), ADR-0010.

Pure Python (numpy, pydantic); no fastapi.
"""
import contextlib
import dataclasses
import hashlib
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from ..transducer.models.pose import Pose
from .kinematics import KinematicsError, KinematicsSolver
from .planning import MotionPlanner, PlanningWorld
from .models import RobotErrorCode, SolverConfig, SolverDescription, SolverFile

if TYPE_CHECKING:  # robot.compiler imports this package's kinematics; import it lazily at runtime
    from ..robot.compiler import RobotBinding


SOLVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TCP_FRAME = "tcp"
DEFAULT_MAX_SOLVERS = 1024  # limits: None means no limit
DEFAULT_MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class SolverSource:
    """What a backend builds a solver from: the uploaded files (stored in ``directory``) and their text."""
    urdf_path: Path
    xrdf_path: Path
    urdf_text: str
    xrdf_text: str
    directory: Path
    tool_frame: Optional[str] = None


SolverFactory = Callable[[SolverSource], KinematicsSolver]
"""Builds a solver from a :class:`SolverSource`; raises (e.g. ``ValueError``, ``RuntimeError``) if the robot
description can't be loaded."""

PlannerFactory = Callable[[KinematicsSolver], MotionPlanner]
"""Builds a :class:`~.planning.MotionPlanner` (with an empty world) for a solver of the same backend."""


class RegistryError(Exception):
    """A registry request can't be served: ``code`` (a robot error code) and the HTTP ``status``."""

    def __init__(self, code: str, message: str, status: int = 422, solver: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message
        self.status = status
        self.solver = solver


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_stem(filename: Optional[str], default: str = "robot") -> str:
    stem = Path((filename or "").replace("\\", "/").split("/")[-1]).stem
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "_", stem).strip(" .")
    return stem[:100] or default


def _decode(data: Union[bytes, str], kind: str) -> str:
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RegistryError(RobotErrorCode.INVALID_DESCRIPTION, f"the {kind.upper()} file is not UTF-8 text") from None


class SolverEntry:
    """One solver of a registry, with its lock, active tool frame, TCP offset, and files."""

    def __init__(self, id: str, solver: KinematicsSolver, *, backend: Optional[str] = None,
                 files: Optional[List[Tuple[SolverFile, Path]]] = None, directory: Optional[Path] = None,
                 binding: Optional["RobotBinding"] = None, lock: Optional[threading.RLock] = None):
        self.id = id
        self._solver = solver
        self.backend = backend or solver.backend
        self.lock = lock or threading.RLock()
        self.binding = binding  # a RobotApi's robot: read-only here
        self.files: List[Tuple[SolverFile, Path]] = list(files or [])
        self.directory = directory
        self.created = _now()
        self.deleted = False
        self._tool_frame = solver.default_tool_frame
        self._flange_frame = solver.default_tool_frame
        self._tcp_offset: Optional[Pose] = None
        self.planner_factory: Optional[PlannerFactory] = None
        self._planner: Optional[MotionPlanner] = None
        self.world: Optional[PlanningWorld] = None  # the obstacles set through the API (service solvers)

    # ---- state ----------------------------------------------------------------------------------------

    @property
    def solver(self) -> KinematicsSolver:
        """The solver (a RobotApi robot's active combination's)."""
        return self.binding.solver if self.binding is not None else self._solver

    @property
    def planner(self) -> Optional[MotionPlanner]:
        """The motion planner: a RobotApi robot's own (its world is the scene), or one built by the backend's planner
        factory on first use (its world is set through the API); None if the backend has no planner."""
        if self.binding is not None:
            return self.binding.active_planner()
        if self._planner is None and self.planner_factory is not None:
            self._planner = self.planner_factory(self._solver)
        return self._planner

    def set_world(self, world: Optional[PlanningWorld]) -> None:
        """Replace the obstacles (None: none); raises ``READ_ONLY`` for a RobotApi robot, ``UNSUPPORTED`` without a
        planner."""
        if self.read_only:
            raise RegistryError(RobotErrorCode.READ_ONLY, f"solver '{self.id}' is a RobotAPI robot: its world is the "
                                f"simulation's scene", status=409, solver=self.id)
        planner = self.planner
        if planner is None:
            raise RegistryError(RobotErrorCode.UNSUPPORTED, f"the {self.backend} backend has no motion planner",
                                solver=self.id)
        world = world or PlanningWorld(reference="base", obstacles=[])
        try:
            planner.set_world(world, world.base_pose)
        except ValueError as ex:
            raise RegistryError(RobotErrorCode.INVALID_OBSTACLE, str(ex), solver=self.id) from None
        self.world = world

    @property
    def read_only(self) -> bool:
        return self.binding is not None

    @property
    def tool_frame(self) -> str:
        """The active tool frame: the default ``frame`` of every request (a RobotApi robot's own)."""
        return self.binding.tool_frame if self.binding is not None else self._tool_frame

    @property
    def flange_frame(self) -> str:
        return self.binding.flange_frame if self.binding is not None else self._flange_frame

    @property
    def tcp_offset(self) -> Optional[Pose]:
        return self.binding.tcp_offset if self.binding is not None else self._tcp_offset

    def frame(self, frame: Optional[str]) -> str:
        """``frame``, or the active tool frame if None; raises ``UNKNOWN_FRAME``."""
        return self.solver.check_frame(frame if frame is not None else self.tool_frame)

    def describe(self) -> SolverDescription:
        from .server import kinematics_description  # the wire conversion lives with the routes
        base = kinematics_description(self.id, self.solver).model_dump()
        base["backend"] = self.backend
        return SolverDescription(
            **base, tool_frame=self.tool_frame, flange_frame=self.flange_frame, tcp_offset=self.tcp_offset,
            capabilities=list(self.solver.capabilities), files=[f for f, _ in self.files], read_only=self.read_only,
            created=self.created)

    def config(self) -> SolverConfig:
        return SolverConfig(tool_frame=self.tool_frame, tcp_offset=self.tcp_offset)

    def configure(self, config: SolverConfig) -> SolverConfig:
        """Apply a configuration write (omitted fields unchanged); raises ``READ_ONLY`` for a RobotApi's robot."""
        if self.read_only:
            raise RegistryError(RobotErrorCode.READ_ONLY, f"solver '{self.id}' is a RobotAPI robot: configure it with "
                                f"POST /api/robot/config/write", status=409, solver=self.id)
        fields = config.model_fields_set
        if config.clear_tcp and config.tcp_offset is not None:
            raise RegistryError(RobotErrorCode.CONFLICT, "give tcp_offset or clear_tcp, not both", solver=self.id)
        solver = self.solver
        if (config.clear_tcp or config.tcp_offset is not None) and "frames" not in solver.capabilities:
            raise RegistryError(RobotErrorCode.UNSUPPORTED, f"the {solver.backend} solver can't define a TCP frame",
                                solver=self.id)
        tool_frame = self._tool_frame
        if config.clear_tcp:
            if TCP_FRAME in solver.frame_names:
                solver.set_frames({TCP_FRAME: None})
            self._tcp_offset = None
            if tool_frame == TCP_FRAME:
                tool_frame = solver.default_tool_frame
        if config.tcp_offset is not None:
            p, o = config.tcp_offset.to_numpy()
            solver.set_frames({TCP_FRAME: (self._flange_frame, p, o)})
            self._tcp_offset = config.tcp_offset
            if "tool_frame" not in fields or config.tool_frame is None:
                tool_frame = TCP_FRAME
        if "tool_frame" in fields and config.tool_frame is not None:
            tool_frame = solver.check_frame(config.tool_frame)
        self._tool_frame = tool_frame
        return self.config()

    def limits_binding(self, velocity_limits: Optional[Mapping[str, float]] = None,
                       acceleration_limits: Optional[Mapping[str, float]] = None,
                       jump: Optional[Mapping[str, float]] = None) -> "RobotBinding":
        """A :class:`~..robot.compiler.RobotBinding` carrying this solver's effective limits (ADR-0013), for
        sampling and checking trajectories: a RobotApi robot's own binding with the overrides on top, or a
        binding of this solver. ``jump``: ``jump_margin``, ``max_step_revolute``, ``max_step_prismatic``."""
        from ..robot.compiler import RobotBinding
        names = self.solver.joint_names
        unknown = sorted({n for n in list(velocity_limits or {}) + list(acceleration_limits or {}) if n not in names})
        if unknown:
            raise KinematicsError(RobotErrorCode.UNKNOWN_JOINT, f"unknown joint(s) {unknown}; joints: {names}")
        for name, value in list((velocity_limits or {}).items()) + list((acceleration_limits or {}).items()):
            if not value > 0:
                raise KinematicsError(RobotErrorCode.JOINT_LIMIT, f"the limit of '{name}' must be positive, not {value}")
        jump = dict(jump or {})
        if self.binding is not None:
            base = self.binding
            return dataclasses.replace(
                base, velocity_limits={**(base.velocity_limits or {}), **(velocity_limits or {})},
                acceleration_limits={**(base.acceleration_limits or {}), **(acceleration_limits or {})},
                jump_margin=jump.get("jump_margin", base.jump_margin),
                max_step_revolute=jump.get("max_step_revolute", base.max_step_revolute),
                max_step_prismatic=jump.get("max_step_prismatic", base.max_step_prismatic))
        return RobotBinding(self.solver, component="", velocity_limits=dict(velocity_limits or {}),
                            acceleration_limits=dict(acceleration_limits or {}), tool_frame=self.tool_frame,
                            flange_frame=self.flange_frame, planner=self.planner, **jump)

    def file_path(self, name: str) -> Path:
        for info, path in self.files:
            if info.name == name:
                return path
        raise RegistryError(RobotErrorCode.UNKNOWN_SOLVER, f"solver '{self.id}' has no file '{name}'; files: "
                            f"{[f.name for f, _ in self.files]}", status=404, solver=self.id)

    def _dispose(self) -> None:
        self.deleted = True
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)


class SolverRegistry:
    """Solvers by id.

    :param factories: ``{backend name: SolverFactory}``; the first is the default. Without factories the
        registry is read-only (see :meth:`from_bindings`).
    :param max_solvers: The most solvers at once (None: no limit).
    :param max_upload_bytes: The largest URDF or XRDF file (None: no limit).
    :param temp_root: Where the per-solver directories are made (default: the system's temporary directory).
    """

    def __init__(self, factories: Optional[Mapping[str, SolverFactory]] = None, max_solvers: Optional[int] = DEFAULT_MAX_SOLVERS,
                 max_upload_bytes: Optional[int] = DEFAULT_MAX_UPLOAD_BYTES, temp_root: Optional[Union[str, Path]] = None,
                 planner_factories: Optional[Mapping[str, PlannerFactory]] = None):
        if not (max_solvers is None or (isinstance(max_solvers, int) and max_solvers > 0)):
            raise ValueError("max_solvers must be a positive int or None")
        if not (max_upload_bytes is None or (isinstance(max_upload_bytes, int) and max_upload_bytes > 0)):
            raise ValueError("max_upload_bytes must be a positive int or None")
        self._factories: Dict[str, SolverFactory] = dict(factories or {})
        self._planner_factories: Dict[str, PlannerFactory] = dict(planner_factories or {})
        for name, factory in self._factories.items():
            if not callable(factory):
                raise TypeError(f"the factory of backend '{name}' is not callable")
        self.max_solvers = max_solvers
        self.max_upload_bytes = max_upload_bytes
        self._temp_root = Path(temp_root) if temp_root is not None else None
        self._entries: Dict[str, SolverEntry] = {}
        self._reserved: set = set()
        self._lock = threading.Lock()

    @classmethod
    def from_bindings(cls, robots: Mapping[str, "RobotBinding"]) -> "SolverRegistry":
        """A read-only registry of a RobotApi's robots (solver id = robot id), sharing nothing but the solvers."""
        registry = cls(max_solvers=max(1, len(robots)))
        for id, binding in robots.items():
            if not isinstance(binding.solver, KinematicsSolver):
                raise TypeError(f"robot '{id}': {binding.solver!r} is not a KinematicsSolver")
            registry._entries[id] = SolverEntry(id, binding.solver, binding=binding)
        return registry

    # ---- queries ------------------------------------------------------------------------------------------

    @property
    def creatable(self) -> bool:
        return bool(self._factories)

    @property
    def backends(self) -> List[str]:
        return list(self._factories)

    @property
    def planning_backends(self) -> List[str]:
        """The backends that can plan around obstacles set through the API."""
        return list(self._planner_factories)

    @property
    def default_backend(self) -> Optional[str]:
        return next(iter(self._factories), None)

    def __len__(self) -> int:
        return len(self._entries)

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._entries)

    def entries(self) -> List[SolverEntry]:
        with self._lock:
            return list(self._entries.values())

    def get(self, id: str) -> SolverEntry:
        with self._lock:
            entry = self._entries.get(id)
        if entry is None:
            raise RegistryError(RobotErrorCode.UNKNOWN_SOLVER, f"no solver '{id}'; solvers: {self.ids()}", status=404, solver=id)
        return entry

    @contextlib.contextmanager
    def use(self, id: str) -> Iterator[SolverEntry]:
        """The entry, with its lock held (``UNKNOWN_SOLVER`` if it was deleted meanwhile)."""
        entry = self.get(id)
        with entry.lock:
            if entry.deleted:
                raise RegistryError(RobotErrorCode.UNKNOWN_SOLVER, f"solver '{id}' was deleted", status=404, solver=id)
            yield entry

    # ---- create and delete ---------------------------------------------------------------------------------

    def create(self, urdf: Union[bytes, str], xrdf: Union[bytes, str], name: Optional[str] = None,
               tool_frame: Optional[str] = None, backend: Optional[str] = None, urdf_filename: Optional[str] = None,
               xrdf_filename: Optional[str] = None, fragments: Optional[Sequence[Union[bytes, str, Mapping]]] = None,
               combination: Optional[Union[bytes, str, Mapping]] = None) -> SolverEntry:
        """Create a solver from a URDF and an XRDF (bytes or text). With end-effector ``fragments`` (XRDF fragment
        text or dicts) and a ``combination``, the XRDF is first merged into that combination's XRDF (ADR-0012, the
        same merge the RobotAPI's combinations use); the solver loads the merged one. Raises :class:`RegistryError`."""
        if not self.creatable:
            raise RegistryError(RobotErrorCode.READ_ONLY, "solvers can't be created here", status=405)
        backend = backend or self.default_backend
        factory = self._factories.get(backend)
        if factory is None:
            raise RegistryError(RobotErrorCode.UNKNOWN_BACKEND, f"unknown backend '{backend}'; backends: {self.backends}")
        for kind, data in (("urdf", urdf), ("xrdf", xrdf)):
            size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
            if self.max_upload_bytes is not None and size > self.max_upload_bytes:
                raise RegistryError(RobotErrorCode.UPLOAD_TOO_LARGE, f"the {kind.upper()} has {size} bytes, more than the "
                                    f"maximum of {self.max_upload_bytes}", status=413)
            if size == 0:
                raise RegistryError(RobotErrorCode.INVALID_DESCRIPTION, f"the {kind.upper()} is empty")
        urdf_text, xrdf_text = _decode(urdf, "urdf"), _decode(xrdf, "xrdf")
        robot_xrdf_text: Optional[str] = None
        extra_files: List[Tuple[str, str, str]] = []  # (kind, file name, text)
        if fragments or combination is not None:
            if not fragments or combination is None:
                raise RegistryError(RobotErrorCode.INVALID_FRAGMENT, "give both fragments and a combination (or neither)")
            robot_xrdf_text = xrdf_text
            xrdf_text, extra_files = _merge_upload(xrdf_text, fragments, combination, self.max_upload_bytes, urdf_text)
        if name is not None and not SOLVER_ID_PATTERN.match(name):
            raise RegistryError(RobotErrorCode.INVALID_NAME, f"invalid solver name {name!r}: use "
                                f"{SOLVER_ID_PATTERN.pattern}")
        with self._lock:
            if self.max_solvers is not None and len(self._entries) + len(self._reserved) >= self.max_solvers:
                raise RegistryError(RobotErrorCode.TOO_MANY_SOLVERS, f"the service already has {self.max_solvers} solvers "
                                    f"(the maximum); delete one first", status=409)
            if name is not None and (name in self._entries or name in self._reserved):
                raise RegistryError(RobotErrorCode.NAME_TAKEN, f"a solver named '{name}' exists", status=409, solver=name)
            id = name
            while id is None or id in self._entries or id in self._reserved:
                id = f"solver-{uuid.uuid4().hex[:8]}"
            self._reserved.add(id)
        directory: Optional[Path] = None
        try:
            directory = Path(tempfile.mkdtemp(prefix=f"amt-kinematics-{id}-", dir=self._temp_root))
            stem_u = _safe_stem(urdf_filename)
            stem_x = _safe_stem(xrdf_filename, stem_u)
            files: List[Tuple[SolverFile, Path]] = []
            uploads = [("urdf", f"{stem_u}.urdf", urdf_text), ("xrdf", f"{stem_x}.xrdf", xrdf_text)]
            if robot_xrdf_text is not None:
                uploads += [("robot_xrdf", f"{stem_x}.robot.xrdf", robot_xrdf_text)] + extra_files
            for kind, file_name, text in uploads:
                path = directory / file_name
                data = text.encode("utf-8")
                path.write_bytes(data)
                files.append((SolverFile(name=path.name, kind=kind, size=len(data), sha256=hashlib.sha256(data).hexdigest()), path))
            source = SolverSource(urdf_path=files[0][1], xrdf_path=files[1][1], urdf_text=urdf_text, xrdf_text=xrdf_text,
                                  directory=directory, tool_frame=tool_frame)
            try:
                solver = factory(source)
            except RegistryError:
                raise
            except KinematicsError as ex:
                raise RegistryError(ex.code, ex.message) from None
            except Exception as ex:  # the backend's own errors: a description it can't load
                raise RegistryError(RobotErrorCode.INVALID_DESCRIPTION,
                                    f"the {backend} backend can't load this robot: {type(ex).__name__}: {ex}") from None
            if not isinstance(solver, KinematicsSolver):
                raise TypeError(f"the factory of backend '{backend}' returned {solver!r}, not a KinematicsSolver")
            entry = SolverEntry(id, solver, backend=backend, files=files, directory=directory)
            entry.planner_factory = self._planner_factories.get(backend)
        except BaseException:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
            with self._lock:
                self._reserved.discard(id)
            raise
        with self._lock:
            self._reserved.discard(id)
            self._entries[id] = entry
        return entry

    def delete(self, id: str) -> None:
        """Delete a solver and its files (waits for a request using it to finish)."""
        if not self.creatable:
            raise RegistryError(RobotErrorCode.READ_ONLY, "solvers can't be deleted here", status=405, solver=id)
        entry = self.get(id)
        with entry.lock:
            with self._lock:
                if self._entries.get(id) is not entry:
                    raise RegistryError(RobotErrorCode.UNKNOWN_SOLVER, f"solver '{id}' was deleted", status=404, solver=id)
                del self._entries[id]
            entry._dispose()

    def clear(self) -> None:
        """Delete every solver and its files (when the service stops)."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            with entry.lock:
                entry._dispose()


def _merge_upload(xrdf_text: str, fragments: Sequence[Union[bytes, str, Mapping]], combination: Union[bytes, str, Mapping],
                  max_bytes: Optional[int], urdf_text: Optional[str] = None) -> Tuple[str, List[Tuple[str, str, str]]]:
    """The merged XRDF text and the files to keep (fragments, combination) for an upload with fragments."""
    import json  # noqa: PLC0415
    from .endeffector import Combination, FragmentError, load_fragment, load_yaml, merge_xrdf, render_xrdf, source_line
    files: List[Tuple[str, str, str]] = []
    try:
        loaded = {}
        for i, item in enumerate(fragments):
            if isinstance(item, (bytes, str)):
                text = _decode(item, "fragment")
                if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
                    raise RegistryError(RobotErrorCode.UPLOAD_TOO_LARGE, f"fragment {i} is larger than {max_bytes} bytes", status=413)
            else:
                text = json.dumps(item, indent=2)
            fragment = load_fragment(text if isinstance(item, (bytes, str)) else dict(item))
            if fragment.name in loaded:
                raise FragmentError(f"two fragments are named '{fragment.name}'")
            loaded[fragment.name] = fragment
            files.append(("fragment", f"fragment-{_safe_stem(fragment.name, f'fragment{i}')}.yaml", text))
        if isinstance(combination, (bytes, str)):
            combination_text = _decode(combination, "combination")
            combination_data = load_yaml(combination_text)
        else:
            combination_data = dict(combination)
            combination_text = json.dumps(combination_data, indent=2)
        try:
            spec = Combination.model_validate(combination_data)
        except Exception as ex:  # noqa: BLE001 (a pydantic ValidationError)
            raise FragmentError(f"combination: {ex}") from None
        files.append(("combination", "combination.yaml", combination_text))
        robot = load_yaml(xrdf_text)
        if not isinstance(robot, dict):
            raise RegistryError(RobotErrorCode.INVALID_DESCRIPTION, "the XRDF is not a YAML mapping")
        try:
            merged = merge_xrdf(robot, loaded, spec, urdf_text=urdf_text)
        except Exception as ex:  # noqa: BLE001 (a URDF that is not XML: the backend reports it)
            if isinstance(ex, FragmentError):
                raise
            merged = merge_xrdf(robot, loaded, spec)
    except FragmentError as ex:
        raise RegistryError(RobotErrorCode.INVALID_FRAGMENT, str(ex)) from None
    header = ["Merged XRDF (kinematics service upload): robot XRDF + end-effector fragments. Sources:",
              source_line("robot.xrdf", xrdf_text)] + [source_line(n, t) for _, n, t in files]
    return render_xrdf(merged, header), files

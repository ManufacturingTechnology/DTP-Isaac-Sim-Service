"""SegmentEngine: one synchronization domain (a segment) of one or more automation components.

Clients queue command frames; every simulation step, the owner of the segment calls
:meth:`SegmentEngine.step`, which applies pending configuration writes, applies the next command frame
to all components (unless the segment is halted), reads all components, and emits the response frame.
Each frame is one row holding every component, so commands for several machines are applied on the
same step (synchronized motion across machines).

Every event the engine emits (``frame``, ``config``, ``status``) gets the next ``sequence`` and is kept in
a small retransmission buffer, so a stream can be resumed or repaired after drops or disconnects. A
failed frame halts the queue until :meth:`SegmentEngine.resume` or :meth:`SegmentEngine.clear`
(ADR-0021).

The engine has no HTTP or asyncio dependency: :class:`~.server.SegmentApi` serves it, and listeners
(:meth:`SegmentEngine.add_listener`) receive its events. Design: ``docs/src/Agents/Specs/segment-protocol.md``.
"""
import builtins
import collections
import concurrent.futures
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel

from ...logging import _logger
from ..component import (
    GENERATED_MODULE_RESERVED_NAMES,
    AutomationComponentBase,
    ComponentDefinitionError,
    ComponentWriteError,
    find_class_name_conflicts,
    id_problem,
    path_to_id,
    schema_fingerprint,
    to_class_name,
)
from .models import (
    BufferHeader,
    CommandFrameBase,
    CommandResponseBase,
    ComponentStateBase,
    ConfigEvent,
    ConfigOutcome,
    ConfigurationBase,
    ErrorCode,
    HaltInfo,
    ResponseFrameBase,
    SegmentError,
    SegmentModels,
    SegmentStatus,
    StatusEvent,
    build_segment_models,
    error_code,
)


DEFAULT_BUFFER_SIZE = 600  # 60 Hz x 10 s of outage to ride through


class QueueFullError(Exception):
    """A write would exceed the queue's maximum length."""


class QueueClearedError(Exception):
    """A frame was removed from the queue (by clear) before it was applied."""


class SegmentHaltedError(Exception):
    """The segment is halted (a frame failed): resume or clear before executing a frame."""


Monitor = Callable[[Optional[BaseModel], Dict[str, Tuple[Optional[BaseModel], Optional[BaseModel]]]], List[SegmentError]]
"""A step monitor: called by :meth:`SegmentEngine.step` (on the simulation thread, without the engine's lock) with the
frame applied this step (or None) and every component's ``(actuals, commands)`` after it; it returns the errors to
halt the segment with (each naming its component), or an empty list."""


class QueueChangedError(Exception):
    """The queue changed (another write, a clear, or a withdrawal) since the ``queue_revision`` a write
    expected, so frames compiled against the old queue would start from the wrong state."""


class OutOfRangeError(Exception):
    """A resume or retransmit point is outside the retransmission buffer."""

    def __init__(self, message: str, header: BufferHeader):
        super().__init__(message)
        self.message = message
        self.header = header

    @property
    def detail(self) -> Dict[str, Any]:
        """The body of the HTTP error: ``{code, message, header}``."""
        return {"code": ErrorCode.OUT_OF_RANGE, "message": self.message, "header": self.header.model_dump()}


class SegmentEvent:
    """One event emitted by the engine: ``event`` is ``frame``, ``config``, or ``status``. ``executed``
    tells whether a ``frame`` event's step applied a command frame. The JSON is computed once."""

    __slots__ = ("event", "sequence", "model", "executed", "_json")

    def __init__(self, event: str, sequence: int, model: BaseModel, executed: bool = False):
        self.event = event
        self.sequence = sequence
        self.model = model
        self.executed = executed
        self._json: Optional[str] = None

    @property
    def json(self) -> str:
        if self._json is None:
            self._json = self.model.model_dump_json()
        return self._json

    def __repr__(self) -> str:
        return f"SegmentEvent({self.event!r}, {self.sequence})"


@dataclass(eq=False)
class _QueueEntry:
    frame: BaseModel
    # Set for frames written through execute; resolved with the response frame of the step that applies it.
    future: Optional[concurrent.futures.Future] = None
    # Shared by the frames of one submit_many() call, so withdrawing its future removes them all.
    batch: Optional[object] = None


@dataclass(eq=False)
class _ConfigRequest:
    value: BaseModel
    future: concurrent.futures.Future


ComponentsArg = Union[AutomationComponentBase, Mapping[str, AutomationComponentBase], Sequence[AutomationComponentBase]]
Listener = Callable[[SegmentEvent], None]


class SegmentEngine:
    """The segment engine: components, models, the frame queue, the halt state, configuration writes,
    ``step()``, sequence numbers, and the retransmission buffer. Thread-safe: ``step()`` runs on the
    simulation's thread, the queue methods on any thread (e.g. the API's).

    :param components: The components to control: one AutomationComponent, a ``{id: component}``
        mapping, or a sequence (each id is then the component's name converted to an identifier, e.g.
        ``AMT_VM_A1``).
    :param name: The segment's name (an API's title); defaults to the component's name if there is one
        component, else "Segment".
    :param model_prefix: Prefix of the class names of the models that hold every component
        (``{prefix}CommandFrame``, ``{prefix}ComponentCommands``, ...); defaults to the name converted to a
        class name (``AMT VM-A1`` -> ``AmtVmA1``), or to that plus ``Segment`` if the default would name a
        class the same as one of a component's (a declarative ``JointPaloozaComponent`` already has
        ``JointPaloozaComponentCommands``). A prefix that is given is used as is. Each component's own models
        keep its own prefix.
    :param max_queue_length: The most command frames the queue holds; writes that would exceed it raise
        :class:`QueueFullError`.
    :param buffer_size: The most events the retransmission buffer keeps (0 disables it).
    """

    def __init__(
        self,
        components: ComponentsArg,
        name: Optional[str] = None,
        model_prefix: Optional[str] = None,
        max_queue_length: int = 10_000,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ):
        self._components: Dict[str, AutomationComponentBase] = self._normalize_components(components)
        single = next(iter(self._components.values())) if len(self._components) == 1 else None
        if name is None:
            name = single.name if single is not None else "Segment"
        if not isinstance(name, str) or not name.strip():
            raise TypeError("name must be a non-empty str")
        prefix_given = model_prefix is not None
        if model_prefix is None:
            model_prefix = to_class_name(name)
        if not isinstance(model_prefix, str) or not model_prefix.isidentifier():
            raise ValueError(f"model_prefix {model_prefix!r} must be a valid Python identifier")
        if not isinstance(max_queue_length, int) or max_queue_length < 1:
            raise ValueError("max_queue_length must be a positive int")
        if not isinstance(buffer_size, int) or buffer_size < 0:
            raise ValueError("buffer_size must be an int >= 0 (0 disables the retransmission buffer)")
        self._name = name
        self._max_queue_length = max_queue_length
        self._buffer_size = buffer_size

        models = build_segment_models(model_prefix, self._components)
        problems = self._class_name_problems(model_prefix, models)
        if problems and not prefix_given:
            # The default prefix can collide with a component's own class names: a declarative component
            # class JointPaloozaComponent makes JointPaloozaComponentCommands, and so does a segment named
            # "Joint Palooza". Fall back to {prefix}Segment before giving up.
            fallback_models = build_segment_models(model_prefix + "Segment", self._components)
            if not self._class_name_problems(model_prefix + "Segment", fallback_models):
                model_prefix, models, problems = model_prefix + "Segment", fallback_models, []
        if problems:
            problems.append("Fix for the segment-level classes: pass model_prefix=... to the API (or SegmentEngine).")
            raise ComponentDefinitionError(f"Segment '{name}'", problems)
        self._model_prefix = model_prefix
        self._models = models
        self._fingerprint = schema_fingerprint(self._models.command_frame, self._models.response_frame,
                                               self._models.configuration)

        self._lock = threading.RLock()
        self._queue: Deque[_QueueEntry] = collections.deque()
        self._queue_revision = 0  # increments when frames are added or removed other than by step()
        self._applying: Optional[_QueueEntry] = None  # the frame step() is applying (not yet in _latest)
        self._config_requests: Deque[_ConfigRequest] = collections.deque()
        self._instance_id = uuid.uuid4().hex
        self._next_sequence = 1
        self._buffer: Deque[SegmentEvent] = collections.deque(maxlen=buffer_size or 1)
        self._listeners: List[Listener] = []
        self._steps = 0
        self._physics_time = 0.0  # sum of the physics time steps so far
        self._physics_time_step: Optional[float] = None
        self._timeline_time: Optional[float] = None
        self._timeline_time_step: Optional[float] = None
        self._latest: Optional[SegmentEvent] = None  # the latest frame event
        self._halt: Optional[HaltInfo] = None
        self._paused = False
        self._monitors: List[Monitor] = []
        self._config_revision = 0
        self._failing_reads: Set[str] = set()  # components whose reads are failing (logged once)
        self._config: Dict[str, Optional[BaseModel]] = {
            id: self._read_config(id) for id in self._models.component_configs.model_fields}

    def _normalize_components(self, components: ComponentsArg) -> Dict[str, AutomationComponentBase]:
        if isinstance(components, AutomationComponentBase):
            items = [(path_to_id(components.name), components, "converted from its name")]
        elif isinstance(components, Mapping):
            items = [(id, c, "given") for id, c in components.items()]
        elif isinstance(components, Sequence) and not isinstance(components, str):
            items = [(path_to_id(c.name) if isinstance(c, AutomationComponentBase) else "", c, "converted from its name")
                     for c in components]
        else:
            raise TypeError("components must be an AutomationComponentBase, a Mapping[str, AutomationComponentBase], "
                            "or a Sequence[AutomationComponentBase]")
        if not items:
            raise ValueError("at least one automation component is required")

        problems: List[str] = []
        result: Dict[str, AutomationComponentBase] = {}
        seen: Dict[int, str] = {}
        for id, c, how in items:
            if not isinstance(c, AutomationComponentBase):
                problems.append(f"{c!r} is not an AutomationComponentBase (e.g. amt.dtp.ise.transducer.core.AutomationComponent)")
                continue
            problem = id_problem(id)
            if problem is not None:
                problems.append(f"component id of '{c.name}' ({how}): {problem}.\nFix: pass the components as a dict "
                                f"with explicit ids.")
            elif id in result:
                problems.append(f"'{result[id].name}' and '{c.name}' both have the id '{id}'.\nFix: pass the "
                                f"components as a dict with explicit ids.")
            elif builtins.id(c) in seen:
                problems.append(f"'{c.name}' is listed twice, as '{seen[builtins.id(c)]}' and '{id}'.")
            else:
                result[id] = c
                seen[builtins.id(c)] = id
        if problems:
            raise ComponentDefinitionError("Segment", problems)
        return result

    def _class_name_problems(self, model_prefix: str, models: SegmentModels) -> List[str]:
        from .codegen import client_class_name  # the generated module defines the client class too
        owners = []
        for id, c in self._components.items():
            owner = f"component '{id}' ({c.name})"
            owners += [(owner, c.actual_model), (owner, c.command_model), (owner, models.states[id])]
            if c.config_model is not None:
                owners.append((owner, c.config_model))
        owners += [(f"segment '{self._name}'", m) for m in models.roots]
        reserved = set(GENERATED_MODULE_RESERVED_NAMES) | {client_class_name(model_prefix)}
        return find_class_name_conflicts(
            owners, reserved,
            library_classes=(CommandFrameBase, ResponseFrameBase, CommandResponseBase, ComponentStateBase,
                             ConfigurationBase, SegmentError))

    # ---- properties ---------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def components(self) -> Dict[str, AutomationComponentBase]:
        """The components, by id."""
        return dict(self._components)

    @property
    def model_prefix(self) -> str:
        return self._model_prefix

    @property
    def models(self) -> SegmentModels:
        return self._models

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def instance_id(self) -> str:
        """Identifies this engine; a new engine (e.g. on every play) restarts the sequence numbers."""
        return self._instance_id

    @property
    def max_queue_length(self) -> int:
        return self._max_queue_length

    @property
    def buffer_size(self) -> int:
        return self._buffer_size

    @property
    def steps(self) -> int:
        with self._lock:
            return self._steps

    @property
    def physics_time_step(self) -> Optional[float]:
        with self._lock:
            return self._physics_time_step

    @property
    def timeline_time_step(self) -> Optional[float]:
        with self._lock:
            return self._timeline_time_step

    @property
    def queue_length(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def queue_revision(self) -> int:
        """Increments whenever frames are added to or removed from the queue other than by :meth:`step`
        (write, execute, clear, withdraw). A compiler that predicts the state at the end of the queue passes
        it back to :meth:`enqueue` / :meth:`submit_many` to append only if nothing changed meanwhile."""
        with self._lock:
            return self._queue_revision

    def prediction_snapshot(self) -> Tuple[Optional[BaseModel], List[BaseModel], int]:
        """``(latest response frame, frames not yet reflected in it, queue_revision)``, taken atomically:
        the frame being applied by a running step (if any) followed by the queued frames. Folding those
        frames' commands onto the latest command readback gives the commands at the end of the queue."""
        with self._lock:
            pending = ([self._applying.frame] if self._applying is not None else []) + [e.frame for e in self._queue]
            latest = self._latest.model if self._latest is not None else None
            return latest, pending, self._queue_revision

    def _check_revision(self, expected_revision: Optional[int]) -> None:
        # Called with self._lock held.
        if expected_revision is not None and expected_revision != self._queue_revision:
            raise QueueChangedError(f"the queue changed (revision {self._queue_revision}, expected {expected_revision})")

    @property
    def halted(self) -> bool:
        with self._lock:
            return self._halt is not None

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def halt(self) -> Optional[HaltInfo]:
        """The failed frame, while halted."""
        with self._lock:
            return self._halt

    @property
    def config_revision(self) -> int:
        with self._lock:
            return self._config_revision

    @property
    def latest(self) -> Optional[BaseModel]:
        """The response frame of the latest step, or None if no step has run yet."""
        with self._lock:
            return self._latest.model if self._latest is not None else None

    @property
    def latest_event(self) -> Optional[SegmentEvent]:
        """The ``frame`` event of the latest step."""
        with self._lock:
            return self._latest

    def header(self) -> BufferHeader:
        """The state of the retransmission buffer."""
        with self._lock:
            return self._header()

    def _header(self) -> BufferHeader:
        buffered = self._buffer_size > 0 and len(self._buffer) > 0
        return BufferHeader(
            instance_id=self._instance_id,
            buffer_size=self._buffer_size,
            first_sequence=self._buffer[0].sequence if buffered else None,
            last_sequence=self._buffer[-1].sequence if buffered else None,
            next_sequence=self._next_sequence,
        )

    def _state(self) -> str:
        return "halted" if self._halt is not None else "paused" if self._paused else "running"

    def status(self) -> SegmentStatus:
        with self._lock:
            return SegmentStatus(state=self._state(), queue_length=len(self._queue), halt=self._halt, paused=self._paused)

    # ---- listeners -------------------------------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        """Call ``listener(event)`` for every event the engine emits, in sequence order. It is called on
        the emitting thread (usually the simulation's) with the engine's lock held, so it must be quick
        and must not call back into the engine (e.g. hand the event to another thread's loop)."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def add_monitor(self, monitor: Monitor) -> None:
        """Add a step monitor (see :data:`Monitor`): if it returns errors, the step's response frame carries them
        and the segment halts (``reason: monitor_failed``), as for a failed frame. It runs on the simulation
        thread every step, so it must be quick; an exception in it is logged and ignored."""
        with self._lock:
            self._monitors.append(monitor)

    def remove_monitor(self, monitor: Monitor) -> None:
        with self._lock:
            if monitor in self._monitors:
                self._monitors.remove(monitor)

    def _emit(self, event: str, model: BaseModel, sequence: int, executed: bool = False) -> SegmentEvent:
        # Called with self._lock held, with a sequence from _take_sequence.
        item = SegmentEvent(event, sequence, model, executed)
        if self._buffer_size > 0:
            self._buffer.append(item)
        for listener in list(self._listeners):
            try:
                listener(item)
            except Exception as ex:
                _logger.error(f"Segment '{self._name}': a listener failed", exception=ex)
        return item

    def _take_sequence(self) -> int:
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def _emit_status(self, reason: str, state: Optional[str] = None) -> StatusEvent:
        # Called with self._lock held.
        if state is None:
            state = self._state()
        sequence = self._take_sequence()
        event = StatusEvent(instance_id=self._instance_id, sequence=sequence, state=state, reason=reason,
                            queue_length=len(self._queue), halt=self._halt, paused=self._paused,
                            wall_timestamp=time.time())
        self._emit("status", event, sequence)
        return event

    # ---- queue (thread-safe) ------------------------------------------------------------------------

    def coerce_frame(self, frame: Any) -> BaseModel:
        """A command frame (an instance of the API's command frame model) from a model or a dict."""
        if isinstance(frame, self._models.command_frame):
            return frame
        return self._models.command_frame.model_validate(frame, from_attributes=True)

    def _check_capacity(self, count: int) -> None:
        # Called with self._lock held.
        if len(self._queue) + count > self._max_queue_length:
            raise QueueFullError(
                f"writing {count} frame(s) would exceed the maximum queue length of {self._max_queue_length} "
                f"({len(self._queue)} frame(s) are already queued). Wait for the queue to drain, or write fewer frames."
            )

    def enqueue(self, frames: Sequence[Any], expected_revision: Optional[int] = None) -> BaseModel:
        """Append command frames to the queue (all or nothing); returns the command response. Frames are
        accepted while halted too (they run after a resume). With ``expected_revision``, raises
        :class:`QueueChangedError` unless :attr:`queue_revision` still has that value."""
        frames = [self.coerce_frame(f) for f in frames]
        with self._lock:
            self._check_revision(expected_revision)
            self._check_capacity(len(frames))
            self._queue.extend(_QueueEntry(f) for f in frames)
            self._queue_revision += 1
            return self._models.command_response(accepted=len(frames), queue_length=len(self._queue),
                                                 halted=self._halt is not None)

    def _check_not_halted(self) -> None:
        # Called with self._lock held.
        if self._halt is not None:
            raise SegmentHaltedError(
                f"the segment is halted: frame {self._halt.frame_id} (sequence {self._halt.sequence}) failed. "
                f"Resume (POST /api/segment/process/resume) or clear the queue first.")

    def submit(self, frame: Any) -> concurrent.futures.Future:
        """Append one frame and return a future resolved with the response frame of the step that applies
        it (or failed with :class:`QueueClearedError`). Raises :class:`SegmentHaltedError` while halted."""
        frame = self.coerce_frame(frame)
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            self._check_not_halted()
            self._check_capacity(1)
            self._queue.append(_QueueEntry(frame, future))
            self._queue_revision += 1
        return future

    def submit_many(self, frames: Sequence[Any], expected_revision: Optional[int] = None) -> concurrent.futures.Future:
        """Append several frames at once (all or nothing) and return a future resolved with the response
        frame of the step that applies the **last** one. Raises :class:`SegmentHaltedError` while halted, and
        :class:`QueueChangedError` as :meth:`enqueue`. :meth:`withdraw` removes all of them still queued."""
        frames = [self.coerce_frame(f) for f in frames]
        if not frames:
            raise ValueError("submit_many needs at least one frame")
        future: concurrent.futures.Future = concurrent.futures.Future()
        batch = object()
        with self._lock:
            self._check_not_halted()
            self._check_revision(expected_revision)
            self._check_capacity(len(frames))
            self._queue.extend(_QueueEntry(f, batch=batch) for f in frames[:-1])
            self._queue.append(_QueueEntry(frames[-1], future, batch))
            self._queue_revision += 1
        return future

    def withdraw(self, future: concurrent.futures.Future) -> bool:
        """Remove the queued frame of a :meth:`submit` future (or every still-queued frame of a
        :meth:`submit_many` future), if it hasn't been applied yet."""
        with self._lock:
            for entry in self._queue:
                if entry.future is future:
                    if entry.batch is not None:
                        for other in [e for e in self._queue if e.batch is entry.batch]:
                            self._queue.remove(other)
                    else:
                        self._queue.remove(entry)
                    self._queue_revision += 1
                    return True
        return False

    def pending(self) -> List[BaseModel]:
        """The command frames waiting in the queue, in execution order."""
        with self._lock:
            return [e.frame for e in self._queue]

    def clear(self, replacement: Optional[Callable[[Optional[BaseModel], List[BaseModel]], Sequence[Any]]] = None
              ) -> List[BaseModel]:
        """Remove every command frame from the queue and return them, and release a halt (not a pause). The
        actuators keep their last commands (the stop policy ``hold``). Waiting :meth:`submit` futures fail with
        :class:`QueueClearedError`.

        ``replacement(latest, applying)`` (called with the engine's lock held, so it must be quick) returns frames
        to queue in the same atomic step, e.g. a robot's deceleration ramp: ``latest`` is the latest response frame
        and ``applying`` the frame a running step is applying (a list of zero or one)."""
        with self._lock:
            entries = list(self._queue)
            self._queue.clear()
            if replacement is not None:
                applying = [self._applying.frame] if self._applying is not None else []
                frames = [self.coerce_frame(f) for f in replacement(self._latest.model if self._latest else None, applying)]
                self._queue.extend(_QueueEntry(f) for f in frames[:self._max_queue_length])
            self._queue_revision += 1
            self._halt = None
            self._emit_status("cleared")
        self._fail(entries, QueueClearedError("the queue was cleared before the frame was applied"))
        return [e.frame for e in entries]

    def pause(self) -> SegmentStatus:
        """Pause the queue without clearing it (as robot controllers' remote-motion interfaces offer): from the next step on, no frame is
        applied (the actuators hold their last commands) and the queued frames wait; writes are still accepted and
        execute waits. :meth:`resume` continues. Idempotent."""
        with self._lock:
            if not self._paused:
                self._paused = True
                self._emit_status("paused")
            return SegmentStatus(state=self._state(), queue_length=len(self._queue), halt=self._halt, paused=True)

    def resume(self) -> SegmentStatus:
        """Release a pause and a halt: the next step applies the next queued frame. A no-op when neither."""
        with self._lock:
            if self._halt is not None or self._paused:
                self._halt = None
                self._paused = False
                self._emit_status("resumed")
            return SegmentStatus(state="running", queue_length=len(self._queue), halt=None, paused=False)

    def notify_stopped(self) -> None:
        """Emit a ``stopped`` status event (e.g. when the API serving the segment stops)."""
        with self._lock:
            self._emit_status("stopped", state="stopped")

    def fail_waiting(self, exception: BaseException) -> None:
        """Fail every waiting :meth:`submit` and :meth:`config_write` future (the frames stay queued)."""
        with self._lock:
            entries = [e for e in self._queue if e.future is not None]
            requests = list(self._config_requests)
            self._config_requests.clear()
        self._fail(entries, exception)
        for request in requests:
            if not request.future.done():
                request.future.set_exception(exception)

    @staticmethod
    def _fail(entries: Sequence[_QueueEntry], exception: BaseException) -> None:
        for entry in entries:
            if entry.future is not None and not entry.future.done():
                entry.future.set_exception(exception)

    # ---- configuration (thread-safe) --------------------------------------------------------------------

    def _read_config(self, id: str) -> Optional[BaseModel]:
        try:
            return self._components[id].config_read()
        except Exception as ex:
            _logger.error(f"Segment '{self._name}': reading the configuration of '{id}' failed", exception=ex)
            return None

    def config_read(self) -> BaseModel:
        """The configuration (an instance of the API's configuration model): a snapshot taken when the
        engine was created and after every configuration write."""
        with self._lock:
            configs = self._models.component_configs(**{id: c for id, c in self._config.items() if c is not None})
            return self._models.configuration(instance_id=self._instance_id, config_revision=self._config_revision,
                                              components=configs)

    def config_write(self, value: Any) -> concurrent.futures.Future:
        """Queue a configuration write (an instance of the API's component configs model, or a dict),
        applied at the start of the next step. Returns a future resolved with the :class:`ConfigEvent`."""
        if not isinstance(value, self._models.component_configs):
            value = self._models.component_configs.model_validate(value, from_attributes=True)
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            self._config_requests.append(_ConfigRequest(value, future))
        return future

    def withdraw_config(self, future: concurrent.futures.Future) -> bool:
        """Remove a configuration write that hasn't been applied yet."""
        with self._lock:
            for request in self._config_requests:
                if request.future is future:
                    self._config_requests.remove(request)
                    return True
        return False

    def _apply_config(self, value: BaseModel) -> Tuple[Dict[str, ConfigOutcome], bool]:
        """Apply one configuration write (outside the lock). Errors carry no sequence yet."""
        outcomes: Dict[str, ConfigOutcome] = {}
        any_applied = False
        for id in value.model_fields_set:
            config = getattr(value, id)
            if config is None:
                continue
            attempted = [t for t in config.model_fields_set if getattr(config, t) is not None]
            failed: Dict[Optional[str], BaseException] = {}
            try:
                self._components[id].config_write(config)
            except ComponentWriteError as ex:
                failed.update(ex.errors)
            except Exception as ex:
                failed[None] = ex
            if failed:
                _logger.error(f"Segment '{self._name}': configuring '{id}' failed: "
                              + "; ".join(f"{t or id}: {type(e).__name__}: {e}" for t, e in failed.items()))
            applied = [] if None in failed else [t for t in attempted if t not in failed]
            any_applied = any_applied or bool(applied)
            outcomes[id] = ConfigOutcome(
                status="failed" if failed else "applied",
                applied=applied,
                errors=[SegmentError(code=error_code(e, ErrorCode.CONFIG_WRITE_FAILED), message=f"{type(e).__name__}: {e}",
                                     component=id, transducer=t) for t, e in failed.items()],
            )
            self._config[id] = self._read_config(id)
        return outcomes, any_applied

    # ---- buffer queries (thread-safe) --------------------------------------------------------------------

    def events_since(self, after: int, clamp: bool = False) -> Tuple[List[SegmentEvent], Optional[Tuple[int, int]]]:
        """The buffered events after sequence ``after``, to resume a stream, and the range of sequences
        that were lost (only with ``clamp``). Raises :class:`OutOfRangeError` if events after ``after``
        were evicted (unless ``clamp``: then it starts at the oldest buffered event) or ``after`` is later
        than the last event."""
        with self._lock:
            header = self._header()
            last = self._next_sequence - 1
            if after > last:
                raise OutOfRangeError(f"sequence {after} is later than the last event ({last})", header)
            if after == last:
                return [], None
            first = self._buffer[0].sequence if self._buffer_size > 0 and self._buffer else self._next_sequence
            if after + 1 >= first:
                return [e for e in self._buffer if e.sequence > after], None
            if not clamp:
                raise OutOfRangeError(
                    f"events after sequence {after} are no longer buffered (the buffer holds "
                    f"{'nothing' if first == self._next_sequence else f'{first}..{last}'}); resynchronize from "
                    f"process/read, or pass clamp=true to resume from the oldest buffered event", header)
            events = list(self._buffer) if self._buffer_size > 0 else []
            return events, (after + 1, first - 1)

    def retransmit(
        self,
        sequences: Optional[Sequence[int]] = None,
        from_: Optional[int] = None,
        to: Optional[int] = None,
        count: int = 1000,
        clamp: bool = False,
    ) -> Tuple[BufferHeader, List[SegmentEvent], List[int], bool]:
        """Buffered events by a list of ``sequences`` (those not buffered are returned as missing) or by
        an inclusive range ``from_``..``to`` (at most ``count``). Returns ``(header, events, missing,
        truncated)``. Raises :class:`OutOfRangeError` for a range that starts outside the buffer (unless
        ``clamp``: then it starts at the oldest buffered event)."""
        if (sequences is None) == (from_ is None):
            raise ValueError("pass either sequences or from_")
        with self._lock:
            header = self._header()
            buffer = list(self._buffer) if self._buffer_size > 0 else []
        first = buffer[0].sequence if buffer else header.next_sequence

        def at(sequence: int) -> Optional[SegmentEvent]:
            index = sequence - first
            return buffer[index] if 0 <= index < len(buffer) else None

        if sequences is not None:
            events, missing, seen = [], [], set()
            for sequence in sequences:
                if sequence in seen:
                    continue
                seen.add(sequence)
                event = at(sequence)
                if event is None:
                    missing.append(sequence)
                else:
                    events.append(event)
            return header, sorted(events, key=lambda e: e.sequence), missing, False

        if from_ > header.next_sequence:
            raise OutOfRangeError(f"from={from_} is later than next_sequence ({header.next_sequence})", header)
        if from_ < first:
            if not clamp:
                raise OutOfRangeError(
                    f"from={from_} is no longer buffered (the oldest buffered event is "
                    f"{first if buffer else 'none'}); pass clamp=true to start at the oldest", header)
            from_ = first
        last = buffer[-1].sequence if buffer else header.next_sequence - 1
        to = last if to is None else min(to, last)
        events = [e for e in buffer if from_ <= e.sequence <= to]
        truncated = len(events) > count
        return header, events[:count], [], truncated

    # ---- simulation side ------------------------------------------------------------------------------

    def step(
        self,
        physics_time_step: float,
        timeline_time: Optional[float] = None,
        physics_time: Optional[float] = None,
    ) -> BaseModel:
        """Advance one physics step: apply pending configuration writes, apply the next command frame (if
        any, and unless halted) to all components, read all components, and emit the response frame.
        Call exactly once per physics step (e.g. from one behavior script's ``on_physics``:
        ``step(physics_time_step=delta_time, timeline_time=current_time)``), on the thread that owns the
        simulation.

        :param physics_time_step: The duration of this physics step in seconds.
        :param timeline_time: The timeline's current time in seconds (``on_physics``'s ``current_time``).
        :param physics_time: The physics simulation time of this step, if the caller knows it exactly;
            otherwise it is the sum of the physics time steps of all steps so far, including this one.

        Each component fails on its own: if writing its commands raises, its status is ``failed`` (with
        typed errors), the other components are still written, every component is read, and the segment
        halts (the remaining frames wait for :meth:`resume` or :meth:`clear`). A read failure is recorded
        as an error (null actuals and commands) and doesn't halt.
        """
        with self._lock:
            requests = list(self._config_requests)
            self._config_requests.clear()
            entry = self._queue.popleft() if (self._queue and self._halt is None and not self._paused) else None
            self._applying = entry
            monitors = list(self._monitors)

        applied_configs = [(request, *self._apply_config(request.value)) for request in requests]

        statuses: Dict[str, str] = {id: "not_addressed" for id in self._components}
        errors: Dict[str, List[Tuple[str, str, Optional[str]]]] = {id: [] for id in self._components}
        if entry is not None:
            row = entry.frame.components
            frame_label = entry.frame.frame_id if entry.frame.frame_id is not None else "(no frame_id)"
            for id in (row.model_fields_set if row is not None else ()):
                commands = getattr(row, id)
                if commands is None:
                    continue
                try:
                    self._components[id].actuator_write(commands)
                    statuses[id] = "applied"
                except Exception as ex:
                    statuses[id] = "failed"
                    if isinstance(ex, ComponentWriteError):
                        for transducer, transducer_ex in ex.errors.items():
                            errors[id].append((error_code(transducer_ex, ErrorCode.WRITE_FAILED),
                                               f"{type(transducer_ex).__name__}: {transducer_ex}", transducer))
                    else:
                        errors[id].append((error_code(ex, ErrorCode.WRITE_FAILED), f"{type(ex).__name__}: {ex}", None))
                    _logger.error(f"Segment '{self._name}': applying frame {frame_label} to '{id}' failed", exception=ex)

        readings: Dict[str, Tuple[Optional[BaseModel], Optional[BaseModel]]] = {}
        for id, c in self._components.items():
            actuals = commands = None
            try:
                actuals = c.sensor_read()
                commands = c.actuator_read()
            except Exception as ex:
                actuals = commands = None
                errors[id].append((error_code(ex, ErrorCode.READ_FAILED), f"reading failed: {type(ex).__name__}: {ex}", None))
                if id not in self._failing_reads:  # log once, until reading works again
                    self._failing_reads.add(id)
                    _logger.error(f"Segment '{self._name}': reading '{id}' failed", exception=ex)
            else:
                self._failing_reads.discard(id)
            readings[id] = (actuals, commands)

        monitor_errors: List[SegmentError] = []
        for monitor in monitors:
            try:
                monitor_errors += list(monitor(entry.frame if entry is not None else None, readings) or [])
            except Exception as ex:
                _logger.error(f"Segment '{self._name}': a step monitor failed", exception=ex)
        for error in monitor_errors:
            if error.component in errors:
                errors[error.component].append((error.code, error.message, error.transducer))

        with self._lock:
            self._steps += 1
            self._physics_time_step = float(physics_time_step)
            self._physics_time = float(physics_time) if physics_time is not None else self._physics_time + self._physics_time_step
            if timeline_time is not None:
                timeline_time = float(timeline_time)
                if self._timeline_time is not None and timeline_time > self._timeline_time:
                    self._timeline_time_step = timeline_time - self._timeline_time
                self._timeline_time = timeline_time
            wall_time = time.time()

            # Configuration events come first (they were applied before this step's frame), then the frame.
            frame_sequence = self._next_sequence + len(applied_configs)
            config_events: List[Tuple[_ConfigRequest, ConfigEvent]] = []
            for request, outcomes, any_applied in applied_configs:
                sequence = self._take_sequence()
                if any_applied:
                    self._config_revision += 1
                for outcome in outcomes.values():
                    for error in outcome.errors:
                        error.sequence = sequence
                event = ConfigEvent(instance_id=self._instance_id, sequence=sequence, config_revision=self._config_revision,
                                    effective_sequence=frame_sequence, physics_timestamp=self._physics_time,
                                    wall_timestamp=wall_time, components=outcomes)
                self._emit("config", event, sequence)
                config_events.append((request, event))

            sequence = self._take_sequence()
            states: Dict[str, BaseModel] = {}
            failed_errors: List[SegmentError] = []
            for id in self._components:
                typed = [SegmentError(code=code, message=message, component=id, transducer=transducer, sequence=sequence)
                         for code, message, transducer in errors[id]]
                if statuses[id] == "failed":
                    failed_errors += [e for e in typed if e.code != ErrorCode.READ_FAILED]
                actuals, commands = readings[id]
                states[id] = self._models.states[id](status=statuses[id], actuals=actuals, commands=commands, errors=typed)
            halting = any(s == "failed" for s in statuses.values())
            monitor_halting = bool(monitor_errors) and not halting and self._halt is None
            if halting:
                self._halt = HaltInfo(sequence=sequence, frame_id=entry.frame.frame_id, errors=failed_errors)
            elif monitor_halting:
                self._halt = HaltInfo(sequence=sequence, frame_id=entry.frame.frame_id if entry is not None else None,
                                      errors=[e.model_copy(update={"sequence": sequence}) for e in monitor_errors])
            response = self._models.response_frame(
                instance_id=self._instance_id,
                sequence=sequence,
                config_revision=self._config_revision,
                halted=self._halt is not None,
                physics_timestamp=self._physics_time,
                timeline_timestamp=timeline_time,
                wall_timestamp=wall_time,
                frame_id=entry.frame.frame_id if entry is not None else None,
                components=self._models.component_states(**states),
            )
            self._latest = self._emit("frame", response, sequence, executed=entry is not None)
            self._applying = None
            if monitor_halting:
                self._emit_status("monitor_failed")
                _logger.error(f"Segment '{self._name}': halted by a step monitor: "
                              + "; ".join(e.message for e in monitor_errors))
            if halting:
                self._emit_status("frame_failed")
                _logger.error(f"Segment '{self._name}': halted after frame "
                              f"{entry.frame.frame_id if entry.frame.frame_id is not None else '(no frame_id)'} failed; "
                              f"{len(self._queue)} frame(s) wait for a resume or a clear")

        for request, event in config_events:
            if not request.future.done():
                request.future.set_result(event)
        if entry is not None and entry.future is not None and not entry.future.done():
            entry.future.set_result(response)
        return response

"""SegmentApi: the HTTP interface of a segment (one or more automation components).

Clients write command frames into the segment engine's queue; every simulation step, the owner of the
API calls :meth:`SegmentApi.step`, which applies the next frame to all components, reads them, and
publishes the response frame to readers and stream subscribers. Each frame is one row holding every
component, so commands for several machines are applied on the same step.

The API never touches the simulation itself: requests only enqueue frames or configuration writes, or
read what the engine published, so the HTTP server runs on its own thread and event loop, and the
simulation thread does a constant amount of work per step beyond reading and writing the components.

Requires the ``server`` extra (fastapi, uvicorn). Design: ``docs/src/Agents/Specs/segment-protocol.md``.

Note: don't add ``from __future__ import annotations`` to this module. The route handlers are
annotated with the dynamically created per-API models, which FastAPI must see as classes.
"""
import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union

from fastapi import APIRouter, Body, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ..component import AutomationComponentBase
from ..core.server import API_PREFIX, AssetsArg, CoreApi
from .codegen import client_class_name, client_file_name, generate_client_module
from .engine import (
    DEFAULT_BUFFER_SIZE,
    ComponentsArg,
    OutOfRangeError,
    QueueChangedError,
    QueueClearedError,
    QueueFullError,
    SegmentEngine,
    SegmentEvent,
    SegmentHaltedError,
)
from .models import (
    BufferedEvent,
    ComponentDescription,
    ConfigEvent,
    ErrorCode,
    ErrorEvent,
    Heartbeat,
    ResetEvent,
    RetransmitResponse,
    SegmentError,
    SegmentInfo,
    SegmentPreviewInfo,
    SegmentStatus,
)


SEGMENT_PREFIX = f"{API_PREFIX}/segment"
HEARTBEAT_INTERVAL = 15.0  # seconds without events before a heartbeat


class ServerStoppedError(Exception):
    """The API stopped before a frame (or a configuration write) was applied."""


def _coded(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"code": code, "message": message, **extra}


class _Subscriber:
    """One stream connection. Lives on the server loop."""

    _OVERFLOW = object()
    _CLOSED = object()

    def __init__(self, every: int, executed_only: bool, queue_size: int):
        self.every = every
        self.executed_only = executed_only
        self.queue_size = queue_size
        self.queue: asyncio.Queue = asyncio.Queue()
        self.count = 0
        self.last_sequence = 0  # the newest event seen (sent, queued, or filtered out)
        self.done = False

    def accept(self, event: SegmentEvent) -> bool:
        """Whether to send ``event``: skips events already seen (a resumed stream's replay and the live
        events can overlap) and frames filtered out by ``every`` / ``executed_only``."""
        if event.sequence <= self.last_sequence:
            return False
        self.last_sequence = event.sequence
        if event.event != "frame":
            return True
        if self.executed_only and not event.executed:
            return False
        self.count += 1
        return (self.count - 1) % self.every == 0

    def offer(self, event: SegmentEvent) -> None:
        if self.done or not self.accept(event):
            return
        if self.queue.qsize() >= self.queue_size:
            self.done = True
            self.queue.put_nowait(self._OVERFLOW)
            return
        self.queue.put_nowait(event)

    def close(self) -> None:
        if not self.done:
            self.done = True
            self.queue.put_nowait(self._CLOSED)


class SegmentApi(CoreApi):
    """The segment API for one or more automation components.

    Typical use from a behavior script::

        def on_play(self):
            self._mill = AmtVmA1(prim=self.prim_path)
            self._api = SegmentApi(self._mill, port=8020)
            self._api.start()

        def on_physics(self, current_time, delta_time):
            self._api.step(physics_time_step=delta_time, timeline_time=current_time)

        def on_stop(self):
            self._api.stop()

    :param components: The components to control: one AutomationComponent, a ``{id: component}``
        mapping, or a sequence (each id is then the component's name converted to an identifier, e.g.
        ``AMT_VM_A1``); or a :class:`SegmentEngine` built elsewhere.
    :param title: The API's title; defaults to the component's name if there is one component.
    :param description: Defaults to the component's description if there is one component.
    :param version: The API version set by its author.
    :param model_prefix: Prefix of the class names of the API's models, which hold every component
        (``{prefix}CommandFrame``, ``{prefix}ComponentCommands``, ``{prefix}Client``, ...); defaults to the
        title converted to a class name (``AMT VM-A1`` -> ``AmtVmA1``), plus ``Segment`` if that would collide
        with a component's own class names (see :class:`SegmentEngine`).
    :param max_queue_length: The most command frames the queue holds; writes that would exceed it are
        rejected (HTTP 413).
    :param buffer_size: The most events the retransmission buffer keeps, to resume and repair streams
        (0 disables it).
    :param subscriber_queue_size: The most events queued per stream subscriber; a subscriber that falls
        further behind gets an ``error`` event (STREAM_OVERFLOW) and is disconnected.
    :param port, host, assets: See :class:`CoreApi`.
    :param preview_features: Also serve the deferred preview features (built, but not offered
        yet): ``POST /process/pause``, ``process/write?expected_revision=``, and ``paused`` / ``queue_revision`` in
        ``/info`` (and, in a RobotApi, compiling from a given start). Off by default; the code stays for later.
    """

    def __init__(
        self,
        components: Union[ComponentsArg, SegmentEngine],
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
        preview_features: bool = False,
    ):
        if not isinstance(subscriber_queue_size, int) or subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be a positive int")

        if isinstance(components, SegmentEngine):
            engine = components
            if model_prefix is not None and model_prefix != engine.model_prefix:
                raise ValueError(f"model_prefix {model_prefix!r} differs from the engine's ({engine.model_prefix!r})")
            if title is None:
                title = engine.name
        else:
            engine = SegmentEngine(components, name=title, model_prefix=model_prefix,
                                   max_queue_length=max_queue_length, buffer_size=buffer_size)
            title = engine.name
        single = next(iter(engine.components.values())) if len(engine.components) == 1 else None
        if description is None:
            description = single.description if single is not None else ""
        super().__init__(title, description, version, port, host, assets)
        self._segment = engine
        self._subscriber_queue_size = subscriber_queue_size
        self._preview = bool(preview_features)
        self._client_source = self._generate_client_source()
        self._subscribers: Set[_Subscriber] = set()  # touched on the server loop only
        engine.add_listener(self._on_event)
        self.add_router(self._segment_router())

    def _generate_client_source(self) -> str:
        """The generated client module (called once by the constructor). Subclasses extend it."""
        engine = self._segment
        return generate_client_module(
            engine.model_prefix, self.title, self.description, self.version, engine.components, engine.models,
            engine.fingerprint,
        )

    # ---- properties ---------------------------------------------------------------------------

    @property
    def segment(self) -> SegmentEngine:
        """The segment engine this API serves."""
        return self._segment

    @property
    def components(self) -> Dict[str, AutomationComponentBase]:
        """The served components, by id."""
        return self._segment.components

    @property
    def model_prefix(self) -> str:
        return self._segment.model_prefix

    @property
    def component_commands_model(self) -> Type[BaseModel]:
        """The row of commands in a command frame, e.g. ``MachiningCellComponentCommands``."""
        return self._segment.models.component_commands

    @property
    def component_states_model(self) -> Type[BaseModel]:
        """The row of component states in a response frame, e.g. ``MachiningCellComponentStates``."""
        return self._segment.models.component_states

    @property
    def component_configs_model(self) -> Type[BaseModel]:
        """The configuration by component id, e.g. ``MachiningCellComponentConfigs``."""
        return self._segment.models.component_configs

    @property
    def configuration_model(self) -> Type[BaseModel]:
        """E.g. ``MachiningCellConfiguration``."""
        return self._segment.models.configuration

    @property
    def command_frame_model(self) -> Type[BaseModel]:
        """E.g. ``MachiningCellCommandFrame``."""
        return self._segment.models.command_frame

    @property
    def response_frame_model(self) -> Type[BaseModel]:
        """E.g. ``MachiningCellResponseFrame``."""
        return self._segment.models.response_frame

    @property
    def command_response_model(self) -> Type[BaseModel]:
        """E.g. ``MachiningCellCommandResponse``."""
        return self._segment.models.command_response

    @property
    def fingerprint(self) -> str:
        return self._segment.fingerprint

    @property
    def client_source(self) -> str:
        """The generated client module served at ``/api/segment/models.py``."""
        return self._client_source

    @property
    def client_file_name(self) -> str:
        return client_file_name(self._segment.model_prefix)

    @property
    def latest(self) -> Optional[BaseModel]:
        """The response frame of the latest step, or None if no step has run yet."""
        return self._segment.latest

    # ---- the segment (thread-safe; usable from Python as well as through the API) -------------------

    def step(self, physics_time_step: float, timeline_time: Optional[float] = None,
             physics_time: Optional[float] = None) -> BaseModel:
        """Advance one physics step; see :meth:`SegmentEngine.step`. Call it once per physics step, from
        one behavior script's ``on_physics``."""
        return self._segment.step(physics_time_step, timeline_time=timeline_time, physics_time=physics_time)

    def enqueue(self, frames: List[Any]) -> BaseModel:
        """Append command frames to the queue (all or nothing)."""
        return self._segment.enqueue(frames)

    def pending(self) -> List[BaseModel]:
        """The command frames waiting in the queue, in execution order."""
        return self._segment.pending()

    def clear(self) -> List[BaseModel]:
        """Remove every command frame from the queue and return them; releases a halt."""
        return self._segment.clear()

    @property
    def preview_features(self) -> bool:
        """Whether the deferred preview features are served (see the constructor)."""
        return self._preview

    def resume(self) -> SegmentStatus:
        """Release a halt (after a failed frame), or a pause (a preview feature)."""
        return self._segment.resume()

    def pause(self) -> SegmentStatus:
        """Pause the queue without clearing it (resume continues). A preview feature (deferred): needs
        ``preview_features=True``."""
        if not self._preview:
            raise RuntimeError("pause is a deferred preview feature: construct the API with preview_features=True")
        return self._segment.pause()

    # ---- server-loop helpers -----------------------------------------------------------------------

    def _on_event(self, event: SegmentEvent) -> None:
        # Called by the engine on the emitting thread (in sequence order); hand the event to the loop.
        self.call_soon_on_server_loop(self._publish, event)

    def _publish(self, event: SegmentEvent) -> None:
        for subscriber in list(self._subscribers):
            subscriber.offer(event)

    def _close_subscribers(self) -> None:
        for subscriber in list(self._subscribers):
            subscriber.close()

    def _on_stopping(self) -> None:
        super()._on_stopping()
        self._segment.notify_stopped()  # the last event on every stream
        self.call_soon_on_server_loop(self._close_subscribers)
        self._segment.fail_waiting(ServerStoppedError("the API stopped before the request was applied"))

    def _info(self) -> SegmentInfo:
        engine = self._segment
        state = engine.status()
        preview = dict(paused=state.paused, queue_revision=engine.queue_revision) if self._preview else {}
        return (SegmentPreviewInfo if self._preview else SegmentInfo)(
            title=self.title,
            description=self.description,
            version=self.version,
            fingerprint=engine.fingerprint,
            components=[
                ComponentDescription(id=id, name=c.name, description=c.description, version=c.version,
                                     sensors=c.sensor_ids, actuators=c.actuator_ids,
                                     configurable=c.config_model is not None)
                for id, c in engine.components.items()
            ],
            steps=engine.steps,
            physics_time_step=engine.physics_time_step,
            timeline_time_step=engine.timeline_time_step,
            queue_length=state.queue_length,
            max_queue_length=engine.max_queue_length,
            halted=state.state == "halted",
            halt=state.halt,
            config_revision=engine.config_revision,
            buffer=engine.header(),
            **preview,
        )

    @staticmethod
    def _sse(event: SegmentEvent, instance_id: str, render: Optional[Callable[[SegmentEvent], str]] = None) -> str:
        data = render(event) if render is not None and event.event == "frame" else event.json
        return f"id: {instance_id}:{event.sequence}\nevent: {event.event}\ndata: {data}\n\n"

    def _open_stream(
        self,
        every: int,
        executed_only: bool,
        resume_after: Optional[int],
        instance_id: Optional[str],
        clamp: bool,
        last_event_id: Optional[str],
        render: Optional[Callable[[SegmentEvent], str]] = None,
    ) -> StreamingResponse:
        """A Server-Sent Events stream of the segment's events (see the stream route). ``render`` turns a
        ``frame`` event into the JSON sent for it (e.g. the RobotAPI's robot frame); it runs on the server
        loop, once per subscriber and event."""
        engine = self._segment
        subscriber = _Subscriber(every, executed_only, self._subscriber_queue_size)
        after, instance_changed = self._resume_point(resume_after, instance_id, last_event_id)
        preamble: List[str] = []
        if instance_changed:
            preamble.append(self._unsequenced("reset", ResetEvent(
                code=ErrorCode.INSTANCE_CHANGED, header=engine.header(),
                message="the segment restarted (its instance_id changed); continuing live")))
        if after is not None:
            try:
                replay, lost = engine.events_since(after, clamp)
            except OutOfRangeError as ex:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ex.detail)
            if lost is not None:
                preamble.append(self._unsequenced("reset", ResetEvent(
                    code=ErrorCode.OUT_OF_RANGE, header=engine.header(), lost_from=lost[0], lost_to=lost[1],
                    message=f"events {lost[0]}..{lost[1]} are no longer buffered; resuming from the oldest buffered event")))
            subscriber.last_sequence = after
        else:
            latest = engine.latest_event
            replay = [latest] if latest is not None else []
        # No await between taking the replay and subscribing, so live events published meanwhile (on
        # this loop) arrive after the subscription; the subscriber drops any it already had.
        replay = [e for e in replay if subscriber.accept(e)]
        self._subscribers.add(subscriber)
        current = engine.instance_id

        async def events():
            try:
                for text in preamble:
                    yield text
                for event in replay:
                    yield self._sse(event, current, render)
                while True:
                    try:
                        item = await asyncio.wait_for(subscriber.queue.get(), timeout=HEARTBEAT_INTERVAL)
                    except asyncio.TimeoutError:
                        header = engine.header()
                        yield self._unsequenced("heartbeat", Heartbeat(instance_id=header.instance_id,
                                                                       next_sequence=header.next_sequence))
                        continue
                    if item is _Subscriber._OVERFLOW:
                        yield self._unsequenced("error", ErrorEvent(header=engine.header(), errors=[SegmentError(
                            code=ErrorCode.STREAM_OVERFLOW,
                            message=f"the subscriber fell more than {self._subscriber_queue_size} events behind and "
                                    f"was disconnected; reconnect with resume_after=<the last sequence received> "
                                    f"to continue without a gap")]))
                        return
                    if item is _Subscriber._CLOSED:
                        return
                    yield self._sse(item, current, render)
            finally:
                self._subscribers.discard(subscriber)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @staticmethod
    def _unsequenced(event: str, model: BaseModel) -> str:
        return f"event: {event}\ndata: {model.model_dump_json()}\n\n"

    def _resume_point(self, resume_after: Optional[int], instance_id: Optional[str],
                      last_event_id: Optional[str]) -> Tuple[Optional[int], bool]:
        """Where a stream resumes: ``(after, instance_changed)``."""
        current = self._segment.instance_id
        if resume_after is not None:
            if instance_id is not None and instance_id != current:
                return None, True
            return resume_after, False
        if last_event_id:
            instance, _, sequence = last_event_id.strip().partition(":")
            if instance == current and sequence.isdigit():
                return int(sequence), False
            return None, True
        return None, False

    # ---- API -------------------------------------------------------------------------------------

    def api_description(self) -> str:
        file_name = self.client_file_name
        components = ", ".join(f"`{id}` ({c.name} v{c.version})" for id, c in self.components.items())
        return super().api_description() + f"""

**Segment API** for {components} (schema fingerprint `{self.fingerprint}`). Each frame is one row holding
every component: a command frame's commands are applied to all components on the same simulation step.

- **Write**: `POST {SEGMENT_PREFIX}/process/write` appends command frames to the queue. One frame is applied
  per simulation step, in order.
- **Read**: `GET {SEGMENT_PREFIX}/process/read` returns the response frame of the latest step.
- **Stream**: `GET {SEGMENT_PREFIX}/stream` streams a response frame per step (Server-Sent Events), plus
  `config` and `status` events; resume with `Last-Event-ID` or `?resume_after=`.
- **Execute**: `POST {SEGMENT_PREFIX}/process/execute` writes one frame and waits for its response frame.
- **Clear**: `DELETE {SEGMENT_PREFIX}/process/clear` empties the queue (and releases a halt).
- **Resume**: a frame that fails halts the queue until `POST {SEGMENT_PREFIX}/process/resume` (or a clear).
- **Configuration**: `GET {SEGMENT_PREFIX}/config/read`, `POST {SEGMENT_PREFIX}/config/write` (confirmed).
- **Retransmit**: `GET {SEGMENT_PREFIX}/retransmit` re-sends buffered events to repair a stream.

**Python client**: download [`{file_name}`]({SEGMENT_PREFIX}/models.py), then `from {file_name[:-3]} import *`
and use `{client_class_name(self.model_prefix)}` (or the alias `Client`). Requires `pip install amt-dtp-ise-lib`."""

    def _segment_router(self) -> APIRouter:
        engine = self._segment
        models = engine.models
        CommandFrame_ = models.command_frame
        ResponseFrame_ = models.response_frame
        ComponentConfigs_ = models.component_configs
        file_name = self.client_file_name
        router = APIRouter(prefix=SEGMENT_PREFIX, tags=["Segment"])

        def queue_full(ex: QueueFullError) -> HTTPException:
            return HTTPException(status_code=413, detail=str(ex))  # 413 Content Too Large

        @router.get("/info", summary="Get Segment Info", response_model=SegmentPreviewInfo if self._preview else SegmentInfo)
        async def info():
            return self._info()

        @router.get(
            "/models.py",
            summary="Download Client Models",
            description=f"A Python module with every model needed to talk to this API and a typed client "
                        f"class. Save it as `{file_name}` and import it; requires `amt-dtp-ise-lib`.",
            response_class=PlainTextResponse,
        )
        async def models_py():
            return PlainTextResponse(
                self._client_source,
                media_type="text/x-python; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{file_name}"',
                    "ETag": f'"{engine.fingerprint}"',
                },
            )

        @router.get(
            "/process/read",
            summary="Read",
            description="The response frame of the latest simulation step.",
            response_model=ResponseFrame_,
            responses={503: {"description": "No simulation step has run yet."}},
        )
        async def read():
            latest = engine.latest
            if latest is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="no simulation step has run yet (is the simulation playing?)",
                )
            return latest

        def enqueue(frames: List[BaseModel], expected_revision: Optional[int] = None) -> BaseModel:
            try:
                return engine.enqueue(frames, expected_revision=expected_revision)
            except QueueFullError as ex:
                raise queue_full(ex)
            except QueueChangedError as ex:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_coded(ErrorCode.QUEUE_CHANGED, str(ex)))

        write_description = ("Append command frames to the queue; one frame is applied per simulation step, in "
                             "order. The whole batch is validated first and either all frames are queued or none are. "
                             "While the segment is halted, frames are queued and run after a resume.")
        if self._preview:  # deferred preview feature: optimistic writes
            @router.post(
                "/process/write",
                summary="Write",
                description=write_description + " With expected_revision, the frames are appended only if the queue's "
                            "revision (info, or a RobotAPI compile) still has that value (409 QUEUE_CHANGED otherwise), "
                            "e.g. rows merged from compiled frames.",
                response_model=models.command_response,
                responses={409: {"description": "The queue changed since expected_revision (code QUEUE_CHANGED)."},
                           413: {"description": "The write would exceed the maximum queue length."}},
            )
            async def write(
                frames: List[CommandFrame_] = Body(..., description="The command frames, in execution order."),
                expected_revision: Optional[int] = Query(None, ge=0, description="Append only if the queue revision is this."),
            ):
                return enqueue(frames, expected_revision)
        else:
            @router.post(
                "/process/write",
                summary="Write",
                description=write_description,
                response_model=models.command_response,
                responses={413: {"description": "The write would exceed the maximum queue length."}},
            )
            async def write(
                frames: List[CommandFrame_] = Body(..., description="The command frames, in execution order."),
                expected_revision: Optional[int] = Query(None, include_in_schema=False),
            ):
                if expected_revision is not None:  # refuse rather than silently append without the check
                    raise HTTPException(status_code=400, detail="expected_revision is a deferred preview feature "
                                                                "that this API doesn't serve")
                return enqueue(frames)

        @router.delete(
            "/process/clear",
            summary="Clear",
            description="Remove every command frame from the queue and return them, and release a halt. The "
                        "actuators keep their last commands (velocity-driven actuators keep moving). Waiting "
                        "execute requests fail with 409.",
            response_model=List[CommandFrame_],
        )
        async def clear():
            return engine.clear()

        @router.post(
            "/process/resume",
            summary="Resume",
            description="Continue draining the queue: release a halt (when a frame fails, the segment stops draining the "
                        "queue until the client resumes or clears it). A no-op when not halted.",
            response_model=SegmentStatus,
        )
        async def resume():
            return engine.resume()

        if self._preview:  # deferred preview feature: pause without clearing
            @router.post(
                "/process/pause",
                summary="Pause",
                description="Pause without clearing (as robot controllers' remote-motion interfaces offer): from the next "
                            "step on no frame is "
                            "applied (the actuators hold their last commands) and the queued frames wait until POST "
                            "process/resume. Writes are still accepted; execute waits. A status event (paused) is emitted. "
                            "Idempotent.",
                response_model=SegmentStatus,
            )
            async def pause():
                return engine.pause()

        @router.post(
            "/process/execute",
            summary="Execute",
            description="Append one command frame to the end of the queue and wait until it has been applied, "
                        "then return the response frame of that step.",
            response_model=ResponseFrame_,
            responses={
                409: {"description": "The segment is halted (code HALTED), or the queue was cleared before the "
                                     "frame was applied."},
                413: {"description": "The write would exceed the maximum queue length."},
                503: {"description": "The API stopped before the frame was applied."},
                504: {"description": "The frame was not applied within the timeout; it was removed from the queue."},
            },
        )
        async def execute(
            frame: CommandFrame_ = Body(..., description="The command frame."),
            timeout: float = Query(30.0, gt=0, description="How long to wait for the frame to be applied, in seconds."),
        ):
            try:
                future = engine.submit(frame)
            except SegmentHaltedError as ex:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_coded(ErrorCode.HALTED, str(ex)))
            except QueueFullError as ex:
                raise queue_full(ex)
            waiting = asyncio.wrap_future(future)
            try:
                return await asyncio.wait_for(asyncio.shield(waiting), timeout)
            except asyncio.TimeoutError:
                removed = engine.withdraw(future)
                if future.done() and not future.cancelled() and future.exception() is None:
                    return future.result()
                future.cancel()
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"the frame was not applied within {timeout} s"
                           + ("; it was removed from the queue" if removed else ""),
                )
            except QueueClearedError as ex:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ex))
            except ServerStoppedError as ex:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(ex))

        @router.get(
            "/config/read",
            summary="Read Configuration",
            description="The configuration of every configurable component, with its revision.",
            response_model=models.configuration,
        )
        async def config_read():
            return engine.config_read()

        @router.post(
            "/config/write",
            summary="Write Configuration",
            description="A confirmed configuration write: validated, applied at the next step boundary (before that "
                        "step's frame, even while frames are queued), and answered with the outcome per component "
                        "and transducer, the new config_revision, and the first response frame that runs with it. "
                        "Only what is sent changes.",
            response_model=ConfigEvent,
            responses={503: {"description": "The API stopped before the write was applied."},
                       504: {"description": "No step ran within the timeout; the write was withdrawn."}},
        )
        async def config_write(
            value: ComponentConfigs_ = Body(..., description="The configuration to write, by component id."),
            timeout: float = Query(10.0, gt=0, description="How long to wait for the next step, in seconds."),
        ):
            future = engine.config_write(value)
            try:
                return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout)
            except asyncio.TimeoutError:
                removed = engine.withdraw_config(future)
                if future.done() and not future.cancelled() and future.exception() is None:
                    return future.result()
                future.cancel()
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"no simulation step ran within {timeout} s" + ("; the write was withdrawn" if removed else ""),
                )
            except ServerStoppedError as ex:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(ex))

        @router.get(
            "/stream",
            summary="Stream",
            description=(
                "A Server-Sent Events stream: `frame` events (a response frame per simulation step), `config` and "
                "`status` events. Each of these has `id: <instance_id>:<sequence>`. Without a resume point the "
                "latest response frame is sent first. To resume after a disconnect, send `Last-Event-ID` (browsers do) "
                "or `?resume_after=<sequence>`: buffered events after it are sent first (404 OUT_OF_RANGE if they "
                "are no longer buffered, unless `clamp=true`). Unsequenced events: `reset` (the stream could not "
                "resume exactly), `heartbeat` (every 15 s while idle, with `next_sequence`), and `error` "
                f"(STREAM_OVERFLOW: the subscriber fell more than {self._subscriber_queue_size} events behind; the "
                "stream ends)."
            ),
            response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}, "description": "A stream of events."},
                       404: {"description": "The resume point is no longer buffered (code OUT_OF_RANGE)."}},
        )
        async def stream(
            every: int = Query(1, ge=1, description="Only send every Nth response frame."),
            executed_only: bool = Query(False, description="Only send response frames of steps that applied a frame."),
            resume_after: Optional[int] = Query(None, ge=0, description="Resume after this sequence (instead of "
                                                                         "Last-Event-ID)."),
            instance_id: Optional[str] = Query(None, description="The instance_id resume_after belongs to; a "
                                                                 "mismatch sends a reset event instead of resuming."),
            clamp: bool = Query(False, description="If the resume point is no longer buffered, resume from the "
                                                   "oldest buffered event (after a reset event) instead of failing."),
            last_event_id: Optional[str] = Header(None, description="Set by EventSource when it reconnects."),
        ):
            return self._open_stream(every, executed_only, resume_after, instance_id, clamp, last_event_id)

        @router.get(
            "/retransmit",
            summary="Retransmit",
            description="Re-send buffered events to repair a stream: by a list (`sequences=4,8,15`; those not "
                        "buffered are listed in `missing`) or by an inclusive range (`from`, `to`, at most `count`). "
                        "The retransmission buffer is small and only for repair; see the header for what it holds.",
            response_model=RetransmitResponse,
            responses={404: {"description": "The range starts outside the buffer (code OUT_OF_RANGE)."},
                       409: {"description": "instance_id is not the current one (code INSTANCE_CHANGED)."}},
        )
        async def retransmit(
            sequences: Optional[str] = Query(None, description="Comma-separated sequences, e.g. 4,8,15."),
            from_: Optional[int] = Query(None, alias="from", ge=0, description="The first sequence of a range."),
            to: Optional[int] = Query(None, ge=0, description="The last sequence of a range (default: the newest)."),
            count: int = Query(1000, ge=1, description="The most events a range returns."),
            clamp: bool = Query(False, description="Start a range that begins before the buffer at the oldest "
                                                   "buffered event instead of failing."),
            instance_id: Optional[str] = Query(None, description="If given and not the current instance_id: 409."),
        ):
            if instance_id is not None and instance_id != engine.instance_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_coded(
                    ErrorCode.INSTANCE_CHANGED, "the segment restarted (its instance_id changed)",
                    header=engine.header().model_dump()))
            if (sequences is None) == (from_ is None):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="pass either sequences=... or from=... (with an optional to=)")
            numbers: Optional[List[int]] = None
            if sequences is not None:
                try:
                    numbers = [int(s) for s in sequences.split(",") if s.strip()]
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail="sequences must be comma-separated integers")
            try:
                header, found, missing, truncated = engine.retransmit(numbers, from_, to, count, clamp)
            except OutOfRangeError as ex:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ex.detail)
            return RetransmitResponse(
                header=header, missing=missing, truncated=truncated,
                events=[BufferedEvent(event=e.event, sequence=e.sequence, data=json.loads(e.json)) for e in found],
            )

        return router

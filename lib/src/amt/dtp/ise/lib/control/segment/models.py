"""Models of the segment API, shared by the engine, the server, and clients (no fastapi needed).

A segment controls one or more automation components. Each frame is one row of the frame queue that
holds every component, like a fieldbus frame (e.g. EtherCAT) that passes through every machine on each
cycle: a command frame carries commands for all components (applied together, on the same simulation
step, for synchronized motion across machines), and a response frame carries the state of all
components after that step.

The generic classes here end in ``Base``; each API subclasses them with its own models (e.g.
``MachiningCellCommandFrame``), and a generated client module gives those the fixed aliases
``CommandFrame``, ``ResponseFrame``, ``CommandResponse``, ``ComponentCommands``, ``ComponentStates``,
``ComponentConfigs`` and ``Configuration``.
"""
from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Literal, Mapping, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..component import AutomationComponentBase, ModelFieldSpec, build_container_model


TA = TypeVar("TA", bound=BaseModel)  # a component's actuals model
TC = TypeVar("TC", bound=BaseModel)  # a component's commands model (or the API's component commands)
TS = TypeVar("TS", bound=BaseModel)  # the API's component states
TG = TypeVar("TG", bound=BaseModel)  # the API's component configurations


# ---- errors ---------------------------------------------------------------------------------------

class ErrorCode:
    """The error codes of :class:`SegmentError` (and of coded HTTP errors). Codes are strings, so new
    codes don't break old clients."""
    WRITE_FAILED = "WRITE_FAILED"
    READ_FAILED = "READ_FAILED"
    IK_UNREACHABLE = "IK_UNREACHABLE"
    PHYSICS_VIEW_UNAVAILABLE = "PHYSICS_VIEW_UNAVAILABLE"
    CONFIG_WRITE_FAILED = "CONFIG_WRITE_FAILED"
    STREAM_OVERFLOW = "STREAM_OVERFLOW"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    INSTANCE_CHANGED = "INSTANCE_CHANGED"
    HALTED = "HALTED"
    QUEUE_CHANGED = "QUEUE_CHANGED"
    PATH_TOLERANCE = "PATH_TOLERANCE"
    GOAL_TOLERANCE = "GOAL_TOLERANCE"


def error_code(ex: BaseException, default: str) -> str:
    """The error code of an exception: its class's ``error_code`` attribute (e.g. ``IK_UNREACHABLE``),
    else ``default``."""
    code = getattr(ex, "error_code", None)
    return code if isinstance(code, str) and code else default


# --8<-- [start:SegmentError]
class SegmentError(BaseModel):
    """A typed error: why applying a frame (or a configuration) failed, or why a stream request can't
    be served."""
    code: str = Field(
        title="Code",
        description="WRITE_FAILED, READ_FAILED, IK_UNREACHABLE, PHYSICS_VIEW_UNAVAILABLE, CONFIG_WRITE_FAILED, "
                    "STREAM_OVERFLOW, OUT_OF_RANGE, INSTANCE_CHANGED, HALTED, QUEUE_CHANGED, PATH_TOLERANCE, or "
                    "GOAL_TOLERANCE (more may be added).",
    )
    message: str = Field(title="Message", description="What happened, for people.")
    component: Optional[str] = Field(None, title="Component", description="The component id, if the error is about one.")
    transducer: Optional[str] = Field(None, title="Transducer", description="The transducer id, if the error is about one.")
    sequence: Optional[int] = Field(None, title="Sequence", description="The sequence of the event that carries the error.")
# --8<-- [end:SegmentError]


# ---- process data -----------------------------------------------------------------------------------

ComponentStatus = Literal["applied", "failed", "not_addressed"]


# --8<-- [start:FrameModels]
class CommandFrameBase(BaseModel, Generic[TC]):
    """One frame of commands for every component, applied on one physics step. Frames are applied one
    per step, in order; all of a frame's commands are applied on the same step."""
    model_config = ConfigDict(extra="forbid")
    frame_id: Optional[int] = Field(
        None,
        title="Frame ID",
        description="An optional id chosen by the client. It is echoed in the response frame of the step "
                    "that applies this frame, so the client can tell which frames have been applied.",
    )
    components: Optional[TC] = Field(
        None,
        title="Components",
        description="Commands, by component id: send only what should change. Components, actuators, and "
                    "joints that are omitted (or null) are left unchanged, so a frame without components "
                    "changes nothing and just takes one step (e.g. to wait in the middle of a batch).",
    )


class ComponentStateBase(BaseModel, Generic[TA, TC]):
    """One component's state after a step: its actual values, the readback of its commands, its status
    for this frame, and any errors."""
    status: ComponentStatus = Field(
        title="Status",
        description="applied: the frame had commands for this component and writing them succeeded. failed: "
                    "writing them raised (see errors; the segment halts). not_addressed: no frame was applied "
                    "this step, or it had nothing for this component.",
    )
    actuals: Optional[TA] = Field(
        title="Actuals",
        description="The measured (actual) values, read after applying the frame; null if reading failed "
                    "(see errors).",
    )
    commands: Optional[TC] = Field(
        title="Commands",
        description="Readback of the commanded targets in effect after applying the frame; null if reading "
                    "failed (see errors).",
    )
    errors: List[SegmentError] = Field(
        default_factory=list,
        title="Errors",
        description="Why applying this component's commands, or reading it, failed this step. A failed write "
                    "names the transducer; the other transducers of the frame were still written (writes are "
                    "not transactional).",
    )


class ResponseFrameBase(BaseModel, Generic[TS]):
    """The state of every component after one physics step."""
    instance_id: str = Field(
        title="Instance ID",
        description="Identifies the segment; it changes whenever sequence numbering restarts (every play).",
    )
    sequence: int = Field(
        title="Sequence",
        description="Assigned by the server to every event it produces, starting at 1 (outbound only; not "
                    "the client's frame_id). A gap means events were missed (when the stream isn't filtered).",
    )
    config_revision: int = Field(
        title="Configuration Revision",
        description="The revision of the configuration this step ran with (0 until the first configuration write).",
    )
    halted: bool = Field(
        title="Halted",
        description="Whether the queue is halted after this step: a frame failed, and queued frames wait until "
                    "the client resumes (POST /api/segment/process/resume) or clears the queue.",
    )
    physics_timestamp: float = Field(
        title="Physics Timestamp",
        description="The physics simulation time of the step in seconds: the sum of the physics time steps "
                    "of all steps since the simulation started playing, including this one.",
    )
    timeline_timestamp: Optional[float] = Field(
        None,
        title="Timeline Timestamp",
        description="The timeline time when the step ran, in seconds. The timeline advances once per app "
                    "update, which can span several physics steps, so consecutive response frames can share it.",
    )
    wall_timestamp: float = Field(
        title="Wall Timestamp",
        description="The wall-clock time when the step ran, in seconds since the Unix epoch (UTC).",
    )
    frame_id: Optional[int] = Field(
        None,
        title="Frame ID",
        description="The frame_id of the command frame applied this step, if one was applied and it had a frame_id.",
    )
    components: TS = Field(
        title="Components",
        description="The state of every component, by component id.",
    )
# --8<-- [end:FrameModels]


class CommandResponseBase(BaseModel):
    """Acknowledgement of a write to the queue: the frames were accepted (validated and queued), not yet
    applied."""
    accepted: int = Field(title="Accepted", description="The number of command frames added to the queue.")
    queue_length: int = Field(title="Queue Length", description="The number of command frames in the queue after the write.")
    halted: bool = Field(
        title="Halted",
        description="Whether the segment is halted: if so, the frames wait until the client resumes.",
    )


# ---- configuration ------------------------------------------------------------------------------------

class ConfigurationBase(BaseModel, Generic[TG]):
    """The configuration of every configurable component."""
    instance_id: str = Field(title="Instance ID")
    config_revision: int = Field(title="Configuration Revision")
    components: TG = Field(title="Components", description="The configuration, by component id.")


class ConfigOutcome(BaseModel):
    """The outcome of a configuration write for one component."""
    status: Literal["applied", "failed"] = Field(
        title="Status",
        description="applied: every transducer in the write was configured. failed: at least one failed (see "
                    "errors); the others were configured (not transactional).",
    )
    applied: List[str] = Field(default_factory=list, title="Applied", description="The transducer ids configured.")
    errors: List[SegmentError] = Field(default_factory=list, title="Errors")


class ConfigEvent(BaseModel):
    """A configuration write, applied at a step boundary: the answer to POST /api/segment/config/write
    and a `config` event on the stream."""
    instance_id: str = Field(title="Instance ID")
    sequence: int = Field(title="Sequence", description="The sequence of this config event.")
    config_revision: int = Field(
        title="Configuration Revision",
        description="The revision after the write; incremented if at least one transducer was configured.",
    )
    effective_sequence: int = Field(
        title="Effective Sequence",
        description="The sequence of the first response frame produced with this configuration.",
    )
    physics_timestamp: float = Field(title="Physics Timestamp")
    wall_timestamp: float = Field(title="Wall Timestamp")
    components: Dict[str, ConfigOutcome] = Field(title="Components", description="The outcome by component id.")


# ---- status, stream, and retransmission -----------------------------------------------------------------

SegmentState = Literal["running", "paused", "halted", "stopped"]


class HaltInfo(BaseModel):
    """Why the segment halted: the frame that failed, or the step whose monitor (e.g. a robot's path tolerance)
    failed."""
    sequence: int = Field(title="Sequence", description="The sequence of the response frame that failed.")
    frame_id: Optional[int] = Field(None, title="Frame ID")
    errors: List[SegmentError] = Field(default_factory=list, title="Errors")


class SegmentStatus(BaseModel):
    """Whether the segment drains its queue."""
    state: SegmentState = Field(
        title="State",
        description="running: one queued frame is applied per step. paused: the client paused the queue; queued "
                    "frames wait for a resume. halted: a frame (or a monitor) failed; queued frames wait for a resume or "
                    "a clear. stopped: the API stopped.",
    )
    queue_length: int = Field(title="Queue Length")
    paused: bool = Field(False, title="Paused", description="Whether the client paused the queue (also while halted).")
    halt: Optional[HaltInfo] = Field(None, title="Halt", description="The failed frame, while halted.")


class StatusEvent(SegmentStatus):
    """A change of the segment's status (a `status` event on the stream)."""
    instance_id: str = Field(title="Instance ID")
    sequence: int = Field(title="Sequence")
    reason: Literal["frame_failed", "monitor_failed", "paused", "resumed", "cleared", "stopped"] = Field(title="Reason")
    wall_timestamp: float = Field(title="Wall Timestamp")


class BufferHeader(BaseModel):
    """The state of the retransmission buffer (MTConnect's header fields, in snake case)."""
    instance_id: str = Field(title="Instance ID")
    buffer_size: int = Field(title="Buffer Size", description="The most events the buffer holds (0: no buffer).")
    first_sequence: Optional[int] = Field(None, title="First Sequence", description="The oldest buffered event (null if empty).")
    last_sequence: Optional[int] = Field(None, title="Last Sequence", description="The newest buffered event (null if empty).")
    next_sequence: int = Field(title="Next Sequence", description="The sequence the next event will have.")


class ErrorEvent(BaseModel):
    """An error about a stream connection (an `error` event; the stream ends after it)."""
    errors: List[SegmentError] = Field(title="Errors")
    header: Optional[BufferHeader] = Field(None, title="Header")


class ResetEvent(BaseModel):
    """A `reset` event: the stream could not continue exactly where the client asked, and starts afresh."""
    code: str = Field(title="Code", description="INSTANCE_CHANGED or OUT_OF_RANGE.")
    message: str = Field(title="Message")
    header: BufferHeader = Field(title="Header")
    lost_from: Optional[int] = Field(None, title="Lost From", description="The first sequence that can't be resent (OUT_OF_RANGE).")
    lost_to: Optional[int] = Field(None, title="Lost To", description="The last sequence that can't be resent (OUT_OF_RANGE).")


class Heartbeat(BaseModel):
    """A `heartbeat` event on an idle stream; it doesn't consume a sequence number."""
    instance_id: str = Field(title="Instance ID")
    next_sequence: int = Field(title="Next Sequence")


class BufferedEvent(BaseModel):
    """One event from the retransmission buffer."""
    event: Literal["frame", "config", "status"] = Field(title="Event")
    sequence: int = Field(title="Sequence")
    data: Dict[str, Any] = Field(title="Data", description="The event, as it was sent on the stream.")


class RetransmitResponse(BaseModel):
    """Buffered events, to repair a stream."""
    header: BufferHeader = Field(title="Header")
    events: List[BufferedEvent] = Field(title="Events", description="The events found, in sequence order.")
    missing: List[int] = Field(default_factory=list, title="Missing", description="Requested sequences that aren't buffered.")
    truncated: bool = Field(False, title="Truncated", description="Whether a range was cut at count events.")


class ComponentDescription(BaseModel):
    """One automation component served by a segment API."""
    id: str = Field(title="ID", description="The component's id in command and response frames.")
    name: str = Field(title="Name")
    description: str = Field(title="Description")
    version: str = Field(title="Version", description="The component version set by its author.")
    sensors: List[str] = Field(title="Sensors", description="The sensor ids (fields of its actuals).")
    actuators: List[str] = Field(title="Actuators", description="The actuator ids (fields of its commands).")
    configurable: bool = Field(title="Configurable", description="Whether the component has a configuration.")


class SegmentInfo(BaseModel):
    """Identity and current status of a segment API."""
    title: str = Field(title="Title")
    description: str = Field(title="Description")
    version: str = Field(title="Version", description="The API version set by its author.")
    fingerprint: str = Field(
        title="Fingerprint",
        description="A hash of the structure of the command and response frames and the configuration (every "
                    "component's models). Generated client code embeds the fingerprint it was generated from, "
                    "so a client can detect that the components changed.",
    )
    components: List[ComponentDescription] = Field(title="Components")
    steps: int = Field(title="Steps", description="The number of simulation steps since the segment was created.")
    physics_time_step: Optional[float] = Field(
        None,
        title="Physics Time Step",
        description="The duration of the latest physics step in seconds; one command frame is applied per "
                    "physics step. Null if no step has run yet.",
    )
    timeline_time_step: Optional[float] = Field(
        None,
        title="Timeline Time Step",
        description="How far the timeline time advanced at its latest update, in seconds. One timeline update "
                    "can span several physics steps. Null until the timeline has advanced.",
    )
    queue_length: int = Field(title="Queue Length")
    max_queue_length: int = Field(title="Maximum Queue Length")
    halted: bool = Field(title="Halted", description="Whether a failed frame halted the queue.")
    halt: Optional[HaltInfo] = Field(None, title="Halt", description="The failed frame, while halted.")
    stop_policy: Literal["hold"] = Field(
        "hold", title="Stop Policy",
        description="What clear and a halt (and pause, a preview feature) do to the actuators: hold (they keep their last commands; position "
                    "targets hold, velocity-driven actuators keep moving). The RobotAPI's clear can also decelerate robots.")
    config_revision: int = Field(title="Configuration Revision")
    buffer: BufferHeader = Field(title="Retransmission Buffer")


class SegmentPreviewInfo(SegmentInfo):
    """Segment info with the preview features (deferred; served only with
    ``preview_features=True``): pause, and optimistic writes."""
    paused: bool = Field(False, title="Paused", description="Whether the client paused the queue (POST process/pause).")
    queue_revision: int = Field(
        0, title="Queue Revision",
        description="Increments whenever frames are added to or removed from the queue other than by a step; pass it to "
                    "process/write?expected_revision= to append only if the queue hasn't changed since.")


# ---- per-API models -----------------------------------------------------------------------------------

@dataclass
class SegmentModels:
    """The models of one segment API, e.g. with prefix ``MachiningCell``:

    - ``component_commands``: ``MachiningCellComponentCommands``, one optional field per component id
      (that component's commands model).
    - ``component_states``: ``MachiningCellComponentStates``, one field per component id (that
      component's state model, e.g. ``AmtVmA1State``).
    - ``component_configs``: ``MachiningCellComponentConfigs``, one optional field per configurable
      component (that component's config model); ``configuration`` wraps it with the revision.
    - ``command_frame`` / ``response_frame`` / ``command_response``: ``MachiningCellCommandFrame`` /
      ``...ResponseFrame`` / ``...CommandResponse``.
    """
    component_commands: Type[BaseModel]
    component_states: Type[BaseModel]
    states: Dict[str, Type[BaseModel]]
    component_configs: Type[BaseModel]
    configuration: Type[BaseModel]
    command_frame: Type[BaseModel]
    response_frame: Type[BaseModel]
    command_response: Type[BaseModel]

    @property
    def roots(self) -> List[Type[BaseModel]]:
        """The models a generated client module is rendered from."""
        return [self.component_commands, self.component_states, self.component_configs, self.configuration,
                self.command_frame, self.response_frame, self.command_response]


def build_segment_models(prefix: str, components: Mapping[str, AutomationComponentBase]) -> SegmentModels:
    """Build an API's models. ``prefix`` (from the API's title) names the models that hold every
    component (``{prefix}ComponentCommands``, ``{prefix}ComponentStates``, ``{prefix}ComponentConfigs``,
    ``{prefix}Configuration``, ``{prefix}CommandFrame``, ``{prefix}ResponseFrame``,
    ``{prefix}CommandResponse``); each component's state model holds one component, so it is named after
    the component (``{component prefix}State``), and identical machines share it."""
    states: Dict[str, Type[BaseModel]] = {}
    for id, c in components.items():
        states[id] = create_model(
            f"{c.model_prefix}State",
            __base__=ComponentStateBase[c.actual_model, c.command_model],
            __doc__=f"The state of {c.name} after a simulation step.",
        )
    component_commands = build_container_model(
        f"{prefix}ComponentCommands",
        "Commands by component id. Components that are omitted (or null) are left unchanged.",
        {id: ModelFieldSpec(c.command_model, f"{c.name} v{c.version}: {c.description}".rstrip(": "))
         for id, c in components.items()},
    )
    component_states = create_model(
        f"{prefix}ComponentStates",
        __doc__="The state of every component, by component id.",
        **{id: (states[id], Field(title=id, description=f"{c.name} v{c.version}"))
           for id, c in components.items()},
    )
    component_configs = build_container_model(
        f"{prefix}ComponentConfigs",
        "Configuration by component id (configurable components only). When writing, components and "
        "transducers that are omitted (or null) are left unchanged.",
        {id: ModelFieldSpec(c.config_model, f"{c.name} v{c.version}")
         for id, c in components.items() if c.config_model is not None},
    )
    configuration = create_model(
        f"{prefix}Configuration",
        __base__=ConfigurationBase[component_configs],
        __doc__=ConfigurationBase.__doc__,
    )
    command_frame = create_model(
        f"{prefix}CommandFrame",
        __base__=CommandFrameBase[component_commands],
        __doc__=CommandFrameBase.__doc__,
    )
    response_frame = create_model(
        f"{prefix}ResponseFrame",
        __base__=ResponseFrameBase[component_states],
        __doc__=ResponseFrameBase.__doc__,
    )
    command_response = create_model(
        f"{prefix}CommandResponse",
        __base__=CommandResponseBase,
        __doc__=CommandResponseBase.__doc__,
    )
    return SegmentModels(component_commands, component_states, states, component_configs, configuration,
                         command_frame, response_frame, command_response)

"""Models of the RobotAPI (``/api/robot``), shared by servers and clients (no fastapi needed).

A **robot command** (:class:`RobotCommandBase`) is one row of a robot program: a move per robot (``set``,
``ptp``, or ``lin``), commands for other components passed through to the segment, and an optional
dwell. The RobotAPI compiles commands into segment command frames (one per physics step) when they are
written. The generic classes end in ``Base``; a RobotApi subclasses them with its segment's models, and its
generated client module gives those the fixed aliases ``RobotCommand``, ``RobotResponseFrame``, and
``RobotCompileResponse``.

Wire units: meters and degrees (joints by name; angular speeds in deg/s). Design:
``docs/src/Agents/Specs/robot-control.md`` ("The RobotAPI").
"""
from typing import Annotated, Any, Dict, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..motion.models import JointDescription, JointValues, RobotError, RobotErrorCode  # noqa: F401 (re-exported)
from ..transducer.models.pose import FramedPose, Pose, Vector3


TC = TypeVar("TC", bound=BaseModel)  # the segment's component commands
TF = TypeVar("TF", bound=BaseModel)  # a segment frame model (response or command)


# ---- moves ------------------------------------------------------------------------------------------

class MoveBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: Literal["base", "world"] = Field(
        "base", title="Reference",
        description="The frame the target pose is expressed in: the robot base (default) or the world (only for robots "
                    "with a base pose provider).",
    )
    tool_frame: Optional[str] = Field(
        None, title="Tool Frame", description="The tool frame to place at the target (default: the robot's active tool frame).")

    @model_validator(mode="after")
    def _type_is_always_sent(self) -> "MoveBase":
        # The discriminator must survive model_dump(exclude_unset=True), which clients use to send only
        # what was set (e.g. LinMove(target=...) leaves `type` at its default).
        self.__pydantic_fields_set__.add("type")
        return self


class TerminationMixin(BaseModel):
    """How a move ends (industrial robot termination types): ``fine`` stops at the target; ``cnt`` blends into the next
    command's move of the same robot without stopping (the corner is rounded, the target is not reached)."""
    termination: Literal["fine", "cnt"] = Field(
        "fine", title="Termination",
        description="fine: stop at the target (exact stop). cnt: blend into the next command's move of the same robot "
                    "(ptp into ptp; lin or circ into lin or circ), when both commands move only this robot and this one has "
                    "no dwell; otherwise the move ends at rest, like fine.")
    cnt: int = Field(100, ge=1, le=100, title="CNT",
                     description="With termination cnt: how much of the deceleration (and the next move's acceleration) "
                                 "overlaps, in percent; 100 rounds the corner most.", examples=[100])


class SetTcp(MoveBase):
    """Set the TCP: one target pose, reached on the next step (one frame). Jump-checked like every step, so
    a far target is rejected: use ``lin`` or ``ptp`` to get there."""
    type: Literal["set"] = Field("set", title="Type")
    target: Pose = Field(title="Target", description="The pose of the tool frame.")


class PtpMove(MoveBase, TerminationMixin):
    """Point-to-point: synchronized joint interpolation to a joint target or a TCP target (one IK at the
    goal), timed by the joints' velocity, acceleration, and (where the robot description has them) jerk
    limits. The path in Cartesian space is not a line."""
    type: Literal["ptp"] = Field("ptp", title="Type")
    target: Optional[Pose] = Field(None, title="Target", description="A tool frame pose (exactly one of target and joints).")
    joints: Optional[Dict[str, float]] = Field(
        None, title="Joints",
        description="Joint targets by joint name, in degrees (revolute) or meters (prismatic); joints left out keep their "
                    "position (exactly one of target and joints).",
    )
    velocity_scaling: float = Field(1.0, gt=0, le=1, title="Velocity Scaling",
                                    description="Fraction of the joints' velocity limits.", examples=[1.0])
    acceleration_scaling: float = Field(1.0, gt=0, le=1, title="Acceleration Scaling",
                                        description="Fraction of the joints' acceleration limits.", examples=[1.0])
    duration: Optional[float] = Field(None, gt=0, title="Duration",
                                      description="Seconds: stretch the move to this duration (rounded up to whole steps).")

    @model_validator(mode="after")
    def _one_goal(self) -> "PtpMove":
        if (self.target is None) == (self.joints is None):
            raise ValueError("give exactly one of target (a pose) and joints")
        return self


class CartesianLimits(BaseModel):
    velocity: float = Field(0.25, gt=0, title="Velocity", description="m/s (UR movel's default).", examples=[0.25])
    acceleration: Optional[float] = Field(1.2, gt=0, title="Acceleration",
                                          description="m/s² (UR movel's default); null: unlimited.", examples=[1.2])
    jerk: Optional[float] = Field(None, gt=0, title="Jerk",
                                  description="m/s³; null (the default): not limited (a trapezoidal profile). With a jerk "
                                              "(and an acceleration), the profile is jerk-limited (double-S).")
    angular_velocity: float = Field(90.0, gt=0, title="Angular Velocity", description="deg/s.", examples=[90.0])
    angular_acceleration: Optional[float] = Field(360.0, gt=0, title="Angular Acceleration",
                                                  description="deg/s²; null: unlimited.", examples=[360.0])
    angular_jerk: Optional[float] = Field(None, gt=0, title="Angular Jerk", description="deg/s³; null: not limited.")
    duration: Optional[float] = Field(None, gt=0, title="Duration",
                                      description="Seconds: stretch the move to this duration (rounded up to whole steps).")


class LinMove(MoveBase, CartesianLimits, TerminationMixin):
    """Linear: the tool frame moves along a straight line (position) with slerp (orientation), timed by the
    tighter of the linear and angular limits; IK runs for every step, then the jump check."""
    type: Literal["lin"] = Field("lin", title="Type")
    target: Pose = Field(title="Target", description="The pose of the tool frame.")


class CircMove(MoveBase, CartesianLimits, TerminationMixin):
    """Circular: the tool frame's origin moves along the circular arc from the start through ``via`` to the
    target (MoveIt Pilz CIRC's interim point); the orientation goes from the start's to the
    target's by slerp. Timed by the arc length and the rotation; IK runs for every step, then the jump check."""
    type: Literal["circ"] = Field("circ", title="Type")
    via: Vector3 = Field(title="Via", description="A point on the arc between the start and the target (meters, in the "
                                                  "target's reference frame).")
    target: Pose = Field(title="Target", description="The pose of the tool frame at the end of the arc.")


class PlanMove(MoveBase):
    """Plan and move: a collision-free path to a joint or TCP target from the robot's motion planner (obstacles
    from the simulation's scene, and the robot's own collision spheres), timed like a chain of ptp moves and
    checked for collisions at every step. Needs a planner: a RobotAPI robot's (obstacles from the scene), or a kinematics
    service solver's (obstacles the client sends to its world; sampling only, nothing is executed)."""
    type: Literal["plan"] = Field("plan", title="Type")
    target: Optional[Pose] = Field(None, title="Target", description="A tool frame pose (exactly one of target and joints).")
    joints: Optional[Dict[str, float]] = Field(
        None, title="Joints",
        description="Joint targets by joint name, in degrees (revolute) or meters (prismatic); joints left out keep their "
                    "position (exactly one of target and joints).",
    )
    velocity_scaling: float = Field(1.0, gt=0, le=1, title="Velocity Scaling",
                                    description="Fraction of the joints' velocity limits.", examples=[1.0])
    acceleration_scaling: float = Field(1.0, gt=0, le=1, title="Acceleration Scaling",
                                        description="Fraction of the joints' acceleration limits.", examples=[1.0])
    smooth: bool = Field(True, title="Smooth",
                         description="Blend through the path's corners without stopping (kept only if the blended path "
                                     "is collision-free too; otherwise the robot stops at each corner).")
    attempts: int = Field(3, ge=1, le=10, title="Attempts",
                          description="Plans to try (each with another random seed) until one is collision-free at every "
                                      "physics step.", examples=[3])

    @model_validator(mode="after")
    def _one_goal(self) -> "PlanMove":
        if (self.target is None) == (self.joints is None):
            raise ValueError("give exactly one of target (a pose) and joints")
        return self


RobotMove = Annotated[Union[SetTcp, PtpMove, LinMove, CircMove, PlanMove], Field(discriminator="type")]
"""A move of the RobotAPI."""

SampledMove = Annotated[Union[SetTcp, PtpMove, LinMove, CircMove], Field(discriminator="type")]
"""A move without a scene (the kinematics service's trajectory sampling): every move but plan."""


# ---- commands and frames -------------------------------------------------------------------------------

class RobotCommandBase(BaseModel, Generic[TC]):
    """One row of a robot program, compiled into one or more segment frames. Every robot's move starts on the
    command's first step; the command lasts as long as its longest move, then ``dwell``."""
    model_config = ConfigDict(extra="forbid")
    frame_id: Optional[int] = Field(
        None, title="Frame ID",
        description="Copied to every frame the command compiles to (so the stream shows which command runs; it has "
                    "finished on the last response frame with this id).",
    )
    robots: Dict[str, RobotMove] = Field(default_factory=dict, title="Robots", description="One move per robot id.")
    components: Optional[TC] = Field(
        None, title="Components",
        description="Segment commands for other components (or a robot's other transducers), applied on the command's "
                    "first step. They must not address the joints of a robot that moves in this command.",
    )
    dwell: float = Field(0.0, ge=0, title="Dwell", description="Seconds to hold after the motion (rounded up to steps).",
                         examples=[0.0])


class RobotState(BaseModel):
    """A robot's kinematic state after a step."""
    tool_frame: Optional[str] = Field(None, title="Tool Frame", description="The active tool frame.")
    target_tcp_pose: Optional[FramedPose] = Field(
        None, title="Target TCP Pose", description="FK of the commanded joints, in the robot base frame.")
    actual_tcp_pose: Optional[FramedPose] = Field(
        None, title="Actual TCP Pose", description="FK of the measured joints, in the robot base frame.")
    combination: Optional[str] = Field(None, title="Combination", description="The active end-effector combination.")
    errors: List[RobotError] = Field(default_factory=list, title="Errors")


class RobotResponseFrameBase(BaseModel, Generic[TF]):
    """One step: the segment's response frame and each robot's kinematic state, in one message."""
    frame: TF = Field(title="Frame", description="The segment's response frame of this step.")
    robots: Dict[str, RobotState] = Field(title="Robots", description="By robot id.")


class CompiledCommand(BaseModel):
    """What one robot command compiled to."""
    index: int = Field(title="Index", description="The command's index in the request.")
    frame_id: Optional[int] = Field(None, title="Frame ID")
    first_step: int = Field(title="First Step", description="Index of its first frame among the frames of the request.")
    steps: int = Field(title="Steps", description="The number of frames (physics steps) it takes.")
    duration: float = Field(title="Duration", description="Seconds (steps × the physics step).")
    blended: bool = Field(False, title="Blended",
                          description="Whether it blends into the next command (termination cnt): the next command's first "
                                      "frames overlap its deceleration and carry the next command's frame_id.")


class RobotCommandResponse(BaseModel):
    """Acknowledgement of a robot write: compiled and queued (not yet applied)."""
    accepted: int = Field(title="Accepted", description="The number of segment frames queued.")
    queue_length: int = Field(title="Queue Length")
    halted: bool = Field(title="Halted", description="Whether the segment is halted (the frames wait for a resume).")
    commands: List[CompiledCommand] = Field(title="Commands")


class RobotCompileRequestBase(BaseModel, Generic[TC]):
    """Compile robot commands from a given start (compile-then-merge): ``start`` replaces the predicted end of the
    queue for the robots it names, and ``dt`` the segment's physics step. A preview feature (deferred): served only by a RobotApi with ``preview_features=True``."""
    model_config = ConfigDict(extra="forbid")
    commands: List[RobotCommandBase[TC]] = Field(title="Commands", description="The robot commands, in order.")
    start: Optional[Dict[str, JointValues]] = Field(
        None, title="Start",
        description="By robot id: the joints (degrees or meters, by name; every joint) to start from instead of the "
                    "commanded joints at the end of the queue, e.g. the end of another compile.")
    dt: Optional[float] = Field(None, gt=0, title="dt",
                                description="Seconds per frame (default: the segment's physics step).")


class RobotCompileResponseBase(BaseModel, Generic[TF]):
    """Compiled robot commands, not queued: the segment command frames (ready for /api/segment/process/write)."""
    frames: List[TF] = Field(title="Frames")
    commands: List[CompiledCommand] = Field(title="Commands")


# The compile answer's preview fields (deferred): added to a RobotApi's compile response model only with
# preview_features=True (create_model field definitions).
COMPILE_PREVIEW_FIELDS: Dict[str, Any] = {
    "queue_revision": (Optional[int], Field(
        None, title="Queue Revision",
        description="The segment's queue revision the start state was predicted from: write the frames (or rows merged "
                    "from them) with /api/segment/process/write?expected_revision=… to append them only if the queue hasn't "
                    "changed since.")),
    "end": (Dict[str, JointValues], Field(
        default_factory=dict, title="End",
        description="By robot id: the joints at the end of the compiled frames (degrees or meters), to compile what follows "
                    "from (start).")),
}


class RobotPlanRequest(BaseModel):
    """Plan one robot's collision-free move without queueing it."""
    model_config = ConfigDict(extra="forbid")
    robot: str = Field(title="Robot")
    move: PlanMove = Field(title="Move")
    start: Optional[JointValues] = Field(
        None, title="Start", description="The joints to start from (default: the commanded joints at the end of the queue).")
    include_samples: bool = Field(False, title="Include Samples", description="Also return the joints at every step.")


class RobotPlanResponse(BaseModel):
    """A planned, timed, and collision-checked move."""
    robot: str = Field(title="Robot")
    waypoints: List[JointValues] = Field(title="Waypoints", description="The planner's path, from the start to the goal.")
    steps: int = Field(title="Steps")
    duration: float = Field(title="Duration", description="Seconds.")
    smoothed: bool = Field(title="Smoothed", description="Whether the corners are blended (else the robot stops at each).")
    attempts: int = Field(title="Attempts", description="Plans made until one was collision-free at every step.")
    samples: Optional[List[JointValues]] = Field(None, title="Samples", description="The joints at every step (include_samples).")


class RobotClearResponse(BaseModel):
    """The answer to a robot clear: what was removed, and how the robots stop."""
    removed: int = Field(title="Removed", description="The number of segment frames removed from the queue.")
    stop: Literal["hold", "decelerate"] = Field(title="Stop", description="The stop policy requested.")
    robots: Dict[str, Literal["hold", "decelerate"]] = Field(
        default_factory=dict, title="Robots",
        description="By robot id, how it stops: decelerate (a ramp to rest was queued) or hold (it holds its last target: it "
                    "was at rest, its velocity is unknown, or the ramp would leave its joint limits).")
    steps: int = Field(0, title="Steps", description="The frames queued for the deceleration.")


# ---- description and configuration ----------------------------------------------------------------------

class RobotJointDescription(JointDescription):
    """A robot joint as the RobotAPI uses it: its command field and its effective velocity limit."""
    field: str = Field(title="Field", description="The joint's field in the segment component's transducer commands.")
    velocity_limit_source: Literal["override", "urdf", "fallback"] = Field(
        title="Velocity Limit Source",
        description="override: the robot binding's; urdf: the robot description's; fallback: none was usable, so the "
                    "jump check's max_step per physics step is the limit.",
    )
    max_step: Optional[float] = Field(
        None, title="Max Step", description="The jump check's largest change per physics step (deg or m); null before the first step.")


class PlannerDescription(BaseModel):
    backend: str = Field(title="Backend")
    obstacles: List[str] = Field(title="Obstacles", description="The obstacles' names (e.g. USD prim paths).")
    revision: int = Field(title="Revision", description="Increments whenever the obstacles are replaced.")


class RobotDescription(BaseModel):
    """One robot served by a RobotApi."""
    id: str = Field(title="ID")
    component: str = Field(title="Component", description="The segment component id.")
    transducer: str = Field(title="Transducer", description="The component's articulation transducer id.")
    backend: str = Field(title="Backend")
    base_frame: str = Field(title="Base Frame")
    joints: List[RobotJointDescription] = Field(title="Joints")
    frames: List[str] = Field(title="Frames")
    tool_frames: List[str] = Field(title="Tool Frames")
    tool_frame: str = Field(title="Active Tool Frame")
    tcp_offset: Optional[Pose] = Field(None, title="TCP Offset", description="The tcp frame relative to the flange tool frame, if set.")
    jump_margin: float = Field(title="Jump Margin")
    world_reference: bool = Field(title="World Reference", description="Whether targets may use reference 'world'.")
    combination: Optional[str] = Field(None, title="Combination", description="The active end-effector combination.")
    combinations: List[str] = Field(default_factory=list, title="Combinations",
                                    description="The end-effector combinations (one solver each, built ahead of time).")
    planner: Optional[PlannerDescription] = Field(None, title="Planner", description="The motion planner (plan moves), if any.")
    path_tolerance: Optional[JointValues] = Field(
        None, title="Path Tolerance", description="Deg or m per joint: |actual − commanded| while moving; more halts the segment.")
    goal_tolerance: Optional[JointValues] = Field(
        None, title="Goal Tolerance", description="Deg or m per joint, to be reached within goal_time after a motion.")
    goal_time: float = Field(1.0, title="Goal Time", description="Seconds.")
    start_tolerance: Optional[JointValues] = Field(
        None, title="Start Tolerance", description="Deg or m per joint: a compile from a robot at rest is refused (409) beyond it.")


class RobotApiInfo(BaseModel):
    robots: List[RobotDescription] = Field(title="Robots")
    physics_time_step: Optional[float] = Field(None, title="Physics Time Step", description="Seconds; null before the first step.")


class RobotConfig(BaseModel):
    """A robot's configuration: which tool frame targets refer to, and the TCP definition."""
    model_config = ConfigDict(extra="forbid")
    combination: Optional[str] = Field(
        None, title="Combination",
        description="The active end-effector combination (one of the robot's combinations). Writing it switches the robot's "
                    "solver (and planner) and resets the tool frame to the combination's active TCP, unless tool_frame is "
                    "given too; a TCP offset set before is dropped.")
    tool_frame: Optional[str] = Field(None, title="Tool Frame", description="The active tool frame.")
    tcp_offset: Optional[Pose] = Field(
        None, title="TCP Offset",
        description="The pose of the 'tcp' frame relative to the flange tool frame (like UR's set_tcp). Writing it "
                    "defines or moves 'tcp' and makes it the active tool frame (unless tool_frame is given too).",
    )


class RobotConfiguration(BaseModel):
    """The configuration of every robot (read), or the changes to make (write; omitted robots and fields
    are unchanged)."""
    model_config = ConfigDict(extra="forbid")
    robots: Dict[str, RobotConfig] = Field(default_factory=dict, title="Robots")

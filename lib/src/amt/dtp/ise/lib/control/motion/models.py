"""Models of the kinematics service (``/api/kinematics``), shared by servers and clients (no fastapi needed).

Wire units: joint values in **degrees** (revolute) or **meters** (prismatic), keyed by joint name;
positions in meters; orientations as named quaternions; angular tolerances, speeds, and limits in degrees.
Solvers work in radians and meters: the server converts at the boundary. Design:
``docs/src/Agents/Specs/robot-control.md`` ("The kinematics service").
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ..transducer.models.pose import Pose, Vector3


class RobotErrorCode:
    """Error codes of the kinematics service and the RobotAPI (strings, so new codes don't break clients)."""
    IK_UNREACHABLE = "IK_UNREACHABLE"
    JOINT_JUMP = "JOINT_JUMP"
    JOINT_LIMIT = "JOINT_LIMIT"
    DURATION_TOO_SHORT = "DURATION_TOO_SHORT"
    TOO_MANY_STEPS = "TOO_MANY_STEPS"
    UNKNOWN_ROBOT = "UNKNOWN_ROBOT"
    UNKNOWN_FRAME = "UNKNOWN_FRAME"
    UNKNOWN_JOINT = "UNKNOWN_JOINT"
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
    CONFLICT = "CONFLICT"
    NO_STATE = "NO_STATE"
    QUEUE_CHANGED = "QUEUE_CHANGED"
    # the kinematics service
    UNKNOWN_SOLVER = "UNKNOWN_SOLVER"
    INVALID_DESCRIPTION = "INVALID_DESCRIPTION"
    UNKNOWN_BACKEND = "UNKNOWN_BACKEND"
    NAME_TAKEN = "NAME_TAKEN"
    INVALID_NAME = "INVALID_NAME"
    TOO_MANY_SOLVERS = "TOO_MANY_SOLVERS"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    UNSUPPORTED = "UNSUPPORTED"
    READ_ONLY = "READ_ONLY"
    # the upper layer (planning, execution semantics, end-effector combinations)
    INVALID_CIRCLE = "INVALID_CIRCLE"
    NO_PLANNER = "NO_PLANNER"
    PLAN_FAILED = "PLAN_FAILED"
    START_IN_COLLISION = "START_IN_COLLISION"
    GOAL_IN_COLLISION = "GOAL_IN_COLLISION"
    PATH_IN_COLLISION = "PATH_IN_COLLISION"
    START_TOLERANCE = "START_TOLERANCE"
    PATH_TOLERANCE = "PATH_TOLERANCE"
    GOAL_TOLERANCE = "GOAL_TOLERANCE"
    UNKNOWN_COMBINATION = "UNKNOWN_COMBINATION"
    INVALID_FRAGMENT = "INVALID_FRAGMENT"
    INVALID_OBSTACLE = "INVALID_OBSTACLE"


class RobotError(BaseModel):
    """A typed robot error: why a kinematics request or a robot command can't be served."""
    code: str = Field(
        title="Code",
        description="IK_UNREACHABLE, JOINT_JUMP, JOINT_LIMIT, DURATION_TOO_SHORT, TOO_MANY_STEPS, UNKNOWN_ROBOT, "
                    "UNKNOWN_SOLVER, UNKNOWN_FRAME, UNKNOWN_JOINT, REFERENCE_UNAVAILABLE, CONFLICT, NO_STATE, QUEUE_CHANGED, "
                    "UNSUPPORTED, ... (more may be added).",
    )
    message: str = Field(title="Message")
    command: Optional[int] = Field(None, title="Command", description="Index of the robot command in the request.")
    robot: Optional[str] = Field(None, title="Robot", description="The robot id (the solver id in the kinematics service).")
    step: Optional[int] = Field(None, title="Step", description="The step within the command (0-based), or the target index.")
    joint: Optional[str] = Field(None, title="Joint", description="The joint name.")


JointValues = Dict[str, float]

_JOINTS_DESCRIPTION = "By joint name, in degrees (revolute) or meters (prismatic); every joint is required."


# ---- description -------------------------------------------------------------------------------------------

class JointDescription(BaseModel):
    """One joint of a robot, in wire units (degrees or meters)."""
    name: str = Field(title="Name")
    type: Literal["revolute", "prismatic"] = Field(title="Type")
    unit: Literal["deg", "m"] = Field(title="Unit", description="deg for revolute joints, m for prismatic joints.")
    lower: Optional[float] = Field(None, title="Lower Limit", description="Null if unlimited.")
    upper: Optional[float] = Field(None, title="Upper Limit", description="Null if unlimited.")
    velocity_limit: Optional[float] = Field(
        None, title="Velocity Limit", description="deg/s or m/s; null if the robot description has none (or a placeholder).")
    acceleration_limit: Optional[float] = Field(None, title="Acceleration Limit", description="deg/s² or m/s²; null if none.")
    jerk_limit: Optional[float] = Field(None, title="Jerk Limit", description="deg/s³ or m/s³; null if none (used to time ptp and plan moves).")


class KinematicsDescription(BaseModel):
    """A solver's robot description: one robot, one end-effector combination."""
    id: str = Field(title="ID")
    backend: str = Field(title="Backend", description="The solver backend, e.g. cumotion.")
    base_frame: str = Field(title="Base Frame", description="The frame every pose is expressed in.")
    joints: List[JointDescription] = Field(title="Joints", description="In solver order.")
    frames: List[str] = Field(title="Frames", description="Every frame forward kinematics can be computed for.")
    tool_frames: List[str] = Field(title="Tool Frames", description="The first is the default.")
    default_tool_frame: str = Field(title="Default Tool Frame")
    revision: int = Field(title="Revision", description="Increments when the frames change (e.g. a TCP is set).")


class SolverFile(BaseModel):
    """A file a solver was created from."""
    name: str = Field(title="Name")
    kind: Literal["urdf", "xrdf", "robot_xrdf", "fragment", "combination"] = Field(
        title="Kind", description="urdf, xrdf (the one the solver loaded: merged, if fragments were given), robot_xrdf (the "
                                  "robot's own, before the merge), fragment, combination.")
    size: int = Field(title="Size", description="Bytes.")
    sha256: str = Field(title="SHA-256")


class SolverDescription(KinematicsDescription):
    """A solver of the kinematics service."""
    tool_frame: str = Field(title="Active Tool Frame", description="The default frame of every request.")
    flange_frame: str = Field(title="Flange Frame", description="The frame a TCP offset is fixed to.")
    tcp_offset: Optional[Pose] = Field(None, title="TCP Offset", description="The 'tcp' frame relative to the flange frame, if set.")
    capabilities: List[str] = Field(title="Capabilities", description="fk, ik, jacobian, frames, self_collision.")
    files: List[SolverFile] = Field(default_factory=list, title="Files", description="The uploaded robot description.")
    read_only: bool = Field(
        False, title="Read Only",
        description="A RobotAPI's robot: it can't be deleted or configured here (use /api/robot/config/write).")
    created: Optional[datetime] = Field(None, title="Created")


class ServiceLimits(BaseModel):
    """The service's limits; null means no limit."""
    max_solvers: Optional[int] = Field(title="Max Solvers")
    max_upload_bytes: Optional[int] = Field(title="Max Upload Bytes", description="Per file.")
    max_batch: Optional[int] = Field(title="Max Batch", description="Targets or configurations per request.")
    max_samples: Optional[int] = Field(title="Max Samples", description="Steps per sampled trajectory.")


class KinematicsServiceInfo(BaseModel):
    """What the kinematics service offers."""
    backends: List[str] = Field(title="Backends", description="Solver backends that can be created (e.g. cumotion).")
    default_backend: Optional[str] = Field(None, title="Default Backend")
    creatable: bool = Field(title="Creatable", description="Whether solvers can be created and deleted here.")
    planning_backends: List[str] = Field(
        default_factory=list, title="Planning Backends",
        description="Backends whose solvers can plan around obstacles set through /solvers/{solver}/world.")
    solvers: int = Field(title="Solvers", description="The number of solvers.")
    limits: ServiceLimits = Field(title="Limits")


class SolverCreateRequest(BaseModel):
    """Create a solver from a robot's URDF and XRDF (their text; or upload the files as multipart)."""
    model_config = ConfigDict(extra="forbid")
    urdf: str = Field(title="URDF", description="The URDF document.")
    xrdf: str = Field(title="XRDF", description="The XRDF document; it must list tool_frames.")
    name: Optional[str] = Field(
        None, title="Name", description="The solver id ([A-Za-z0-9][A-Za-z0-9_.-]{0,63}, unique); generated if omitted.")
    tool_frame: Optional[str] = Field(None, title="Tool Frame", description="The default tool frame, if not the XRDF's first.")
    backend: Optional[str] = Field(None, title="Backend", description="The solver backend (default: the service's default).")
    fragments: Optional[List[Union[str, Dict[str, Any]]]] = Field(
        None, title="Fragments",
        description="End-effector XRDF fragments (format xrdf-fragment 0.1; YAML/JSON text or objects) merged with the XRDF "
                    "into one combination before loading (needs combination).")
    combination: Optional[Union[str, Dict[str, Any]]] = Field(
        None, title="Combination",
        description="The combination: {stack: [{fragment, prefix, parent, offset?}], active_tcp?, ignore?} (text or object).")


# ---- kinematics --------------------------------------------------------------------------------------------

class FkRequest(BaseModel):
    """Forward kinematics for a batch of joint positions."""
    model_config = ConfigDict(extra="forbid")
    joints: List[JointValues] = Field(title="Joints", description=_JOINTS_DESCRIPTION)
    frame: Optional[str] = Field(None, title="Frame", description="The frame to compute (default: the active tool frame).")


class FkResponse(BaseModel):
    solver: str = Field(title="Solver")
    frame: str = Field(title="Frame")
    reference_frame: str = Field(title="Reference Frame", description="The robot base frame the poses are expressed in.")
    poses: List[Pose] = Field(title="Poses")


class IkRequest(BaseModel):
    """Inverse kinematics for a batch of target poses (in the robot base frame)."""
    model_config = ConfigDict(extra="forbid")
    targets: List[Pose] = Field(title="Targets", description="Poses of the frame, in the robot base frame.")
    frame: Optional[str] = Field(None, title="Frame", description="The frame to place (default: the active tool frame).")
    seed: Optional[JointValues] = Field(
        None, title="Seed", description="Joint positions to start from (degrees or meters; every joint), e.g. the current ones.")
    sequential: bool = Field(
        True, title="Sequential",
        description="Seed each target with the previous target's solution (continuity: one IK branch along a path).")
    position_tolerance: float = Field(0.001, gt=0, title="Position Tolerance", description="Meters.", examples=[0.001])
    orientation_tolerance: float = Field(0.5, gt=0, title="Orientation Tolerance", description="Degrees.", examples=[0.5])


class IkResult(BaseModel):
    success: bool = Field(title="Success")
    joints: Optional[JointValues] = Field(
        None, title="Joints", description="The solution (degrees or meters), or the best attempt when unsuccessful.")
    position_error: Optional[float] = Field(None, title="Position Error", description="Meters, measured by forward kinematics.")
    orientation_error: Optional[float] = Field(None, title="Orientation Error", description="Degrees, measured by forward kinematics.")
    error: Optional[RobotError] = Field(None, title="Error")


class IkResponse(BaseModel):
    solver: str = Field(title="Solver")
    frame: str = Field(title="Frame")
    success: bool = Field(title="Success", description="Whether every target was solved.")
    results: List[IkResult] = Field(title="Results", description="One per target, in order.")


class ReachableResponse(BaseModel):
    """Whether each target has an IK solution within the tolerances (like UR's get_inverse_kin_has_solution)."""
    solver: str = Field(title="Solver")
    frame: str = Field(title="Frame")
    reachable: List[bool] = Field(title="Reachable", description="One per target, in order.")
    all_reachable: bool = Field(title="All Reachable")


class JacobianRequest(BaseModel):
    """Jacobians of a frame at a batch of joint positions."""
    model_config = ConfigDict(extra="forbid")
    joints: List[JointValues] = Field(title="Joints", description=_JOINTS_DESCRIPTION)
    frame: Optional[str] = Field(None, title="Frame", description="Default: the active tool frame.")


class Jacobian(BaseModel):
    """The geometric Jacobian of a frame's origin in the robot base frame, in wire units: column *i* maps joint
    *i*'s velocity (deg/s for revolute joints, m/s for prismatic) to the frame's linear velocity (m/s, rows of
    ``linear``) and angular velocity (deg/s, rows of ``angular``)."""
    linear: List[List[float]] = Field(title="Linear", description="3 rows (x, y, z) × one column per joint.")
    angular: List[List[float]] = Field(title="Angular", description="3 rows (x, y, z) × one column per joint.")
    manipulability: float = Field(
        title="Manipulability",
        description="Yoshikawa's measure sqrt(det(J Jᵀ)) of the SI Jacobian (rad, m); near zero at a singularity.")


class JacobianResponse(BaseModel):
    solver: str = Field(title="Solver")
    frame: str = Field(title="Frame")
    reference_frame: str = Field(title="Reference Frame")
    joint_names: List[str] = Field(title="Joint Names", description="The column order.")
    jacobians: List[Jacobian] = Field(title="Jacobians", description="One per configuration.")


# ---- tool frames -------------------------------------------------------------------------------------------

class SolverFrames(BaseModel):
    solver: str = Field(title="Solver")
    base_frame: str = Field(title="Base Frame")
    frames: List[str] = Field(title="Frames")
    tool_frames: List[str] = Field(title="Tool Frames")
    default_tool_frame: str = Field(title="Default Tool Frame")
    tool_frame: str = Field(title="Active Tool Frame")
    flange_frame: str = Field(title="Flange Frame", description="The frame a TCP offset is fixed to.")
    tcp_offset: Optional[Pose] = Field(None, title="TCP Offset")


class SolverConfig(BaseModel):
    """A solver's configuration (read), or the changes to make (write; omitted fields are unchanged)."""
    model_config = ConfigDict(extra="forbid")
    tool_frame: Optional[str] = Field(None, title="Tool Frame", description="The active tool frame (the default frame of every request).")
    tcp_offset: Optional[Pose] = Field(
        None, title="TCP Offset",
        description="The pose of the 'tcp' frame relative to the flange frame (like UR's set_tcp). Writing it defines or "
                    "moves 'tcp' and makes it the active tool frame (unless tool_frame is given too).")
    clear_tcp: bool = Field(False, title="Clear TCP",
                            description="Remove the 'tcp' frame (the active tool frame falls back to the default if it was tcp).")


# ---- collision ---------------------------------------------------------------------------------------------

class SelfCollisionRequest(BaseModel):
    """Check configurations (one, or a path) against the robot's own collision spheres (no world)."""
    model_config = ConfigDict(extra="forbid")
    joints: List[JointValues] = Field(title="Joints", description=_JOINTS_DESCRIPTION)


class SelfCollisionResult(BaseModel):
    in_collision: bool = Field(title="In Collision")
    pairs: List[List[str]] = Field(default_factory=list, title="Pairs", description="Frames whose spheres intersect, in pairs.")


class SelfCollisionResponse(BaseModel):
    solver: str = Field(title="Solver")
    collision_free: bool = Field(title="Collision Free", description="No configuration is in self-collision.")
    first_collision: Optional[int] = Field(None, title="First Collision", description="Index of the first colliding configuration.")
    results: List[SelfCollisionResult] = Field(title="Results", description="One per configuration.")


class CollisionSpheresRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    joints: JointValues = Field(title="Joints", description=_JOINTS_DESCRIPTION)


class CollisionSphere(BaseModel):
    frame: str = Field(title="Frame")
    center: Vector3 = Field(title="Center", description="Meters, in the robot base frame.")
    radius: float = Field(title="Radius", description="Meters.")


class CollisionSpheres(BaseModel):
    solver: str = Field(title="Solver")
    reference_frame: str = Field(title="Reference Frame")
    spheres: List[CollisionSphere] = Field(title="Spheres")


# ---- world (obstacles set through the API) and world collision --------------------------------------------

class WorldDescription(BaseModel):
    """A solver's obstacles (its planner's world)."""
    solver: str = Field(title="Solver")
    reference: Literal["world", "base"] = Field(title="Reference")
    base_pose: Optional[Pose] = Field(None, title="Base Pose", description="The robot base's pose in the world frame.")
    obstacles: List[Dict[str, Any]] = Field(title="Obstacles", description="As set (see PlanningWorld).")
    revision: int = Field(title="Revision", description="Increments whenever the obstacles are replaced.")
    backend: str = Field(title="Backend", description="The planner's backend.")


class WorldCollisionRequest(BaseModel):
    """Check configurations (one, or a path) against the obstacles and the robot's own spheres."""
    model_config = ConfigDict(extra="forbid")
    joints: List[JointValues] = Field(title="Joints", description=_JOINTS_DESCRIPTION)


class WorldCollisionResult(BaseModel):
    in_collision: bool = Field(title="In Collision")
    detail: str = Field("", title="Detail", description="What collides (obstacles, self-collision), if anything.")


class WorldCollisionResponse(BaseModel):
    solver: str = Field(title="Solver")
    collision_free: bool = Field(title="Collision Free")
    first_collision: Optional[int] = Field(None, title="First Collision")
    results: List[WorldCollisionResult] = Field(title="Results", description="One per configuration.")

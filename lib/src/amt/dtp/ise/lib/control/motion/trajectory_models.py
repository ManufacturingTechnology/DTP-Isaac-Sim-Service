"""Models of the kinematics service's trajectory routes (``/api/kinematics/solvers/{solver}/trajectories``).

Separate from :mod:`.models` because they use the RobotAPI's move models (``set``, ``ptp``, ``lin``), and
``robot.models`` imports :mod:`.models`. Wire units as in :mod:`.models`.
"""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..robot.models import RobotMove
from ..transducer.models.pose import Pose
from .models import _JOINTS_DESCRIPTION, JointValues, RobotError




class TrajectoryLimits(BaseModel):
    """Limits for timing and the jump check (ADR-0013); the robot description's apply where none is given."""
    model_config = ConfigDict(extra="forbid")
    velocity_limits: Optional[JointValues] = Field(
        None, title="Velocity Limits", description="Overrides by joint name, deg/s or m/s.")
    acceleration_limits: Optional[JointValues] = Field(
        None, title="Acceleration Limits", description="Overrides by joint name, deg/s² or m/s².")
    jump_margin: float = Field(1.0, gt=0, title="Jump Margin",
                               description="A step may change a joint by velocity limit × dt × margin.", examples=[1.0])
    max_step_revolute: float = Field(5.0, gt=0, title="Max Step Revolute",
                                     description="Degrees per step for joints without a usable velocity limit.", examples=[5.0])
    max_step_prismatic: float = Field(0.01, gt=0, title="Max Step Prismatic",
                                      description="Meters per step for joints without a usable velocity limit.", examples=[0.01])


class TrajectoryRequest(TrajectoryLimits):
    """Sample one move at ``dt`` from ``start``: exactly as the RobotAPI compiles a move (timing, IK per sample,
    joint limits, the jump check), without a segment. Targets are in the robot base frame."""
    start: JointValues = Field(title="Start", description=_JOINTS_DESCRIPTION)
    move: RobotMove = Field(
        title="Move", description="set, ptp, lin, circ, or plan (the RobotAPI's move models; reference must be base; plan needs a planning backend).")
    dt: float = Field(gt=0, title="dt", description="Seconds per sample (e.g. the physics step).", examples=[0.01])
    include_poses: bool = Field(False, title="Include Poses",
                                description="Also return the tool frame's pose (FK) at each sample.")


class TrajectoryResponse(BaseModel):
    solver: str = Field(title="Solver")
    tool_frame: str = Field(title="Tool Frame")
    dt: float = Field(title="dt", description="Seconds.")
    steps: int = Field(title="Steps", description="Number of samples; the start is not repeated, the last is the goal.")
    duration: float = Field(title="Duration", description="steps × dt, seconds.")
    joints: List[JointValues] = Field(title="Joints", description="One per sample, at k × dt for k = 1…steps.")
    poses: Optional[List[Pose]] = Field(None, title="Poses", description="The tool frame at each sample (include_poses).")


class PathCheckRequest(TrajectoryLimits):
    """Check a joint path (one configuration per step of ``dt``): joint limits and the jump check."""
    start: Optional[JointValues] = Field(
        None, title="Start", description="The configuration before the first sample (default: the first sample).")
    joints: List[JointValues] = Field(title="Joints", description=_JOINTS_DESCRIPTION)
    dt: float = Field(gt=0, title="dt", description="Seconds per step.", examples=[0.01])


class PathCheckResponse(BaseModel):
    solver: str = Field(title="Solver")
    ok: bool = Field(title="OK", description="Within the joint limits and the jump check.")
    steps: int = Field(title="Steps")
    errors: List[RobotError] = Field(
        default_factory=list, title="Errors", description="The first JOINT_LIMIT and the first JOINT_JUMP violation, if any.")

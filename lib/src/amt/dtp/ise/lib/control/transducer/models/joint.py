from typing import List, Literal, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Every model forbids unknown fields (extra="forbid"), so a misspelt field in a command is a
# validation error rather than silently ignored (which would leave that actuator unchanged).
# Joint commands and measured (actual) values share the field names position and velocity: a command's
# value is a target, which its model implies, so the field name doesn't repeat it (ADR-0007).


# Drive modes are inferred from the drive gains: stiffness > 0 -> position control,
# stiffness == 0 and damping > 0 -> velocity control, both 0 -> no drive.
DriveMode = Literal["position", "velocity", "none"]


# --8<-- [start:JointActuatorModels]
class RevoluteJointPositionModel(BaseModel):
    """Position command of a position-driven revolute joint."""
    model_config = ConfigDict(extra="forbid")

    position: float = Field(
        title="Position",
        description="The target angular position in degrees.",
    )


class RevoluteJointVelocityModel(BaseModel):
    """Velocity command of a velocity-driven revolute joint."""
    model_config = ConfigDict(extra="forbid")

    velocity: float = Field(
        title="Velocity",
        description="The target angular velocity in degrees per second.",
    )


class PrismaticJointPositionModel(BaseModel):
    """Position command of a position-driven prismatic joint."""
    model_config = ConfigDict(extra="forbid")

    position: float = Field(
        title="Position",
        description="The target linear position in meters.",
    )


class PrismaticJointVelocityModel(BaseModel):
    """Velocity command of a velocity-driven prismatic joint."""
    model_config = ConfigDict(extra="forbid")

    velocity: float = Field(
        title="Velocity",
        description="The target linear velocity in meters per second.",
    )
# --8<-- [end:JointActuatorModels]


# --8<-- [start:JointSensorModels]
class RevoluteJointSensorModel(BaseModel):
    """Measured state of a revolute (angular) joint DOF within an articulation."""
    model_config = ConfigDict(extra="forbid")

    drive_mode: DriveMode = Field(
        title="Drive Mode",
        description="The drive mode of the joint, inferred from its drive gains.",
    )
    position: float = Field(
        title="Position",
        description="The angular position in degrees.",
    )
    velocity: float = Field(
        title="Velocity",
        description="The angular velocity in degrees per second.",
    )
    effort: float = Field(
        title="Effort",
        description="The measured joint effort (torque) in N*m.",
    )
    forces: Tuple[float, float, float] = Field(
        title="Forces",
        description="The joint reaction force [x, y, z] in N, in the child link's frame.",
    )
    torques: Tuple[float, float, float] = Field(
        title="Torques",
        description="The joint reaction torque [x, y, z] in N*m, in the child link's frame.",
    )


class PrismaticJointSensorModel(BaseModel):
    """Measured state of a prismatic (linear) joint DOF within an articulation."""
    model_config = ConfigDict(extra="forbid")

    drive_mode: DriveMode = Field(
        title="Drive Mode",
        description="The drive mode of the joint, inferred from its drive gains.",
    )
    position: float = Field(
        title="Position",
        description="The linear position in meters.",
    )
    velocity: float = Field(
        title="Velocity",
        description="The linear velocity in meters per second.",
    )
    effort: float = Field(
        title="Effort",
        description="The measured joint effort (force) in N.",
    )
    forces: Tuple[float, float, float] = Field(
        title="Forces",
        description="The joint reaction force [x, y, z] in N, in the child link's frame.",
    )
    torques: Tuple[float, float, float] = Field(
        title="Torques",
        description="The joint reaction torque [x, y, z] in N*m, in the child link's frame.",
    )


class FixedJointSensorModel(BaseModel):
    """Measured reaction of a fixed joint within an articulation. Fixed joints have no DOF,
    but the incoming joint force of their child link is measurable."""
    model_config = ConfigDict(extra="forbid")

    forces: Tuple[float, float, float] = Field(
        title="Forces",
        description="The joint reaction force [x, y, z] in N, in the child link's frame.",
    )
    torques: Tuple[float, float, float] = Field(
        title="Torques",
        description="The joint reaction torque [x, y, z] in N*m, in the child link's frame.",
    )
# --8<-- [end:JointSensorModels]


# Per-axis models shared by the multi-axis standalone joint transducers (Spherical, D6).
# Axis tokens are the UsdPhysics DriveAPI/LimitAPI instance names. Units per axis:
# rot* -> degrees / deg-per-second, trans* -> meters / m-per-second.
JointAxisToken = Literal["transX", "transY", "transZ", "rotX", "rotY", "rotZ"]


class JointAxisActuatorModel(BaseModel):
    """The command of one axis of a multi-axis joint: exactly one of a target position or a target velocity."""
    model_config = ConfigDict(extra="forbid")
    axis: JointAxisToken
    position: Optional[float] = Field(
        None,
        title="Position",
        description="The target linear position in meters for a trans* axis, or rotational position in degrees for a rot* axis."
    )
    velocity: Optional[float] = Field(
        None,
        title="Velocity",
        description="The target linear velocity in m/s for a trans* axis, or rotational velocity in deg/s for a rot* axis."
    )

    @model_validator(mode="after")
    def _validate(self) -> "JointAxisActuatorModel":
        if (self.position is None) == (self.velocity is None):
            raise ValueError("exactly one of position or velocity must be specified")
        return self


class JointAxisSensorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    axis: JointAxisToken
    position: float
    velocity: float


class SphericalJointSensorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    axes: List[JointAxisSensorModel]


class D6JointActuatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    axes: List[JointAxisActuatorModel]


class D6JointSensorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    axes: List[JointAxisSensorModel]

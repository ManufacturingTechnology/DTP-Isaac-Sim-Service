from pydantic import BaseModel, ConfigDict

# Every model forbids unknown fields (extra="forbid"), so a misspelt field in a command is a
# validation error rather than silently ignored (which would leave that actuator unchanged).


# --8<-- [start:ArticulationModels]
class ArticulationSensorModel(BaseModel):
    """Base of every articulation's actuals (sensor) model.

    An ArticulationTransducer builds a subclass for its articulation, with one field per joint:
    a ``RevoluteJointSensorModel`` or ``PrismaticJointSensorModel`` per DOF, and a
    ``FixedJointSensorModel`` per fixed joint. E.g., for a mill named ``Mill``::

        class Mill_Actuals(ArticulationSensorModel):
            X_Axis: PrismaticJointSensorModel
            Spindle: RevoluteJointSensorModel
            Vise: FixedJointSensorModel
    """
    model_config = ConfigDict(extra="forbid")


class ArticulationActuatorModel(BaseModel):
    """Base of every articulation's commands (actuator) model.

    An ArticulationTransducer builds a subclass for its articulation, with one optional field per
    drivable joint: a ``*JointPositionModel`` (a subclass carrying the joint's limits) for a
    position-driven joint, or a ``*JointVelocityModel`` for a velocity-driven joint. Joints that are
    omitted (or None) are left unchanged. E.g., for a mill named ``Mill``::

        class Mill_Commands(ArticulationActuatorModel):
            X_Axis: Optional[Mill_X_Axis_Commands] = None   # PrismaticJointPositionModel, 0 <= position <= 1
            Spindle: Optional[RevoluteJointVelocityModel] = None
    """
    model_config = ConfigDict(extra="forbid")
# --8<-- [end:ArticulationModels]

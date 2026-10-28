from typing import Literal, Tuple

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .pose import Quaternion, Vector3

# Every model forbids unknown fields (extra="forbid"), so a misspelt field in a command is a
# validation error rather than silently ignored (which would leave that actuator unchanged).


class ManipulatorRobotActuatorPose(BaseModel):
    """A pose of the robot's active tool frame, for the robot actuators (``ManipulatorRobotCuMotionActuator``,
    ``ManipulatorRobotLulaActuator``).

    ``position`` and ``orientation`` are the named models :class:`Vector3` and :class:`Quaternion`
    (ADR-0014). On the wire: ``{"position": {"x": ..., "y": ..., "z": ...}, "orientation": {"w": ..., "x": ...,
    "y": ..., "z": ...}}``. From numpy arrays (``position`` ``[x, y, z]``, ``orientation``
    ``[w, x, y, z]``), use :meth:`from_numpy`; the fields themselves don't accept arrays.
    """
    model_config = ConfigDict(extra="forbid")
    position: Vector3 = Field(
        title="Position",
        description="Cartesian position of the tool frame, in meters (stage units).",
    )
    orientation: Quaternion = Field(
        title="Orientation",
        description="Orientation of the tool frame as a quaternion with named components (normalized).",
    )
    frame_of_reference: Literal["local", "global"] = Field(
        "local",
        title="Frame of Reference",
        description="Whether the pose is relative to the articulation root prim position (local) or world (global)",
    )

    def to_numpy(self) -> Tuple[np.ndarray, np.ndarray]:
        """``(position [x, y, z], orientation [w, x, y, z])`` as float64 arrays."""
        return self.position.to_numpy(), self.orientation.to_wxyz()

    @staticmethod
    def from_numpy(
        position: np.ndarray, orientation: np.ndarray, frame_of_reference: Literal["local", "global"] = "local",
    ) -> "ManipulatorRobotActuatorPose":
        """From a position ``[x, y, z]`` and an orientation ``[w, x, y, z]`` (any 3- and 4-element arrays)."""
        return ManipulatorRobotActuatorPose(
            position=Vector3.from_numpy(position),
            orientation=Quaternion.from_wxyz(orientation),
            frame_of_reference=frame_of_reference,
        )


class ToolChangerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attached: bool = Field(
        title="Attached",
        description="Actuator: the command, attach (true) or detach (false). Sensor: whether an end effector "
                    "is seated at the tool changer (present in the clamp).",
    )

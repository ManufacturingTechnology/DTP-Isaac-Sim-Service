"""The robot base's world pose, for RobotAPI targets with ``reference="world"`` (``RobotBinding(base_pose=...)``).

The robot base frame is the robot asset's root frame (the articulation root prim, or its nearest Xformable
ancestor), as for ``ManipulatorRobotCuMotionActuator``. :func:`usd_base_pose` reads it from the USD stage on
every call, which is right for a robot whose base doesn't move while simulating; a mobile base needs its
pose from the physics simulation instead.
"""
from typing import Callable, Union

import omni.usd
import pxr.Sdf
import pxr.Usd
import pxr.UsdGeom

from ..lib.control.transducer.models import Pose, Quaternion, Vector3


def usd_base_pose(prim: Union[pxr.Usd.Prim, pxr.Sdf.Path, str]) -> Callable[[], Pose]:
    """A ``base_pose`` provider: the world pose (meters) of ``prim`` (the robot's root prim), from USD."""
    path = str(prim.GetPath()) if isinstance(prim, pxr.Usd.Prim) else str(prim)

    def base_pose() -> Pose:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        robot = stage.GetPrimAtPath(path)
        if not robot.IsValid():
            raise ValueError(f"no prim at '{path}'")
        while robot.IsValid() and not robot.IsA(pxr.UsdGeom.Xformable):
            robot = robot.GetParent()
        meters = pxr.UsdGeom.GetStageMetersPerUnit(stage)
        transform = pxr.UsdGeom.XformCache().GetLocalToWorldTransform(robot)
        translation = transform.ExtractTranslation()
        rotation = transform.ExtractRotationQuat()
        imaginary = rotation.GetImaginary()
        return Pose(
            position=Vector3(x=translation[0] * meters, y=translation[1] * meters, z=translation[2] * meters),
            orientation=Quaternion(w=rotation.GetReal(), x=imaginary[0], y=imaginary[1], z=imaginary[2]),
        )

    return base_pose

import math
import typing
from typing import Any, ClassVar, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Type
import asyncio
import numpy as np
from abc import abstractmethod
from dataclasses import dataclass
from pydantic import BaseModel, Field, create_model

import isaacsim
import isaacsim.core
import omni
import omni.kit.commands
import omni.timeline
import omni.usd
import pxr
import pxr.Gf
import pxr.Sdf
import pxr.PhysxSchema
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdPhysics

from ..lib.control.component import constrained_subclass, dynamic_model_name, id_problem, path_to_id
from ..lib.control.transducer.models import (
    DriveMode,
    RevoluteJointPositionModel,
    RevoluteJointVelocityModel,
    PrismaticJointPositionModel,
    PrismaticJointVelocityModel,
    RevoluteJointSensorModel,
    PrismaticJointSensorModel,
    FixedJointSensorModel,
    JointAxisToken,
    JointAxisActuatorModel,
    JointAxisSensorModel,
    SphericalJointSensorModel,
    D6JointActuatorModel,
    D6JointSensorModel,
    ArticulationSensorModel,
    ArticulationActuatorModel,
)
from .core import Sensor, Actuator
from ..isaac5_compatibility import SingleArticulation, SingleRigidPrim, ArticulationAction


def _drive_mode_from_gains(stiffness: Optional[float], damping: Optional[float], epsilon: float = 0.0) -> DriveMode:
    """PhysX joint drives are gain-based, not mode-flagged: stiffness > 0 tracks a position target
    (damping may be added for stability); stiffness == 0 with damping > 0 tracks a velocity target."""
    if (stiffness or 0.0) > epsilon:
        return "position"
    if (damping or 0.0) > epsilon:
        return "velocity"
    return "none"


def _joint_limits(prim: pxr.Usd.Prim) -> Tuple[Optional[float], Optional[float]]:
    """The (lower, upper) limits authored on a revolute (degrees) or prismatic (distance) joint;
    None where unlimited or not authored."""
    if prim.IsA(pxr.UsdPhysics.RevoluteJoint):
        joint = pxr.UsdPhysics.RevoluteJoint(prim)
    elif prim.IsA(pxr.UsdPhysics.PrismaticJoint):
        joint = pxr.UsdPhysics.PrismaticJoint(prim)
    else:
        return None, None

    def finite(value: Any) -> Optional[float]:
        return float(value) if value is not None and math.isfinite(float(value)) else None

    return finite(joint.GetLowerLimitAttr().Get()), finite(joint.GetUpperLimitAttr().Get())


class FixedJoint:

    @classmethod
    def define(
        cls,
        path: pxr.Sdf.Path | str,
    ) -> "FixedJoint":
        """
        Create a FixedJoint at a specified prim path.

        :param path: The path to the FixedJoint to create.
        :return: An instance of the calling class wrapping the created FixedJoint.
        """

        if isinstance(path, pxr.Sdf.Path):
            path = str(path)
        if not isinstance(path, str):
            raise TypeError("path must be a pxr.Sdf.Path | str")
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(path)
        if prim.IsValid():
            raise ValueError("prim already exists")

        joint: pxr.UsdPhysics.FixedJoint = pxr.UsdPhysics.FixedJoint.Define(stage, path)
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            raise Exception("Could not create prim")
        return cls(prim)
        

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        """
        Check whether a prim is a FixedJoint.

        :param prim: The prim or prim path to check.
        :return: Whether the prim is a FixedJoint.
        """
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        return prim.IsA(pxr.UsdPhysics.FixedJoint)

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        self._prim = prim
        
        if not self.is_a(prim):
            raise ValueError("prim is not a FixedJoint")

        self._joint: pxr.UsdPhysics.FixedJoint = pxr.UsdPhysics.FixedJoint(prim)

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim

    @property
    def is_fixed(self) -> bool:
        """
        Check whether the joint is currently fixing prims together.

        :return: Whether the joint is currently fixing prims together.
        """
        return len(self._joint.GetBody1Rel().GetTargets()) > 0

    def fix(
        self,
        root: pxr.Usd.Prim | pxr.Sdf.Path | str,
        link: pxr.Usd.Prim | pxr.Sdf.Path | str,
        disable_gravity: bool = True,
    ) -> None:
        """
        Fix two prims together using the FixedJoint.

        :param root: The root prim.
        :param link: The prim that should be fixed to the root prim.
        :param disable_gravity: Whether to disable gravity for the link prim, defaults to True
        """
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()

        if isinstance(root, pxr.Sdf.Path):
            root = str(root)
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not root.IsValid():
            raise ValueError("root must be a valid prim")

        if isinstance(link, pxr.Sdf.Path):
            link = str(link)
        if isinstance(link, str):
            link = stage.GetPrimAtPath(link)
        if not isinstance(link, pxr.Usd.Prim):
            raise TypeError("link must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not link.IsValid():
            raise ValueError("link must be a valid prim")
        
        if not isinstance(disable_gravity, bool):
            raise TypeError("disable_gravity must be a bool")
        
        # Capture the current relative pose so the workpiece is welded exactly where
        # it sits: localPose1 stays at the workpiece origin and localPose0 is the
        # workpiece frame expressed in root's local space.
        xform_cache = pxr.UsdGeom.XformCache()
        root_to_world = xform_cache.GetLocalToWorldTransform(root)
        workpiece_to_world = xform_cache.GetLocalToWorldTransform(link)
        workpiece_in_root = workpiece_to_world * root_to_world.GetInverse()

        translation = workpiece_in_root.ExtractTranslation()
        rotation = workpiece_in_root.ExtractRotationQuat()
    
        self._joint.CreateBody0Rel().SetTargets([root.GetPath()])
        self._joint.CreateBody1Rel().SetTargets([link.GetPath()])
        self._joint.CreateLocalPos0Attr().Set(pxr.Gf.Vec3f(translation))
        self._joint.CreateLocalRot0Attr().Set(pxr.Gf.Quatf(rotation))
        self._joint.CreateLocalPos1Attr().Set(pxr.Gf.Vec3f(0.0, 0.0, 0.0))
        self._joint.CreateLocalRot1Attr().Set(pxr.Gf.Quatf(1.0, 0.0, 0.0, 0.0))

        # A UsdPhysics.FixedJoint is solved iteratively, so under a constant load it
        # sags proportionally to 1 / solverIterations. Make the hold rigid by:
        #   1) Cranking the workpiece's solver iteration counts (the dominant lever).
        #   2) Disabling gravity on the workpiece so its weight stops fighting the
        #      joint while it is held (re-enabled in _unfix_workpiece).
        physx_rigid: pxr.PhysxSchema.PhysxRigidBodyAPI = pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(link)
        #physx_rigid.CreateSolverPositionIterationCountAttr().Set(64)
        #physx_rigid.CreateSolverVelocityIterationCountAttr().Set(16)
        physx_rigid.CreateSolverPositionIterationCountAttr().Set(32)
        physx_rigid.CreateSolverVelocityIterationCountAttr().Set(1)
        if disable_gravity:
            physx_rigid.CreateDisableGravityAttr().Set(True)

    def unfix(self) -> None:
        """
        Unfix prims fixed by the FixedJoint.
        """
        if not self.is_fixed:
            return

        # body1 (the held workpiece) is the link; resolve it before clearing the
        # relationship, after which it is no longer readable.
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        link_targets = self._joint.GetBody1Rel().GetTargets()
        link = stage.GetPrimAtPath(link_targets[0])

        # Clear the body relationships so the joint no longer constrains anything.
        self._joint.GetBody0Rel().SetTargets([])
        self._joint.GetBody1Rel().SetTargets([])

        # Restore gravity that fix() disabled so the workpiece falls/moves normally once
        # released. (Solver iteration counts are left raised; they only ever improve
        # stability for a free body.)
        physx_rigid = pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(link)
        physx_rigid.CreateDisableGravityAttr().Set(False)

    def remove(self) -> None:
        """
        Unfix prims fixed by the FixedJoint and delete the FixedJoint prim.
        """
        self.unfix()
        omni.kit.commands.execute("DeletePrims", paths=[str(self._prim.GetPath())])
        self._prim = None
        self._joint = None


class _SingleDriveJointActuator(Actuator[BaseModel]):
    """Shared logic of :class:`RevoluteJointActuator` and :class:`PrismaticJointActuator`.

    The model type depends on how the joint's drive is configured in the scene, which is read
    once at construction: a position-driven joint (stiffness > 0) takes a ``*PositionModel``
    command (``position``, with the joint's limits, if it has any), a velocity-driven joint
    (stiffness == 0, damping > 0) takes a ``*VelocityModel`` command (``velocity``). A joint
    with neither can't be actuated and is rejected.

    Writes/reads the drive *target* (the command), not a physics measurement, so it works for
    standalone (maximal-coordinate) joints as well as articulation joints. There is no sensor: a
    standalone joint's live position/velocity/effort have no direct readback in this build.
    """

    _DRIVE_TOKEN: ClassVar[str]
    _JOINT_TYPE: ClassVar[type]
    _JOINT_LABEL: ClassVar[str]
    _POSITION_MODEL: ClassVar[Type[BaseModel]]
    _VELOCITY_MODEL: ClassVar[Type[BaseModel]]
    _POSITION_UNIT: ClassVar[str]

    @classmethod
    def is_a(cls, prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        """Check whether a prim is a joint of this type that already has its drive."""
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        return prim.IsA(cls._JOINT_TYPE) and prim.HasAPI(pxr.UsdPhysics.DriveAPI, cls._DRIVE_TOKEN)

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)
        if not self.is_a(self._prim):
            raise ValueError(f"prim is not a {self._JOINT_LABEL} joint with a{'n' if self._DRIVE_TOKEN[0] in 'aeiou' else ''} "
                             f"{self._DRIVE_TOKEN} drive")
        # The joint and its drive must already exist in the scene (see is_a).
        self._drive: pxr.UsdPhysics.DriveAPI = pxr.UsdPhysics.DriveAPI.Get(self._prim, self._DRIVE_TOKEN)
        self._drive_mode: DriveMode = _drive_mode_from_gains(
            self._drive.GetStiffnessAttr().Get(), self._drive.GetDampingAttr().Get())
        if self._drive_mode == "none":
            raise ValueError(
                f"{self._JOINT_LABEL} joint '{self._prim.GetPath()}' can't be actuated: its {self._DRIVE_TOKEN} drive "
                f"has stiffness 0 and damping 0. Set stiffness > 0 for position control, or damping > 0 (and "
                f"stiffness 0) for velocity control."
            )

    @property
    def drive_mode(self) -> DriveMode:
        """``position`` or ``velocity``, from the drive gains at construction."""
        return self._drive_mode

    @property
    def command_model_type(self) -> Type[BaseModel]:
        if self._drive_mode == "velocity":
            return self._VELOCITY_MODEL

        def build() -> Type[BaseModel]:
            lower, upper = _joint_limits(self._prim)
            return constrained_subclass(
                dynamic_model_name(self.model_prefix, "Commands"),
                self._POSITION_MODEL,
                "position",
                description=f"The target position of {self._prim.GetName()} in {self._POSITION_UNIT}.",
                ge=lower,
                le=upper,
            )

        return self._dynamic_model("actuator", build)

    def actuator_write(self, value: BaseModel) -> None:
        model = self.command_model_type
        if not isinstance(value, model):
            expected = self._VELOCITY_MODEL if self._drive_mode == "velocity" else self._POSITION_MODEL
            if isinstance(value, (expected, Mapping)):
                value = model.model_validate(value, from_attributes=True)  # applies the joint limits
            else:
                raise TypeError(
                    f"{self!r} is {self._drive_mode}-driven, so it takes a {expected.__name__}, "
                    f"not a {type(value).__name__}"
                )
        # The models and the drive API use the same units (degrees / meters), so no conversion.
        if self._drive_mode == "position":
            self._drive.CreateTargetPositionAttr().Set(float(value.position))
        else:
            self._drive.CreateTargetVelocityAttr().Set(float(value.velocity))

    def actuator_read(self) -> BaseModel:
        if self._drive_mode == "velocity":
            velocity = self._drive.GetTargetVelocityAttr().Get() or 0.0
            return self._VELOCITY_MODEL(velocity=float(velocity))
        position = self._drive.GetTargetPositionAttr().Get() or 0.0
        # model_construct: report the authored target even if it is outside the limits.
        return self.command_model_type.model_construct(position=float(position))


class RevoluteJointActuator(_SingleDriveJointActuator):
    """Actuates a single RevoluteJoint via its angular drive (``UsdPhysics.DriveAPI``).

    Position-driven: :class:`RevoluteJointPositionModel` (degrees, limited to the joint's limits).
    Velocity-driven: :class:`RevoluteJointVelocityModel` (degrees per second). See
    :class:`_SingleDriveJointActuator`.
    """

    # UsdPhysics drives are multiple-apply; a revolute joint's drive instance is "angular".
    _DRIVE_TOKEN = "angular"
    _JOINT_TYPE = pxr.UsdPhysics.RevoluteJoint
    _JOINT_LABEL = "Revolute"
    _POSITION_MODEL = RevoluteJointPositionModel
    _VELOCITY_MODEL = RevoluteJointVelocityModel
    _POSITION_UNIT = "degrees"


class PrismaticJointActuator(_SingleDriveJointActuator):
    """Actuates a single PrismaticJoint via its linear drive (``UsdPhysics.DriveAPI``).

    Position-driven: :class:`PrismaticJointPositionModel` (meters, limited to the joint's limits).
    Velocity-driven: :class:`PrismaticJointVelocityModel` (meters per second). See
    :class:`_SingleDriveJointActuator`.

    Units: linear drives are authored directly in distance units / distance-per-second (meters
    and m/s on a meters stage), so no conversion is applied.
    """

    # UsdPhysics drives are multiple-apply; a prismatic joint's drive instance is "linear".
    _DRIVE_TOKEN = "linear"
    _JOINT_TYPE = pxr.UsdPhysics.PrismaticJoint
    _JOINT_LABEL = "Prismatic"
    _POSITION_MODEL = PrismaticJointPositionModel
    _VELOCITY_MODEL = PrismaticJointVelocityModel
    _POSITION_UNIT = "meters"


class _AxialJointTransducer:
    """Shared logic for standalone (maximal-coordinate) multi-axis joint transducers.

    Actuation drives the per-axis ``UsdPhysics.DriveAPI`` targets that already exist on the
    joint (written and read-back commands cover exactly those axes). Sensing computes per-axis
    position/velocity from the two bodies' poses and velocities — there is no standalone
    joint-state readback in this build, and effort/reaction forces are unavailable, so the
    sensor reports position+velocity only.

    Units: rot* axes in degrees / deg-per-second, trans* axes in meters / m-per-second.

    NOTE (needs in-sim verification): the kinematic read decomposes the relative joint-frame
    transform into per-axis coordinates (Euler XYZ for rotation), and projects relative body
    velocities onto the joint frame ignoring lever-arm/Coriolis terms. Signs/axis-order and
    multi-axis rotation decomposition should be validated against the running sim.
    """

    _ANGULAR_AXES = ("rotX", "rotY", "rotZ")
    _LINEAR_AXES = ("transX", "transY", "transZ")

    @staticmethod
    def _axis_is_angular(axis: str) -> bool:
        return axis.startswith("rot")

    def _candidate_axes(self) -> Tuple[str, ...]:
        """Axis tokens this transducer may operate on (subclass-specific)."""
        raise NotImplementedError()

    def _discover_drives(self) -> Dict[str, "pxr.UsdPhysics.DriveAPI"]:
        """Map axis token -> DriveAPI for every candidate axis that already has a drive."""
        drives: Dict[str, pxr.UsdPhysics.DriveAPI] = {}
        for axis in self._candidate_axes():
            if self._prim.HasAPI(pxr.UsdPhysics.DriveAPI, axis):
                drives[axis] = pxr.UsdPhysics.DriveAPI.Get(self._prim, axis)
        return drives

    @staticmethod
    def _axis_drive_mode(drive: "pxr.UsdPhysics.DriveAPI") -> DriveMode:
        return _drive_mode_from_gains(drive.GetStiffnessAttr().Get(), drive.GetDampingAttr().Get())

    def _write_axis_commands(self, axes: List[JointAxisActuatorModel]) -> None:
        for entry in axes:
            drive = self._drives.get(entry.axis)
            if drive is None:
                raise ValueError(f"axis '{entry.axis}' has no drive; cannot write a command")
            # Drive targets are authored in the same units as the model (deg for rot*,
            # m for trans*), so no conversion is applied.
            if entry.position is not None:
                drive.CreateTargetPositionAttr().Set(float(entry.position))
            elif entry.velocity is not None:
                drive.CreateTargetVelocityAttr().Set(float(entry.velocity))
            else:
                raise ValueError(f"axis '{entry.axis}' command must specify position or velocity")

    def _read_axis_commands(self) -> List[JointAxisActuatorModel]:
        entries: List[JointAxisActuatorModel] = []
        for axis, drive in self._drives.items():
            if self._axis_drive_mode(drive) == "velocity":
                velocity = drive.GetTargetVelocityAttr().Get() or 0.0
                entries.append(JointAxisActuatorModel(axis=axis, velocity=float(velocity)))
            else:
                position = drive.GetTargetPositionAttr().Get() or 0.0
                entries.append(JointAxisActuatorModel(axis=axis, position=float(position)))
        return entries

    def _compute_axis_states(self) -> Dict[str, Tuple[float, float]]:
        """Best-effort ``{axis: (position, velocity)}`` for every candidate axis, computed
        from body kinematics. See the class NOTE for accuracy caveats."""
        joint = pxr.UsdPhysics.Joint(self._prim)
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()

        def _local_matrix(pos_attr, rot_attr) -> pxr.Gf.Matrix4d:
            pos = pos_attr.Get()
            rot = rot_attr.Get()
            m = pxr.Gf.Matrix4d(1.0)
            if rot is not None:
                m.SetRotateOnly(pxr.Gf.Quatd(rot))
            if pos is not None:
                m.SetTranslateOnly(pxr.Gf.Vec3d(pos[0], pos[1], pos[2]))
            return m

        def _body(rel) -> Optional[pxr.Usd.Prim]:
            targets = rel.GetTargets()
            if not targets:
                return None
            prim = stage.GetPrimAtPath(targets[0])
            return prim if prim.IsValid() else None

        cache = pxr.UsdGeom.XformCache()
        body0 = _body(joint.GetBody0Rel())
        body1 = _body(joint.GetBody1Rel())
        world0 = cache.GetLocalToWorldTransform(body0) if body0 is not None else pxr.Gf.Matrix4d(1.0)
        world1 = cache.GetLocalToWorldTransform(body1) if body1 is not None else pxr.Gf.Matrix4d(1.0)

        # Joint frames in world, then body1's joint frame expressed in body0's joint frame.
        frame0 = _local_matrix(joint.GetLocalPos0Attr(), joint.GetLocalRot0Attr()) * world0
        frame1 = _local_matrix(joint.GetLocalPos1Attr(), joint.GetLocalRot1Attr()) * world1
        rel = frame1 * frame0.GetInverse()

        translation = rel.ExtractTranslation()  # meters, in frame0 axes
        euler = rel.ExtractRotation().Decompose(
            pxr.Gf.Vec3d.XAxis(), pxr.Gf.Vec3d.YAxis(), pxr.Gf.Vec3d.ZAxis()
        )  # degrees about X, Y, Z

        # Relative body velocities projected onto frame0 axes (best-effort; 0 if unavailable).
        frame0_rot_inv = frame0.ExtractRotation().GetInverse()
        lin_rel = pxr.Gf.Vec3d(0.0, 0.0, 0.0)
        ang_rel = pxr.Gf.Vec3d(0.0, 0.0, 0.0)
        try:
            def _vel(prim) -> Tuple[pxr.Gf.Vec3d, pxr.Gf.Vec3d]:
                if prim is None:
                    return pxr.Gf.Vec3d(0.0, 0.0, 0.0), pxr.Gf.Vec3d(0.0, 0.0, 0.0)
                rigid = SingleRigidPrim(str(prim.GetPath()))
                lin = rigid.get_linear_velocity()
                ang = rigid.get_angular_velocity()
                return pxr.Gf.Vec3d(float(lin[0]), float(lin[1]), float(lin[2])), \
                    pxr.Gf.Vec3d(float(ang[0]), float(ang[1]), float(ang[2]))
            lin0, ang0 = _vel(body0)
            lin1, ang1 = _vel(body1)
            lin_rel = frame0_rot_inv.TransformDir(lin1 - lin0)
            ang_rel = frame0_rot_inv.TransformDir(ang1 - ang0)
        except Exception:
            pass  # physics view unavailable this frame -> report zero velocity

        states: Dict[str, Tuple[float, float]] = {
            "transX": (float(translation[0]), float(lin_rel[0])),
            "transY": (float(translation[1]), float(lin_rel[1])),
            "transZ": (float(translation[2]), float(lin_rel[2])),
            "rotX": (float(euler[0]), math.degrees(float(ang_rel[0]))),
            "rotY": (float(euler[1]), math.degrees(float(ang_rel[1]))),
            "rotZ": (float(euler[2]), math.degrees(float(ang_rel[2]))),
        }
        return states


class SphericalJointSensor(
    Sensor[SphericalJointSensorModel],
    _AxialJointTransducer,
):
    """Sensor for a standalone SphericalJoint (3 angular DOFs: rotX/rotY/rotZ).

    Sensor-only: PhysX does not drive a maximal-coordinate spherical joint (it is a ball
    joint with a cone limit and no drive — per-axis spherical drives exist only inside an
    articulation). To actuate 3 rotational DOFs standalone, author a D6 joint with rotX/rotY/
    rotZ drives and use :class:`D6JointTransducer`. ``sensor_read()`` reports computed per-axis
    position (deg) and velocity (deg/s) for all 3 angular axes; see
    :class:`_AxialJointTransducer` for caveats.
    """

    def _candidate_axes(self) -> Tuple[str, ...]:
        return self._ANGULAR_AXES

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        """Check whether a prim is a SphericalJoint."""
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        return prim.IsA(pxr.UsdPhysics.SphericalJoint)

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)
        if not self.is_a(self._prim):
            raise ValueError("prim is not a SphericalJoint")

    @property
    def actual_model_type(self) -> Type[SphericalJointSensorModel]:
        return SphericalJointSensorModel

    def sensor_read(self) -> SphericalJointSensorModel:
        states = self._compute_axis_states()
        axes = [
            JointAxisSensorModel(axis=axis, position=states[axis][0], velocity=states[axis][1])
            for axis in self._candidate_axes()
        ]
        return SphericalJointSensorModel(axes=axes)


class D6JointTransducer(
    Sensor[D6JointSensorModel],
    Actuator[D6JointActuatorModel],
    _AxialJointTransducer,
):
    """Actuator + Sensor for a standalone generic (D6) joint, up to 6 DOFs.

    Actuation drives whichever of the 6 axes already have a ``DriveAPI`` (written and read-back
    commands cover those axes). ``sensor_read()`` reports computed per-axis position and velocity
    for all 6 axes (trans* in m / m-per-second, rot* in deg / deg-per-second). See
    :class:`_AxialJointTransducer` for caveats.
    """

    _ALL_AXES = ("transX", "transY", "transZ", "rotX", "rotY", "rotZ")

    def _candidate_axes(self) -> Tuple[str, ...]:
        return self._ALL_AXES

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        """Check whether a prim is a generic D6 joint: a UsdPhysics.Joint that is not one of
        the specialized joint types (revolute/prismatic/spherical/fixed/distance) or a
        gear / rack-and-pinion coupling."""
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        if not prim.IsA(pxr.UsdPhysics.Joint):
            return False
        specialized = (
            pxr.UsdPhysics.RevoluteJoint,
            pxr.UsdPhysics.PrismaticJoint,
            pxr.UsdPhysics.SphericalJoint,
            pxr.UsdPhysics.FixedJoint,
            pxr.UsdPhysics.DistanceJoint,
            pxr.PhysxSchema.PhysxPhysicsGearJoint,
            pxr.PhysxSchema.PhysxPhysicsRackAndPinionJoint,
        )
        return not any(prim.IsA(t) for t in specialized)

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)
        if not self.is_a(self._prim):
            raise ValueError("prim is not a generic D6 joint")
        self._drives: Dict[str, pxr.UsdPhysics.DriveAPI] = self._discover_drives()

    @property
    def command_model_type(self) -> Type[BaseModel]:
        """A D6JointActuatorModel subclass whose ``axis`` only accepts this joint's driven axes, so a
        command for an undriven axis is rejected when it's validated (e.g. when a frame is written to
        a segment API) rather than failing when it's applied."""
        def build() -> Type[BaseModel]:
            driven = tuple(self._drives)
            if driven:
                entry = create_model(
                    dynamic_model_name(self.model_prefix, "Axis_Commands"),
                    __base__=JointAxisActuatorModel,
                    __doc__=JointAxisActuatorModel.__doc__,
                    axis=(Literal[driven], Field(title="Axis", description=f"A driven axis: {', '.join(driven)}.")),
                )
                entry.model_config["from_attributes"] = True
                entry.model_rebuild(force=True)
                axes = (List[entry], Field(title="Axes", description="Commands of driven axes; axes that are "
                                                                     "omitted are left unchanged."))
            else:
                axes = (List[JointAxisActuatorModel], Field(max_length=0, title="Axes",
                                                            description="This joint has no driven axes."))
            model = create_model(
                dynamic_model_name(self.model_prefix, "Commands"),
                __base__=D6JointActuatorModel,
                __doc__=f"Commands of D6 joint {self.prim.GetPath()}.",
                axes=axes,
            )
            model.model_config["from_attributes"] = True
            model.model_rebuild(force=True)
            return model
        return self._dynamic_model("actuator", build)

    @property
    def actual_model_type(self) -> Type[D6JointSensorModel]:
        return D6JointSensorModel

    def actuator_write(self, value: D6JointActuatorModel | Mapping[str, Any]) -> None:
        model = self.command_model_type
        if not isinstance(value, model):
            if isinstance(value, D6JointActuatorModel):
                value = model.model_validate(value.model_dump())  # applies this joint's axis restriction
            elif isinstance(value, Mapping):
                value = model.model_validate(value)
            else:
                raise TypeError(f"value must be a {model.__name__} (a D6JointActuatorModel), not a {type(value).__name__}")
        self._write_axis_commands(value.axes)

    def actuator_read(self) -> BaseModel:
        return self.command_model_type.model_construct(axes=self._read_axis_commands())

    def sensor_read(self) -> D6JointSensorModel:
        states = self._compute_axis_states()
        axes = [
            JointAxisSensorModel(axis=axis, position=states[axis][0], velocity=states[axis][1])
            for axis in self._candidate_axes()
        ]
        return D6JointSensorModel(axes=axes)


class ArticulationTransducer(Sensor[ArticulationSensorModel], Actuator[ArticulationActuatorModel]):
    """Sensor + actuator for an articulation, with one model field per joint.

    The models are built from the articulation's joints when first used (so an AutomationComponent
    can name the transducer first). With ``name="Mill"``:

    - ``Mill_Actuals``: one required field per DOF (``RevoluteJointSensorModel`` or
      ``PrismaticJointSensorModel``, by DOF type) and per fixed joint (``FixedJointSensorModel``).
    - ``Mill_Commands``: one optional field per drivable DOF. A position-driven joint takes a
      ``*JointPositionModel`` (a ``Mill_<joint>_Commands`` subclass carrying the joint's limits),
      a velocity-driven joint a ``*JointVelocityModel``. Joints left out (or None) are unchanged.
      Joints without drive gains, spherical joints (which PhysX can't drive here), and fixed joints
      are not actuators.

    Field names are the DOF names; a DOF name that isn't a valid identifier (e.g. a spherical
    joint's ``Spherical:0``) is converted like a prim path (``Spherical_0``). Field names must be
    unique across moving and fixed joints.

    Units: angular DOFs in degrees / degrees per second, linear DOFs in meters / meters per second.
    """

    # Joint types PhysX articulations support as tree joints. Anything else in the tree
    # (generic D6, gear, rack-and-pinion, distance) is not usable with this transducer.
    # (Confirmed: the parser does not admit a generic D6 as an articulation tree joint.)
    _SUPPORTED_ARTICULATION_JOINTS = (
        pxr.UsdPhysics.FixedJoint,
        pxr.UsdPhysics.RevoluteJoint,
        pxr.UsdPhysics.PrismaticJoint,
        pxr.UsdPhysics.SphericalJoint,
    )

    @classmethod
    def is_a(cls, prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        """Whether ``prim`` is usable as an ArticulationTransducer.

        True iff ``prim`` is an articulation root (``UsdPhysics.ArticulationRootAPI``) and
        every tree joint under it is a supported type (fixed / revolute / prismatic /
        spherical). Joints flagged ``excludeFromArticulation`` are loop constraints rather
        than tree joints and are ignored. Unsupported tree joints (generic D6, gear,
        rack-and-pinion, distance) make the articulation unusable here and yield False.
        """
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        if not prim.HasAPI(pxr.UsdPhysics.ArticulationRootAPI):
            return False

        for descendant in pxr.Usd.PrimRange(prim):
            if not descendant.IsA(pxr.UsdPhysics.Joint):
                continue
            joint = pxr.UsdPhysics.Joint(descendant)
            if joint.GetExcludeFromArticulationAttr().Get():
                continue  # loop constraint, not a tree joint
            if not joint.GetBody0Rel().GetTargets() or not joint.GetBody1Rel().GetTargets():
                # Connects a link to the world (e.g., the generic root joint of Isaac Sim's Franka,
                # /panda/rootJoint): it anchors the articulation, it is not a tree joint between links
                continue
            if not any(descendant.IsA(t) for t in cls._SUPPORTED_ARTICULATION_JOINTS):
                return False
        return True

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)

        if not self.is_a(self._prim):
            raise ValueError(
                "prim is not a usable articulation: it must be an articulation root whose "
                "tree joints are all fixed/revolute/prismatic/spherical (generic D6, gear, "
                "rack-and-pinion, and distance joints are not supported)"
            )

        self._articulation = SingleArticulation(
            prim_path=str(self.prim.GetPath()),
            name="Articulation",
        )
        # Cache to reduce API calls. Assume these don't change at runtime.
        self._joint_names = self._articulation.dof_names

        # DOF names must be unique: sensor_read()/actuator_write() address joints by name, so
        # duplicates (e.g. two joints with the same leaf name under different parents)
        # would silently collapse or misroute commands. Fail loudly instead.
        seen: set = set()
        duplicates = sorted({name for name in self._joint_names if name in seen or seen.add(name)})
        if duplicates:
            raise ValueError(
                f"articulation '{self.prim.GetPath()}' has duplicate joint/DOF names "
                f"{duplicates}; joint names must be unique within an articulation "
                f"(rename the offending joints, e.g. add a suffix)"
            )

        self._joint_drive_modes: List[DriveMode] = self._get_drive_modes_raw()
        self._joint_dof_types: List[Literal["linear", "angular"]] = self._compute_dof_types()
        self._dof_joint_prims: List[pxr.Usd.Prim] = self._resolve_dof_joint_prims()
        self._dof_link_indices: List[int] = self._compute_dof_link_indices()
        # Spherical-joint DOFs are excluded from the actuator model because spherical joints
        # cannot be driven in this PhysX integration. They remain in the sensor model.
        self._dof_is_spherical: List[bool] = self._compute_dof_is_spherical()
        # Fixed joints have no DOF, so their reactions are reported separately in sensor_read():
        # (joint name, child-link force-row index).
        self._fixed_joint_links: List[Tuple[str, int]] = self._compute_fixed_joint_links()
        self._dof_limits: List[Tuple[Optional[float], Optional[float]]] = self._compute_dof_limits()

        # Model field names: one per DOF, then one per fixed joint.
        self._dof_fields: List[str] = [self._field_name(n) for n in self._joint_names]
        self._fixed_fields: List[str] = [self._field_name(n) for n, _ in self._fixed_joint_links]
        self._check_field_names()
        self._dof_index_by_field: Dict[str, int] = {f: i for i, f in enumerate(self._dof_fields)}
        self._dof_index_by_name: Dict[str, int] = {n: i for i, n in enumerate(self._joint_names)}

    @staticmethod
    def _field_name(joint_name: str) -> str:
        return joint_name if id_problem(joint_name) is None else path_to_id(joint_name)

    def _check_field_names(self) -> None:
        labels: Dict[str, List[str]] = {}
        for joint_name, field, prim in zip(self._joint_names, self._dof_fields, self._dof_joint_prims):
            labels.setdefault(field, []).append(f"DOF '{joint_name}' ({prim.GetPath() if prim.IsValid() else '?'})")
        for (joint_name, _), field in zip(self._fixed_joint_links, self._fixed_fields):
            labels.setdefault(field, []).append(f"fixed joint '{joint_name}'")
        problems = []
        for field, owners in labels.items():
            if len(owners) > 1:
                problems.append(f"{', '.join(owners)} all become the model field '{field}'")
            problem = id_problem(field)
            if problem is not None:
                problems.append(f"{owners[0]}: {problem}")
        if problems:
            raise ValueError(
                f"articulation '{self.prim.GetPath()}': joint names become model field names, so they must be "
                f"unique valid identifiers across its moving and fixed joints: " + "; ".join(problems)
                + ". Fix: rename the joint prims."
            )

    def _compute_dof_limits(self) -> List[Tuple[Optional[float], Optional[float]]]:
        limits: List[Tuple[Optional[float], Optional[float]]] = []
        for prim, dof_type, is_spherical in zip(self._dof_joint_prims, self._joint_dof_types, self._dof_is_spherical):
            if is_spherical or not prim.IsValid():
                limits.append((None, None))
                continue
            lower, upper = _joint_limits(prim)
            if dof_type == "angular":
                # PhysX confines articulation revolute DOFs to [-2pi, 2pi] = [-360, 360] deg.
                bound = self._ARTICULATION_REVOLUTE_LIMIT_DEG
                lower = -bound if lower is None else max(lower, -bound)
                upper = bound if upper is None else min(upper, bound)
            limits.append((lower, upper))
        return limits

    # ---- models -----------------------------------------------------------------------------

    @property
    def actual_model_type(self) -> Type[BaseModel]:
        def build() -> Type[BaseModel]:
            fields: Dict[str, Any] = {}
            for field, dof_type in zip(self._dof_fields, self._joint_dof_types):
                model = RevoluteJointSensorModel if dof_type == "angular" else PrismaticJointSensorModel
                fields[field] = (model, ...)
            for field in self._fixed_fields:
                fields[field] = (FixedJointSensorModel, ...)
            return create_model(
                dynamic_model_name(self.model_prefix, "Actuals"),
                __base__=ArticulationSensorModel,
                __doc__=f"Measured state of articulation {self.prim.GetPath()}: one field per joint DOF and per "
                        f"fixed joint.",
                **fields,
            )
        return self._dynamic_model("sensor", build)

    @property
    def command_model_type(self) -> Type[BaseModel]:
        def build() -> Type[BaseModel]:
            fields: Dict[str, Any] = {}
            for i, field in self._actuated_dofs():
                fields[field] = (Optional[self._dof_command_model(i, field)], None)
            return create_model(
                dynamic_model_name(self.model_prefix, "Commands"),
                __base__=ArticulationActuatorModel,
                __doc__=f"Commands of articulation {self.prim.GetPath()}: one field per drivable joint DOF. "
                        f"Joints that are omitted (or null) are left unchanged.",
                **fields,
            )
        return self._dynamic_model("actuator", build)

    def _actuated_dofs(self) -> List[Tuple[int, str]]:
        return [
            (i, field) for i, field in enumerate(self._dof_fields)
            if not self._dof_is_spherical[i] and self._joint_drive_modes[i] != "none"
        ]

    def _dof_command_model(self, i: int, field: str) -> Type[BaseModel]:
        angular = self._joint_dof_types[i] == "angular"
        if self._joint_drive_modes[i] == "velocity":
            return RevoluteJointVelocityModel if angular else PrismaticJointVelocityModel
        lower, upper = self._dof_limits[i]
        return constrained_subclass(
            dynamic_model_name(self.model_prefix, field, "Commands"),
            RevoluteJointPositionModel if angular else PrismaticJointPositionModel,
            "position",
            description=f"The target position of {self._joint_names[i]} in {'degrees' if angular else 'meters'}.",
            ge=lower,
            le=upper,
        )

    @property
    def joint_names(self) -> List[str]:
        """The DOF names, in articulation order."""
        return self._joint_names

    # Canonical model units: angular DOFs in degrees / deg-per-second, linear DOFs in
    # meters / m-per-second (matching the standalone joint actuators and UsdPhysics
    # authoring). The PhysX tensor API used here is radians / rad-per-second for angular
    # DOFs (linear is meters either way), so angular values are converted at this boundary.
    # PhysX also confines articulation revolute DOFs to [-2pi, 2pi] = [-360, 360] deg.
    _ARTICULATION_REVOLUTE_LIMIT_DEG = 360.0

    @staticmethod
    def _native_to_model(value: float, dof_type: Literal["linear", "angular"]) -> float:
        """Convert a tensor-API value (radians for angular DOFs) to model units (degrees)."""
        return math.degrees(float(value)) if dof_type == "angular" else float(value)

    @staticmethod
    def _model_to_native(value: float, dof_type: Literal["linear", "angular"]) -> float:
        """Convert a model value (degrees for angular DOFs) to tensor-API units (radians)."""
        return math.radians(float(value)) if dof_type == "angular" else float(value)

    def sensor_read(self) -> BaseModel:
        joint_forces, joint_torques = self._articulation.get_measured_joint_forces_and_torques()
        joint_efforts = self._articulation.get_measured_joint_efforts()
        joint_positions = self._articulation.get_joint_positions()
        joint_velocities = self._articulation.get_joint_velocities()

        fields: Dict[str, BaseModel] = {}
        for i, field in enumerate(self._dof_fields):
            dof_type = self._joint_dof_types[i]
            # forces/torques are per-link; map this DOF to its joint's child-link row.
            # A multi-DOF joint's DOFs share the same (per-joint) 6-D reaction.
            link_idx = self._dof_link_indices[i]
            model = RevoluteJointSensorModel if dof_type == "angular" else PrismaticJointSensorModel
            fields[field] = model(
                drive_mode=self._joint_drive_modes[i],
                position=self._native_to_model(joint_positions[i], dof_type),
                velocity=self._native_to_model(joint_velocities[i], dof_type),
                effort=float(joint_efforts[i]),
                forces=joint_forces[link_idx].tolist(),
                torques=joint_torques[link_idx].tolist(),
            )

        # Fixed joints have no DOF; report their per-link reactions.
        for (_, link_idx), field in zip(self._fixed_joint_links, self._fixed_fields):
            fields[field] = FixedJointSensorModel(
                forces=joint_forces[link_idx].tolist(),
                torques=joint_torques[link_idx].tolist(),
            )

        return self.actual_model_type(**fields)

    def actuator_write(self, value: BaseModel | Mapping[str, Any]) -> None:
        model = self.command_model_type
        if isinstance(value, Mapping):
            value = model.model_validate(value)
        elif not isinstance(value, model):
            raise TypeError(f"value must be a {model.__name__} (or an equivalent dict), not a {type(value).__name__}")

        positions: Dict[int, float] = {}
        velocities: Dict[int, float] = {}
        for field in value.model_fields_set:
            command = getattr(value, field)
            if command is None:
                continue
            i = self._dof_index_by_field[field]
            if self._joint_drive_modes[i] == "position":
                positions[i] = command.position
            else:
                velocities[i] = command.velocity
        self._set_targets(positions, velocities)

    def actuator_read(self) -> BaseModel:
        position_targets = self._articulation.get_dof_position_targets()
        velocity_targets = self._articulation.get_dof_velocity_targets()
        model = self.command_model_type
        fields: Dict[str, BaseModel] = {}
        for i, field in self._actuated_dofs():
            dof_type = self._joint_dof_types[i]
            field_model = typing.get_args(model.model_fields[field].annotation)[0]
            # model_construct: report the targets as they are, even outside the joint limits.
            if self._joint_drive_modes[i] == "position":
                fields[field] = field_model.model_construct(position=self._native_to_model(position_targets[i], dof_type))
            else:
                fields[field] = field_model.model_construct(velocity=self._native_to_model(velocity_targets[i], dof_type))
        return model.model_construct(**fields)

    # ---- joint targets by DOF name (for internal callers, e.g. robot actuators) ----------------

    def write_joint_positions(self, positions: Mapping[str, float]) -> None:
        """Set position targets by DOF name, in model units (degrees / meters). Spherical DOFs are
        ignored (they can't be driven)."""
        targets: Dict[int, float] = {}
        for joint_name, position in positions.items():
            i = self._dof_index_by_name.get(joint_name)
            if i is None:
                raise ValueError(f"'{joint_name}' is not a joint of articulation '{self.prim.GetPath()}' "
                                 f"(joints: {self._joint_names})")
            if not self._dof_is_spherical[i]:
                targets[i] = float(position)
        self._set_targets(targets, {})

    def read_joint_position_targets(self) -> Dict[str, Optional[float]]:
        """Position targets by DOF name, in model units (degrees / meters); None for DOFs that
        aren't position-driven. Spherical DOFs are omitted."""
        position_targets = self._articulation.get_dof_position_targets()
        result: Dict[str, Optional[float]] = {}
        for i, joint_name in enumerate(self._joint_names):
            if self._dof_is_spherical[i]:
                continue
            if self._joint_drive_modes[i] == "position":
                result[joint_name] = self._native_to_model(position_targets[i], self._joint_dof_types[i])
            else:
                result[joint_name] = None
        return result

    def _set_targets(self, positions: Mapping[int, float], velocities: Mapping[int, float]) -> None:
        position_values: List[float] = []
        position_idxs: List[int] = []
        for i, position in positions.items():
            dof_type = self._joint_dof_types[i]
            if dof_type == "angular" and abs(position) > self._ARTICULATION_REVOLUTE_LIMIT_DEG:
                raise ValueError(
                    f"joint '{self._joint_names[i]}' position {position} deg is outside "
                    f"the articulation revolute range [-360, 360] deg (PhysX confines "
                    f"articulation revolute DOFs to [-2pi, 2pi])"
                )
            position_values.append(self._model_to_native(position, dof_type))
            position_idxs.append(i)
        velocity_values = [self._model_to_native(v, self._joint_dof_types[i]) for i, v in velocities.items()]
        velocity_idxs = list(velocities.keys())
        if position_values:
            self._articulation.set_dof_position_targets(position_values, position_idxs)
        if velocity_values:
            self._articulation.set_dof_velocity_targets(velocity_values, velocity_idxs)

    def reorder_positions(self, disordered_joint_names: List[str], positions: List[List[float | int]]) -> List[List[float]]:
        """
        Reorder positions so that they match the articulation joint name order.
        """
        if not isinstance(disordered_joint_names, list) or not all(isinstance(name, str) for name in disordered_joint_names):
            raise TypeError("disordered_joint_names must be a list of strings")
        if not all(jn in self.joint_names for jn in disordered_joint_names):
            raise ValueError("disordered_joint_names must be in articulation.joint_names")
        
        new_positions: List[List[float]] = []
        if not isinstance(positions, list):
            raise TypeError("positions must be a List[List[float | int]]")
        for p_list in positions:
            if not isinstance(p_list, list) or not (all(isinstance(p, (float, int)) for p in p_list)):
                raise TypeError("positions must be a List[List[float | int]]")
            if len(disordered_joint_names) != len(p_list):
                raise ValueError("all positions lists must have the same length as joint_names")
            p_dict = {}
            for i in range(len(disordered_joint_names)):
                p_dict[disordered_joint_names[i]] = p_list[i]
            
            # Reorder by articulation joint order
            np_list: List[float] = []
            for jn in self.joint_names:
                np_list.append(p_dict[jn])
            new_positions.append(np_list)

        return new_positions


    def _get_drive_modes_raw(self) -> List[DriveMode]:
        """Return the active drive mode of each joint, ordered to match :attr:`joint_names`.

        Each entry is ``"position"`` (drive stiffness > 0) or ``"velocity"`` (stiffness == 0
        with damping > 0). A DOF with both stiffness and damping at zero reports ``"none"``.

        PhysX joint drives are gain-based, not mode-flagged: a position drive uses a high
        stiffness (and some damping for stability) so it tracks ``get_positions_raw()``; a
        pure velocity drive sets stiffness to 0 and relies on damping to track
        ``get_velocities_raw()``. So the only reliable way to tell which setpoint is in
        effect is to inspect the gains.
        """
        stiffnesses, dampings = self._articulation.get_dof_gains()
        modes: List[str] = []
        for stiffness, damping in zip(stiffnesses, dampings):
            if stiffness > 1e-6: # Damping can be added for stability, but stiffness -> position control
                modes.append("position")
            elif damping > 1e-6: # stiffness == 0 but damping > 0 -> velocity control
                modes.append("velocity")
            else: # free-floating
                modes.append("none")
        return modes
    
    def _compute_dof_types(self) -> List[Literal["linear", "angular"]]:
        """Classify each DOF as 'angular' (rotation) or 'linear' (translation), ordered to
        match :attr:`joint_names`.

        Uses the tensor view's per-DOF type, which expands multi-DOF joints correctly
        (e.g. a spherical joint -> 3 angular DOFs) — unlike matching DOF names to joint
        prim names, which only works for single-DOF joints.
        """
        dof_types = list(self._articulation.dof_types)
        if len(dof_types) != len(self._joint_names):
            raise ValueError(
                f"dof_types length ({len(dof_types)}) does not match dof count "
                f"({len(self._joint_names)}) for '{self.prim.GetPath()}'"
            )
        return dof_types

    def _resolve_dof_joint_prims(self) -> List[pxr.Usd.Prim]:
        """Resolve each DOF's owning joint prim (in DOF order) from ``dof_paths``.

        A multi-DOF joint (e.g. spherical) repeats its joint path once per DOF, so the same
        prim appears multiple times. Used to classify DOFs by joint type and to map them to
        their joint's child link.
        """
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        return [stage.GetPrimAtPath(joint_path) for joint_path in self._articulation.dof_paths]

    def _compute_dof_link_indices(self) -> List[int]:
        """For each DOF (in DOF order), the row index in the per-link force/torque arrays
        that holds that DOF's joint reaction — i.e. the index of the joint's child link.

        ``get_measured_joint_forces_and_torques`` is per-LINK (one 6-D reaction per link's
        incoming joint), while position/velocity/effort are per-DOF. A multi-DOF joint
        (e.g. spherical) has several DOFs but a single child link, so its DOFs all map to
        the same force row. The child link is the joint's ``body1`` (USD articulation
        convention: body0 = parent, body1 = child).
        """
        link_row_by_path = {path: i for i, path in enumerate(self._articulation.link_paths)}

        indices: List[int] = []
        for joint_prim, joint_path in zip(self._dof_joint_prims, self._articulation.dof_paths):
            body1 = pxr.UsdPhysics.Joint(joint_prim).GetBody1Rel().GetTargets() if joint_prim.IsValid() else []
            child_path = str(body1[0]) if body1 else None
            link_idx = link_row_by_path.get(child_path)
            if link_idx is None:
                raise ValueError(
                    f"Could not map DOF joint '{joint_path}' to a link force row "
                    f"(child body '{child_path}' not found in the articulation's links) "
                    f"under '{self.prim.GetPath()}'"
                )
            indices.append(link_idx)
        return indices

    def _compute_dof_is_spherical(self) -> List[bool]:
        """Per-DOF flag: whether the DOF belongs to a SphericalJoint (ignored downstream)."""
        return [
            joint_prim.IsValid() and joint_prim.IsA(pxr.UsdPhysics.SphericalJoint)
            for joint_prim in self._dof_joint_prims
        ]

    def _compute_fixed_joint_links(self) -> List[Tuple[str, int]]:
        """``(joint name, child-link force-row index)`` for each fixed tree joint.

        Fixed joints contribute no DOFs (so they never appear in ``dof_paths``/``sensor_read()``'s
        per-DOF list), but their child link's incoming-joint force is measurable. We
        enumerate them by walking the USD tree and map each to its ``body1`` child link's
        row in the per-link force/torque arrays. Joints flagged ``excludeFromArticulation``
        (loop constraints) and any whose child link isn't an articulation link are skipped.
        """
        link_row_by_path = {path: i for i, path in enumerate(self._articulation.link_paths)}

        result: List[Tuple[str, int]] = []
        for descendant in pxr.Usd.PrimRange(self.prim):
            if not descendant.IsA(pxr.UsdPhysics.FixedJoint):
                continue
            joint = pxr.UsdPhysics.Joint(descendant)
            if joint.GetExcludeFromArticulationAttr().Get():
                continue  # loop constraint, not a tree joint
            body1 = joint.GetBody1Rel().GetTargets()
            child_path = str(body1[0]) if body1 else None
            link_idx = link_row_by_path.get(child_path)
            if link_idx is None:
                continue  # child link isn't an articulation link -> no reaction row
            result.append((descendant.GetName(), link_idx))
        return result


class ArticulationAnimationFrame:

    def __init__(self, articulation: ArticulationTransducer, model: BaseModel | Mapping[str, Any]):
        if not isinstance(articulation, ArticulationTransducer):
            raise TypeError("articulation must be an ArticulationTransducer")
        self._articulation = articulation

        model_type = articulation.command_model_type
        if isinstance(model, Mapping):
            model = model_type.model_validate(model)
        if not isinstance(model, model_type):
            raise TypeError(f"model must be a {model_type.__name__} (the articulation's command model)")
        self._model = model

    @property
    def articulation(self) -> ArticulationTransducer:
        return self._articulation

    @property
    def model(self) -> BaseModel:
        return self._model

    def set(self) -> None:
        self._articulation.actuator_write(self.model)


class ArticulationLinearPositionAnimationBuilder:
    """Builds a linear position animation of an articulation's position-driven joints.

    Joint positions are given as an articulation command model (only the joints it sets are
    animated) or as a ``{joint field name: position}`` mapping, in model units (degrees / meters).
    """

    def __init__(
        self,
        initial_model: BaseModel | Mapping[str, float],
        time_step_size: Optional[float] = None,
    ):
        initial = self._to_positions(initial_model)
        # Canonical joint order; every model added later must carry exactly these joints.
        self._joint_names: List[str] = list(initial.keys())
        # Interpolated frames are stored as ordered position arrays (one entry per
        # joint_name); models are converted in/out at the boundaries.
        self._positions: List[np.ndarray] = [self._model_to_positions(initial_model)]

        if time_step_size is None:
            time_step_size  = 1.0 / omni.timeline.get_timeline_interface().get_time_codes_per_seconds()
        if not isinstance(time_step_size, float):
            raise TypeError("time_step_size must be a float")
        self._time_step_size = time_step_size

        self._time = 0

    @staticmethod
    def _to_positions(model: BaseModel | Mapping[str, float]) -> Dict[str, float]:
        if isinstance(model, Mapping):
            return {str(k): float(v) for k, v in model.items()}
        if not isinstance(model, BaseModel):
            raise TypeError("model must be an articulation command model or a Mapping[str, float]")
        positions: Dict[str, float] = {}
        for field in model.model_fields_set:
            command = getattr(model, field)
            if command is None:
                continue
            position = getattr(command, "position", None)
            if position is None:
                raise ValueError(f"joint '{field}' has no position (a position animation needs position-driven joints)")
            positions[field] = float(position)
        return positions

    def _model_to_positions(self, model: BaseModel | Mapping[str, float]) -> np.ndarray:
        """Extract an ordered position array from a model, aligned to the builder's joint
        order. Raises if the joint set differs."""
        by_name = self._to_positions(model)
        if set(by_name.keys()) != set(self._joint_names):
            raise ValueError("model joints must match the builder's joint names")
        return np.array([by_name[name] for name in self._joint_names], dtype=np.float32)

    def _positions_to_model(self, articulation: "ArticulationTransducer", positions: np.ndarray) -> BaseModel:
        """Build a position-only command model from an ordered position array."""
        return articulation.command_model_type.model_validate({
            name: {"position": float(position)} for name, position in zip(self._joint_names, positions)
        })

    @property
    def positions(self) -> List[np.ndarray]:
        return self._positions

    @property
    def time_step_size(self) -> float:
        return self._time_step_size

    def build(self, articulation: ArticulationTransducer) -> List[ArticulationAnimationFrame]:
        if not isinstance(articulation, ArticulationTransducer):
            raise TypeError("articulation must be an ArticulationTransducer")

        frames: List[ArticulationAnimationFrame] = []
        for positions in self._positions:
            frames.append(ArticulationAnimationFrame(
                articulation=articulation,
                model=self._positions_to_model(articulation, positions),
            ))

        return frames

    def _interpolate_to(self, positions: np.ndarray, duration: float) -> None:
        """Append frames linearly interpolating each joint position from the last frame to
        ``positions`` over ``duration``."""
        last_positions = self._positions[-1]

        n_steps = int(math.floor(duration / self._time_step_size))
        mod_steps = duration % self._time_step_size
        for i in range(n_steps):
            t = float(i) / n_steps

            # Linearly interpolate between last position and target position.
            delta_position = positions - last_positions
            i_positions = last_positions + delta_position * t
            self._positions.append(i_positions)

        # Check whether the last step was a partial step
        if mod_steps > 0.000001:
            # It was a partial step, so it didn't finish at the target position.
            # Add a last step so it finishes exactly at the target position.
            self._positions.append(positions)

    def add_by_duration(self, model: BaseModel | Mapping[str, float], duration: float) -> "ArticulationLinearPositionAnimationBuilder":
        positions = self._model_to_positions(model)

        if not isinstance(duration, float):
            raise TypeError("duration must be a float")

        self._interpolate_to(positions, duration)

        return self

    def add_by_velocity(self, model: BaseModel | Mapping[str, float], velocity: float) -> "ArticulationLinearPositionAnimationBuilder":
        positions = self._model_to_positions(model)

        if not isinstance(velocity, float):
            raise TypeError("velocity must be a float")

        last_positions = self._positions[-1]
        distance = float(np.linalg.norm(positions - last_positions))
        duration = distance / velocity
        self._interpolate_to(positions, duration)

        return self

    def add_multiple_by_duration(
        self,
        models: List[BaseModel | Mapping[str, float]],
        duration: float,
    ) -> "ArticulationLinearPositionAnimationBuilder":
        if not isinstance(models, list):
            raise TypeError("models must be a list")
        for model in models:
            self.add_by_duration(model, duration)

        return self

    def add_multiple_by_velocity(
        self,
        models: List[BaseModel | Mapping[str, float]],
        velocity: float,
    ) -> "ArticulationLinearPositionAnimationBuilder":
        if not isinstance(models, list):
            raise TypeError("models must be a list")
        for model in models:
            self.add_by_velocity(model, velocity)

        return self


class ArticulationAnimator:

    def __init__(
        self,
        frames: List[ArticulationAnimationFrame],
    ):
        if not isinstance(frames, list) or not all(isinstance(f, ArticulationAnimationFrame) for f in frames):
            raise TypeError("frames must be a List[ArticulationAnimationFrame]")
        if len(frames) == 0:
            raise ValueError("frames must not be empty")
        self._frames = frames

        self.reset()

    @property
    def frames(self) -> List[ArticulationAnimationFrame]:
        return self._frames

    @property
    def is_finished(self) -> bool:
        return self._i >= len(self._frames) - 1

    def reset(self) -> None:
        self._i = -1

    def step(self) -> None:
        if self.is_finished:
            raise Exception("animation already finished")

        self._i += 1
        self._frames[self._i].set()

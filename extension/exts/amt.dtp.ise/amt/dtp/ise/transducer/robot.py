from abc import ABC, abstractmethod
import math
from typing import ClassVar, Optional, Any, Tuple, Union, List, Dict, Sequence, Union, Type, TypeVar, Generic, Literal
import numpy as np
import time
import os
import pathlib
import copy
from xml.etree import ElementTree
import yaml
from pydantic import BaseModel, Field, field_validator, SerializeAsAny
from pydantic.json_schema import models_json_schema

#from isaacsim.util.debug_draw import _debug_draw
import omni
import omni.usd
import omni.physx
import carb
import pxr
import pxr.Gf
import pxr.Sdf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdPhysics
import pxr.UsdShade
#from isaacsim.core.prims import SingleArticulation
#from isaacsim.core.utils.types import ArticulationAction
import isaacsim.core.experimental.materials
import isaacsim.core.experimental.prims
from ..isaac5_compatibility import SingleArticulation, ArticulationAction, SingleRigidPrim, PhysicsViewUnavailable
# RETAINED (deprecated): Isaac Sim 6.0 has no drop-in LulaKinematicsSolver replacement.
# isaacsim.robot_motion.experimental.motion_generation / cumotion use different paradigms.
import isaacsim.robot_motion.motion_generation.lula
# cuMotion: the successor to Lula (used by ManipulatorRobotCuMotionActuator). Importing the
# isaacsim.robot_motion.cumotion extension module first registers the DLL directory that the
# cumotion .pyd needs on Windows.
import isaacsim.robot_motion.cumotion
import cumotion


from ..lib.logging import _logger
from .core import Sensor, Actuator
from ..lib.control.transducer.models import ManipulatorRobotActuatorPose, ToolChangerModel
from ..robot.kinematics import CUMOTION_LOCK, CuMotionKinematicsSolver
from .articulation import ArticulationTransducer


class IKUnreachableError(ValueError):
    """Inverse kinematics found no solution for a robot target. A segment reports it with the error
    code ``IK_UNREACHABLE`` (its ``error_code``)."""
    error_code = "IK_UNREACHABLE"


class ManipulatorRobotLulaActuator(Actuator[ManipulatorRobotActuatorPose]):

    def __init__(
        self, 
        prim: pxr.Usd.Prim | pxr.Sdf.Path | str, 
        yaml_path: str,
        urdf_path: str,
        final_link_offset: Optional[np.ndarray] = None,
        end_effector_offset: Optional[np.ndarray] = None,
        name: Optional[str] = None,
        raise_when_unreachable: bool = False,
    ):
        """:param raise_when_unreachable: Raise from actuator_write() when inverse kinematics finds no
            solution for the pose (instead of keeping the current targets silently), as an
            :class:`IKUnreachableError`. Use it where a write must report failure, e.g. in an
            AutomationComponent served by a segment API (which then halts its queue)."""
        super().__init__(prim, name)
        self._raise_when_unreachable = bool(raise_when_unreachable)

        if not isinstance(yaml_path, str):
            raise TypeError("yaml_path must be a str")
        if not os.path.isfile(yaml_path):
            raise FileExistsError(f"file does not exist at yaml_path '{yaml_path}'")
        self._yaml_path = yaml_path

        if not isinstance(urdf_path, str):
            raise TypeError("urdf_path must be a str")
        if not os.path.isfile(urdf_path):
            raise FileExistsError(f"file does not exist at urdf_path '{urdf_path}'")
        self._urdf_path = urdf_path

        if final_link_offset is not None:
            if not isinstance(final_link_offset, np.ndarray) or final_link_offset.shape != (3,) or final_link_offset.dtype != np.float64:
                raise TypeError("final_link_offset must be an Optional[np.ndarray[(3,),float64]]")
        self._final_link_offset = final_link_offset

        if end_effector_offset is not None:
            if not isinstance(end_effector_offset, np.ndarray) or end_effector_offset.shape != (3,) or end_effector_offset.dtype != np.float64:
                raise TypeError("end_effector_offset must be an Optional[np.ndarray[(3,),float64]]")
        self._end_effector_offset = end_effector_offset
        
        # source_path = pathlib.Path(get_prim_source_path(prim))
        # source_root = os.path.join(str(source_path.parent), source_path.stem)
        # self._yaml_path = f"{source_root}.yaml"
        # self._urdf_path = f"{source_root}.urdf"
        # if not os.path.isfile(self._yaml_path):
        #     raise FileExistsError(f"manipulator robot yaml file '{self._yaml_path}' does not exist")
        # if not os.path.isfile(self._urdf_path):
        #     raise FileExistsError(f"manipulator robot urdf file '{self._urdf_path}' does not exist")

        self._articulation = ArticulationTransducer(prim)

        self._kinematics_solver = isaacsim.robot_motion.motion_generation.lula.LulaKinematicsSolver(
            robot_description_path=self._yaml_path,
            urdf_path=self._urdf_path,
        )
        # Get the last frame/link: the end of the manipulator robot
        self._frame_name = self._kinematics_solver.get_all_frame_names()[-1]

    def articulation(self) -> ArticulationTransducer:
        return self._articulation
    
    @property
    def command_model_type(self) -> Type[BaseModel]:
        return ManipulatorRobotActuatorPose
    
    def actuator_write(self, value: ManipulatorRobotActuatorPose) -> None:
        if not isinstance(value, ManipulatorRobotActuatorPose):
            raise TypeError("value must be a ManipulatorRobotPose")
        position, orientation = value.to_numpy()
        
        # Robot position must continuously set if the robot is moving
        if value.frame_of_reference == "local":
            # Set current robot pose relative to local robot coordinate system
            self._kinematics_solver.set_robot_base_pose(np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0, 0.0]))
        elif value.frame_of_reference == "global":
            # Set current robot pose relative to world coordinate system
            rob_pos, rob_ori = self._articulation._articulation.get_world_pose()
            self._kinematics_solver.set_robot_base_pose(rob_pos, rob_ori)
        else:
            raise ValueError(f"Invalid frame_of_reference: {value.frame_of_reference}")

        # Perform inverse kinematics
        joint_positions = self._convert_cart_to_joint(position, orientation)
        if joint_positions is None:
            # Inverse kinematics found no solution; the current targets are kept.
            if self._raise_when_unreachable:
                raise IKUnreachableError(f"no inverse kinematics solution for position {value.position.to_numpy().round(6).tolist()}, orientation "
                                         f"{value.orientation.to_wxyz().round(6).tolist()} (wxyz) ({value.frame_of_reference}); the robot keeps its targets")
            return
        if len(joint_positions) != len(self._articulation.joint_names):
            raise ValueError("joint positions length and joint names length do not match")
    
        self._articulation.write_joint_positions({
            name: math.degrees(position) for name, position in zip(self._articulation.joint_names, joint_positions)
        })

    def actuator_read(self) -> ManipulatorRobotActuatorPose:
        # Get current joint positions ordered by joint_names. The articulation reports angular
        # positions in degrees; Lula forward kinematics expects radians (this mirrors the
        # radians->degrees conversion done in actuator_write).
        joint_map = self._articulation.read_joint_position_targets()
        current_joint_positions: List[float] = [math.radians(joint_map[name]) for name in self._articulation.joint_names]
        pos, ori = self._convert_joint_to_cart(current_joint_positions)
        return ManipulatorRobotActuatorPose.from_numpy(pos, ori)

    def _teleport(self, position: np.ndarray, orientation: np.ndarray, local: bool = True):
        if not isinstance(position, np.ndarray) or position.shape != (3,) or position.dtype != np.float64:
            raise TypeError("position must be a np.array[(3,),float64]")
        if not isinstance(orientation, np.ndarray) or orientation.shape != (4,) or orientation.dtype != np.float64:
            raise TypeError("orientation must be a np.array[(4,),float64]")
        
        # Robot position must continuously set if the robot is moving
        if local:
            self._kinematics_solver.set_robot_base_pose(np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0, 0.0]))
        else:
            # World
            # Set current robot position for world
            rob_pos, rob_ori = self._articulation._articulation.get_world_pose()
            self._kinematics_solver.set_robot_base_pose(rob_pos, rob_ori)

        joint_positions = self._convert_cart_to_joint(position, orientation)
        if joint_positions is not None:
            self._articulation.set_joint_positions(joint_positions)

    # def write(self, position: np.ndarray, orientation: np.ndarray, local: bool = True):
    #     if not isinstance(position, np.ndarray) or position.shape != (3,) or position.dtype != np.float64:
    #         raise TypeError("position must be a np.array[(3,),float64]")
    #     if not isinstance(orientation, np.ndarray) or orientation.shape != (4,) or orientation.dtype != np.float64:
    #         raise TypeError("orientation must be a np.array[(4,),float64]")
        
    #     # Robot position must continuously set if the robot is moving
    #     if local:
    #         self._kinematics_solver.set_robot_base_pose(np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0, 0.0]))
    #     else:
    #         # World
    #         # Set current robot position for world
    #         rob_pos, rob_ori = self._articulation._articulation.get_world_pose()
    #         self._kinematics_solver.set_robot_base_pose(rob_pos, rob_ori)

    #     joint_positions = self._convert_cart_to_joint(position, orientation)
    #     if len(joint_positions) != len(self._articulation.joint_names):
    #         raise ValueError("joint positions length and joint names length do not match")
        
    #     if joint_positions is not None:
    #         joint_models: List[JointActuatorModel] = []
    #         for name, position in zip(self._articulation.joint_names, joint_positions):
    #             position_degrees = math.degrees(position)
    #             joint_models.append(JointActuatorModel(name=name, position=position_degrees))
    #         self._articulation.actuator_write(ArticulationActuatorModel(joints=joint_models))
    #         #self._articulation.apply_action(ArticulationAction(joint_positions))
    #     pass

    def _offset_cart(
        self,
        position: np.ndarray, # shape (3,) - desired position of the tip/TCP
        orientation: np.ndarray, # shape (4,) - quaternion as [w, x, y, z] (common in Isaac Sim/ROS)
        offset_local: np.ndarray = np.array([0.0, 0.0, 0.1]), # Local offset from EE frame to tip (along +Z by default)
    ) -> np.ndarray:
        """
        Compute the adjusted end-effector target position so that when the EE frame reaches it
        with the given orientation, the actual tip/TCP ends up at the desired_tip_position.
        
        - Positive Z offset means the tip is forward along the EE's +Z axis (common for grippers/tools).
        - Use negative Z if your tip is "behind" the EE frame.
        - Measure your offset by inspecting the robot (e.g., distance from panda_hand to fingertip midpoint).
        
        Returns: adjusted EE target position as np.array shape (3,)
        """
        # Convert to Gf types for reliable quaternion math
        quat = pxr.Gf.Quatd(orientation[0], orientation[1], orientation[2], orientation[3])  # w,x,y,z
        offset_vec = pxr.Gf.Vec3d(offset_local[0], offset_local[1], offset_local[2])
        
        # Rotate the local offset into world space
        rotated_offset = quat.Transform(offset_vec)
        
        # Subtract the rotated offset: EE must be "behind" the tip along the approach direction
        adjusted_position = np.array(position) - np.array([rotated_offset[0], rotated_offset[1], rotated_offset[2]])
        
        return adjusted_position

    def _convert_cart_to_joint(
        self,
        target_position: np.ndarray,
        target_orientation: np.ndarray,
        current_joint_positions: Optional[np.ndarray] = None
    ) -> Optional[np.ndarray]:
        if not isinstance(target_position, np.ndarray) or target_position.shape != (3,) or target_position.dtype != np.float64:
            raise TypeError("target_position must be a np.array[(3,),float64]")
        if not isinstance(target_orientation, np.ndarray) or target_orientation.shape != (4,) or target_orientation.dtype != np.float64:
            raise TypeError("target_orientation must be a np.array[(4,),float64]")
        if current_joint_positions is not None:
            if not isinstance(current_joint_positions, np.ndarray) or target_orientation.dtype != np.float64:
                raise TypeError("current_joint_positions must be a np.array[float64]")

        if self._final_link_offset is not None:
            target_position = self._offset_cart(target_position, target_orientation, self._final_link_offset)
        if self._end_effector_offset is not None:
            target_position = self._offset_cart(target_position, target_orientation, self._end_effector_offset)

        # Clean inputs before they are sent to solver
        if current_joint_positions is None:
            current_joint_positions = self._articulation._articulation.get_dof_position_targets()
        if not isinstance(current_joint_positions, np.ndarray):
            current_joint_positions = None
        if current_joint_positions is not None and np.isnan(current_joint_positions).any():
            current_joint_positions = None

        result_joint_positions, success = self._kinematics_solver.compute_inverse_kinematics(
            frame_name=self._frame_name,
            target_position=target_position,
            target_orientation=target_orientation,
            warm_start=current_joint_positions, # Should be in radians
        )
        if not success:
            return None
        
        if not isinstance(result_joint_positions, np.ndarray):
            raise Exception(f"Invalid result_joint_positions type: {type(result_joint_positions)}")

        if np.isnan(result_joint_positions).any():
            success = False
            raise Exception(f"Invalid result_joint_positions: contains NaN values")

        return result_joint_positions

    def _convert_joint_to_cart(
        self,
        joint_positions: np.ndarray | List[float],
    ) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if isinstance(joint_positions, list) and all(isinstance(j, float) for j in joint_positions):
            joint_positions = np.array(joint_positions, dtype=np.float64)
        if not isinstance(joint_positions, np.ndarray):
            raise TypeError("joint_positions must be a np.ndarray | List[float]")
        if np.isnan(joint_positions).any():
            raise ValueError("joint_positions can not contain NaN")

        # # Robot position must continuously set if the robot is moving
        # if local:
        #     self._kinematics_solver.set_robot_base_pose(np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0, 0.0]))
        # else:
        #     # World
        #     # Set current robot position for world
        #     rob_pos, rob_ori = self._articulation.get_world_pose()
        #     self._kinematics_solver.set_robot_base_pose(rob_pos, rob_ori)

        position, rotation_matrix = self._kinematics_solver.compute_forward_kinematics(
            frame_name=self._frame_name,
            joint_positions=joint_positions,
        )

        if not isinstance(position, np.ndarray) or np.isnan(position).any():
            raise Exception(f"Invalid forward kinematics position: {position}")
        if not isinstance(rotation_matrix, np.ndarray) or np.isnan(rotation_matrix).any():
            raise Exception(f"Invalid forward kinematics rotation: {rotation_matrix}")

        # Convert the 3x3 rotation matrix to a [w, x, y, z] quaternion.
        # Lula returns R in the column-vector convention (p_world = R @ p_local), but pxr.Gf
        # matrices use the row-vector convention (v' = v * M), so a Gf matrix holds the
        # transpose of the same rotation. Transpose R before loading it into Gf.Matrix3d;
        # otherwise ExtractRotation yields the inverse orientation, which both returns the
        # conjugate quaternion and rotates the tip/EE offset reversal the wrong way.
        gf_rotation = pxr.Gf.Matrix3d(*rotation_matrix.T.flatten().tolist()).ExtractRotation()
        gf_quat: pxr.Gf.Quatd = gf_rotation.GetQuat()
        imag = gf_quat.GetImaginary()
        orientation = np.array([gf_quat.GetReal(), imag[0], imag[1], imag[2]], dtype=np.float64)

        position = np.array(position, dtype=np.float64)

        # Reverse the tip/end-effector offsets applied during inverse kinematics so the
        # returned pose describes the tip/TCP rather than the solver's end-effector frame.
        if self._end_effector_offset is not None:
            position = self._offset_cart(position, orientation, -self._end_effector_offset)
        if self._final_link_offset is not None:
            position = self._offset_cart(position, orientation, -self._final_link_offset)

        return position, orientation


class ManipulatorRobotCuMotionActuator(Actuator[ManipulatorRobotActuatorPose]):
    """Cartesian pose actuator for a manipulator robot, solved with cuMotion inverse kinematics.

    Same interface as :class:`ManipulatorRobotLulaActuator`, but uses cuMotion (the successor to
    Lula, ``isaacsim.robot_motion.cumotion``) instead of the deprecated Lula kinematics solver.
    The robot is described by a cuMotion XRDF file instead of a Lula robot description YAML file.

    ``prim`` is the robot's prim: the articulation root, or a prim containing exactly one
    articulation root. Robot assets put the articulation root in different places: on the
    robot's root prim (e.g., the AMT MR-A1), on its root link, or on its fixed root joint (e.g.,
    Isaac Sim's UR10, ``ur10/root_joint``). Like Isaac Sim's ``Articulation``, the actuator
    searches ``prim`` and its descendants for it, and the robot's links under ``prim``.

    Frames:
        - Base frame: the URDF root link (``Kinematics.base_frame_name()``). cuMotion solves in
          this frame. It is assumed to be the robot asset's root frame: the articulation root prim,
          or its nearest Xformable ancestor if it is a joint (e.g., ``ur10`` for ``ur10/root_joint``).
          Link frames are not used, because a URDF link frame can differ from its USD prim: e.g.,
          the AMT MR-A1's ``BaseLink`` prim is rotated 180 degrees about Z from its URDF frame, and
          Isaac Sim's UR10 ``wrist_3_link`` is rotated and offset. While simulating, the base pose
          follows the root link (the body fixed to the world by the root joint), offset by the
          root link to base frame transform authored in USD.
        - Flange link: the robot link (rigid body prim) that the flange tool frame is fixed to
          (e.g., ``Link6``): where end effectors are attached.
        - Flange tool frame: an XRDF ``tool_frames`` entry (``tool_frame``; defaults to the first
          entry). Required: cuMotion's planners, trajectory optimizer, and RMPflow all need a tool
          frame, so requiring it here keeps those capabilities available. By convention this is
          ``tool0``, the flange face: the tool frame with no end effector attached. It can be
          overridden at runtime with :meth:`set_flange_frame` (a ``flange_override`` frame fixed
          to the XRDF flange tool frame, e.g., at the face of a tool changer mounted on the
          flange) and reset with :meth:`clear_flange_frame`. :attr:`flange_frame` is the
          override while it is set.
        - TCP: the tool center point of the attached end effector, set at runtime with
          :meth:`set_tcp` (a ``tcp`` frame fixed to the flange tool frame, including its
          override) and removed with :meth:`clear_tcp`. Like Universal Robots' ``set_tcp()``,
          there is one active TCP. It stays fixed relative to the flange tool frame, so it moves
          when the flange tool frame is overridden or reset.
        - Active tool frame (:attr:`tool_frame`): ``tcp`` when a TCP is set, otherwise the flange
          tool frame. Poses written and read are poses of the active tool frame. Tool frames use
          +Z as the approach direction.

    Units: positions are in stage units. cuMotion only works in meters, so values are scaled
    by the stage ``metersPerUnit``. Orientations are ``[w, x, y, z]`` quaternions.
    """

    # Name of the frame added by set_tcp()
    TCP_FRAME: ClassVar[str] = "tcp"
    # Name of the frame added by set_flange_frame()
    FLANGE_OVERRIDE_FRAME: ClassVar[str] = "flange_override"

    def __init__(
        self,
        prim: pxr.Usd.Prim | pxr.Sdf.Path | str,
        xrdf_path: str,
        urdf_path: str,
        tool_frame: Optional[str] = None,
        name: Optional[str] = None,
        raise_when_unreachable: bool = False,
    ):
        """:param raise_when_unreachable: Raise from actuator_write() when inverse kinematics finds no
            solution within tolerances (instead of keeping the current targets silently; see
            last_ik_results), as an :class:`IKUnreachableError`. Use it where a write must report failure,
            e.g. in an AutomationComponent served by a segment API (which then halts its queue)."""
        super().__init__(prim, name)
        self._raise_when_unreachable = bool(raise_when_unreachable)

        if not isinstance(xrdf_path, str):
            raise TypeError("xrdf_path must be a str")
        if not os.path.isfile(xrdf_path):
            raise FileExistsError(f"file does not exist at xrdf_path '{xrdf_path}'")
        self._xrdf_path = xrdf_path

        if not isinstance(urdf_path, str):
            raise TypeError("urdf_path must be a str")
        if not os.path.isfile(urdf_path):
            raise FileExistsError(f"file does not exist at urdf_path '{urdf_path}'")
        self._urdf_path = urdf_path

        if tool_frame is not None and not isinstance(tool_frame, str):
            raise TypeError("tool_frame must be an Optional[str]")

        self._articulation = ArticulationTransducer(self._find_articulation_root())

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        self._meters_per_unit: float = pxr.UsdGeom.GetStageMetersPerUnit(stage)

        # FK/IK and frames come from the KinematicsSolver: the robot description is loaded
        # from memory so the TCP and the flange override can be set and cleared at runtime (each change
        # adds an XRDF add_frame modifier and reloads it, ~0.2 ms for a 6-DOF robot). It raises if the
        # XRDF has no tool_frames, or tool_frame is not one of them.
        self._solver = CuMotionKinematicsSolver(urdf_path=self._urdf_path, xrdf_path=self._xrdf_path, tool_frame=tool_frame)
        self._xrdf: Dict[str, Any] = self._solver.xrdf
        self._urdf_text: str = self._solver.urdf_text
        # Flange tool frame override set at runtime: (position (stage units), orientation wxyz) in
        # the XRDF flange tool frame
        self._flange_override: Optional[Tuple[np.ndarray, np.ndarray]] = None
        # TCP set at runtime: (position (stage units), orientation wxyz) in the flange tool frame
        self._tcp: Optional[Tuple[np.ndarray, np.ndarray]] = None

        if self.TCP_FRAME in self._solver.frame_names:
            raise ValueError(f"the robot already has a '{self.TCP_FRAME}' frame; it is reserved for set_tcp()")
        if self.FLANGE_OVERRIDE_FRAME in self._solver.frame_names:
            raise ValueError(f"the robot already has a '{self.FLANGE_OVERRIDE_FRAME}' frame; it is reserved for set_flange_frame()")
        # The XRDF flange tool frame (flange_frame is the override while it is set)
        self._flange_frame = self._solver.default_tool_frame

        # cuMotion c-space joints (XRDF cspace.joint_names order). Joints are matched to the
        # articulation by name, so the articulation's DOF order does not need to match.
        self._cspace_joint_names: List[str] = list(self._solver.joint_names)
        missing = [name for name in self._cspace_joint_names if name not in self._articulation.joint_names]
        if missing:
            raise ValueError(
                f"XRDF c-space joints {missing} are not joints of the articulation at "
                f"'{self._articulation.prim.GetPath()}' (articulation joints: {self._articulation.joint_names})"
            )
        if len(self._articulation.joint_names) != len(self._cspace_joint_names):
            # Extra articulation DOFs are left untouched. A common cause is a gripper attached with
            # a joint that is not excluded from the articulation, which merges it into the robot.
            _logger.warning(
                f"articulation '{self._articulation.prim.GetPath()}' has joints {self._articulation.joint_names}, "
                f"but the XRDF c-space only has {self._cspace_joint_names}; only c-space joints are driven"
            )
        dof_types = dict(zip(self._articulation.joint_names, self._articulation._joint_dof_types))
        self._cspace_dof_types: List[Literal["linear", "angular"]] = [dof_types[name] for name in self._cspace_joint_names]

        # Flange link: where end effectors are attached (see ToolChanger)
        self._flange_link: str = self._find_flange_link()

        # Base frame (see the class docstring): the root link's pose, times the base frame's pose in
        # the root link frame (authored in USD; e.g., 180 degrees about Z for the AMT MR-A1's BaseLink)
        self._root_link: str = self._find_root_link()
        self._root_link_to_base: cumotion.Pose3 = self._authored_pose_in(self._root_link, self._find_base_prim())

    def articulation(self) -> ArticulationTransducer:
        return self._articulation

    @property
    def command_model_type(self) -> Type[BaseModel]:
        return ManipulatorRobotActuatorPose

    @property
    def solver(self) -> CuMotionKinematicsSolver:
        """The robot's KinematicsSolver (FK/IK in the robot base frame, meters and radians). Its frames
        include the TCP and the flange override while they are set; share it with a RobotApi
        (``RobotBinding(actuator.solver, ...)``) so both see the same TCP definitions."""
        return self._solver

    @property
    def _kinematics(self) -> cumotion.Kinematics:
        return self._solver.kinematics

    @property
    def cumotion_robot(self) -> "isaacsim.robot_motion.cumotion.CumotionRobot":
        """The current cuMotion robot (description and kinematics), including the TCP frame.

        Replaced whenever the TCP is set or cleared, so anything built from it (planners,
        RMPflow, trajectory generators) must be rebuilt after such a change.
        """
        return self._solver.cumotion_robot

    @property
    def flange_frame(self) -> str:
        """The flange tool frame: the tool frame with no end effector attached, and the frame the TCP is relative to.

        The XRDF flange tool frame (e.g., ``tool0``), or :attr:`FLANGE_OVERRIDE_FRAME` while it is
        overridden (see :meth:`set_flange_frame`).
        """
        return self.FLANGE_OVERRIDE_FRAME if self._flange_override is not None else self._flange_frame

    @property
    def flange_link(self) -> str:
        """Prim path of the robot link (rigid body) the flange tool frame is fixed to, e.g., ``.../Link6``."""
        return self._flange_link

    def flange_frame_world_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Current world pose of the flange tool frame (e.g., tool0, or its override): ``(position (3,) stage units, orientation (4,) wxyz)``.

        From the base pose and forward kinematics of the measured joint positions.
        """
        measured = self._measured_cspace_positions()
        with CUMOTION_LOCK:
            base_to_flange = self._kinematics.pose(measured, self.flange_frame)
        return self._from_pose3(self._world_to_base() * base_to_flange)

    @property
    def flange_frame_override(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """The flange tool frame override in the XRDF flange tool frame, ``(position (3,) stage units, orientation (4,) wxyz)``, or None if not set."""
        if self._flange_override is None:
            return None
        return self._flange_override[0].copy(), self._flange_override[1].copy()

    def set_flange_frame(self, position: np.ndarray, orientation: Optional[np.ndarray] = None) -> None:
        """Override the flange tool frame, relative to the XRDF flange tool frame (e.g., tool0).

        For example, at the face of a tool changer mounted on the flange: end effectors are then
        attached there (see :class:`ToolChanger`), and the TCP is relative to it. Adds (or moves)
        the ``flange_override`` frame. Rebuilds the cuMotion robot (see :attr:`cumotion_robot`).
        A TCP that is set keeps its pose relative to the flange tool frame, so it moves with it.

        Args:
            position: Position of the override in the XRDF flange tool frame, ``(3,)`` float64, stage units.
            orientation: Orientation of the override in the XRDF flange tool frame, ``(4,)`` float64
                ``[w, x, y, z]`` quaternion. Defaults to the identity (axes aligned with the XRDF flange tool frame).
        """
        override = self._validate_frame_pose(position, orientation)
        previous = self._flange_override
        self._flange_override = override
        try:
            self._apply_frames()
        except Exception:
            self._flange_override = previous
            raise

    def clear_flange_frame(self) -> None:
        """Reset the flange tool frame to the XRDF flange tool frame (e.g., tool0). Rebuilds the cuMotion robot."""
        if self._flange_override is None:
            return
        self._flange_override = None
        self._apply_frames()

    @property
    def tool_frame(self) -> str:
        """The active tool frame: ``tcp`` when a TCP is set, otherwise :attr:`flange_frame`."""
        return self.TCP_FRAME if self._tcp is not None else self.flange_frame

    @property
    def tcp(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """The TCP pose in the flange tool frame, ``(position (3,) stage units, orientation (4,) wxyz)``, or None if not set."""
        if self._tcp is None:
            return None
        return self._tcp[0].copy(), self._tcp[1].copy()

    def set_tcp(self, position: np.ndarray, orientation: Optional[np.ndarray] = None) -> None:
        """Set the TCP of the attached end effector, relative to the flange tool frame, and make it the active tool frame.

        Adds (or moves) the ``tcp`` frame: equivalent to an XRDF ``add_frame`` modifier plus a
        ``tool_frames`` entry. Rebuilds the cuMotion robot (see :attr:`cumotion_robot`).

        Args:
            position: Position of the TCP in the flange tool frame, ``(3,)`` float64, stage units.
            orientation: Orientation of the TCP in the flange tool frame, ``(4,)`` float64 ``[w, x, y, z]``
                quaternion. Defaults to the identity (TCP axes aligned with the flange tool frame).
        """
        tcp = self._validate_frame_pose(position, orientation)
        previous = self._tcp
        self._tcp = tcp
        try:
            self._apply_frames()
        except Exception:
            self._tcp = previous
            raise

    def clear_tcp(self) -> None:
        """Remove the TCP and make the flange tool frame the active tool frame. Rebuilds the cuMotion robot."""
        if self._tcp is None:
            return
        self._tcp = None
        self._apply_frames()

    @staticmethod
    def _validate_frame_pose(position: np.ndarray, orientation: Optional[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
        """Check a frame pose given to set_tcp() or set_flange_frame(); returns a copy with a unit quaternion."""
        if not isinstance(position, np.ndarray) or position.shape != (3,) or position.dtype != np.float64:
            raise TypeError("position must be a np.ndarray[(3,),float64]")
        if orientation is None:
            orientation = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        if not isinstance(orientation, np.ndarray) or orientation.shape != (4,) or orientation.dtype != np.float64:
            raise TypeError("orientation must be an Optional[np.ndarray[(4,),float64]]")
        norm = np.linalg.norm(orientation)
        if norm < 1e-9:
            raise ValueError("orientation must be a non-zero quaternion")
        return position.copy(), orientation / norm

    def _apply_frames(self) -> None:
        """Set the solver's runtime frames to the flange tool frame override and the TCP frame (if set).
        The TCP is fixed to the flange tool frame, which is the override while it is set. Positions are
        converted from stage units to meters. The solver keeps its previous frames if this fails."""
        def frame(parent: str, pose: Optional[Tuple[np.ndarray, np.ndarray]]):
            return None if pose is None else (parent, pose[0] * self._meters_per_unit, pose[1])
        self._solver.set_frames({
            self.FLANGE_OVERRIDE_FRAME: frame(self._flange_frame, self._flange_override),
            self.TCP_FRAME: frame(self.flange_frame, self._tcp),
        })

    @property
    def ik_config(self) -> cumotion.IkConfig:
        """cuMotion IK settings (tolerances, iteration counts, ...). ``cspace_seeds`` is overwritten on every solve."""
        return self._solver.ik_config

    @property
    def last_ik_results(self) -> Optional[cumotion.IkResults]:
        """Results of the most recent IK solve (``success``, ``position_error``, ...), for diagnostics."""
        return self._solver.last_ik_results

    def actuator_write(self, value: ManipulatorRobotActuatorPose) -> None:
        if not isinstance(value, ManipulatorRobotActuatorPose):
            raise TypeError("value must be a ManipulatorRobotActuatorPose")
        position, orientation = value.to_numpy()

        target_tool = self._to_pose3(position, orientation)  # in stage frame or base frame, meters
        if value.frame_of_reference == "local":
            base_to_tool = target_tool
        elif value.frame_of_reference == "global":
            # Read the base pose every write since the robot may be moving
            base_to_tool = self._world_to_base().inverse() * target_tool
        else:
            raise ValueError(f"Invalid frame_of_reference: {value.frame_of_reference}")

        joint_positions = self._solve_ik(base_to_tool)
        if joint_positions is None:
            # No IK solution within tolerances; the current targets are kept.
            if self._raise_when_unreachable:
                results = self.last_ik_results
                error = "" if results is None else f" (position error {getattr(results, 'position_error', '?')})"
                raise IKUnreachableError(f"no inverse kinematics solution within tolerances for position {value.position.to_numpy().round(6).tolist()}, "
                                         f"orientation {value.orientation.to_wxyz().round(6).tolist()} (wxyz) ({value.frame_of_reference}){error}; "
                                         f"the robot keeps its targets")
            return

        self._articulation.write_joint_positions({
            name: self._native_to_model(position, dof_type)
            for name, dof_type, position in zip(self._cspace_joint_names, self._cspace_dof_types, joint_positions)
        })

    def actuator_read(self) -> ManipulatorRobotActuatorPose:
        """Active tool frame pose for the current joint position setpoints, in the base frame (``frame_of_reference="local"``)."""
        joint_positions = self._read_cspace_setpoints()
        if joint_positions is None:
            raise ValueError("joint position setpoints are not available (joints must be position driven)")
        with CUMOTION_LOCK:
            base_to_tool = self._kinematics.pose(joint_positions, self.tool_frame)
        position, orientation = self._from_pose3(base_to_tool)
        return ManipulatorRobotActuatorPose.from_numpy(position, orientation)

    def _solve_ik(self, base_to_tool: cumotion.Pose3) -> Optional[np.ndarray]:
        """Solve for c-space joint positions (radians / meters, c-space order) that put the tool frame at ``base_to_tool``."""
        # Warm start from the current setpoints so consecutive solves stay on the same IK branch
        warm_start = self._read_cspace_setpoints()
        results = self._solver.solve_ik_native(base_to_tool, self.tool_frame, warm_start)
        if not results.success:
            return None

        joint_positions = np.asarray(results.cspace_position, dtype=np.float64).flatten()
        if np.isnan(joint_positions).any():
            raise Exception("Invalid IK joint positions: contains NaN values")
        return joint_positions

    def _read_cspace_setpoints(self) -> Optional[np.ndarray]:
        """Current joint position setpoints in c-space order and cuMotion units, or None if unavailable."""
        setpoints = self._articulation.read_joint_position_targets()
        positions: List[float] = []
        for name, dof_type in zip(self._cspace_joint_names, self._cspace_dof_types):
            position = setpoints.get(name)
            if position is None or math.isnan(position):
                return None
            positions.append(ArticulationTransducer._model_to_native(position, dof_type))
        return np.array(positions, dtype=np.float64)

    @staticmethod
    def _native_to_model(value: float, dof_type: Literal["linear", "angular"]) -> float:
        """cuMotion units (radians / meters) to articulation model units (degrees / meters)."""
        return ArticulationTransducer._native_to_model(value, dof_type)

    def _world_to_base(self) -> cumotion.Pose3:
        """Pose of the robot base (URDF root link) in the world frame (meters).

        The root link's world pose from the physics simulation, times the base frame's pose in the
        root link frame (see the class docstring).
        """
        return self._to_pose3(*rigid_body_world_pose(self._root_link)) * self._root_link_to_base

    def _measured_cspace_positions(self) -> np.ndarray:
        """Measured joint positions in c-space order and cuMotion units (radians / meters)."""
        measured = dict(zip(self._articulation.joint_names, np.asarray(self._articulation._articulation.get_joint_positions(), dtype=np.float64).flatten()))
        return np.array([measured[name] for name in self._cspace_joint_names], dtype=np.float64)

    def _find_root_link(self) -> str:
        """Prim path of the robot's root link: the body a joint fixes to the world (the joint's other body is empty;
        e.g., body1 for the UR10's root_joint, body0 for the Franka's rootJoint), else the articulation root if it is a rigid body."""
        for prim in pxr.Usd.PrimRange(self._prim, pxr.Usd.TraverseInstanceProxies()):
            if prim.IsA(pxr.UsdPhysics.Joint):
                joint = pxr.UsdPhysics.Joint(prim)
                body0, body1 = joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()
                if bool(body0) != bool(body1):
                    return str((body0 or body1)[0])
        root = self._articulation.prim
        if root.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
            return str(root.GetPath())
        raise ValueError(f"cannot find the root link of the robot at '{self._prim.GetPath()}' (no joint fixes a link to the world)")

    def _find_base_prim(self) -> pxr.Usd.Prim:
        """The robot asset's root frame prim: the articulation root, or its nearest Xformable ancestor if it is not Xformable (e.g., a root joint)."""
        prim = self._articulation.prim
        while prim.IsValid() and not prim.IsA(pxr.UsdGeom.Xformable):
            prim = prim.GetParent()
        return prim

    def _authored_pose_in(self, frame_path: str, prim: pxr.Usd.Prim) -> cumotion.Pose3:
        """Pose (meters) of ``prim`` in the frame of the prim at ``frame_path``, from their USD transforms."""
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        cache = pxr.UsdGeom.XformCache()
        frame_to_world = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(frame_path))
        # Row-vector convention: prim local -> world -> frame local
        prim_in_frame = cache.GetLocalToWorldTransform(prim) * frame_to_world.GetInverse()
        translation = prim_in_frame.ExtractTranslation()
        rotation = prim_in_frame.ExtractRotationQuat()
        imaginary = rotation.GetImaginary()
        return self._to_pose3(
            np.array([translation[0], translation[1], translation[2]], dtype=np.float64),
            np.array([rotation.GetReal(), imaginary[0], imaginary[1], imaginary[2]], dtype=np.float64),
        )

    def _find_flange_link(self) -> str:
        """Prim path of the robot link (a rigid body under the articulation) that the flange tool frame is fixed to.

        Walks from the flange tool frame up through its parents (XRDF ``add_frame`` modifiers,
        then URDF joints) until reaching a frame with a rigid body prim of the same name.
        """
        parents: Dict[str, str] = {}
        for joint in ElementTree.fromstring(self._urdf_text).iter("joint"):
            parent, child = joint.find("parent"), joint.find("child")
            if parent is not None and child is not None:
                parents[child.get("link")] = parent.get("link")
        for modifier in self._xrdf.get("modifiers") or []:
            add_frame = modifier.get("add_frame") if isinstance(modifier, dict) else None
            if add_frame:
                parents[add_frame["frame_name"]] = add_frame["parent_frame_name"]
        links = {
            prim.GetName(): str(prim.GetPath())
            for prim in pxr.Usd.PrimRange(self._prim, pxr.Usd.TraverseInstanceProxies())
            if prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI)
        }
        frame: Optional[str] = self._flange_frame
        while frame is not None:
            if frame in links:
                return links[frame]
            frame = parents.get(frame)
        raise ValueError(
            f"cannot find the link that tool frame '{self._flange_frame}' is fixed to: no rigid body prim under "
            f"'{self._prim.GetPath()}' is named after it or one of its parent frames"
        )

    def _find_articulation_root(self) -> pxr.Usd.Prim:
        """The articulation root at or under the robot's prim (it may be the robot prim, its root link, or its root joint)."""
        roots = [
            prim for prim in pxr.Usd.PrimRange(self._prim, pxr.Usd.TraverseInstanceProxies())
            if prim.HasAPI(pxr.UsdPhysics.ArticulationRootAPI)
        ]
        if len(roots) == 0:
            raise ValueError(f"no articulation root (Physics > Articulation Root) at or under '{self._prim.GetPath()}'")
        if len(roots) > 1:
            raise ValueError(
                f"several articulation roots under '{self._prim.GetPath()}': {[str(r.GetPath()) for r in roots]}; "
                f"use the robot's own prim"
            )
        return roots[0]

    def _to_pose3(self, position: np.ndarray, orientation: np.ndarray) -> cumotion.Pose3:
        """Stage-unit position and wxyz quaternion to a cuMotion pose in meters."""
        w, x, y, z = (float(v) for v in orientation)
        return cumotion.Pose3(
            rotation=cumotion.Rotation3(w, x, y, z),
            translation=np.asarray(position, dtype=np.float64) * self._meters_per_unit,
        )

    def _from_pose3(self, pose: cumotion.Pose3) -> Tuple[np.ndarray, np.ndarray]:
        """cuMotion pose in meters to a stage-unit position and wxyz quaternion."""
        position = np.asarray(pose.translation, dtype=np.float64).flatten() / self._meters_per_unit
        rotation = pose.rotation
        orientation = np.array([rotation.w(), rotation.x(), rotation.y(), rotation.z()], dtype=np.float64)
        return position, orientation






def multiply_poses(
    position_a: np.ndarray, orientation_a: np.ndarray, position_b: np.ndarray, orientation_b: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Compose poses: returns pose A * pose B, i.e., pose B (expressed in frame A) expressed in A's parent frame.

    Positions are ``(3,)``; orientations are ``(4,)`` ``[w, x, y, z]`` quaternions.
    """
    w1, x1, y1, z1 = orientation_a
    w2, x2, y2, z2 = orientation_b
    orientation = np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ], dtype=np.float64)
    return np.asarray(position_a, dtype=np.float64) + _quat_rotate(orientation_a, position_b), orientation


def invert_pose(position: np.ndarray, orientation: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Invert a pose (``(3,)`` position, ``(4,)`` ``[w, x, y, z]`` orientation)."""
    conjugate = np.array([orientation[0], -orientation[1], -orientation[2], -orientation[3]], dtype=np.float64)
    return -_quat_rotate(conjugate, position), conjugate


def _quat_rotate(orientation: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Rotate a ``(3,)`` vector by a ``[w, x, y, z]`` unit quaternion."""
    w = float(orientation[0])
    u = np.asarray(orientation[1:], dtype=np.float64)
    v = np.asarray(vector, dtype=np.float64)
    return v + 2.0 * np.cross(u, np.cross(u, v) + w * v)


def _axis_angle(orientation_a: np.ndarray, orientation_b: np.ndarray, axis: np.ndarray) -> float:
    """Angle (radians) between ``axis`` rotated by each of two ``[w, x, y, z]`` unit quaternions."""
    a = _quat_rotate(orientation_a, axis)
    b = _quat_rotate(orientation_b, axis)
    return math.acos(max(-1.0, min(1.0, float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)))))


# Physics views for rigid bodies whose pose the PhysX interface does not report (e.g., articulation links)
_rigid_prim_views: Dict[str, SingleRigidPrim] = {}


def rigid_body_world_pose(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Current world pose of a rigid body in the physics simulation: ``(position (3,), orientation (4,) wxyz)``.

    Read from the physics simulation rather than USD, since a rigid body's USD transform may not
    be updated while simulating.
    """
    result = omni.physx.get_physx_interface().get_rigidbody_transformation(path)
    if result.get("ret_val"):
        x, y, z, w = result["rotation"]
        return np.array(result["position"], dtype=np.float64), np.array([w, x, y, z], dtype=np.float64)
    view = _rigid_prim_views.get(path)
    if view is None:
        view = _rigid_prim_views[path] = SingleRigidPrim(path)
    position, orientation = view.get_world_pose()
    return np.asarray(position, dtype=np.float64), np.asarray(orientation, dtype=np.float64)


class ToolChanger(Sensor[ToolChangerModel], Actuator[ToolChangerModel]):
    """A tool changer at a robot's flange: attaches and detaches end effectors with a fixed joint.

    Like a physical tool changer, it does not know which end effectors exist. Writing
    ``attached=True`` attaches whatever rigid body is seated at the attachment frame, if any; if
    nothing is seated, nothing is attached (write again to retry). Writing ``attached=False``
    detaches. Which end effector to pick up, and its TCP
    (:meth:`ManipulatorRobotCuMotionActuator.set_tcp`), is up to the caller.

    :meth:`sensor_read` reports whether something is seated at the attachment frame, like a
    proximity sensor in the clamp; it does not report the state of the clamp. A seated end
    effector reads ``True`` before it is attached, and after it is detached until the robot moves
    away from it. To confirm an attach or detach, move away (e.g., retreat from the rack) and read
    the sensor: ``True`` means the end effector came along (attached), ``False`` means it stayed
    behind (detached).

    Frames:
        - Attachment frame: the robot's flange tool frame (e.g., ``tool0``, or its override, see
          :meth:`ManipulatorRobotCuMotionActuator.set_flange_frame`), from the robot
          (:meth:`ManipulatorRobotCuMotionActuator.flange_frame_world_pose`). End effectors are
          welded to the link it is fixed to (e.g., ``Link6``).
        - Seated: a rigid body (not part of the robot) whose collision geometry is within
          ``position_tolerance`` of the attachment frame, whose origin is within
          ``position_tolerance`` of it, and whose +Z axis is within ``orientation_tolerance`` of
          the attachment frame's +Z axis (the approach axis; rotation about it is not checked).

    Attaching welds the seated body to the flange link at their current relative pose, so nothing
    jumps. The fixed joint is authored on the stage's session layer, so it is never saved into
    the scene file, and it excludes itself from articulations so the robot and end effector
    articulations stay separate.

    Units: ``position_tolerance`` is in stage units, ``orientation_tolerance`` in degrees.
    """

    JOINT_NAME: ClassVar[str] = "ToolChangerFixedJoint"

    def __init__(
        self,
        robot: ManipulatorRobotCuMotionActuator,
        position_tolerance: float = 0.01,
        orientation_tolerance: float = 10.0,
        name: Optional[str] = None,
    ):
        if not isinstance(robot, ManipulatorRobotCuMotionActuator):
            raise TypeError("robot must be a ManipulatorRobotCuMotionActuator")
        super().__init__(robot.flange_link, name)
        if not isinstance(position_tolerance, (int, float)) or position_tolerance <= 0:
            raise ValueError("position_tolerance must be a positive number")
        if not isinstance(orientation_tolerance, (int, float)) or orientation_tolerance <= 0:
            raise ValueError("orientation_tolerance must be a positive number")
        self._robot = robot
        self._robot_path = robot.prim.GetPath()
        self._position_tolerance = float(position_tolerance)
        self._orientation_tolerance = math.radians(orientation_tolerance)

        self._setpoint = ToolChangerModel(attached=False)
        self._attached_body: Optional[str] = None
        self._remove_stale_joints()

    @property
    def actual_model_type(self) -> Type[ToolChangerModel]:
        return ToolChangerModel

    @property
    def command_model_type(self) -> Type[ToolChangerModel]:
        return ToolChangerModel

    @property
    def attached_body(self) -> Optional[str]:
        """Prim path of the attached (welded) rigid body, or None. For diagnostics; a physical tool changer has no such signal."""
        return self._attached_body if self._joint_is_valid() else None

    def attachment_frame_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """World pose of the attachment frame (e.g., tool0): ``(position (3,), orientation (4,) wxyz)``."""
        return self._robot.flange_frame_world_pose()

    def actuator_write(self, value: ToolChangerModel) -> None:
        if not isinstance(value, ToolChangerModel):
            raise TypeError("value must be a ToolChangerModel")
        self._setpoint = value
        attached = self.attached_body is not None
        if value.attached and not attached:
            body = self._find_seated_body()
            if body is not None:
                self._attach(body)
        elif not value.attached and attached:
            self._detach()

    def actuator_read(self) -> ToolChangerModel:
        return self._setpoint.model_copy()

    def sensor_read(self) -> ToolChangerModel:
        """Whether something is seated at the attachment frame (not whether the clamp is closed)."""
        return ToolChangerModel(attached=self._find_seated_body() is not None)

    def _find_seated_body(self) -> Optional[str]:
        """The rigid body seated at the attachment frame (see the class docstring), or None."""
        frame_position, frame_orientation = self.attachment_frame_pose()

        # Proximity: rigid bodies with collision geometry near the attachment frame
        candidates: List[str] = []
        def report(hit) -> bool:
            body = hit.rigid_body
            if body and body not in candidates and not pxr.Sdf.Path(body).HasPrefix(self._robot_path):
                candidates.append(body)
            return True  # keep looking
        omni.physx.get_physx_scene_query_interface().overlap_sphere(
            self._position_tolerance, carb.Float3(*frame_position.tolist()), report, False)

        # Seated: origin at the attachment frame, +Z aligned with its +Z
        seated, seated_distance = None, math.inf
        z = np.array([0.0, 0.0, 1.0])
        for body in candidates:
            body_position, body_orientation = rigid_body_world_pose(body)
            distance = float(np.linalg.norm(body_position - frame_position))
            angle = _axis_angle(frame_orientation, body_orientation, z)
            if distance <= self._position_tolerance and angle <= self._orientation_tolerance and distance < seated_distance:
                seated, seated_distance = body, distance
        return seated

    def _attach(self, body: str) -> None:
        # Weld the body at its current pose relative to the flange link, so nothing jumps:
        # localPose0 is the body frame expressed in the flange link frame.
        link_position, link_orientation = rigid_body_world_pose(str(self._prim.GetPath()))
        body_position, body_orientation = rigid_body_world_pose(body)
        relative_position, relative_orientation = multiply_poses(*invert_pose(link_position, link_orientation), body_position, body_orientation)

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        with pxr.Usd.EditContext(stage, stage.GetSessionLayer()):
            joint = pxr.UsdPhysics.FixedJoint.Define(stage, self._joint_path(body))
            joint.CreateBody0Rel().SetTargets([self._prim.GetPath()])
            joint.CreateBody1Rel().SetTargets([pxr.Sdf.Path(body)])
            joint.CreateLocalPos0Attr().Set(pxr.Gf.Vec3f(*relative_position.tolist()))
            joint.CreateLocalRot0Attr().Set(pxr.Gf.Quatf(float(relative_orientation[0]), *relative_orientation[1:].tolist()))
            joint.CreateLocalPos1Attr().Set(pxr.Gf.Vec3f(0.0, 0.0, 0.0))
            joint.CreateLocalRot1Attr().Set(pxr.Gf.Quatf(1.0, 0.0, 0.0, 0.0))
            # Keep the robot and end effector articulations separate (otherwise the end
            # effector's bodies and joints are merged into the robot's articulation)
            joint.CreateExcludeFromArticulationAttr().Set(True)
            joint.CreateCollisionEnabledAttr().Set(False)
        self._attached_body = body

    def _detach(self) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        with pxr.Usd.EditContext(stage, stage.GetSessionLayer()):
            stage.RemovePrim(self._joint_path(self._attached_body))
        self._attached_body = None

    def _joint_path(self, body: str) -> str:
        return f"{body}/{self.JOINT_NAME}"

    def _joint_is_valid(self) -> bool:
        """Whether the fixed joint to the attached body still exists and connects the bodies."""
        if self._attached_body is None:
            return False
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self._joint_path(self._attached_body))
        if not prim.IsValid() or not prim.IsA(pxr.UsdPhysics.FixedJoint):
            return False
        joint = pxr.UsdPhysics.FixedJoint(prim)
        return len(joint.GetBody0Rel().GetTargets()) > 0 and len(joint.GetBody1Rel().GetTargets()) > 0

    def _remove_stale_joints(self) -> None:
        """Remove tool changer joints at this flange left over from a previous run (e.g., stopped while attached)."""
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        stale = [
            prim.GetPath() for prim in stage.Traverse()
            if prim.GetName() == self.JOINT_NAME and prim.IsA(pxr.UsdPhysics.FixedJoint)
            and self._prim.GetPath() in pxr.UsdPhysics.FixedJoint(prim).GetBody0Rel().GetTargets()
        ]
        with pxr.Usd.EditContext(stage, stage.GetSessionLayer()):
            for path in stale:
                stage.RemovePrim(path)

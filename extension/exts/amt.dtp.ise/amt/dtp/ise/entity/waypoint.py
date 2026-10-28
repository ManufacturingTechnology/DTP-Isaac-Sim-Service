from pydantic import BaseModel, ConfigDict, Field
from typing import List, ClassVar, Tuple, Optional, Dict, Callable, Sequence, Set
from dataclasses import dataclass
import itertools
import numpy as np
import math

import carb._carb
import isaacsim
import isaacsim.core
from ..isaac5_compatibility import SingleRigidPrim
from isaacsim.util.debug_draw import _debug_draw
import omni
import omni.timeline
import omni.ui
import omni.usd
import omni.physx
import omni.physx.bindings
import omni.physx.bindings._physx
import pxr
import pxr.Gf
import pxr.Sdf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdPhysics


class WaypointModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    model_name: ClassVar[str] = "Waypoint"
    custom_data_key: ClassVar[str] = "amt:dtp:waypoint:data"

    enabled: bool = Field(
        True,
        title="Enabled",
        description="Whether the waypoint is enabled.",
    )
    tags: List[str] = Field(
        title="Tags",
        description="A list of tags for the waypoint."
    )

class Waypoint:

    @staticmethod
    def define(prim: pxr.Usd.Prim | pxr.Sdf.Path | str, waypoint_model: WaypointModel) -> "Waypoint":
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        if prim.HasCustomDataKey(WaypointModel.custom_data_key):
            raise Exception(f"prim is already a waypoint")
        prim.SetCustomDataByKey(WaypointModel.custom_data_key, waypoint_model.model_dump_json())

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        return prim.HasCustomDataKey(WaypointModel.custom_data_key)

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
            raise ValueError("prim is not a Waypoint")

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim
    
    @property
    def enabled(self) -> bool:
        return self.get_model().enabled
    
    @enabled.setter
    def enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        model = self.get_model()
        model.enabled = enabled
        self.set_model(model)

    @property
    def visible(self) -> bool:
        imageable = pxr.UsdGeom.Imageable(self.prim)
        return imageable.GetVisibilityAttr().Get() != pxr.UsdGeom.Tokens.invisible

    @visible.setter
    def visible(self, visible: bool) -> None:
        if not isinstance(visible, bool):
            raise TypeError("visible must be a bool")
        imageable = pxr.UsdGeom.Imageable(self.prim)
        if visible:
            imageable.MakeVisible()
        else:
            imageable.MakeInvisible()
    

    def get_model(self) -> WaypointModel:
        return WaypointModel.model_validate_json(self.prim.GetCustomDataByKey(WaypointModel.custom_data_key))
    
    def set_model(self, waypoint_model: WaypointModel) -> None:
        if not isinstance(waypoint_model, WaypointModel):
            raise TypeError("waypoint_model must be a WaypointModel")
        self.prim.SetCustomDataByKey(WaypointModel.custom_data_key, waypoint_model.model_dump_json())

    def get_pose_gf(self) -> Tuple[pxr.Gf.Vec3d, pxr.Gf.Quatf]:
        """
        Get the pose of the waypoint.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        xformable = pxr.UsdGeom.Xformable(self._prim)
        world_transform: pxr.Gf.Matrix4d = xformable.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        pos: pxr.Gf.Vec3d = world_transform.ExtractTranslation()
        ori: pxr.Gf.Quatf = pxr.Gf.Quatf(world_transform.ExtractRotationQuat())
        return pos, ori

    def get_pose_np(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get the pose of the waypoint.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        pos, ori = self.get_pose_py()
        return np.array(pos, dtype=float), np.array(ori, dtype=float)

    def get_pose_py(self) -> Tuple[List[float], List[float]]:
        """
        Get the pose of the waypoint.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        # xformable = pxr.UsdGeom.Xformable(self._prim)
        # world_transform: pxr.Gf.Matrix4d = xformable.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        # pos: pxr.Gf.Vec3d = world_transform.ExtractTranslation()
        # ori: pxr.Gf.Quatf = pxr.Gf.Quatf(world_transform.ExtractRotationQuat())
        pos, ori = self.get_pose_gf()
        return [pos[0], pos[1], pos[2]], [ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2]]

    def get_distance_from(
        self,
        position: Tuple[float, float, float] | List[float] | pxr.Gf.Vec3f | pxr.Gf.Vec3d | np.ndarray
    ) -> float:
        if isinstance(position, (tuple, list, np.ndarray)):
            if len(position) != 3:
                raise ValueError("position must have a length of 3")
            if not all(isinstance(p, (float, int)) for p in position):
                raise TypeError("position must be a Sequence[float,3]")
        if isinstance(position, (tuple, list, pxr.Gf.Vec3f, pxr.Gf.Vec3d)):
            position = np.array([float(position[0]), float(position[1]), float(position[2])], dtype=float)
        if not isinstance(position, np.ndarray) or not np.issubdtype(position.dtype, np.floating):
            raise TypeError("position must be a Sequence[float,3]")
        
        pos = self.get_pose_py()[0]
        pos = np.array([float(pos[0]), float(pos[1]), float(pos[2])], dtype=float)
        return np.linalg.norm(pos - position)

    def get_rigid_bodies_in_range(self, radius: float | int, filter_fn: Optional[Callable[[pxr.Usd.Prim | pxr.Sdf.Path | str], bool]] = None) -> List[pxr.Usd.Prim]:
        """
        Get rigid body prims within a spherical radius of the waypoint.

        :param radius: The radius of the sphere to identify prims in, relative to the waypoint position.
        :return: A list of rigid body prims.
        """
        if isinstance(radius, int):
            radius = float(radius)
        if not isinstance(radius, float):
            raise TypeError("radius must be a float | int")
        
        if filter_fn is not None and not isinstance(filter_fn, Callable):
            raise TypeError("filter_fn must be a Callable[[pxr.Usd.Prim | pxr.Sdf.Path | str], bool]")
        
        # TODO: must be playing for this to work, otherwise silently fails to identify hits
        # requires rigid bodies which are only active while running
        if omni.timeline.get_timeline_interface().is_stopped():
            raise Exception("Method can not be used when stopped")
        
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        
        prims: List[pxr.Usd.Prim] = []  # Collect hit prim paths

        def report_hit(hit: omni.physx.bindings._physx.OverlapHit) -> bool:
            # Record hits, but exclude the waypoint prim
            if hit.rigid_body == None:
                return True # Continue reporting other hits
            prim: pxr.Usd.Prim = stage.GetPrimAtPath(hit.rigid_body)
            if prim is None:
                return True # Continue reporting other hits
            if not prim.IsValid():
                return True # Continue reporting other hits
            prims.append(prim)
            return True # Continue reporting other hits
        
        physx_scene_query_interface = omni.physx.get_physx_scene_query_interface()
        pos, ori = self.get_pose_py()
        f_pos = carb._carb.Float3(*pos)
        number_of_hits = physx_scene_query_interface.overlap_sphere(radius, f_pos, report_hit)
        
        if filter_fn is not None:
            old_prims = list(prims)
            prims = []
            for prim in old_prims:
                if filter_fn(prim):
                    prims.append(prim)

        return prims

    def __eq__(self, other: "Waypoint") -> bool:
        if not isinstance(other, Waypoint):
            return False
        return self._prim.GetPath() == other._prim.GetPath()

    def __hash__(self) -> int:
        return hash(self._prim.GetPath())
    
    def __repr__(self):
        return f"Waypoint({self._prim.GetPath()})"


@dataclass
class WaypointCacheData:
    all: List[Waypoint]
    by_tag_set: Dict[str, List[Waypoint]]


class WaypointCache:

    def __init__(self):
        self._data: Optional[WaypointCacheData] = None

    @property
    def data(self) -> WaypointCacheData:
        if self._data is not None:
            return self._data # TODO: deep copy instead? How much slower?
        return self._build_cache_from_stage()
    
    def build(self) -> None:
        self._data = self._build_cache_from_stage()

    def clear(self) -> None:
        self._data = None

    def get_all(self) -> List[Waypoint]:
        return [w for w in self.data.all if w.get_model().enabled] # Only return enabled waypoints

    def get_by_tag(self, tag_set: List[str] | Set[str] | str) -> List[Waypoint]:
        tag_set = self._encode_tag_set(tag_set)
        waypoints = self.data.by_tag_set.get(tag_set, [])
        return [w for w in waypoints if w.get_model().enabled] # Only return enabled waypoints

    def get_all_tags(self) -> List[str]:
        return list(self.data.by_tag_set.keys())

    @staticmethod
    def _build_cache_from_stage() -> WaypointCacheData:
        all: Optional[List[Waypoint]] = []
        by_tag_set: Optional[Dict[str, List[Waypoint]]] = {}

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        all_prims_recursive: List[pxr.Usd.Prim] = list(stage.Traverse())
        for prim in all_prims_recursive:
            if not Waypoint.is_a(prim):
                continue
            waypoint = Waypoint(prim)

            # Enable all waypoints
            model = waypoint.get_model()
            if model.enabled == False:
                model.enabled = True
                waypoint.set_model(model)

            if waypoint not in all:
                all.append(waypoint)

            for tag in WaypointCache._generate_tag_set_permutations(waypoint.get_model().tags):
                if tag not in by_tag_set:
                    by_tag_set[tag] = []
                if waypoint not in by_tag_set[tag]:
                    by_tag_set[tag].append(waypoint)
        
        return WaypointCacheData(
            all=all,
            by_tag_set=by_tag_set,
        )

    @staticmethod
    def _encode_tag_set(tag_set: List[str] | Set[str] | str) -> str:
        if isinstance(tag_set, str):
            tag_set = list(tag_set.split(";"))
        if not isinstance(tag_set, (list, tuple, set)) and all(isinstance(ts, str) for ts in tag_set):
            raise TypeError("tag_set must be a List[str] | Set[str] | str")
        tag_set = [t.strip() for t in list(tag_set)] # Remove lead/trail whitespace, don't modify the original
        tag_set = sorted(set(tag_set)) # De-duplicate and sort
        tag_set = ";".join(tag_set)
        return tag_set

    @staticmethod
    def _generate_tag_set_permutations(tag_set: List[str] | Set[str] | str) -> List[str]:
        # tags is already sorted and de-duplicated by _encode_tag_set, so each
        # combination is emitted in canonical (sorted) form and matches the keys
        # produced for individual tag sets.
        tag_set = WaypointCache._encode_tag_set(tag_set)
        tags = tag_set.split(";")
        permutations: List[str] = []

        for size in range(1, len(tags) + 1):
            for combo in itertools.combinations(tags, size):
                permutations.append(";".join(combo))

        return permutations

@dataclass
class WaypointMoveAnimationFrame:

    @staticmethod
    def get(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> "WaypointMoveAnimationFrame":
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        rigid_prim = SingleRigidPrim(str(prim.GetPath()))

        pose = rigid_prim.get_world_pose()
        return WaypointMoveAnimationFrame(
            prim=prim,
            position=pose[0],
            orientation=pose[1],
        )

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim

    @property
    def position(self) -> np.ndarray:
        return self._position

    @property
    def orientation(self) -> np.ndarray:
        return self._orientation

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, position: np.ndarray, orientation: np.ndarray):
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        if not prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
            raise ValueError("prim must already be a RigidPrim (have UsdPhysics.RigidBodyAPI applied)")
        self._prim = prim
        self._rigid_prim = SingleRigidPrim(str(prim.GetPath()))

        if not isinstance(position, np.ndarray):
            raise TypeError("position must be a np.ndarray")
        if not len(position) == 3:
            raise ValueError("position must be 3 long")
        self._position = position

        if not isinstance(orientation, np.ndarray):
            raise TypeError("orientation must be a np.ndarray")
        if not len(orientation) == 4:
            raise ValueError("orientation must be 4 long")
        self._orientation = orientation
    
    def set(self):
        if self.position is not None:
            if self.orientation is not None:
                self._rigid_prim.set_world_pose(self.position, self.orientation)
            else:
                self._rigid_prim.set_world_pose(position=self.position)
        else:
            if self.orientation is not None:
                self._rigid_prim.set_world_pose(orientation=self.orientation)

        self._rigid_prim.set_linear_velocity(np.array([0.0, 0.0, 0.0], dtype=np.float64))
        self._rigid_prim.set_angular_velocity(np.array([0.0, 0.0, 0.0], dtype=np.float64))

    def __repr__(self) -> str:
        return f"WaypointMoveAnimationFrame({self.prim.GetPath()}: {self.position}, {self.orientation})"


class WaypointMoveAnimationBuilder:

    def __init__(
        self,
        frame: WaypointMoveAnimationFrame,
        time_step_size: Optional[float] = None
    ):
        if not isinstance(frame, WaypointMoveAnimationFrame):
            raise TypeError("frame must be a WaypontMoveAnimationFrame")
        self._frames: List[WaypointMoveAnimationFrame] = [frame]

        if time_step_size is None:
            time_step_size  = 1.0 / omni.timeline.get_timeline_interface().get_time_codes_per_seconds()
        if not isinstance(time_step_size, float):
            raise TypeError("time_step_size must be a float")
        self._time_step_size = time_step_size

        self._time = 0

    @property
    def frames(self) -> List[WaypointMoveAnimationFrame]:
        return self._frames

    @property
    def time_step_size(self) -> float:
        return self._time_step_size
    
    def add_by_duration(
        self,
        position: Optional[np.ndarray],
        orientation: Optional[np.ndarray],
        duration: float,
    ) -> None:
        last_frame = self._frames[-1]

        if position is None:
            position = last_frame.position
        if not isinstance(position, np.ndarray):
            raise TypeError("position must be a np.ndarray")
        if not len(position) == 3:
            raise ValueError("position must be 3 long")

        if orientation is None:
            orientation = last_frame.orientation
        if not isinstance(orientation, np.ndarray):
            raise TypeError("orientation must be a np.ndarray")
        if not len(orientation) == 4:
            raise ValueError("orientation must be 4 long")

        if not isinstance(duration, float):
            raise TypeError("duration must be a float")

        n_steps = int(math.floor(duration / self._time_step_size))
        mod_steps = duration % self._time_step_size
        for i in range(n_steps):
            t = float(i) / n_steps
            self._frames.append(WaypointMoveAnimationFrame(
                prim=last_frame.prim,
                position=self._interpolate_position(last_frame.position, position, t),
                orientation=self._interpolate_quat_slerp_numpy(last_frame.orientation, orientation, t),
            ))

        # Check whether the last step was a partial step
        if mod_steps > 0.000001:
            # Add last step so it finishes at the target position and orientation
            self._frames.append(WaypointMoveAnimationFrame(
                prim=last_frame.prim,
                position=position,
                orientation=orientation,
            ))

    def add_by_velocity(
        self,
        position: np.ndarray,
        orientation: Optional[np.ndarray],
        velocity: float,
    ) -> None:
        if not isinstance(velocity, float):
            raise TypeError("velocity must be a float")
        
        if not isinstance(position, np.ndarray):
            raise TypeError("position must be a np.ndarray")
        if len(position) != 3:
            raise ValueError("position must be 3 long")
        
        last_frame = self._frames[-1]
        distance = float(np.linalg.norm(position - last_frame.position))
        duration = distance / velocity
        self.add_by_duration(position, orientation, duration)

    def _quat_normalize(self, q: np.ndarray):
        return q / np.linalg.norm(q)
    
    def _interpolate_position(self, p0: np.ndarray, p1: np.ndarray, t: float) -> np.ndarray:
        if not isinstance(p0, np.ndarray):
            raise TypeError("p0 must be a np.ndarray")
        if not len(p0) == 3:
            raise ValueError("p0 must be 3 long")
        if not isinstance(p1, np.ndarray):
            raise TypeError("p1 must be a np.ndarray")
        if not len(p1) == 3:
            raise ValueError("p1 must be 3 long")
        
        delta_position = p1 - p0
        return p0 + delta_position * t
    
    def _interpolate_quat_slerp_numpy(self, q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
        """
        Interpolate between two quaternionis.

        :param q0: The start quaternion, wxyz order.
        :param q1: The end quaternion, wxyz order.
        :param t: The time/progress between the two quaternions, from 0 to 1.
        :return: The interpolated quaternion
        """
        if not isinstance(q0, np.ndarray):
            raise TypeError("q0 must be a np.ndarray")
        if not len(q0) == 4:
            raise ValueError("q0 must be 4 long")
        if not isinstance(q1, np.ndarray):
            raise TypeError("q1 must be a np.ndarray")
        if not len(q1) == 4:
            raise ValueError("q1 must be 4 long")

        # Spherical linear interpolation between two quaternions (wxyz order)
        q0 = self._quat_normalize(q0)
        q1 = self._quat_normalize(q1)
        
        # Dot product
        dot = np.dot(q0, q1)
        
        # If quaternions are opposed, flip one to take shortest path
        if dot < 0.0:
            q1 = -q1
            dot = -dot
        
        # Threshold for linear interpolation (avoid divide-by-zero)
        DOT_THRESHOLD = 0.9995
        if dot > DOT_THRESHOLD:
            # Fall back to LERP if too close
            result = q0 + t * (q1 - q0)
        else:
            theta_0 = np.arccos(dot)
            sin_theta_0 = np.sin(theta_0)
            theta = theta_0 * t
            sin_theta = np.sin(theta)
            
            s0 = np.cos(theta) - dot * sin_theta / sin_theta_0
            s1 = sin_theta / sin_theta_0
            result = (s0 * q0) + (s1 * q1)
        
        return self._quat_normalize(result)  # final safety normalize

class WaypointMoveAnimator:

    @staticmethod
    def generate_move(
        prim: pxr.Usd.Prim | pxr.Sdf.Path | str,
        start: Waypoint,
        end: Waypoint,
        velocity: float = 1.3,
    ):
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        if not prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
            raise ValueError("prim must already be a RigidPrim (have UsdPhysics.RigidBodyAPI applied)")
        rigid_prim = SingleRigidPrim(str(prim.GetPath()))

        if not isinstance(start, Waypoint):
            raise TypeError("start must be a Waypoint")
        if not isinstance(end, Waypoint):
            raise TypeError("end must be a Waypoint")
        if not isinstance(velocity, float):
            raise TypeError("velocity must be a float")

        init_pos, init_ori = rigid_prim.get_world_pose() # returns np.ndarrays
        end_pos, end_ori = end.get_pose_np()

        # Just above initial position
        pos1 = np.array(init_pos)
        pos1[2] += 0.5

        # Just above end position
        pos2 = np.array(end_pos)
        pos2[2] += 0.5

        timeline_time_step  = 1.0 / omni.timeline.get_timeline_interface().get_time_codes_per_seconds()   # e.g. 60.0

        builder = WaypointMoveAnimationBuilder(
            WaypointMoveAnimationFrame(prim, init_pos, init_ori),
            timeline_time_step, # TODO: get from service
        )
        builder.add_by_velocity(pos1, init_ori, velocity)
        builder.add_by_velocity(pos2, end_ori, velocity)
        builder.add_by_velocity(end_pos, end_ori, velocity)
        return WaypointMoveAnimator(builder.frames, start, end)
    
    @staticmethod
    def generate_move_relative(
        prim: pxr.Usd.Prim | pxr.Sdf.Path | str,
        start: Waypoint,
        end: Waypoint,
        velocity: float = 1.3,
    ):
        if isinstance(prim, pxr.Sdf.Path):
            prim = str(prim)
        if isinstance(prim, str):
            stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        if not prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
            raise ValueError("prim must already be a RigidPrim (have UsdPhysics.RigidBodyAPI applied)")
        rigid_prim = SingleRigidPrim(str(prim.GetPath()))

        if not isinstance(start, Waypoint):
            raise TypeError("start must be a Waypoint")
        if not isinstance(end, Waypoint):
            raise TypeError("end must be a Waypoint")
        if not isinstance(velocity, float):
            raise TypeError("velocity must be a float")

        prim_pos, prim_ori = pose = rigid_prim.get_world_pose()
        src_pos, src_ori = start.get_pose_np()
        dst_pos, dst_ori = end.get_pose_np()
        delta_pos = dst_pos - src_pos

        init_pos = prim_pos
        init_ori = prim_ori

        end_pos = prim_pos + delta_pos
        end_orie = prim_ori

        # Just above initial position
        pos1 = np.array(init_pos)
        pos1[2] += 0.5

        # Just above end position
        pos2 = np.array(end_pos)
        pos2[2] += 0.5

        timeline_time_step  = 1.0 / omni.timeline.get_timeline_interface().get_time_codes_per_seconds()   # e.g. 60.0

        builder = WaypointMoveAnimationBuilder(
            WaypointMoveAnimationFrame(prim, init_pos, init_ori),
            timeline_time_step, # TODO: get from service
        )
        builder.add_by_velocity(velocity, pos1, init_ori)
        builder.add_by_velocity(velocity, pos2, end_orie)
        builder.add_by_velocity(velocity, end_pos, end_orie)
        return WaypointMoveAnimator(builder.frames, start, end)

    def __init__(
        self,
        frames: List[WaypointMoveAnimationFrame],
        start: Waypoint,
        end: Waypoint,
    ):
        if not isinstance(frames, list) and all(isinstance(f, WaypointMoveAnimationFrame) for f in frames):
            raise TypeError("frames must be a List[WaypointMoveAnimationFrame]")
        self._frames = frames

        if not isinstance(start, Waypoint):
            raise TypeError("start must be a Waypoint")
        self._start = start

        if not isinstance(end, Waypoint):
            raise TypeError("end must be a Waypoint")
        self._end = end

        self.reset()

    @property
    def frames(self) -> List[WaypointMoveAnimationFrame]:
        return self._frames

    @property
    def is_finished(self) -> bool:
        return self._i >= len(self._frames) - 1

    def reset(self) -> None:
        self.cancel()
        self._i = -1

    def cancel(self):
        self._start.enabled = True
        self._end.enabled = True

    def step(self) -> None:
        if self.is_finished:
            raise Exception("animation already finished")
        if self._i == 0:
            # First step
            self._start.enabled = False
            self._end.enabled = False

        self._i += 1
        self._frames[self._i].set()

        if self.is_finished:
            self._start.enabled = True
            self._end.enabled = True
        
import math
from typing import Optional, Type, Tuple, List, Sequence
import numpy as np

import omni
import omni.usd
import omni.physx
import pxr
import pxr.Gf
import pxr.Sdf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdShade
import isaacsim.core.experimental.materials
import isaacsim.core.experimental.prims

from amt.dtp.ise.service.draw import DebugDrawService

from ..lib.control.transducer.models import DistanceModel, VelocityModel, ColorModel
from .core import Sensor, Actuator


class ConveyorActuator(Actuator[VelocityModel]):

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)

        attr_name = "physxSurfaceVelocity:surfaceVelocityEnabled"
        self._surface_vel_enabled_attr: pxr.Usd.Attribute = self.prim.GetAttribute(attr_name)
        if not self._surface_vel_enabled_attr:
            raise ValueError(f"prim must have a '{attr_name}' attribute.")
            self._surface_vel_enabled_attr = self.prim.CreateAttribute(attr_name, pxr.Sdf.ValueTypeNames.Bool)

        attr_name = "physxSurfaceVelocity:surfaceVelocity"
        self._surface_vel_velocity_attr: pxr.Usd.Attribute = self.prim.GetAttribute(attr_name)
        if not self._surface_vel_velocity_attr:
            raise ValueError(f"prim must have a '{attr_name}' attribute.")
            self._surface_vel_velocity_attr = self.prim.CreateAttribute(attr_name, pxr.Sdf.ValueTypeNames.Float3)

    @property
    def command_model_type(self) -> Type[VelocityModel]:
        return VelocityModel

    def actuator_write(self, value: VelocityModel) -> None:
        if not isinstance(value, VelocityModel):
            raise ValueError("value must be a VelocityModel")

        # Surface velocity has a quirk where if you set the velocity to zero when enabled
        #   is set to true, then set the velocity to a non-zero value, you have to toggle
        #   the enabled attribute. Disabling then enabling is a workaround for this quirk.
        self._surface_vel_enabled_attr.Set(False)
        #self._surface_vel_velocity_attr.Set(pxr.Gf.Vec3f(0.0, value.velocity, 0.0)) # X, Y, Z
        self._surface_vel_velocity_attr.Set(pxr.Gf.Vec3f(value.velocity, 0.0, 0.0)) # X, Y, Z
        self._surface_vel_enabled_attr.Set(True)

    def actuator_read(self) -> VelocityModel:
        vector: pxr.Gf.Vec3d = self._surface_vel_velocity_attr.Get()
        velocity: float = vector[0] # X direction, as written by actuator_write
        return VelocityModel(velocity=velocity)


class CornerConveyorActuator(Actuator[VelocityModel]):

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)

        attr_name = "physxSurfaceVelocity:surfaceVelocityEnabled"
        self._surface_vel_enabled_attr: pxr.Usd.Attribute = self.prim.GetAttribute(attr_name)
        if not self._surface_vel_enabled_attr:
            raise ValueError(f"prim must have a '{attr_name}' attribute.")
            self._surface_vel_enabled_attr = self.prim.CreateAttribute(attr_name, pxr.Sdf.ValueTypeNames.Bool)

        attr_name = "physxSurfaceVelocity:surfaceVelocity"
        self._surface_vel_velocity_attr: pxr.Usd.Attribute = self.prim.GetAttribute(attr_name)
        if not self._surface_vel_velocity_attr:
            raise ValueError(f"prim must have a '{attr_name}' attribute.")
            self._surface_vel_velocity_attr = self.prim.CreateAttribute(attr_name, pxr.Sdf.ValueTypeNames.Float3)

        attr_name = "physxSurfaceVelocity:surfaceAngularVelocity"
        self._surface_ang_velocity_attr: pxr.Usd.Attribute = self.prim.GetAttribute(attr_name)
        if not self._surface_ang_velocity_attr:
            raise ValueError(f"prim must have a '{attr_name}' attribute.")
            self._surface_ang_velocity_attr = self.prim.CreateAttribute(attr_name, pxr.Sdf.ValueTypeNames.Float3)

    @property
    def command_model_type(self) -> Type[VelocityModel]:
        return VelocityModel

    def actuator_write(self, value: VelocityModel) -> None:
        if not isinstance(value, VelocityModel):
            raise TypeError("value must be a VelocityModel")

        # Surface velocity has a quirk where if you set the velocity to zero when enabled
        #   is set to true, then set the velocity to a non-zero value, you have to toggle
        #   the enabled attribute. Disabling then enabling is a workaround for this quirk.
        self._surface_vel_enabled_attr.Set(False)
        self._surface_vel_velocity_attr.Set(pxr.Gf.Vec3f(-value.velocity, value.velocity, 0.0)) # X, Y, Z
        self._surface_ang_velocity_attr.Set(pxr.Gf.Vec3f(0.0, 0.0, 110.0 * value.velocity)) # X, Y, Z
        self._surface_vel_enabled_attr.Set(True)

    def actuator_read(self) -> VelocityModel:
        vector: pxr.Gf.Vec3d = self._surface_vel_velocity_attr.Get()
        velocity: float = vector[1] # Y direction
        return VelocityModel(velocity=velocity)


class DistanceSensor(Sensor[DistanceModel]):

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, maximum_distance: float | int = 1.0, name: Optional[str] = None):
        super().__init__(prim, name)
        if not isinstance(maximum_distance, (float, int)):
            raise TypeError("maximum_distance must be a float | int")
        self._maximum_distance = float(maximum_distance)

        self._hit: Optional[bool] = None
        self._start_pos: Optional[List[float]] = None
        self._end_pos: Optional[List[float]] = None

        self._physx_interface = omni.physx.get_physx_scene_query_interface()

    @property
    def actual_model_type(self) -> Type[DistanceModel]:
        return DistanceModel

    @property
    def maximum_distance(self) -> float:
        return self._maximum_distance

    def sensor_read(self) -> Optional[DistanceModel]:
        self._hit, self._start_pos, direction, self._end_pos = self.get_raycast()
        distance = float(np.linalg.norm(np.array(self._end_pos, dtype=float) - np.array(self._start_pos, dtype=float)))
        return DistanceModel(distance=distance)

    def draw_raycast(self) -> None:
        """
        Draw the raycast ray captured during the last sensor_read() operation.
        """
        if self._hit is not None and self._start_pos is not None and self._end_pos is not None:
            self._cast_ray(self._hit, self._start_pos, self._end_pos)

    def get_raycast(self) -> Tuple[bool, List[float], List[float], List[float]]:
        """
        Get the raycast. Returns:
        hit: Whether the raycast hit anything
        start_position: The 3D point where the raycast starts
        direction: The normalized 3D direction vector from the start position
        end_position: The 3D point where the raycast ends
        """
        start_pos, direction = self._get_prim_position_and_direction()

        hit = self._physx_interface.raycast_closest(start_pos, direction, self._maximum_distance)
        if hit and "hit" in hit and hit["hit"]:
            end_pos = np.array(hit["position"])
            return True, start_pos.tolist(), direction.tolist(), end_pos.tolist()
        else:
            # No hit: Draw green line full length
            end_pos = start_pos + direction * self._maximum_distance
            return False, start_pos.tolist(), direction.tolist(), end_pos.tolist()

    def _get_prim_position_and_direction(self) -> Tuple[np.array, np.array]:
        # Compute world transform
        xform = pxr.UsdGeom.Xformable(self._prim)
        world_transform = xform.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())

        # Extract position
        position_gf: pxr.Gf.Vec3d = world_transform.ExtractTranslation()
        start_pos = np.array([position_gf[0], position_gf[1], position_gf[2]])

        # Extract rotation quaternion
        rotation_gf: pxr.Gf.Quatd = world_transform.ExtractRotationQuat()

        # Define local direction (e.g., -Z; adjust based on prim convention)
        local_dir: pxr.Gf.Vec3d = pxr.Gf.Vec3d(0, 0, -1)

        # Rotate to world space and normalize
        world_dir_gf = rotation_gf.Transform(local_dir)
        world_dir_gf = world_dir_gf.GetNormalized()
        direction = np.array([world_dir_gf[0], world_dir_gf[1], world_dir_gf[2]])

        return start_pos, direction

    def _cast_ray(self, hit: bool, start_pos: np.array, end_pos: np.array):
        if hit:
            color = [1.0, 0.0, 0.0, 1.0]  # Red RGBA (floats 0-1)
        else:
            # No hit: Draw green line full length
            color = [0.0, 1.0, 0.0, 1.0]  # Green

        thickness = 2.0
        DebugDrawService().draw_line(start_pos, end_pos, color, thickness)


class ColorActuator(Actuator[ColorModel]):
    _temp_material_name = "TemporaryMaterialOverride"

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        super().__init__(prim, name)
        # Changing color takes ~7-10 ms, so don't change color unless it actually changed.
        self._last_set_value: ColorModel = self.actuator_read()

    @property
    def command_model_type(self) -> Type[ColorModel]:
        return ColorModel

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim

    def actuator_write(self, value: ColorModel):
        if not isinstance(value, ColorModel):
            raise TypeError("value must be an ColorModel")

        if value.is_colorless:
            if self._last_set_value.is_colorless:
                return # Nothing to do, already set to None/Colorless
            self._unset_color()
            self._last_set_value = value
            return

        if self._last_set_value == value:
            return # Nothing to do, already set to this value

        self._set_color(value)
        self._last_set_value = value

    def actuator_read(self) -> ColorModel:
        return self._get_color()

    def _set_color(self, value: ColorModel):
        if not isinstance(value, ColorModel):
            raise TypeError("value must be a ColorModel")

        material_path = f"{str(self._prim.GetPath())}/{self._temp_material_name}"
        material = isaacsim.core.experimental.materials.OmniPbrMaterial(material_path)
        material.set_input_values("diffuse_color_constant", np.array(value.to_sequence()))
        xform_prim = isaacsim.core.experimental.prims.XformPrim(str(self._prim.GetPath()))
        xform_prim.apply_visual_materials(material)

    def _unset_color(self):
        # Unbind the material
        binding_api = pxr.UsdShade.MaterialBindingAPI(self._prim)
        binding_api.UnbindAllBindings()

        # Delete the temp material prim
        material_path = f"{str(self._prim.GetPath())}/{self._temp_material_name}"
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(material_path)

    def _get_color(self) -> ColorModel:
        material_path = f"{str(self._prim.GetPath())}/{self._temp_material_name}"

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(material_path)
        if material_prim is None or not material_prim.IsValid():
            return ColorModel.colorless()

        material = isaacsim.core.experimental.materials.OmniPbrMaterial(material_path)
        color_wp = material.get_input_values("diffuse_color_constant")
        if color_wp is None:
            return ColorModel.colorless()

        color_np = np.asarray(color_wp.numpy()).reshape(-1)
        # model_construct: report the authored color even if it is outside [0, 1].
        return ColorModel.model_construct(r=float(color_np[0]), g=float(color_np[1]), b=float(color_np[2]))

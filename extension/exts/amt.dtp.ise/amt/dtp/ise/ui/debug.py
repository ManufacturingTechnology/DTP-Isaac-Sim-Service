from isaacsim.gui.components.element_wrappers import (
    Button,
    CheckBox,
    CollapsableFrame,
    ColorPicker,
    DropDown,
    FloatField,
    IntField,
    StateButton,
    StringField,
    TextBlock,
    XYPlot,
)
from isaacsim.gui.components.ui_utils import get_style
import asyncio
from typing import List
import omni
import omni.ui
import omni.usd
import pxr
import pxr.Gf
import pxr.Usd
import pxr.UsdGeom
import pxr.Vt

from .core import ExtensionWindow
from ..coprocessor.client import MeshData
from ..lib.coprocessorclient import CoProcessorClient, GeometryRequest, GeometryResponse
from ..lib.models import FaceGeometry, MaterialProperties
from ..lib.logging import _logger
from ..service.entity import EntityService
from ..service.config import ConfigurationService
from ..transducer.articulation import PrismaticJointActuator, PrismaticJointPositionModel
from ..entity.workpiece import (
    Workpiece, 
    FaceWorkpiece,
    RigidWorkpiece, 
    DeformableWorkpiece, 
    ParticleWorkpiece, 
    VoxelWorkpiece,
    FaceData,
    MeshGenerator,
    ParticleGenerator,
    _workpiece_instancer
)


class DebugWindow(ExtensionWindow):

    def __init__(self):
        super().__init__("Debug")

    def construct(self) -> None:
        WorkpieceTest()
        StampingTest()


class WorkpieceTest:

    def __init__(self):
        self._id = 0

        self._frame = CollapsableFrame("Workpiece Lifecycle Demo", collapsed=True)
        with self._frame:
            with omni.ui.VStack(style=get_style(), spacing=5, height=0):
                
                material_data = MaterialProperties(
                    visual_color=(0.4, 0.0, 0.0)
                )

                def first_workpiece_remove():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    rigid = None
                    if RigidWorkpiece.is_a(first):
                        rigid = RigidWorkpiece(first)

                    voxel = None
                    if VoxelWorkpiece.is_a(first):
                        voxel = VoxelWorkpiece(first)

                    if rigid is not None:
                        rigid.remove()
                    elif voxel is not None:
                        voxel.remove()
                    else:
                        omni.kit.commands.execute("DeletePrims", paths=[first])

                Button(
                    "First Workpiece Remove",
                    "Go",
                    on_click_fn=first_workpiece_remove
                )

                def first_workpiece_to_rigid():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    workpiece = Workpiece.get_workpiece(first)
                    workpiece.convert_to_rigid(EntityService().workpiece_cache)


                Button(
                    "First Workpiece To Rigid",
                    "Go",
                    on_click_fn=first_workpiece_to_rigid
                )

                def first_workpiece_to_deformable():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    workpiece = Workpiece.get_workpiece(first)
                    workpiece.convert_to_deformable(EntityService().workpiece_cache)

                Button(
                    "First Workpiece To Deformable",
                    "Go",
                    on_click_fn=first_workpiece_to_deformable
                )

                def first_workpiece_to_particle():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    workpiece = Workpiece.get_workpiece(first)
                    workpiece.convert_to_particle(EntityService().workpiece_cache)

                Button(
                    "First Workpiece To Fluid",
                    "Go",
                    on_click_fn=first_workpiece_to_particle
                )

                def first_workpiece_to_voxel():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    workpiece = Workpiece.get_workpiece(first)
                    workpiece.convert_to_voxel(EntityService().workpiece_cache)

                Button(
                    "First Workpiece To Voxel",
                    "Go",
                    on_click_fn=first_workpiece_to_voxel
                )

                def first_workpiece_teleport():
                    first = _workpiece_instancer.get_existing_paths()[0]

                    workpiece = Workpiece.get_workpiece(first)
                    rigids = workpiece.convert_to_rigid(EntityService().workpiece_cache)
                    for rigid in rigids:
                        pos, ori = rigid.get_pose_py()
                        pos[2] += 3.0
                        rigid.set_pose(position=pos)

                Button(
                    "First Workpiece Teleport",
                    "Go",
                    on_click_fn=first_workpiece_teleport
                )

                def spawn_rigid_cube():
                    mesh_data = MeshGenerator.generate_cube(
                        size=pxr.Gf.Vec3d(1, 1, 1),
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    definition = RigidWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.4, 0.0, 0.0),
                        )
                    )
                    workpiece = RigidWorkpiece(definition.path)
                    _logger.debug("Spawned")

                Button(
                    "Spawn Rigid Cube",
                    "Go",
                    on_click_fn=spawn_rigid_cube
                )

                def spawn_rigid_cylinder():
                    mesh_data = MeshGenerator.generate_cylinder(
                        diameter=1.0,
                        length=1.0,
                        degree_step=5.0,
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    definition = RigidWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.4, 0.0),
                        )
                    )
                    workpiece = RigidWorkpiece(definition.path)
                    _logger.debug("Spawned")

                Button(
                    "Spawn Rigid Cylinder",
                    "Go",
                    on_click_fn=spawn_rigid_cylinder
                )

                def spawn_rigid_sphere():
                    mesh_data = MeshGenerator.generate_sphere(
                        diameter=1.0,
                        degree_step=5.0,
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    definition = RigidWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.0, 0.4),
                        )
                    )
                    workpiece = RigidWorkpiece(definition.path)
                    _logger.debug("Spawned")

                Button(
                    "Spawn Rigid Sphere",
                    "Go",
                    on_click_fn=spawn_rigid_sphere
                )

                def spawn_deformable_cube():
                    mesh_data = MeshGenerator.generate_cube(
                        size=pxr.Gf.Vec3d(1, 1, 1),
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    DeformableWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.4, 0.0, 0.0),
                        )
                    )

                Button(
                    "Spawn Deformable Cube",
                    "Go",
                    on_click_fn=spawn_deformable_cube
                )

                def spawn_deformable_cylinder():
                    mesh_data = MeshGenerator.generate_cylinder(
                        diameter=1.0,
                        length=1.0,
                        degree_step=5.0,
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    DeformableWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.4, 0.0),
                        )
                    )

                Button(
                    "Spawn Deformable Cylinder",
                    "Go",
                    on_click_fn=spawn_deformable_cylinder
                )

                def spawn_deformable_sphere():
                    mesh_data = MeshGenerator.generate_sphere(
                        diameter=1.0,
                        degree_step=5.0,
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    mesh_data = mesh_data.split_until_max_edge_length(0.1)
                    DeformableWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.0, 0.4),
                        )
                    )

                Button(
                    "Spawn Deformable Sphere",
                    "Go",
                    on_click_fn=spawn_deformable_sphere
                )

                def spawn_fluid_cube():
                    particle_data = ParticleGenerator.generate_cube(size=(1.0, 1.0, 1.0))
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = ParticleWorkpiece.define(
                        EntityService().workpiece_cache,
                        point_data=particle_data, 
                        material_data=MaterialProperties(
                            visual_color=(0.4, 0.0, 0.0),
                        )
                    )

                Button(
                    "Spawn Particle Cube",
                    "Go",
                    on_click_fn=spawn_fluid_cube
                )

                def spawn_fluid_cylinder():
                    particle_data = ParticleGenerator.generate_cylinder(diameter=1.0, height=1.0)
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = ParticleWorkpiece.define(
                        EntityService().workpiece_cache,
                        point_data=particle_data, 
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.4, 0.0),
                        )
                    )

                Button(
                    "Spawn Particle Cylinder",
                    "Go",
                    on_click_fn=spawn_fluid_cylinder
                )

                def spawn_fluid_sphere():
                    particle_data = ParticleGenerator.generate_sphere(diameter=1.0)
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = ParticleWorkpiece.define(
                        EntityService().workpiece_cache,
                        point_data=particle_data, 
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.0, 0.4),
                        )
                    )

                Button(
                    "Spawn Particle Sphere",
                    "Go",
                    on_click_fn=spawn_fluid_sphere
                )

                def spawn_voxel_cube():
                    particle_data = ParticleGenerator.generate_cube(size=(1.0, 1.0, 1.0))
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = VoxelWorkpiece.define(
                        EntityService().workpiece_cache,
                        particle_data=particle_data,
                        material_data=MaterialProperties(
                            visual_color=(0.4, 0.0, 0.0),
                        )
                    )
                    pass

                Button(
                    "Spawn Voxel Cube",
                    "Go",
                    on_click_fn=spawn_voxel_cube
                )

                def spawn_voxel_cylinder():
                    particle_data = ParticleGenerator.generate_cylinder(diameter=1.0, height=1.0)
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = VoxelWorkpiece.define(
                        EntityService().workpiece_cache,
                        particle_data=particle_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.4, 0.0),
                        )
                    )

                Button(
                    "Spawn Voxel Cylinder",
                    "Go",
                    on_click_fn=spawn_voxel_cylinder
                )

                def spawn_voxel_sphere():
                    particle_data = ParticleGenerator.generate_sphere(diameter=1.0)
                    particle_data = particle_data.transform(
                        position=pxr.Gf.Vec3d(0.0, 0.0, 3.0),
                    )
                    particle_workpiece = VoxelWorkpiece.define(
                        EntityService().workpiece_cache,
                        particle_data=particle_data,
                        material_data=MaterialProperties(
                            visual_color=(0.0, 0.0, 0.4),
                        )
                    )

                Button(
                    "Spawn Voxel Sphere",
                    "Go",
                    on_click_fn=spawn_voxel_sphere
                )

                

class StampingTest:

    def __init__(self):
        self._id = 0

        self._frame = CollapsableFrame("Stamping Demo", collapsed=False)
        with self._frame:
            with omni.ui.VStack(style=get_style(), spacing=5, height=0):
                
                material_data = MaterialProperties(
                    visual_color=(0.4, 0.0, 0.0)
                )

                def reset():
                    feed_clamp_bottom_feed_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Bottom/FeedJoint")
                    feed_clamp_bottom_feed_joint.actuator_write(PrismaticJointPositionModel(position=0.0))

                    #feed_clamp_bottom_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Bottom/PrismaticJoint")
                    #feed_clamp_bottom_joint.actuator_write(PrismaticJointPositionModel(position=-0.10))

                    feed_clamp_top_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Top/PrismaticJoint")
                    feed_clamp_top_joint.actuator_write(PrismaticJointPositionModel(position=0.05))

                    feed_clamp_back = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Back/PrismaticJoint")
                    feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=0.05))

                    punch = PrismaticJointActuator("/Main_Assembly/Upper_Assembly/PrismaticJoint")
                    punch.actuator_write(PrismaticJointPositionModel(position=0.09))

                Button(
                    "Reset",
                    "Go",
                    on_click_fn=reset
                )

                def initialize():
                    strip_length = 1.2
                    strip_center = - strip_length / 2
                    mesh_data = MeshGenerator.generate_cube(
                        size=pxr.Gf.Vec3d(strip_length, 0.130, 0.005),
                    )
                    mesh_data = mesh_data.transform(
                        position=pxr.Gf.Vec3d(strip_center, 0.0, 0.015),
                    )
                    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length
                    maximum_edge_length = 0.050 # 0.050 # Debug override, comment out to use the configured value
                    mesh_data = mesh_data.split_until_max_edge_length(maximum_edge_length)
                    DeformableWorkpiece.define(
                        EntityService().workpiece_cache,
                        mesh_data=mesh_data,
                        maximum_edge_length=maximum_edge_length,
                        material_data=MaterialProperties(
                            density=7.85e3, # Steel
                            deformable_youngs_modulus=1e35, #2e11, # Steel
                            deformable_static_friction=0.5,
                            deformable_dynamic_friction=0.5, #0.3,
                            deformable_poissons_ratio=0.3, # steel
                            visual_color=(0.4, 0.0, 0.0),
                        )
                    )
                    feed_clamp_bottom_feed_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Bottom/FeedJoint")
                    feed_clamp_bottom_feed_joint.actuator_write(PrismaticJointPositionModel(position=0.0))
                    feed_clamp_top_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Top/PrismaticJoint")
                    feed_clamp_top_joint.actuator_write(PrismaticJointPositionModel(position=-0.0))
                    feed_clamp_back = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Back/PrismaticJoint")
                    feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=0.010))

                Button(
                    "Initialize",
                    "Go",
                    on_click_fn=initialize
                )

                def feed():
                    async def _run():
                        feed_clamp_back = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Back/PrismaticJoint")
                        feed_clamp_top_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Top/PrismaticJoint")
                        feed_clamp_bottom_feed_joint = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Bottom/FeedJoint")

                        feed_clamp_top_joint.actuator_write(PrismaticJointPositionModel(position=-0.002)) # Clamp top clamp
                        await asyncio.sleep(0.5)
                        feed_clamp_top_joint.actuator_write(PrismaticJointPositionModel(position=-0.002)) # Clamp top clamp
                        feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=0.01)) # Release back clamp
                        await asyncio.sleep(0.25)
                        feed_clamp_bottom_feed_joint.actuator_write(PrismaticJointPositionModel(position=0.040)) # Feed forward
                        await asyncio.sleep(1.5) # Let strip settle and reach equilibrium
                        feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=-0.002)) # Clamp back clamp
                        await asyncio.sleep(0.25)
                        feed_clamp_top_joint.actuator_write(PrismaticJointPositionModel(position=0.015)) # Release top clamp
                        #feed_clamp_bottom_joint.actuator_write(PrismaticJointPositionModel(position=-0.020)) # Release bottom clamp
                        await asyncio.sleep(0.25)
                        feed_clamp_bottom_feed_joint.actuator_write(PrismaticJointPositionModel(position=0.0)) # Feed backward

                    asyncio.ensure_future(_run())

                Button(
                    "Feed",
                    "Go",
                    on_click_fn=feed
                )

                def press_down():
                    async def _run():
                        punch = PrismaticJointActuator("/Main_Assembly/Upper_Assembly/PrismaticJoint")
                        punch.actuator_write(PrismaticJointPositionModel(position=-0.01))
                        #await asyncio.sleep(0.5)

                        feed_clamp_back = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Back/PrismaticJoint")
                        feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=0.01)) # Release back clamp

                    asyncio.ensure_future(_run())

                Button(
                    "Press Down",
                    "Go",
                    on_click_fn=press_down
                )

                def press_bos():
                    trim_punches_path = "/Main_Assembly/Upper_Assembly/TrimPunches"
                    maximum_edge_length = 0.050 # Match initialize()

                    existing_paths = _workpiece_instancer.get_existing_paths()
                    if len(existing_paths) == 0 or not FaceWorkpiece.is_a(existing_paths[0]):
                        return
                    face_workpiece: FaceWorkpiece = Workpiece.get_workpiece(existing_paths[0])
                    # Normals are dropped: split meshes carry face-varying normals, and the coprocessor recomputes them
                    workpiece_geometry = face_workpiece.get_mesh_data().to_face_geometry().model_copy(update={"normals": None})

                    # Trim punches in world space, since they move with the press
                    stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
                    tool_geometries: List[FaceGeometry] = []
                    for prim in pxr.Usd.PrimRange(stage.GetPrimAtPath(trim_punches_path), pxr.Usd.TraverseInstanceProxies()):
                        if not prim.IsA(pxr.UsdGeom.Mesh):
                            continue
                        local_to_world = pxr.UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
                        local_mesh = MeshData.from_prim(prim)
                        world_mesh = MeshData(
                            vertices=pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(local_to_world.Transform(pxr.Gf.Vec3d(p))) for p in local_mesh.vertices]),
                            face_vertex_counts=local_mesh.face_vertex_counts,
                            face_vertex_indices=local_mesh.face_vertex_indices,
                            extents=None,
                            vertex_normals=None,
                        )
                        tool_geometry = world_mesh.to_object_geometry() # Triangulated
                        if tool_geometry is not None:
                            tool_geometries.append(tool_geometry)
                    if len(tool_geometries) == 0:
                        _logger.warning(f"No trim punch meshes found under {trim_punches_path}")
                        return

                    # Run boolean subtract, workpiece minus trim punches; blocking, so no frame passes before the replace
                    client = CoProcessorClient()
                    request = GeometryRequest(workpiece=[workpiece_geometry], tools=tool_geometries)
                    response: GeometryResponse = client.difference(request)

                    # The trimmed result becomes the new rest shape, freezing the formed geometry
                    material_data = face_workpiece.get_material()
                    workpiece_model = face_workpiece.get_model()
                    face_workpiece.remove()
                    for i, face_geometry in enumerate(response.workpieces):
                        mesh_data = FaceData.from_face_geometry(face_geometry)
                        mesh_data = mesh_data.split_until_max_edge_length(maximum_edge_length)
                        DeformableWorkpiece.define(
                            EntityService().workpiece_cache,
                            mesh_data=mesh_data,
                            maximum_edge_length=maximum_edge_length,
                            material_data=material_data,
                            workpiece_model=workpiece_model if i == 0 else None, # Offcuts get new IDs
                        )

                Button(
                    "Press BOS",
                    "Go",
                    on_click_fn=press_bos
                )

                def press_up():
                    async def _run():
                        punch = PrismaticJointActuator("/Main_Assembly/Upper_Assembly/PrismaticJoint")
                        punch.actuator_write(PrismaticJointPositionModel(position=0.09))
                        #await asyncio.sleep(0.5)

                        feed_clamp_back = PrismaticJointActuator("/Main_Assembly/Feed_Assembly/Feed_Clamp_Back/PrismaticJoint")
                        feed_clamp_back.actuator_write(PrismaticJointPositionModel(position=-.002)) # Clamp back clamp

                    asyncio.ensure_future(_run())

                Button(
                    "Press Up",
                    "Go",
                    on_click_fn=press_up
                )



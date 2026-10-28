import copy
import os
import pathlib
import shutil
from typing import Annotated, Any, List, Optional, Callable, Dict, Type

from amt.dtp.ise.service.subscription import SubscriptionService
from fastapi import Depends, FastAPI, Form, HTTPException, status, UploadFile, File

import isaacsim
from isaacsim.core.simulation_manager import SimulationManager
import omni
import omni.usd
import pxr
import pxr.Usd
from pydantic import Json
import uvicorn
import asyncio
import threading
import time
import logging
#from concurrent.futures import ThreadPoolExecutor
import asyncio

from omni.services.core.routers import ServiceAPIRouter
import omni.usd
import omni.kit.app

from ..__version__ import __version__

from ..coprocessor.client import MeshData
from ..entity.workpiece import DeformableWorkpiece, MeshGenerator, RigidWorkpiece
from ..lib.coprocessorclient import CoProcessorClient, GeometryRefineRequest
from ..lib.apiclient import *
from ..lib.environment import PackageValidationError, extract
from ..lib.models import *
from ..lib.geometry import *
from ..lib.coprocessorclient import *
from .core import ApiController
from ..entity.workpiece import *
from ..lib.logging import _logger
#from ..service.logging import _logger
from ..service.entity import EntityService


import asyncio
from concurrent.futures import ProcessPoolExecutor
_process_pool = ProcessPoolExecutor()



#_process_pool = ThreadPoolExecutor()
routers: List[ServiceAPIRouter] = []



# Create router (tags appear in Swagger sidebar)
_application_router = ServiceAPIRouter(
    prefix="/application", 
    tags=["Application"]
)
routers.append(_application_router)

@_application_router.delete(
    "/exit",
    summary="Exit the Isaac Sim application."
)
async def application_exit() -> str:
    os._exit(0)

@_application_router.get(
    "/version",
    summary="Get the Isaac Sim version."
)
async def application_version():
    return omni.kit.app.get_app().get_app_version()

@_application_router.get(
    "/working_directory",
    summary="Get the working directory path."
)
async def application_working_directory():
    return os.getcwd().replace("\\", "/")




_extension_router = ServiceAPIRouter(
    prefix="/extension", 
    tags=["Extension"]
)
routers.append(_extension_router)

@_extension_router.get(
    "/ping",
    summary="Ping the extension."
)
async def extension_ping() -> None:
    return

@_extension_router.get(
    "/version",
    summary="Get the extension version."
)
async def extension_version() -> str:
    return __version__

@_extension_router.get(
    "/configuration/path",
    summary="Get the AMT DTP ISE configuration file path."
)
async def extension_configuration_path():
    return ConfigurationService().path.replace("\\", "/")

@_extension_router.get(
    "/configuration/resolved",
    summary="Get the AMT DTP ISE configuration resolved from all configuration layers."
)
async def extension_configuration_resolved() -> ApplicationConfiguration:
    return ConfigurationService().configuration

@_extension_router.get(
    "/configuration/default",
    summary="Get the AMT DTP ISE default configuration layer."
)
async def extension_configuration_default() -> ApplicationConfiguration:
    return ConfigurationService().default_configuration_layer

@_extension_router.get(
    "/configuration/global",
    summary="Get the AMT DTP ISE global configuration layer."
)
async def extension_configuration_global() -> ApplicationConfiguration:
    return ConfigurationService().global_configuration_layer

@_extension_router.get(
    "/configuration/environment",
    summary="Get the AMT DTP ISE environment configuration layer."
)
async def extension_configuration_environment() -> ApplicationConfiguration:
    return ConfigurationService().environment_configuration_layer





_timeline_router = ServiceAPIRouter(
    prefix="/timeline", 
    tags=["Timeline"]
)
routers.append(_timeline_router)

@_timeline_router.get(
    "/info",
    summary="Get information about the timeline."
)
async def timeline_info() -> TimelineInfoReponse:

    async def run() -> TimelineInfoReponse:
        timeline = omni.timeline.get_timeline_interface()
        is_playing = timeline.is_playing()
        current_time = timeline.get_current_time()

        #timeline = omni.timeline.get_timeline_interface()

        # Timecodes per second == render frames per second
        timeline_time_step  = 1.0 / omni.timeline.get_timeline_interface().get_time_codes_per_seconds()   # e.g. 60.0

        # stage = omni.usd.get_context().get_stage()
        # physics_scene = pxr.UsdPhysics.Scene.Get(stage, "/physicsScene")
        # steps_per_sec = physics_scene.GetTimeCodesPerSecondAttr().Get()  # e.g. 60
        # physics_dt = 1.0 / steps_per_sec

        
        physics_time_step = None
        try:
            physics_time_step = SimulationManager.get_physics_dt()
        except Exception as ex:
            pass
        if physics_time_step is None:
            try:
                physics_scene = pxr.UsdPhysics.Scene.Get(omni.usd.get_context().get_stage(), "/physicsScene")
                physics_time_step = 1.0 / physics_scene.GetTimeCodesPerSecondAttr().Get()
            except Exception as ex:
                pass

        return TimelineInfoReponse(
            is_playing=is_playing,
            is_paused=current_time > 0 and not is_playing,
            is_stopped=current_time == 0 and not is_playing,
            current_time=current_time,
            timeline_time_step=timeline_time_step,
            physics_time_step=physics_time_step,
            error=SubscriptionService().error
        )
    
    return await ApiController().run_coro_on_isaac_loop(run())


@_timeline_router.post(
    "/play",
    summary="Play or resume playing the timeline."
)
async def timeline_play() -> None:

    async def run() -> None:
        try:
            omni.timeline.get_timeline_interface().play()
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Exception playing: {ex}",
            )
    
    await ApiController().run_coro_on_isaac_loop(run())


@_timeline_router.post(
    "/pause",
    summary="Pause the timeline."
)
async def timeline_pause() -> None:

    async def run() -> None:
        try:
            omni.timeline.get_timeline_interface().pause()
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Exception pausing: {ex}",
            )
    
    await ApiController().run_coro_on_isaac_loop(run())


@_timeline_router.post(
    "/stop",
    summary="Stop the timeline."
)
async def timeline_stop() -> None:

    async def run() -> None:
        try:
            omni.timeline.get_timeline_interface().stop()
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Exception stopping: {ex}",
            )
    
    await ApiController().run_coro_on_isaac_loop(run())






_environment_router = ServiceAPIRouter(
    prefix="/environment", 
    tags=["Environment"]
)
routers.append(_environment_router)

@_environment_router.get(
    "/info",
    summary="Get information about the currently open environment."
)
async def environment_info() -> EnvironmentInfoResponse:
    stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
    
    root_layer = stage.GetRootLayer()
    file_path = getattr(root_layer, "realPath", None)
    if file_path == "" or file_path is None:
        file_path = None
    else:
        file_path = file_path.replace("\\", "/")
    layer_identifier = root_layer.identifier # file path or anon:... if stage is not a saved file
    
    return EnvironmentInfoResponse(
        file_path=file_path
    )


@_environment_router.delete(
    "/clear",
    summary="Remove all environments."
)
async def environment_list() -> None:
    dir_path = ConfigurationService().configuration.environments_directory_path
    for file in [item for item in Path(dir_path).iterdir() if item.is_dir()]:
        shutil.rmtree(file)


@_environment_router.post(
    "/close",
    summary="Close the currently open environment by creating and opening a new empty environment."
)
async def environment_close() -> None:
    
    async def run():
        context = omni.usd.get_context()
        try:
            success, error = await context.new_stage_async()
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Exception opening environment: {ex}",
            )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error opening environment: {error}",
            )
    
    await ApiController().run_coro_on_isaac_loop(run())


@_environment_router.get(
    "/list",
    summary="Get information about the currently open environment."
)
async def environment_list() -> List[str]:
    environments = []
    dir_path = ConfigurationService().configuration.environments_directory_path
    for file in [item for item in Path(dir_path).iterdir() if item.is_dir()]:
        environments.append(
            file.name    
        )
    return environments


@_environment_router.post(
    "/open",
    summary="Open an environment."
)
async def environment_open(request: EnvironmentIdRequest) -> None:
    dir_path = ConfigurationService().configuration.environments_directory_path
    environment_path = Path(os.path.join(dir_path, request.id))
    if not os.path.isdir(environment_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Environment with name {request.id} not found",
        )
    
    usd_path =  get_environment_usd_path(environment_path)
    if usd_path is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No USD file found for environment {request.id}",
        )
    
    async def run(usd_path: Path):
        context = omni.usd.get_context()
        try:
            success, error = await context.open_stage_async(str(usd_path))
        except Exception as ex:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Exception opening environment: {ex}",
            )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error opening environment: {error}",
            )
    
    return await ApiController().run_coro_on_isaac_loop(run(usd_path))


@_environment_router.delete(
    "/remove",
    summary="Remove an environment."
)
async def environment_remove(request: EnvironmentIdRequest) -> None:
    dir_path = ConfigurationService().configuration.environments_directory_path
    target_dir = os.path.join(dir_path, request.id)
    if not os.path.isdir(target_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Environment with name {request.id} not found",
        )
    shutil.rmtree(target_dir)


@_environment_router.post(
    "/upload",
    summary="Upload a zipped environment."
)
async def environment_upload(file: UploadFile = File(...)) -> None:
    suffix = Path(file.filename).suffix.lower()
    ALLOWED_MIME = {"application/zip", "application/x-zip-compressed"}
    ALLOWED_EXTS = {".zip"}
    if file.content_type not in ALLOWED_MIME or suffix not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Upload must be a ZIP file. Got content_type={file.content_type}, ext={suffix}",
        )
    
    environments_directory_path = ConfigurationService().configuration.environments_directory_path
    Path(environments_directory_path).mkdir(exist_ok=True)

    environment_path = os.path.join(environments_directory_path, Path(file.filename).stem)

    # # Block upload due to existing environment with the same name
    # if os.path.exists(environment_path):
    #     raise HTTPException(
    #         status_code=status.HTTP_409_CONFLICT,
    #         detail=f"Environment already exists",
    #     )

    # Save file to temp directory
    tmp_file_dir = Path(tempfile.mkdtemp()).resolve()
    tmp_file_path = Path(os.path.join(tmp_file_dir, file.filename))
    tmp_extracted_dir = None
    try:
        with open(tmp_file_path, "wb") as tmp:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                tmp.write(chunk)
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not save uploaded file: {file.filename}",
        )

    # Extract the temp zip file to a temp directory (this also validates the package)
    try:
        tmp_extracted_file = extract(tmp_file_path)
        tmp_extracted_dir = Path(tmp_extracted_file).parent
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        if tmp_extracted_dir is not None:
            if os.path.exists(tmp_extracted_dir):
                shutil.rmtree(tmp_extracted_dir)
        if isinstance(ex, PackageValidationError):
            raise HTTPException(
                status_code=422,  # Unprocessable Content (constant name differs across Starlette versions)
                detail={"message": f"Environment {Path(file.filename).stem} is not valid; fix these issues and upload again", "errors": ex.errors},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not extract uploaded file",
        )

    if os.path.exists(tmp_file_dir):
        shutil.rmtree(tmp_file_dir)

    # Remove existing environment and overwrite (only after the upload validated)
    if os.path.exists(environment_path):
        if os.path.isfile(environment_path):
            os.unlink(environment_path)
        if os.path.isdir(environment_path):
            shutil.rmtree(environment_path)

    # Move the temp directory to the environments directory
    try:
        shutil.move(tmp_extracted_file, environments_directory_path)
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        if tmp_extracted_dir is not None:
            if os.path.exists(tmp_extracted_dir):
                shutil.rmtree(tmp_extracted_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not move uploaded file to environments directory",
        )

    if os.path.exists(tmp_extracted_dir):
        shutil.rmtree(tmp_extracted_dir)








_workpiece_router = ServiceAPIRouter(
    prefix="/workpiece", 
    tags=["Workpiece"]
)
routers.append(_workpiece_router)

async def _workpiece_generate_face_cube(request: WorkpieceSpawnCubeRequest) -> FaceData:
    face_geometry = FaceGenerator.generate_cube(request.x, request.y, request.z)
    face_geometry = FaceTransformer.transform(face_geometry, request.position, request.orientation)
    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length

    client = CoProcessorClient()
    face_geometry = await client.geometry_refine_async(
        GeometryRefineRequest(
            face_geometry=face_geometry,
            maximum_edge_length=maximum_edge_length,
        )
    )
    return FaceData.from_face_geometry(face_geometry)

async def _workpiece_generate_face_cylinder(request: WorkpieceSpawnCylinderRequest) -> FaceData:
    face_geometry = FaceGenerator.generate_cylinder(request.xy_diameter, request.z_length, request.degree_step)
    face_geometry = FaceTransformer.transform(face_geometry, request.position, request.orientation)
    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length

    client = CoProcessorClient()
    face_geometry = await client.geometry_refine_async(
        GeometryRefineRequest(
            face_geometry=face_geometry,
            maximum_edge_length=maximum_edge_length,
        )
    )
    return FaceData.from_face_geometry(face_geometry)

async def _workpiece_generate_face_sphere(request: WorkpieceSpawnSphereRequest) -> FaceData:
    face_geometry = FaceGenerator.generate_sphere(request.diameter, request.degree_step)
    face_geometry = FaceTransformer.transform(face_geometry, request.position)
    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length

    client = CoProcessorClient()
    face_geometry = await client.geometry_refine_async(
        GeometryRefineRequest(
            face_geometry=face_geometry,
            maximum_edge_length=maximum_edge_length,
        )
    )
    return FaceData.from_face_geometry(face_geometry)

async def _workpiece_generate_face_arbitrary(request: WorkpieceSpawnFaceRequest) -> FaceData:
    face_geometry = FaceGeometry(
        vertices=request.geometry.vertices,
        faces=request.geometry.faces,
        normals=request.geometry.normals
    )
    face_geometry = FaceTransformer.transform(request.geometry, request.position, request.orientation)
    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length

    client = CoProcessorClient()
    face_geometry = await client.geometry_refine_async(
        GeometryRefineRequest(
            face_geometry=face_geometry,
            maximum_edge_length=maximum_edge_length,
        )
    )
    return FaceData.from_face_geometry(face_geometry)

async def _workpiece_generate_face_stl(request: WorkpieceSpawnStlRequest, file: bytes) -> FaceData:
    if not isinstance(request, WorkpieceSpawnStlRequest):
        raise TypeError("request must be of type WorkpieceSpawnStlRequest")
    if not isinstance(file, bytes):
        raise TypeError("file must be of type bytes")
    
    client = CoProcessorClient() # TODO: configure coprocessor url, or lazy load global client.
    response = client.geometry_convert_stl_to_face(file, GeometryConvertStlToFaceRequest(units=request.units))
    if len(response.face_geometries) != 1:
        raise Exception(f"invalid number of bodies in STL file: {len(response.face_geometries)}")
    face_geometry = response.face_geometries[0]
    face_geometry = FaceTransformer.transform(face_geometry, request.position, request.orientation)
    maximum_edge_length = ConfigurationService().configuration.maximum_edge_length

    client = CoProcessorClient()
    face_geometry = await client.geometry_refine_async(
        GeometryRefineRequest(
            face_geometry=face_geometry,
            maximum_edge_length=maximum_edge_length,
        )
    )
    return FaceData.from_face_geometry(face_geometry)

async def _workpiece_generate_point_cube(request: WorkpieceSpawnCubeRequest) -> PointData:
    particle_diameter = ConfigurationService().configuration.particle_diameter
    point_geometry = PointGenerator.generate_cube(request.x, request.y, request.z, particle_diameter)
    point_geometry = PointTransformer.transform(point_geometry, request.position, request.orientation)
    return PointData.from_point_geometry(point_geometry)

async def _workpiece_generate_point_cylinder(request: WorkpieceSpawnCylinderRequest) -> PointData:
    particle_diameter = ConfigurationService().configuration.particle_diameter
    point_geometry = PointGenerator.generate_cylinder(request.xy_diameter, request.z_length, particle_diameter)
    point_geometry = PointTransformer.transform(point_geometry, request.position, request.orientation)
    return PointData.from_point_geometry(point_geometry)

async def _workpiece_generate_point_sphere(request: WorkpieceSpawnSphereRequest) -> PointData:
    particle_diameter = ConfigurationService().configuration.particle_diameter
    point_geometry = PointGenerator.generate_sphere(request.diameter, particle_diameter)
    point_geometry = PointTransformer.transform(point_geometry, request.position)
    return PointData.from_point_geometry(point_geometry)

async def _workpiece_generate_point_arbitrary(request: WorkpieceSpawnPointRequest) -> PointData:
    if math.fabs(request.geometry.diameter - ConfigurationService().configuration.particle_diameter) > 0.000001:
        raise Exception("request.geometry.diameter is not equal to resolved configuration particle_diameter")
    point_geometry = PointTransformer.transform(request.geometry, request.position, request.orientation)
    return PointData.from_point_geometry(point_geometry)

async def _workpiece_generate_point_stl(request: WorkpieceSpawnStlRequest, file: bytes) -> PointData:
    if not isinstance(request, WorkpieceSpawnStlRequest):
        raise TypeError("request must be of type WorkpieceSpawnStlRequest")
    if not isinstance(file, bytes):
        raise TypeError("file must be of type bytes")
    
    client = CoProcessorClient() # TODO: configure coprocessor url, or lazy load global client.
    response = client.geometry_convert_stl_to_face(file, GeometryConvertStlToFaceRequest(units=request.units))
    if len(response.face_geometries) != 1:
        raise Exception(f"invalid number of bodies in STL file: {len(response.face_geometries)}")
    face_geometry = response.face_geometries[0]
    face_geometry = FaceTransformer.transform(face_geometry, request.position, request.orientation)
    face_data = FaceData.from_face_geometry(face_geometry)

    particle_diameter = ConfigurationService().configuration.particle_diameter
    point_data = WarpMeshConverter().convert(face_data, particle_diameter)
    return point_data

def _get_json_with_file_openapi_extra(
    property_names_and_types: List[Tuple[str,Type[BaseModel]]]
) -> Dict[str, Any]:
    """
    Get openai extra metadata for a route with a file upload and json payloads as input arguments.
    """
    if not isinstance(property_names_and_types, list):
        raise TypeError(f"property_names_and_types must be a List[Tuple[str,Type[BaseModel]]]")
    for t in property_names_and_types:
        if not isinstance(t, tuple) or len(t) != 2:
            raise TypeError(f"property_names_and_types must be a List[Tuple[str,Type[BaseModel]]]")
        if not isinstance(t[0], str) or not isinstance(t[1], type) or not issubclass(t[1], BaseModel):
            raise TypeError(f"property_names_and_types must be a List[Tuple[str,Type[BaseModel]]]")
    
    def _inline_schema_refs(schema: dict) -> dict:
        schema = copy.deepcopy(schema)
        defs = schema.pop("$defs", {})

        def resolve(obj):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    ref_name = obj["$ref"].split("/")[-1]
                    return resolve(copy.deepcopy(defs[ref_name]))
                return {k: resolve(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [resolve(i) for i in obj]
            return obj

        return resolve(schema)
    
    properties = {"file": {"type": "string", "format": "binary"}}
    required = ["file"]

    for k, v in property_names_and_types:
        properties[k] = _inline_schema_refs(v.model_json_schema())
        required.append(k)

    openapi_extra: Dict[str, Any] = {
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            }
        }
    }

    return openapi_extra

@_workpiece_router.post(
    "/spawn/rigid/cube",
    summary="Spawn a Rigid Cube Workpiece."
)
async def workpiece_spawn_rigid_cube(request: WorkpieceSpawnCubeRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        RigidWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    face_data = await _workpiece_generate_face_cube(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/rigid/cylinder",
    summary="Spawn a Rigid Cylinder Workpiece."
)
async def workpiece_spawn_rigid_cylinder(request: WorkpieceSpawnCylinderRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        RigidWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    face_data = await _workpiece_generate_face_cylinder(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/rigid/sphere",
    summary="Spawn a Rigid Sphere Workpiece."
)
async def workpiece_spawn_rigid_sphere(request: WorkpieceSpawnSphereRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        RigidWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_sphere(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/rigid/arbitrary",
    summary="Spawn a Rigid Arbitrary Workpiece."
)
async def workpiece_spawn_rigid_arbitrary(request: WorkpieceSpawnFaceRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        RigidWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_arbitrary(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/rigid/stl",
    summary="Spawn a Rigid STL Workpiece.",
    openapi_extra=_get_json_with_file_openapi_extra([("request", WorkpieceSpawnStlRequest)]),
)
async def workpiece_spawn_rigid_stl(request: Json[WorkpieceSpawnStlRequest], file: UploadFile = File(...)) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        RigidWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_stl(request, await file.read())
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/deformable/cube",
    summary="Spawn a Deformable Cube Workpiece."
)
async def workpiece_spawn_deformable_cube(request: WorkpieceSpawnCubeRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        DeformableWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_cube(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/deformable/cylinder",
    summary="Spawn a Deformable Cylinder Workpiece."
)
async def workpiece_spawn_deformable_cylinder(request: WorkpieceSpawnCylinderRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        DeformableWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_cylinder(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/deformable/sphere",
    summary="Spawn a Deformable Sphere Workpiece."
)
async def workpiece_spawn_deformable_sphere(request: WorkpieceSpawnSphereRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        DeformableWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")
        
    face_data = await _workpiece_generate_face_sphere(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/deformable/arbitrary",
    summary="Spawn a Deformable Arbitrary Workpiece."
)
async def workpiece_spawn_deformable_arbitrary(request: WorkpieceSpawnFaceRequest) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        DeformableWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    face_data = await _workpiece_generate_face_arbitrary(request)
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/deformable/stl",
    summary="Spawn a Deformable STL Workpiece.",
    openapi_extra=_get_json_with_file_openapi_extra([("request", WorkpieceSpawnStlRequest)]),
)
async def workpiece_spawn_deformable_stl(request: Json[WorkpieceSpawnStlRequest], file: UploadFile = File(...)) -> None:
    async def run(face_data: FaceData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        DeformableWorkpiece.define(EntityService().workpiece_cache, face_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    face_data = await _workpiece_generate_face_stl(request, await file.read())
    await ApiController().run_coro_on_isaac_loop(run(face_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/particle/cube",
    summary="Spawn a Particle Cube Workpiece."
)
async def workpiece_spawn_particle_cube(request: WorkpieceSpawnCubeRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        ParticleWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_cube(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/particle/cylinder",
    summary="Spawn a Particle Cylinder Workpiece."
)
async def workpiece_spawn_particle_cylinder(request: WorkpieceSpawnCylinderRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        ParticleWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_cylinder(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/particle/sphere",
    summary="Spawn a Particle Sphere Workpiece."
)
async def workpiece_spawn_particle_sphere(request: WorkpieceSpawnSphereRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        ParticleWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_sphere(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/particle/arbitrary",
    summary="Spawn a Particle Arbitrary Workpiece."
)
async def workpiece_spawn_particle_arbitrary(request: WorkpieceSpawnPointRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        ParticleWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_arbitrary(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/particle/stl",
    summary="Spawn a Particle STL Workpiece.",
    openapi_extra=_get_json_with_file_openapi_extra([("request", WorkpieceSpawnStlRequest)]),
)
async def workpiece_spawn_particle_stl(request: Json[WorkpieceSpawnStlRequest], file: UploadFile = File(...)) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        ParticleWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_stl(request, await file.read())
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/voxel/cube",
    summary="Spawn a Voxel Cube Workpiece."
)
async def workpiece_spawn_voxel_cube(request: WorkpieceSpawnCubeRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        VoxelWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_cube(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/voxel/cylinder",
    summary="Spawn a Voxel Cylinder Workpiece."
)
async def workpiece_spawn_voxel_cylinder(request: WorkpieceSpawnCylinderRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        VoxelWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_cylinder(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/voxel/sphere",
    summary="Spawn a Voxel Sphere Workpiece."
)
async def workpiece_spawn_voxel_sphere(request: WorkpieceSpawnSphereRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        VoxelWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_sphere(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/voxel/arbitrary",
    summary="Spawn a Voxel Arbitrary Workpiece."
)
async def workpiece_spawn_voxel_arbitrary(request: WorkpieceSpawnPointRequest) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        VoxelWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_arbitrary(request)
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.post(
    "/spawn/voxel/stl",
    summary="Spawn a Particle STL Workpiece.",
    openapi_extra=_get_json_with_file_openapi_extra([("request", WorkpieceSpawnStlRequest)]),
)
async def workpiece_spawn_voxel_stl(request: Json[WorkpieceSpawnStlRequest], file: UploadFile = File(...)) -> None:
    async def run(point_data: PointData, material_properties: MaterialProperties, workpiece_model: Optional[WorkpieceModel]) -> None:
        VoxelWorkpiece.define(EntityService().workpiece_cache, point_data, material_properties, workpiece_model)

    if request.model is not None:
        if EntityService().workpiece_cache.contains_id(request.model.id):
            raise Exception(f"Workpiece with id '{request.model.id}' already exists.")

    point_data = await _workpiece_generate_point_stl(request, await file.read())
    await ApiController().run_coro_on_isaac_loop(run(point_data, request.material, request.model))

@_workpiece_router.get(
    "/list",
    summary="List workpieces by id",
)
async def workpiece_list() -> List[str]:
    return EntityService().workpiece_cache.get_ids()





_asset_router = ServiceAPIRouter(
    prefix="/asset", 
    tags=["Asset"]
)
routers.append(_asset_router)


@_asset_router.get(
    "/list",
    summary="List available assets."
)
async def asset_list() -> List[str]:
    assets = []
    dir_path = ConfigurationService().configuration.assets_directory_path
    for file in [item for item in Path(dir_path).iterdir() if item.is_dir()]:
        assets.append(
            file.name    
        )
    return assets


@_asset_router.delete(
    "/clear",
    summary="Remove all assets."
)
async def asset_list() -> None:
    dir_path = ConfigurationService().configuration.assets_directory_path
    for file in [item for item in Path(dir_path).iterdir() if item.is_dir()]:
        shutil.rmtree(file)



def spawn_prim(
        source_file_path: str | Path, 
        destination_prim_path: str | pxr.Sdf.Path, 
        source_prim_path: Optional[str | pxr.Sdf.Path] = None,
        position: Optional[pxr.Gf.Vec3d | Tuple[float, float, float] | List[float]] = None,
        orientation: Optional[pxr.Gf.Quatd | Tuple[float, float, float, float] | List[float]] = None,
    ) -> None:
    if isinstance(source_file_path, str):
        source_file_path = Path(source_file_path)
    if not isinstance(source_file_path, Path):
        raise TypeError("source_file_path must be a str | Path")
    if not os.path.isfile(source_file_path):
        raise FileExistsError(f"source_file_path {source_file_path} does not exist")
    
    if isinstance(destination_prim_path, str):
        destination_prim_path = pxr.Sdf.Path(destination_prim_path)
    if not isinstance(destination_prim_path, pxr.Sdf.Path):
        raise TypeError("destination_prim_path must be a str | pxr.Sdf.Path")
    
    if source_prim_path is not None:
        if isinstance(source_prim_path, str):
            source_prim_path = pxr.Sdf.Path(source_prim_path)
        if not isinstance(source_prim_path, pxr.Sdf.Path):
            raise TypeError("source_prim_path must be an Optional[str | pxr.Sdf.Path]")
        
    if position is not None:
        if isinstance(position, (tuple, list)):
            if len(position) != 3:
                raise ValueError("position must have a length of 3")
            position = pxr.Gf.Vec3d(*position)
        if not isinstance(position, pxr.Gf.Vec3d):
            raise TypeError("position must be an Optional[pxr.Gf.Vec3d | Tuple[float, float, float] | List[float]] ")
        
    if orientation is not None:
        if isinstance(orientation, (tuple, list)):
            if len(orientation) != 4:
                raise ValueError("orientation must have a length of 4")
            orientation = pxr.Gf.Quatd(*orientation)
        if not isinstance(orientation, pxr.Gf.Quatd):
            raise TypeError("orientation must be an Optional[pxr.Gf.Quatd | Tuple[float, float, float, float] | List[float]]")
    
    source_stage: pxr.Usd.Stage = pxr.Usd.Stage.Open(str(source_file_path), pxr.Usd.Stage.LoadAll)
    source_layer = source_stage.GetRootLayer()

    # Get prim by path or use default prim
    if source_prim_path is None:
        source_prim = source_stage.GetDefaultPrim()
        if not source_prim.IsValid():
            raise ValueError(f"No default prim set in {source_file_path}")
        source_prim_path: pxr.Sdf.Path = source_prim.GetPath()
    else:
        source_prim = source_stage.GetPrimAtPath(source_prim_path)
        if not source_prim.IsValid():
            raise ValueError(f"Prim at source_prim_path {source_prim_path} in {source_file_path} is not valid")
        source_prim_path: pxr.Sdf.Path = source_prim.GetPath()

    # # 3. Ensure the parent prim exists on the destination stage.
    # #    CopySpec will NOT create missing parent specs.
    # dest_sdf_path = pxr.Sdf.Path(request.path)
    # parent_path: pxr.Sdf.Path = dest_sdf_path.GetParentPath()
    # parent_is_root = str(parent_path) == "/"

    destination_stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
    destination_layer = destination_stage.GetRootLayer()
    destination_parent_path: pxr.Sdf.Path = destination_prim_path.GetParentPath()

    # if not destination_stage.GetPrimAtPath(parent_path) and not parent_is_root:
    #     destination_stage.DefinePrim(parent_path, "Xform")

    
    

    def copy_prim(src_prim_path: pxr.Sdf.Path, parent_path: pxr.Sdf.Path, root: bool = False):
        """Recursively copy a prim and its children."""
        # a = parent_path.AppendChild(src_prim_path.name)
        # b = str(src_prim_path)
        # target_path = parent_path.AppendChild(src_prim_path.name) if str(src_prim_path) != "." else parent_path
        if root:
            target_path = parent_path
        else:
            target_path = parent_path.AppendChild(src_prim_path.name)

        # Create the prim in target if needed and copy the full spec
        pxr.Sdf.CreatePrimInLayer(destination_layer, target_path)
        pxr.Sdf.CopySpec(source_layer, src_prim_path, destination_layer, target_path)
        
        # Recurse for children
        source_prim = source_stage.GetPrimAtPath(src_prim_path)
        for child in source_prim.GetChildren():
            copy_prim(child.GetPath(), target_path)

    # Perform the copy (start from source root or default prim)
    copy_prim(source_prim_path, destination_prim_path, True)
    #copy_prim(src_prim_path, pxr.Sdf.Path(parent_path))

    prim = destination_stage.GetPrimAtPath(destination_prim_path)
    xformable = pxr.UsdGeom.Xformable(prim)
    xformable.ClearXformOpOrder()
    if position is not None:
        xformable.AddTranslateOp().Set(position)
    # if orientation is not None:
    #     xformable.AddOrientOp().Set(orientation)
    pass

@_asset_router.post(
    "/spawn",
    summary="Spawn an asset in the environment."
)
async def asset_spawn(request: AssetSpawnRequest) -> None:
    
    async def run(request: AssetSpawnRequest):
        dir_path = ConfigurationService().configuration.assets_directory_path
        asset_path = Path(os.path.join(dir_path, request.id))
        if not os.path.isdir(asset_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Environment with name {request.id} not found",
            )
        
        usd_path =  get_environment_usd_path(asset_path)
        if usd_path is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"No USD file found for environment {request.id}",
            )
        
        spawn_prim(usd_path, request.path, None, request.position, request.orientation)
    
    return await ApiController().run_coro_on_isaac_loop(run(request))


@_asset_router.delete(
    "/remove",
    summary="Remove an asset."
)
async def asset_remove(request: AssetIdRequest) -> None:
    dir_path = ConfigurationService().configuration.assets_directory_path
    target_dir = os.path.join(dir_path, request.id)
    if not os.path.isdir(target_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with name {request.id} not found",
        )
    shutil.rmtree(target_dir)


@_asset_router.post(
    "/upload",
    summary="Upload a zipped asset."
)
async def asset_upload(file: UploadFile = File(...)) -> None:
    suffix = Path(file.filename).suffix.lower()
    ALLOWED_MIME = {"application/zip", "application/x-zip-compressed"}
    ALLOWED_EXTS = {".zip"}
    if file.content_type not in ALLOWED_MIME or suffix not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Upload must be a ZIP file. Got content_type={file.content_type}, ext={suffix}",
        )
    
    asset_directory_path = ConfigurationService().configuration.assets_directory_path
    Path(asset_directory_path).mkdir(exist_ok=True)

    asset_path = os.path.join(asset_directory_path, Path(file.filename).stem)

    # # Block upload due to existing asset with the same name
    # if os.path.exists(asset_path):
    #     raise HTTPException(
    #         status_code=status.HTTP_409_CONFLICT,
    #         detail=f"Asset already exists",
    #     )

    # Save file to temp directory
    tmp_file_dir = Path(tempfile.mkdtemp()).resolve()
    tmp_file_path = Path(os.path.join(tmp_file_dir, file.filename))
    tmp_extracted_dir = None
    try:
        with open(tmp_file_path, "wb") as tmp:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                tmp.write(chunk)
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not save uploaded file",
        )
    
    # Extract the temp zip file to a temp directory (this also validates the package)
    try:
        tmp_extracted_file = extract(tmp_file_path)
        tmp_extracted_dir = Path(tmp_extracted_file).parent
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        if tmp_extracted_dir is not None:
            if os.path.exists(tmp_extracted_dir):
                shutil.rmtree(tmp_extracted_dir)
        if isinstance(ex, PackageValidationError):
            raise HTTPException(
                status_code=422,  # Unprocessable Content (constant name differs across Starlette versions)
                detail={"message": f"Asset {Path(file.filename).stem} is not valid; fix these issues and upload again", "errors": ex.errors},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not extract uploaded file",
        )

    if os.path.exists(tmp_file_dir):
        shutil.rmtree(tmp_file_dir)

    # Remove existing asset and overwrite (only after the upload validated)
    if os.path.exists(asset_path):
        if os.path.isfile(asset_path):
            os.unlink(asset_path)
        if os.path.isdir(asset_path):
            shutil.rmtree(asset_path)

    # Move the temp directory to the assets directory
    try:
        shutil.move(tmp_extracted_file, asset_directory_path)
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        if tmp_extracted_dir is not None:
            if os.path.exists(tmp_extracted_dir):
                shutil.rmtree(tmp_extracted_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not move uploaded file to assets directory",
        )

    if os.path.exists(tmp_extracted_dir):
        shutil.rmtree(tmp_extracted_dir)










import copy
import dataclasses
import time
import io
from http.client import responses
from queue import Queue
from typing import List, Dict, Any
from threading import Thread
import tempfile
import shutil
import importlib.util
from pydantic import Json

from fastapi import FastAPI, HTTPException, status, UploadFile, File, APIRouter, Form
from fastapi.responses import FileResponse, StreamingResponse
#import models
from trimesh import Trimesh
import numpy as np
import uuid
import pydantic
from abc import ABC, abstractmethod


try:
    from . import __version__
except Exception as ex:
    __version__ = "0.0.0"

from .geometry import *
from .process import *
from .lib.geometry import FaceRefiner
from .lib.models import FaceGeometry
from .lib.coprocessorclient import *

routers: List[APIRouter] = []



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




_application_router = APIRouter(
    prefix="/application",
    tags=["Application"]
)
routers.append(_application_router)

@_application_router.get(
    "/ping",
    summary="Ping the application."
)
async def application_ping() -> None:
    return

@_application_router.get(
    "/version",
    summary="Get the application version."
)
def application_version():
    return __version__




_geometry_router = APIRouter(
    prefix="/geometry",
    tags=["Geometry"]
)
routers.append(_geometry_router)

@_geometry_router.post(
    "/convert/stl_to_face",
    summary="Convert a file to a face mesh",
    description="Convert",
    openapi_extra=_get_json_with_file_openapi_extra([("request", GeometryConvertStlToFaceRequest)])
)
async def geometry_convert_stl_to_face(request: Json[GeometryConvertStlToFaceRequest], file: UploadFile = File(...)) -> GeometryConvertStlToFaceResponse:
    #request = GeometryConvertStlToFaceRequest.model_validate_json(request)

    suffix = Path(file.filename).suffix.lower()
    ALLOWED_MIME = {"application/octet-stream"}
    ALLOWED_EXTS = {".stl"}
    if file.content_type not in ALLOWED_MIME or suffix not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Upload must be a STL file. Got content_type={file.content_type}, ext={suffix}",
        )

    # Save file to temp directory
    tmp_file_dir = Path(tempfile.mkdtemp()).resolve()
    tmp_file_path = Path(os.path.join(tmp_file_dir, file.filename))
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
    
    try:
        _mesh = trimesh.load(tmp_file_path)
        if isinstance(_mesh, trimesh.Scene):
            # Flatten all meshes in the scene into a single Trimesh
            _mesh: trimesh.Trimesh = trimesh.util.concatenate(
                [trimesh.Trimesh(vertices=m.vertices, faces=m.faces)
                for m in _mesh.dump()]
            )
        # Split meshes in case there are multiple bodies
        meshes = _mesh.split() # scipy required for this
        if len(meshes) == 0:
            meshes = [_mesh] # Not sure why this happens, but sometimes split produces empty list
        # Repair meshes
        meshes = [repair_mesh(m) for m in meshes]
    except Exception as ex:
        if tmp_file_dir is not None:
            if os.path.exists(tmp_file_dir):
                shutil.rmtree(tmp_file_dir)
        if tmp_file_path.suffix.lower() in (".step", ".stp") and importlib.util.find_spec("cascadio") is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not import uploaded file {file.filename}: STEP files need the optional STEP support "
                       f"(install the coprocessor with the 'step' extra: amt-dtp-iscp[step]).",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not import uploaded file {file.filename}: {ex}",
        )
    
    face_geometries: List[FaceGeometry] = []
    for mesh in meshes:

        extents = mesh.extents

        if request.units is not None:
            mesh.units = request.units
        else:
            try:
                mesh.units = trimesh.units.units_from_metadata(mesh, guess=False)
            except Exception as ex:
                # 25 mm  =     1 inch =  0.08 feet
                # 25 m   =   984 inch = 82 feet
                # 50 mm  =     2 inch =  0.16 feet
                # 50 m   = 1,969 inch = 164 feet
                # 75 mm  =     4 inch =  0.32 feet
                # 75 m   = 3,069 inch = 248 feet
                # 100 mm =     4 inch =  0.38 feet
                # 100 m  = 3,969 inch = 348 feet

                max_extents = float(max(extents))
                if max_extents >= 50.0: # Maximum part size: 164 feet / 54 yards
                    # If it's bigger than a half football field in m, it's probably mm
                    mesh.units = "mm"
                else:
                    mesh.units = "m"

        mesh = mesh.convert_units("m")

        face_geometries.append(
            FaceGeometry(
                vertices=mesh.vertices.tolist(),
                faces=mesh.faces.tolist(),
                vertex_normals=mesh.vertex_normals.tolist(),
            )
        )
    
    return GeometryConvertStlToFaceResponse(face_geometries=face_geometries)

@_geometry_router.post(
    "/convert/face_to_stl",
    summary="Convert a face mesh to a stl file",
)
async def geometry_convert_face_to_stl(request: GeometryConvertFaceToStlRequest) -> StreamingResponse:
    vertices = np.array(request.face_geometry.vertices, dtype=np.float64)
    faces = np.array(request.face_geometry.faces, dtype=np.int64)
    vertex_normals = None
    if request.face_geometry.normals is not None:
        vertex_normals = np.array(request.face_geometry.normals, dtype=np.float64)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=vertex_normals, process=True)
    #mesh = mesh.apply_scale(1_000)
    mesh.units = "m"
    if request.units is not None:
        mesh: trimesh.Trimesh = mesh.convert_units(request.units)

    buffer = io.BytesIO()
    mesh.export(buffer, file_type="stl")
    #mesh.export(buffer, file_type="stl_ascii")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=mesh.stl"}
    )

    # Save file to temp directory
    tmp_file_dir = Path(tempfile.mkdtemp()).resolve()
    tmp_file_name = "file.stl"
    tmp_file_path = Path(os.path.join(tmp_file_dir, tmp_file_name))

    return FileResponse(
        path=tmp_file_path,
        filename=tmp_file_name,        # Name shown in the browser's save dialog
        media_type="application/octet-stream"  # MIME type
    )

@_geometry_router.post(
    "/refine",
    summary="Refine a mesh to have a specified maximum face edge length",
)
def geometry_refine(request: GeometryRefineRequest) -> FaceGeometry:
    return FaceRefiner.refine(request.face_geometry, request.maximum_edge_length)


difference_i = 0
@_geometry_router.post(
    "/difference",
    summary="Perform a boolean difference (i.e., subtract) operation",
)
def difference(request: GeometryRequest) -> GeometryResponse:
    print("----------Start")

    # FaceGeometry carries normals (not vertex_normals) and no transform, so use geometries_to_tmesh
    workpiece = geometries_to_tmesh(request.workpiece)
    tool = geometries_to_tmesh(request.tools)

    fixed_workpiece = repair_mesh(workpiece, "Workpiece")
    fixed_tool = repair_mesh(tool, "Tool")

    fixed_intersection_volume = fixed_workpiece.intersection(fixed_tool, engine="manifold", check_volume=False).volume
    print(f"Fixed Intersection Volume: {fixed_intersection_volume:.6f}")

    mesh = fixed_workpiece.difference(fixed_tool, engine="manifold", check_volume=False)
    if mesh.is_empty:
        raise Exception("Produced Empty Mesh from Difference")
    meshes = mesh.split() # scipy required for this
    if len(meshes) == 0:
        meshes = [mesh] # Not sure why this happens, but sometimes split produces empty list
    geometry_objects = []

    for mesh in meshes:
        if mesh.is_empty:
            raise Exception("empty mesh")

        mesh = repair_mesh(mesh, "Difference")


        if not mesh.is_watertight:
            raise Exception("not watertight")
        if len(mesh.vertices) == 0:
            raise Exception("empty vertices")
        if len(mesh.faces) == 0:
            raise Exception("empty faces")
        if len(mesh.vertex_normals) == 0:
            raise Exception("empty vertex_normals")

        # Inverse transform to move from world back to local
        # transform_matrix = request.workpieces[0].transform_matrix
        # matrix_inv = np.linalg.inv(transform_matrix)
        # mesh = mesh.apply_transform(matrix_inv)

        vertices = mesh.vertices.tolist()
        faces = mesh.faces.tolist()
        vertex_normals = mesh.vertex_normals.tolist()



        geometry_objects.append(FaceGeometry(
            vertices=vertices,
            faces=faces,
            normals=vertex_normals
        ))
    response = GeometryResponse(
        workpieces=geometry_objects
    )

    print("----------End")
    return response

@_geometry_router.post(
    "/union",
    summary="Perform a boolean union (i.e., add) operation",
)
def union(request: GeometryRequest) -> GeometryResponse:
    workpiece = objects_to_tmesh(request.workpieces)
    tool = objects_to_tmesh(request.tools)
    mesh = workpiece.difference(tool, engine="manifold", check_volume=False)
    meshes = mesh.split()
    geometry_objects = []
    for mesh in meshes:
        vertices = mesh.vertices.tolist()
        faces = mesh.faces.tolist()
        geometry_objects.append(FaceGeometry(
            vertices=vertices,
            faces=faces,
            transform_matrix=None
        ))
    response = GeometryResponse(
        workpieces=geometry_objects
    )
    return response






_process_router = APIRouter(
    prefix="/process",
    tags=["Process"]
)
routers.append(_process_router)

@_process_router.post(
    "/start/additive",
    summary="Start an additive boolean process",
)
def process_start_additive(request: ProcessStartRequest) -> ProcessIdResponse:
    return process_start(request, AdditiveProcessData)

@_process_router.post(
    "/start/subtractive",
    summary="Start a subtractive boolean process.",
)
def process_start_subtractive(request: ProcessStartRequest) -> ProcessIdResponse:
    return process_start(request, SubtractiveProcessData)

@_process_router.post(
    "/step",
    summary="Add a step to a process.",
)
def process_step(request: ProcessStepRequest) -> None:
    return process_step(request)

@_process_router.post(
    "/is_complete",
    summary="Check whether backend processing of a process is complete.",
)
def process_is_complete(request: ProcessIdRequest) -> ProcessBooleanResponse:
    return process_is_complete(request)

@_process_router.post(
    "/finalize",
    summary="Get the final geometry of a completed process.",
)
def process_finalize(request: ProcessIdRequest) -> ProcessFinalizeResponse:
    return process_finalize(request)

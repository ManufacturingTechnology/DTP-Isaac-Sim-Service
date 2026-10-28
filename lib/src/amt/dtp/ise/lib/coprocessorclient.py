import asyncio
from typing import List
import requests
from pydantic import BaseModel
import time
import json
import io

#from .. import __version__
from .models import *


# class GeometryObject(BaseModel):
#     vertices: List[List[float]]
#     faces: List[List[int]]
#     transform_matrix: List[List[float]] | None
#     vertex_normals: List[List[float]] | None = None

class GeometryConvertStlToFaceRequest(BaseModel):
    units: Literal[None, "m", "meter", "mm", "millimeter", "in", "inches"] = Field(
        None,
        description="The units of measure of the input STL file."
    )

class GeometryConvertStlToFaceResponse(BaseModel):
    face_geometries: List[FaceGeometry] = Field(
        description="The converted geometries in meters."
    )

class GeometryConvertFaceToStlRequest(BaseModel):
    face_geometry: FaceGeometry = Field(
        description="The geometry to convert to a STL file."
    )
    units: Literal["m", "meter", "mm", "millimeter"] = Field(
        "m",
        description="The units of measure of the output STL file."
    )

class GeometryRequest(BaseModel):
    workpiece: List[FaceGeometry]
    tools: List[FaceGeometry]


class GeometryResponse(BaseModel):
    workpieces: List[FaceGeometry]



class GeometryRefineRequest(BaseModel):
    face_geometry: FaceGeometry
    maximum_edge_length: float





# class ObjectGeometry(BaseModel):
#     vertices: List[List[float]]
#     faces: List[List[int]]
#     vertex_normals: List[List[float]]

class ObjectTransform(BaseModel):
    transform_matrix: List[List[float]]




class ProcessStartRequest(BaseModel):
    workpiece: List[FaceGeometry] # Multiple objects in a single workpiece
    tools: List[List[FaceGeometry]] # Multiple objects per tool

class ProcessIdRequest(BaseModel):
    id: str

class ProcessIdResponse(BaseModel):
    id: str

class ProcessStepRequest(BaseModel):
    id: str
    workpiece: ObjectTransform
    tools: List[ObjectTransform]

class ProcessBooleanResponse(BaseModel):
    value: bool

class ProcessFinalizeResponse(BaseModel):
    workpieces: List[FaceGeometry]




class CoProcessorClient:

    def __init__(self, base_url = "http://127.0.0.1:8013/"):
        # use 127.0.0.1 instead of localhost: on Windows 'localhost' may try IPv6 name
        #   resolution first and stall ~2s per call on the fallback to IPv4. Sanitize any
        #   caller-supplied localhost too so an override can't reintroduce the lag.
        base_url = base_url.replace("localhost", "127.0.0.1")
        self._base_url = base_url.rstrip("/") + "/api"

    @property
    def version(self) -> str:
        try:
            # lib version
            from .. import __version__
        except Exception as ex:
            # ISE version
            from ..__version__ import __version__
        return __version__

    def application_ping(self) -> None:
        response: requests.Response = requests.delete(f"{self._base_url}/application/ping")
        response.raise_for_status()

    # def application_exit(self) -> None:
    #     response: requests.Response = requests.delete(f"{self._base_url}/application/exit")
    #     response.raise_for_status()

    def application_version(self) -> str:
        response: requests.Response = requests.get(f"{self._base_url}/application/version")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return response.text

    def geometry_convert_stl_to_face(self, path: str | Path | bytes, request: GeometryConvertStlToFaceRequest) -> GeometryConvertStlToFaceResponse:
        if isinstance(path, Path):
            path = str(path)
        if not isinstance(path, (str, bytes)):
            raise TypeError("file_path must be a str | Path | bytes")
        if isinstance(path, str) and not os.path.exists(path):
            raise FileExistsError("file_path does not exist")
        

        if not isinstance(request, GeometryConvertStlToFaceRequest):
            raise TypeError("request must be a GeometryConvertStlToFaceRequest")

        if isinstance(path, bytes):
            response: requests.Response = requests.post(
                f"{self._base_url}/geometry/convert/stl_to_face",
                files={"file": ("file.stl", io.BytesIO(path), "application/octet-stream")},
                data={
                    # "filename": filename,
                    # "content_type": content_type,
                    # "file_size": len(file_bytes),
                    'request': request.model_dump_json()
                }
                #json=request.model_dump(),
            )
        else:
            name = Path(path).name
            with open(path, "rb") as file:
                response: requests.Response = requests.post(
                    f"{self._base_url}/geometry/convert/stl_to_face",
                    files={"file": (name, file, "application/octet-stream")},
                    data={'request': request.model_dump_json()}
                    #json=request.model_dump(),
                )

        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return GeometryConvertStlToFaceResponse.model_validate_json(response.content)
    
    def geometry_convert_face_to_stl(self, request: GeometryConvertFaceToStlRequest) -> bytes:
        if not isinstance(request, GeometryConvertFaceToStlRequest):
            raise TypeError("request must be a GeometryConvertFaceToStlRequest")
        
        buffer = io.BytesIO()
        with requests.post(
            f"{self._base_url}/geometry/convert/face_to_stl", 
            stream=True, 
            json=request.model_dump()
        ) as response:
            response.raise_for_status()
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: # filter out keep-alive new chunks
                    buffer.write(chunk)

        buffer.seek(0) # Reset pointer for reading
        return buffer.getvalue()

    def geometry_refine(self, request: GeometryRefineRequest) -> FaceGeometry:
        response: requests.Response = requests.post(
            f"{self._base_url}/geometry/refine",
            json=request.model_dump()
        )
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return FaceGeometry.model_validate_json(response.content)
    
    async def geometry_refine_async(self, request: GeometryRefineRequest) -> FaceGeometry:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{self._base_url}/geometry/refine",
                json=request.model_dump()
            )
        )
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return FaceGeometry.model_validate_json(response.content)

    def difference(self, request: GeometryRequest) -> GeometryResponse:
        response = requests.post(
            f"{self._base_url}/geometry/difference",
            json=request.model_dump()
        )
        response.raise_for_status()
        return GeometryResponse.model_validate_json(response.content)

    def union(self, request: GeometryRequest) -> GeometryResponse:
        response = requests.post(
            f"{self._base_url}/geometry/union",
            json=request.model_dump()
        )
        response.raise_for_status()
        return GeometryResponse.model_validate_json(response.content)
    
    def process_start_additive(self, request: ProcessStartRequest) -> ProcessIdResponse:
        response = requests.post(
            f"{self._base_url}/process/start/additive",
            json=request.model_dump()
        )
        response.raise_for_status()
        return ProcessIdResponse.model_validate_json(response.content)

    def process_start_subtractive_start(self, request: ProcessStartRequest) -> ProcessIdResponse:
        response = requests.post(
            f"{self._base_url}/process/start/subtractive",
            json=request.model_dump()
        )
        response.raise_for_status()
        return ProcessIdResponse.model_validate_json(response.content)

    def process_step(self, request: ProcessStepRequest) -> None:
        response = requests.post(
            f"{self._base_url}/process/step",
            json=request.model_dump()
        )
        response.raise_for_status()

    def process_is_complete(self, request: ProcessIdRequest) -> ProcessBooleanResponse:
        response = requests.post(
            f"{self._base_url}/process_is_complete",
            json=request.model_dump()
        )
        response.raise_for_status()
        return ProcessBooleanResponse.model_validate_json(response.content)

    def process_finalize(self, request: ProcessIdRequest) -> ProcessFinalizeResponse:
        response = requests.post(
            f"{self._base_url}/process/finalize",
            json=request.model_dump()
        )
        response.raise_for_status()
        return ProcessFinalizeResponse.model_validate_json(response.content)


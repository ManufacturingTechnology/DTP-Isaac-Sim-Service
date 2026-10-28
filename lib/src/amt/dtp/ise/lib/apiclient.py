import io
import os

from pydantic import BaseModel
from typing import Optional, List, Tuple
from pathlib import Path
import requests

from .models import *
from .environment import *

class TimelineInfoReponse(BaseModel):
    is_playing: bool
    is_paused: bool
    is_stopped: bool
    current_time: float
    timeline_time_step: float
    physics_time_step: Optional[float]
    error: Optional[str]

class AssetIdRequest(BaseModel):
    id: str

class AssetSpawnRequest(BaseModel):
    id: str
    path: Optional[str] = None
    position: Tuple[float, float, float] | List[float] = [0.0, 0.0, 0.0]
    orientation: Tuple[float, float, float, float] | List[float] = [1.0, 0.0, 0.0, 0.0]

class EnvironmentInfoResponse(BaseModel):
    file_path: Optional[str]

class EnvironmentIdRequest(BaseModel):
    id: str

class WorkpieceSpawnCubeRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece."
    )

    x: float = Field(
        1.0,
        description="The x dimension of the cube"
    )
    @field_validator("x")
    @classmethod
    def validate_x(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("x must be greater than or equal to 0.0001")
        return v

    y: float = Field(
        1.0,
        description="The y dimension of the cube"
    )
    @field_validator("y")
    @classmethod
    def validate_y(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("y must be greater than or equal to 0.0001")
        return v

    z: float = Field(
        1.0,
        description="The z dimension of the cube"
    )
    @field_validator("z")
    @classmethod
    def validate_z(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("z must be greater than or equal to 0.0001")
        return v

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    orientation: List[float] = Field(
        [1.0, 0.0, 0.0, 0.0],
        description="The [w,x,y,z] quaternion position of the workpiece"
    )
    @field_validator("orientation")
    @classmethod
    def validate_orientation(cls, v: List[float]) -> float:
        if len(v) != 4:
            raise ValueError("orientation must be a length of 4")
        return v
    
    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )
    
class WorkpieceSpawnCylinderRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece."
    )

    xy_diameter: float = Field(
        1.0,
        description="The xy diameter of the cylinder"
    )
    @field_validator("xy_diameter")
    @classmethod
    def validate_xy_diameter(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("xy_diameter  must be greater than or equal to 0.0001")
        return v
    
    z_length: float = Field(
        1.0,
        description="The z length of the cylinder"
    )
    @field_validator("z_length")
    @classmethod
    def validate_z_length(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("z_length must be greater than or equal to 0.0001")
        return v
    
    degree_step: float = Field(
        5.0,
        description="The number of degrees between each step along the circumference (smaller => finer mesh)"
    )
    @field_validator("degree_step")
    @classmethod
    def validate_degree_step(cls, v: List[float]) -> float:
        if v < 0.1 or v > 90.0:
            raise ValueError("degree_step must be between 0.1 and 90.0")
        return v

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    orientation: List[float] = Field(
        [1.0, 0.0, 0.0, 0.0],
        description="The [w,x,y,z] quaternion position of the workpiece"
    )
    @field_validator("orientation")
    @classmethod
    def validate_orientation(cls, v: List[float]) -> float:
        if len(v) != 4:
            raise ValueError("orientation must be a length of 4")
        return v
    
    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )

class WorkpieceSpawnSphereRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece."
    )

    diameter: float = Field(
        1.0,
        description="The diameter of the sphere"
    )
    @field_validator("diameter")
    @classmethod
    def validate_diameter(cls, v: List[float]) -> float:
        if v < 0.0001:
            raise ValueError("diameter must be greater than or equal to 0.0001")
        return v
    
    degree_step: float = Field(
        5.0,
        description="The number of degrees between each step along the circumference (smaller => finer mesh)"
    )
    @field_validator("degree_step")
    @classmethod
    def validate_degree_step(cls, v: List[float]) -> float:
        if v < 0.1 or v > 90.0:
            raise ValueError("degree_step must be between 0.1 and 90.0")
        return v

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )

class WorkpieceSpawnFaceRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece.",
    )

    geometry: FaceGeometry = Field(
        description="The geometry of the workpiece.",
    )

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    orientation: List[float] = Field(
        [1.0, 0.0, 0.0, 0.0],
        description="The [w,x,y,z] quaternion position of the workpiece"
    )
    @field_validator("orientation")
    @classmethod
    def validate_orientation(cls, v: List[float]) -> float:
        if len(v) != 4:
            raise ValueError("orientation must be a length of 4")
        return v
    
    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )


class WorkpieceSpawnStlRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece.",
    )

    units: Literal[None, "m", "meter", "mm", "millimeter"] = Field(
        None,
        description="The units of measure of the STL file."
    )

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    orientation: List[float] = Field(
        [1.0, 0.0, 0.0, 0.0],
        description="The [w,x,y,z] quaternion position of the workpiece"
    )
    @field_validator("orientation")
    @classmethod
    def validate_orientation(cls, v: List[float]) -> float:
        if len(v) != 4:
            raise ValueError("orientation must be a length of 4")
        return v
    
    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )

class WorkpieceSpawnPointRequest(BaseModel):
    material: MaterialProperties = Field(
        description="The material properties of the workpiece.",
    )

    geometry: PointGeometry = Field(
        description="The geometry of the workpiece.",
    )

    position: List[float] = Field(
        [0.0, 0.0, 0.0],
        description="The [x,y,z] vector position of the workpiece"
    )
    @field_validator("position")
    @classmethod
    def validate_position(cls, v: List[float]) -> float:
        if len(v) != 3:
            raise ValueError("position must be a length of 3")
        return v
    
    orientation: List[float] = Field(
        [1.0, 0.0, 0.0, 0.0],
        description="The [w,x,y,z] quaternion position of the workpiece"
    )
    @field_validator("orientation")
    @classmethod
    def validate_orientation(cls, v: List[float]) -> float:
        if len(v) != 4:
            raise ValueError("orientation must be a length of 4")
        return v

    model: Optional[WorkpieceModel] = Field(
        None,
        description="The metadata model of the workpiece."
    )


def _raise_for_upload_status(response: requests.Response) -> None:
    # Like raise_for_status, but a rejected package (HTTP 422 from the upload routes) raises an
    # HTTPError whose message lists the server's validation errors, so they can be fixed.
    if response.status_code == 422:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict) and isinstance(detail.get("errors"), list):
            lines = "\n".join(f" - {e}" for e in detail["errors"])
            raise requests.HTTPError(f"{detail.get('message', 'upload rejected')}:\n{lines}", response=response)
    response.raise_for_status()


class ApiClient:
    
    def __init__(self, base_url = "http://127.0.0.1:8012/"):
        # use 127.0.0.1 instead of localhost, because localhost may try IPV6 name resolution first and fail,
        #   which in turns causes slow performance, taking ~2 seconds per call on windows. Sanitize any
        #   caller-supplied localhost too so an override can't reintroduce the lag.
        base_url = base_url.replace("localhost", "127.0.0.1")
        self._base_url = base_url.rstrip("/") + "/api"

    @property
    def version(self) -> str:
        try:
            # lib version, when loaded from library
            from . import __version__
        except Exception as ex:
            # ISE version, when loaded from isaac sim
            from ..__version__ import __version__
        return __version__

    def application_exit(self) -> None:
        response: requests.Response = requests.delete(f"{self._base_url}/application/exit")
        response.raise_for_status()

    def application_version(self) -> str:
        response: requests.Response = requests.get(f"{self._base_url}/application/version")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return response.text.lstrip("\"").rstrip("\"")

    def application_working_directory(self) -> str:
        response: requests.Response = requests.get(f"{self._base_url}/application/working_directory")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return response.text.lstrip("\"").rstrip("\"")


    def extension_ping(self) -> None:
        response: requests.Response = requests.get(f"{self._base_url}/extension/ping")
        response.raise_for_status()

    def extension_version(self) -> str:
        response: requests.Response = requests.get(f"{self._base_url}/extension/version")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return response.text.lstrip("\"").rstrip("\"")

    def extension_configuration_path(self) -> str:
        response: requests.Response = requests.get(f"{self._base_url}/extension/configuration/path")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return response.text.lstrip("\"").rstrip("\"")
    
    def extension_configuration_resolved(self) -> ApplicationConfiguration:
        response: requests.Response = requests.get(f"{self._base_url}/extension/configuration/resolved")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return ApplicationConfiguration.model_validate_json(response.content)

    def extension_configuration_global(self) -> ApplicationConfiguration:
        response: requests.Response = requests.get(f"{self._base_url}/extension/configuration/global")
        response.raise_for_status()
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return ApplicationConfiguration.model_validate_json(response.content)

    def extension_configuration_environment(self) -> Optional[ApplicationConfiguration]:
        response: requests.Response = requests.get(f"{self._base_url}/extension/configuration/environment")
        response.raise_for_status()
        if response.text is None:
            return None # TODO: need to test this.
        if not isinstance(response.text, str):
            raise TypeError("api returned non-string")
        return ApplicationConfiguration.model_validate_json(response.content)


    def timeline_info(self) -> TimelineInfoReponse:
        response: requests.Response = requests.get(f"{self._base_url}/timeline/info")
        response.raise_for_status()
        return TimelineInfoReponse.model_validate_json(response.content)
    
    def timeline_play(self) -> None:
        response: requests.Response = requests.post(f"{self._base_url}/timeline/play")
        response.raise_for_status()
    
    def timeline_pause(self) -> None:
        response: requests.Response = requests.post(f"{self._base_url}/timeline/pause")
        response.raise_for_status()
    
    def timeline_stop(self) -> None:
        response: requests.Response = requests.post(f"{self._base_url}/timeline/stop")
        response.raise_for_status()
    

    def environment_info(self) -> EnvironmentInfoResponse:
        response: requests.Response = requests.get(f"{self._base_url}/environment/info")
        response.raise_for_status()
        return EnvironmentInfoResponse.model_validate_json(response.content)
    
    def environment_clear(self) -> None:
        response: requests.Response = requests.delete(f"{self._base_url}/environment/clear")
        response.raise_for_status()

    def environment_close(self) -> None:
        response: requests.Response = requests.post(f"{self._base_url}/environment/close")
        response.raise_for_status()
    
    def environment_list(self) -> List[str]:
        response: requests.Response = requests.get(f"{self._base_url}/environment/list")
        response.raise_for_status()
        environments: List[str] = response.json()
        if not isinstance(environments, list) or not all(isinstance(t, str) for t in environments):
            raise TypeError("api returned a non-List[str]")
        return environments
    
    def environment_open(self, id: str) -> None:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        response: requests.Response = requests.post(
            f"{self._base_url}/environment/open",
            json=EnvironmentIdRequest(
                id=id
            ).model_dump()
        )
        response.raise_for_status()

    def environment_remove(self, id: str) -> None:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        response: requests.Response = requests.delete(
            f"{self._base_url}/environment/remove",
            json=EnvironmentIdRequest(
                id=id
            ).model_dump()
        )
        response.raise_for_status()
    
    def environment_upload(self, path: str | Path) -> None:
        if isinstance(path, Path):
            path = str(path)
        if not isinstance(path, str):
            raise TypeError("file_path must be a str")
        if not os.path.exists(path):
            raise FileExistsError("file_path does not exist")
        name = Path(path).name
        tmp_path = None
        upload_path = path

        if os.path.isdir(path):
            name += ".zip"
            tmp_path = archive(Path(path))
            upload_path = tmp_path

        with open(upload_path, "rb") as f:
            response: requests.Response = requests.post(
                f"{self._base_url}/environment/upload",
                files={"file": (name, f, "application/zip")},
            )

        if tmp_path is not None:
            os.remove(tmp_path)

        _raise_for_upload_status(response)

    def asset_list(self) -> List[str]:
        response: requests.Response = requests.get(f"{self._base_url}/asset/list")
        response.raise_for_status()
        environments: List[str] = response.json()
        if not isinstance(environments, list) or not all(isinstance(t, str) for t in environments):
            raise TypeError("api returned a non-List[str]")
        return environments

    def asset_remove(self, id: str) -> None:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        response: requests.Response = requests.delete(
            f"{self._base_url}/asset/remove",
            json=AssetIdRequest(
                id=id
            ).model_dump()
        )
        response.raise_for_status()

    def asset_spawn(self, id: str, path: str, position: List[float], orientation: List[float] = [1.0, 0.0, 0.0, 0.0]) -> None:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        if not isinstance(path, str):
            raise TypeError("path must be a str")
        if not (isinstance(position, (tuple, float)) or all(isinstance(f, float) for f in position)):
            raise TypeError("position must be a Tuple[float, float, float] | List[float]")
        if len(position) != 3:
            raise ValueError("position must be a Tuple[float, float, float] | List[float]")
        if not (isinstance(orientation, (tuple, float)) or all(isinstance(f, float) for f in orientation)):
            raise TypeError("orientation must be a Tuple[float, float, float, float] | List[float]")
        if len(orientation) != 4:
            raise ValueError("orientation must be a Tuple[float, float, float, float] | List[float]")
        
        response: requests.Response = requests.post(
            f"{self._base_url}/asset/spawn",
            json=AssetSpawnRequest(
                id=id,
                path=path,
                position=position,
                orientation=orientation,
            ).model_dump()
        )
        response.raise_for_status()

    def asset_upload(self, path: str) -> None:
        if not isinstance(path, str):
            raise TypeError("file_path must be a str")
        if not os.path.exists(path):
            raise FileExistsError("file_path does not exist")
        name = Path(path).name
        tmp_path = None
        upload_path = path

        if os.path.isdir(path):
            name += ".zip"
            tmp_path = archive(Path(path))
            upload_path = tmp_path

        with open(upload_path, "rb") as f:
            response: requests.Response = requests.post(
                f"{self._base_url}/asset/upload",
                files={"file": (name, f, "application/zip")},
            )

        if tmp_path is not None:
            os.remove(tmp_path)

        _raise_for_upload_status(response)
        
        
    def workpiece_spawn_rigid_cube(self, request: WorkpieceSpawnCubeRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCubeRequest):
            raise TypeError("specification must be a WorkpieceSpawnCubeRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/cube",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_rigid_cylinder(self, request: WorkpieceSpawnCylinderRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCylinderRequest):
            raise TypeError("specification must be a WorkpieceSpawnCylinderRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/cylinder",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_rigid_sphere(self, request: WorkpieceSpawnSphereRequest) -> None:
        if not isinstance(request, WorkpieceSpawnSphereRequest):
            raise TypeError("specification must be a WorkpieceSpawnSphereRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/sphere",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_rigid_arbitrary(self, request: WorkpieceSpawnFaceRequest) -> None:
        if not isinstance(request, WorkpieceSpawnFaceRequest):
            raise TypeError("specification must be a WorkpieceSpawnFaceRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/arbitrary",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_rigid_stl(self, request: WorkpieceSpawnFaceRequest) -> None:
        if not isinstance(request, WorkpieceSpawnFaceRequest):
            raise TypeError("specification must be a WorkpieceSpawnFaceRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/arbitrary",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_rigid_stl(self, path: str | Path | bytes, request: WorkpieceSpawnStlRequest) -> None:
        if isinstance(path, Path):
            path = str(path)
        if not isinstance(path, (str, bytes)):
            raise TypeError("file_path must be a str | Path | bytes")
        if isinstance(path, str) and not os.path.exists(path):
            raise FileExistsError("file_path does not exist")
        
        if not isinstance(request, WorkpieceSpawnStlRequest):
            raise TypeError("request must be a WorkpieceSpawnStlRequest")

        if isinstance(path, bytes):
            file_tuple = ("file.stl", io.BytesIO(path), "application/octet-stream")
        else:
            with open(path, "rb") as f:
                file_bytes = f.read()
            file_tuple = (Path(path).name, file_bytes, "application/octet-stream")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/rigid/stl",
            files={"file": file_tuple},
            data={'request': request.model_dump_json()}
        )

        response.raise_for_status()

    def workpiece_spawn_deformable_cube(self, request: WorkpieceSpawnCubeRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCubeRequest):
            raise TypeError("specification must be a WorkpieceSpawnCubeRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/deformable/cube",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_deformable_cylinder(self, request: WorkpieceSpawnCylinderRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCylinderRequest):
            raise TypeError("specification must be a WorkpieceSpawnCylinderRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/deformable/cylinder",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_deformable_sphere(self, request: WorkpieceSpawnSphereRequest) -> None:
        if not isinstance(request, WorkpieceSpawnSphereRequest):
            raise TypeError("specification must be a WorkpieceSpawnSphereRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/deformable/sphere",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_deformable_arbitrary(self, request: WorkpieceSpawnFaceRequest) -> None:
        if not isinstance(request, WorkpieceSpawnFaceRequest):
            raise TypeError("specification must be a WorkpieceSpawnFaceRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/deformable/arbitrary",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_particle_cube(self, request: WorkpieceSpawnCubeRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCubeRequest):
            raise TypeError("specification must be a WorkpieceSpawnCubeRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/particle/cube",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_particle_cylinder(self, request: WorkpieceSpawnCylinderRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCylinderRequest):
            raise TypeError("specification must be a WorkpieceSpawnCylinderRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/particle/cylinder",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_particle_sphere(self, request: WorkpieceSpawnSphereRequest) -> None:
        if not isinstance(request, WorkpieceSpawnSphereRequest):
            raise TypeError("specification must be a WorkpieceSpawnSphereRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/particle/sphere",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_particle_arbitrary(self, request: WorkpieceSpawnPointRequest) -> None:
        if not isinstance(request, WorkpieceSpawnPointRequest):
            raise TypeError("specification must be a WorkpieceSpawnPointRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/particle/arbitrary",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_voxel_cube(self, request: WorkpieceSpawnCubeRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCubeRequest):
            raise TypeError("specification must be a WorkpieceSpawnCubeRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/voxel/cube",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_voxel_cylinder(self, request: WorkpieceSpawnCylinderRequest) -> None:
        if not isinstance(request, WorkpieceSpawnCylinderRequest):
            raise TypeError("specification must be a WorkpieceSpawnCylinderRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/voxel/cylinder",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_voxel_sphere(self, request: WorkpieceSpawnSphereRequest) -> None:
        if not isinstance(request, WorkpieceSpawnSphereRequest):
            raise TypeError("specification must be a WorkpieceSpawnSphereRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/voxel/sphere",
            json=request.model_dump()
        )
        response.raise_for_status()

    def workpiece_spawn_voxel_arbitrary(self, request: WorkpieceSpawnPointRequest) -> None:
        if not isinstance(request, WorkpieceSpawnPointRequest):
            raise TypeError("specification must be a WorkpieceSpawnPointRequest")

        response: requests.Response = requests.post(
            f"{self._base_url}/workpiece/spawn/voxel/arbitrary",
            json=request.model_dump()
        )
        response.raise_for_status()

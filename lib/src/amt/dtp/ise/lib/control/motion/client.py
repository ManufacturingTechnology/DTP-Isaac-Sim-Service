"""Client for the kinematics service (``/api/kinematics``; see :mod:`.server`). Also usable against a RobotApi,
which serves the same routes for its robots (read-only solvers). Only depends on requests and pydantic."""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from ..core.client import HttpClient
from ..transducer.models.pose import Pose
from .models import (
    CollisionSpheres,
    CollisionSpheresRequest,
    FkRequest,
    FkResponse,
    IkRequest,
    IkResponse,
    JacobianRequest,
    JacobianResponse,
    KinematicsServiceInfo,
    ReachableResponse,
    SelfCollisionRequest,
    SelfCollisionResponse,
    SolverConfig,
    SolverCreateRequest,
    SolverDescription,
    SolverFrames,
    WorldCollisionRequest,
    WorldCollisionResponse,
    WorldDescription,
)
from .trajectory_models import PathCheckRequest, PathCheckResponse, TrajectoryRequest, TrajectoryResponse


KINEMATICS_PATH = "/kinematics"
Joints = Dict[str, float]
PathArg = Union[str, "os.PathLike[str]"]


class KinematicsClient(HttpClient):
    """Forward and inverse kinematics, trajectory sampling, tool frames, and self-collision over the web.

    ::

        kinematics = KinematicsClient("http://isaac-host:8014")
        arm = kinematics.create_solver("AMT MR-A1.urdf", "AMT MR-A1.xrdf", name="arm").id
        pose = kinematics.fk(arm, [{"Joint1": 0, "Joint2": -20, ...}]).poses[0]
        answer = kinematics.ik(arm, [pose], seed={"Joint1": 0, ...})
        if answer.success:
            joints = answer.results[0].joints        # degrees / meters, by joint name
        kinematics.delete_solver(arm)

    Joint values are by joint name, in degrees (revolute) or meters (prismatic); poses are in the robot base frame.
    """

    def _post(self, path: str, body: Any, model: Any, timeout: Optional[float] = None) -> Any:
        response = self._request("POST", f"{KINEMATICS_PATH}{path}", json=body.model_dump(mode="json", exclude_unset=True),
                                 timeout=timeout)
        return model.model_validate(response.json())

    @staticmethod
    def _solver_path(solver: str) -> str:
        return f"/solvers/{solver}"

    # ---- Solvers ------------------------------------------------------------------------------------

    def kinematics_info(self) -> KinematicsServiceInfo:
        """The backends, limits, and number of solvers."""
        return KinematicsServiceInfo.model_validate(self._request("GET", f"{KINEMATICS_PATH}/info").json())

    def create_solver(
        self,
        urdf_path: Optional[PathArg] = None,
        xrdf_path: Optional[PathArg] = None,
        name: Optional[str] = None,
        tool_frame: Optional[str] = None,
        backend: Optional[str] = None,
        urdf_text: Optional[str] = None,
        xrdf_text: Optional[str] = None,
        upload: str = "multipart",
        timeout: Optional[float] = 60.0,
        fragments: Optional[Sequence[Union[PathArg, str, Dict[str, Any]]]] = None,
        combination: Optional[Union[str, Dict[str, Any]]] = None,
    ) -> SolverDescription:
        """Create a solver from a URDF and an XRDF: files (``urdf_path``, ``xrdf_path``) or text (``urdf_text``,
        ``xrdf_text``). ``upload="multipart"`` (default) uploads them as files; ``"json"`` sends their text (for a
        server without python-multipart). ``name`` becomes the solver id (generated if omitted). With end-effector
        ``fragments`` (fragment files, YAML/JSON text, or dicts) and a ``combination`` (``{stack, active_tcp?}``, text
        or a dict), the service merges them into the XRDF first."""
        if (urdf_path is None) == (urdf_text is None) or (xrdf_path is None) == (xrdf_text is None):
            raise TypeError("pass exactly one of urdf_path / urdf_text and one of xrdf_path / xrdf_text")
        urdf_name = Path(urdf_path).name if urdf_path is not None else "robot.urdf"
        xrdf_name = Path(xrdf_path).name if xrdf_path is not None else "robot.xrdf"
        urdf = Path(urdf_path).read_bytes() if urdf_path is not None else urdf_text.encode("utf-8")
        xrdf = Path(xrdf_path).read_bytes() if xrdf_path is not None else xrdf_text.encode("utf-8")
        path = f"{KINEMATICS_PATH}/solvers"
        pieces: List[Union[str, Dict[str, Any]]] = []
        for item in fragments or []:
            if isinstance(item, dict):
                pieces.append(item)
            elif isinstance(item, os.PathLike) or (isinstance(item, str) and "\n" not in item and Path(item).is_file()):
                pieces.append(Path(item).read_text(encoding="utf-8"))
            else:
                pieces.append(item)
        if upload == "json":
            body = SolverCreateRequest(urdf=urdf.decode("utf-8-sig"), xrdf=xrdf.decode("utf-8-sig"), name=name,
                                       tool_frame=tool_frame, backend=backend, fragments=pieces or None,
                                       combination=combination)
            response = self._request("POST", path, json=body.model_dump(mode="json", exclude_none=True), timeout=timeout)
        elif upload == "multipart":
            fields = {k: v for k, v in (("name", name), ("tool_frame", tool_frame), ("backend", backend)) if v is not None}
            if combination is not None:
                fields["combination"] = combination if isinstance(combination, str) else json.dumps(combination)
            files = [("urdf", (urdf_name, urdf, "application/xml")), ("xrdf", (xrdf_name, xrdf, "application/yaml"))]
            files += [("fragment", (f"fragment{i}.yaml", piece if isinstance(piece, str) else json.dumps(piece),
                                    "application/yaml")) for i, piece in enumerate(pieces)]
            response = self._request("POST", path, data=fields, files=files, timeout=timeout)
        else:
            raise ValueError("upload must be 'multipart' or 'json'")
        return SolverDescription.model_validate(response.json())

    def solvers(self) -> List[SolverDescription]:
        return [SolverDescription.model_validate(s) for s in self._request("GET", f"{KINEMATICS_PATH}/solvers").json()]

    def solver(self, solver: str) -> SolverDescription:
        return SolverDescription.model_validate(self._request("GET", f"{KINEMATICS_PATH}{self._solver_path(solver)}").json())

    def delete_solver(self, solver: str) -> None:
        self._request("DELETE", f"{KINEMATICS_PATH}{self._solver_path(solver)}").close()

    def download_solver_file(self, solver: str, name: str, destination: Optional[PathArg] = None) -> bytes:
        """An uploaded file (``SolverDescription.files``); written to ``destination`` if given."""
        data = self._request("GET", f"{KINEMATICS_PATH}{self._solver_path(solver)}/files/{name}").content
        if destination is not None:
            Path(destination).write_bytes(data)
        return data

    # ---- Kinematics ---------------------------------------------------------------------------------------

    def fk(self, solver: str, joints: Sequence[Joints], frame: Optional[str] = None) -> FkResponse:
        """Poses of ``frame`` (default: the active tool frame) for each set of joint positions."""
        return self._post(f"{self._solver_path(solver)}/fk", FkRequest(joints=list(joints), frame=frame), FkResponse)

    def _ik_request(self, targets, frame, seed, sequential, position_tolerance, orientation_tolerance) -> IkRequest:
        return IkRequest(targets=list(targets), frame=frame, seed=seed, sequential=sequential,
                         position_tolerance=position_tolerance, orientation_tolerance=orientation_tolerance)

    def ik(self, solver: str, targets: Sequence[Pose], frame: Optional[str] = None, seed: Optional[Joints] = None,
           sequential: bool = True, position_tolerance: float = 0.001, orientation_tolerance: float = 0.5) -> IkResponse:
        """Joint positions for each target pose (in the robot base frame); see the route's description."""
        body = self._ik_request(targets, frame, seed, sequential, position_tolerance, orientation_tolerance)
        return self._post(f"{self._solver_path(solver)}/ik", body, IkResponse)

    def reachable(self, solver: str, targets: Sequence[Pose], frame: Optional[str] = None, seed: Optional[Joints] = None,
                  sequential: bool = True, position_tolerance: float = 0.001,
                  orientation_tolerance: float = 0.5) -> ReachableResponse:
        """Whether each target has an IK solution within the tolerances."""
        body = self._ik_request(targets, frame, seed, sequential, position_tolerance, orientation_tolerance)
        return self._post(f"{self._solver_path(solver)}/reachable", body, ReachableResponse)

    def jacobian(self, solver: str, joints: Sequence[Joints], frame: Optional[str] = None) -> JacobianResponse:
        """Jacobians (wire units: deg/s or m/s → m/s and deg/s) and manipulability at each configuration."""
        return self._post(f"{self._solver_path(solver)}/jacobian", JacobianRequest(joints=list(joints), frame=frame),
                          JacobianResponse)

    # ---- Trajectories -------------------------------------------------------------------------------------

    def sample_trajectory(self, solver: str, start: Joints, move: Any, dt: float, include_poses: bool = False,
                          **limits: Any) -> TrajectoryResponse:
        """Sample one move (``SetTcp``, ``PtpMove``, ``LinMove``) from ``start`` at ``dt``; ``limits``:
        ``velocity_limits``, ``acceleration_limits``, ``jump_margin``, ``max_step_revolute``, ``max_step_prismatic``."""
        body = TrajectoryRequest(start=start, move=move, dt=dt, include_poses=include_poses, **limits)
        return self._post(f"{self._solver_path(solver)}/trajectories/sample", body, TrajectoryResponse)

    def check_path(self, solver: str, joints: Sequence[Joints], dt: float, start: Optional[Joints] = None,
                   **limits: Any) -> PathCheckResponse:
        """Joint limits and the jump check on a joint path (one configuration per step of ``dt``)."""
        body = PathCheckRequest(joints=list(joints), dt=dt, start=start, **limits)
        return self._post(f"{self._solver_path(solver)}/trajectories/check", body, PathCheckResponse)

    # ---- Tool Frames ---------------------------------------------------------------------------------------

    def frames(self, solver: str) -> SolverFrames:
        return SolverFrames.model_validate(self._request("GET", f"{KINEMATICS_PATH}{self._solver_path(solver)}/frames").json())

    def solver_config(self, solver: str) -> SolverConfig:
        return SolverConfig.model_validate(
            self._request("GET", f"{KINEMATICS_PATH}{self._solver_path(solver)}/config/read").json())

    def configure_solver(self, solver: str, tool_frame: Optional[str] = None, tcp_offset: Optional[Pose] = None,
                         clear_tcp: bool = False) -> SolverConfig:
        """Set the active tool frame and/or the TCP offset (relative to the flange frame), or clear the TCP."""
        values: Dict[str, Any] = {}
        if tool_frame is not None:
            values["tool_frame"] = tool_frame
        if tcp_offset is not None:
            values["tcp_offset"] = tcp_offset
        if clear_tcp:
            values["clear_tcp"] = True
        return self._post(f"{self._solver_path(solver)}/config/write", SolverConfig(**values), SolverConfig)

    # ---- Collision ---------------------------------------------------------------------------------------------

    def self_collision(self, solver: str, joints: Sequence[Joints]) -> SelfCollisionResponse:
        """Self-collision of each configuration (the robot's own spheres; no world)."""
        return self._post(f"{self._solver_path(solver)}/collision/self", SelfCollisionRequest(joints=list(joints)),
                          SelfCollisionResponse)

    def collision_spheres(self, solver: str, joints: Joints) -> CollisionSpheres:
        return self._post(f"{self._solver_path(solver)}/collision/spheres", CollisionSpheresRequest(joints=joints),
                          CollisionSpheres)

    # ---- World ----------------------------------------------------------------------------------------------

    def world(self, solver: str) -> WorldDescription:
        """The solver's obstacles (its planner's world)."""
        return WorldDescription.model_validate(self._request("GET", f"{KINEMATICS_PATH}{self._solver_path(solver)}/world").json())

    def set_world(self, solver: str, obstacles: Sequence[Any], reference: str = "base",
                  base_pose: Optional[Pose] = None) -> WorldDescription:
        """Replace the solver's obstacles (``CuboidObstacle``, ``SphereObstacle``, ``CylinderObstacle``,
        ``CapsuleObstacle``, ``MeshObstacle``, or dicts), in the robot base frame or, with ``reference="world"``, a
        world frame with the robot's ``base_pose``."""
        body = {"reference": reference, "obstacles": [o.model_dump(mode="json") if hasattr(o, "model_dump") else o
                                                      for o in obstacles]}
        if base_pose is not None:
            body["base_pose"] = base_pose.model_dump(mode="json")
        response = self._request("PUT", f"{KINEMATICS_PATH}{self._solver_path(solver)}/world", json=body)
        return WorldDescription.model_validate(response.json())

    def clear_world(self, solver: str) -> WorldDescription:
        return WorldDescription.model_validate(
            self._request("DELETE", f"{KINEMATICS_PATH}{self._solver_path(solver)}/world").json())

    def world_collision(self, solver: str, joints: Sequence[Joints]) -> WorldCollisionResponse:
        """Check configurations against the solver's obstacles and the robot's own spheres."""
        return self._post(f"{self._solver_path(solver)}/collision/world", WorldCollisionRequest(joints=list(joints)),
                          WorldCollisionResponse)

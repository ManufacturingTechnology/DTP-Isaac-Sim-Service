import os
import tempfile
from typing import Optional, List
import numpy as np
import threading
import queue
import time
from dataclasses import dataclass

import pxr
import pxr.Gf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdShade
import pxr.UsdPhysics
import pxr.PhysxSchema
import pxr.Vt
#from pxr import Usd, UsdGeom
import omni.usd
import omni.physx
from ..isaac5_compatibility import SingleRigidPrim

from .. import __version__
from ..lib.models import *
from ..lib.coprocessorclient import *
from ..entity.workpiece import *






def _gf_matrix_to_np(mat: pxr.Gf.Matrix4d) -> np.ndarray:
    """Safe Gf.Matrix4d → numpy 4x4 (row-major)."""
    return np.array([[mat[i][j] for j in range(4)] for i in range(4)], dtype=np.float32)


@dataclass
class MeshData:
    vertices: pxr.Vt.Vec3fArray
    face_vertex_counts: pxr.Vt.IntArray
    face_vertex_indices: pxr.Vt.IntArray
    extents: pxr.Vt.Vec3fArray
    vertex_normals: Optional[pxr.Vt.Vec3fArray]
    
    def to_object_geometry(self) -> Optional[FaceGeometry]:
        vertices_np = np.asarray(self.vertices, dtype=np.float64)
        if len(vertices_np) == 0:
            return None

        face_counts = np.asarray(self.face_vertex_counts, dtype=np.int64)
        face_indices = np.asarray(self.face_vertex_indices, dtype=np.int64)

        # Triangulate (fan)
        faces_list = []
        start = 0
        for count in face_counts:
            if count < 3:
                start += count
                continue
            for i in range(1, count - 1):
                faces_list.append([
                    face_indices[start],
                    face_indices[start + i],
                    face_indices[start + i + 1]
                ])
            start += count

        if len(faces_list) == 0:
            return None

        faces = np.array(faces_list, dtype=np.int64)

        normals = None
        if self.vertex_normals is not None:
            normals = np.array(self.vertex_normals, dtype=np.float64).tolist()

        return FaceGeometry(
            vertices=vertices_np.tolist(),
            faces=faces.tolist(),
            normals=normals
        )


    @staticmethod
    def from_object_geometry(obj: FaceGeometry) -> "MeshData":
        if not isinstance(obj, FaceGeometry):
            raise TypeError("obj must be a FaceGeometry")

        # Vertices
        vertices_np = np.asarray(obj.vertices, dtype=np.float64)
        vertices = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in vertices_np])

        # Faces (assume triangulated — Trimesh always gives triangles after repair)
        faces = np.asarray(obj.faces, dtype=np.int64)
        face_vertex_counts = pxr.Vt.IntArray([3] * len(faces))
        face_vertex_indices = pxr.Vt.IntArray(faces.flatten().tolist())

        min_pt = vertices_np.min(axis=0)
        max_pt = vertices_np.max(axis=0)
        min_vec = pxr.Gf.Vec3f(float(min_pt[0]), float(min_pt[1]), float(min_pt[2]))
        max_vec = pxr.Gf.Vec3f(float(max_pt[0]), float(max_pt[1]), float(max_pt[2]))
        extents = pxr.Vt.Vec3fArray([min_vec, max_vec])

        vertex_normals = None
        if obj.vertex_normals is not None:
            vertex_normals = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*n) for n in obj.vertex_normals])

        return MeshData(
            vertices=vertices,
            face_vertex_counts=face_vertex_counts,
            face_vertex_indices=face_vertex_indices,
            extents=extents,
            vertex_normals=vertex_normals
        )

    @staticmethod
    def from_prim(mesh_prim: pxr.Usd.Prim) -> "MeshData":
        if not isinstance(mesh_prim, pxr.Usd.Prim):
            raise TypeError("mesh_prim must be a pxr.Usd.Prim")
        if not mesh_prim.IsA(pxr.UsdGeom.Mesh):
            raise ValueError("mesh_prim must be a pxr.UsdGeom.Mesh")

        mesh = pxr.UsdGeom.Mesh(mesh_prim)

        vertex_normals_attr = mesh.GetNormalsAttr()
        vertex_normals = None
        if vertex_normals_attr is not None and vertex_normals_attr.HasValue():
            vertex_normals = vertex_normals_attr.Get()
        return MeshData(
            vertices=mesh.GetPointsAttr().Get() or [],
            face_vertex_counts=mesh.GetFaceVertexCountsAttr().Get() or [],
            face_vertex_indices=mesh.GetFaceVertexIndicesAttr().Get() or [],
            extents=None,
            vertex_normals=vertex_normals,
        )
    
    @staticmethod
    def from_prim_recursive(prim: pxr.Usd.Prim) -> List["MeshData"]:
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        
        mesh_datas: List[MeshData] = []

        # Recursive traversal (includes root)
        for prim in pxr.Usd.PrimRange(prim):
            if not prim.IsA(pxr.UsdGeom.Mesh):
                continue
            mesh_datas.append(MeshData.from_prim(prim))
        
        return mesh_datas


def _mesh_to_request(prim: pxr.Usd.Prim) -> List[FaceGeometry]:
    geom_objs: List[FaceGeometry] = []

    # Recursive traversal (includes root)
    for prim in pxr.Usd.PrimRange(prim):
        if not prim.IsA(pxr.UsdGeom.Mesh):
            continue

        mesh = pxr.UsdGeom.Mesh(prim)

        vertex_normals_attr = mesh.GetNormalsAttr()
        vertex_normals = None
        if vertex_normals_attr is not None and vertex_normals_attr.HasValue():
            vertex_normals = vertex_normals_attr.Get()
        mesh_data = MeshData(
            vertices=mesh.GetPointsAttr().Get() or [],
            face_vertex_counts=mesh.GetFaceVertexCountsAttr().Get() or [],
            face_vertex_indices=mesh.GetFaceVertexIndicesAttr().Get() or [],
            extents=None,
            vertex_normals=vertex_normals,
        )
        go = mesh_data.to_object_geometry()
        if go is not None:
            geom_objs.append(go)

    return geom_objs

def _response_to_new_mesh(mesh_data: MeshData, prim_path: str) -> pxr.Usd.Prim:
    stage = omni.usd.get_context().get_stage()
    mesh = pxr.UsdGeom.Mesh.Define(stage, prim_path)

    mesh.CreatePointsAttr(mesh_data.vertices)
    mesh.CreateFaceVertexCountsAttr(mesh_data.face_vertex_counts)
    mesh.CreateFaceVertexIndicesAttr(mesh_data.face_vertex_indices)
    mesh.CreateExtentAttr(mesh_data.extents)
    if mesh_data.vertex_normals is not None:
        mesh.CreateNormalsAttr(mesh_data.vertex_normals)
        mesh.SetNormalsInterpolation("vertex")

    
    prim: pxr.Usd.Prim = stage.GetPrimAtPath(prim_path)

    rigid_api = pxr.UsdPhysics.RigidBodyAPI.Apply(prim)
    physx_rigid = pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(prim)

    pxr.UsdPhysics.CollisionAPI.Apply(prim)
    mesh_collision_api = pxr.UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_collision_api.CreateApproximationAttr().Set("convexDecomposition")

    return prim

    # # Vertices
    # vertices = np.asarray(obj.vertices, dtype=np.float64)
    # print(f"t1: {time.time() - t:.09f} s")
    # t = time.time()
    # vecs = [pxr.Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in vertices]
    # print(f"t2: {time.time() - t:.09f} s")
    # t = time.time()
    # mesh.CreatePointsAttr(pxr.Vt.Vec3fArray(vecs))
    # print(f"t3: {time.time() - t:.09f} s")
    # t = time.time()

    # # Faces (assume triangulated — Trimesh always gives triangles after repair)
    # faces = np.asarray(obj.faces, dtype=np.int64)
    # mesh.CreateFaceVertexCountsAttr(pxr.Vt.IntArray([3] * len(faces)))
    # print(f"t4: {time.time() - t:.09f} s")
    # t = time.time()
    # mesh.CreateFaceVertexIndicesAttr(pxr.Vt.IntArray(faces.flatten().tolist()))

    # print(f"t5: {time.time() - t:.09f} s")
    # t = time.time()

    # # Normals (vertex normals look best)
    # if obj.vertex_normals is not None:
    #     normals = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*n) for n in obj.vertex_normals])
    #     mesh.CreateNormalsAttr(normals)
    #     mesh.SetNormalsInterpolation("vertex")

    # print(f"t6: {time.time() - t:.09f} s")
    # t = time.time()

    # # Optional uniform color
    # # if color:
    # #     usd_mesh.CreateDisplayColorAttr(pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*color)]))

    # # Extents (required for proper bounding box / culling)
    # min_pt = vertices.min(axis=0)
    # max_pt = vertices.max(axis=0)
    # min_vec = pxr.Gf.Vec3f(float(min_pt[0]), float(min_pt[1]), float(min_pt[2]))
    # max_vec = pxr.Gf.Vec3f(float(max_pt[0]), float(max_pt[1]), float(max_pt[2]))
    # mesh.CreateExtentAttr(pxr.Vt.Vec3fArray([min_vec, max_vec]))

    # print(f"t7: {time.time() - t:.09f} s")
    # t = time.time()

    # new_xform = pxr.UsdGeom.Xformable(usd_mesh.GetPrim())
    # new_xform.ClearXformOpOrder()          # start clean
    # new_xform.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.0)) #position)
    # new_xform.AddOrientOp().Set(pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)) #orientation)    # quaternion orientation
    # new_xform.AddScaleOp().Set(pxr.Gf.Vec3d(1.0, 1.0, 1.0)) #scale)

    # rigid_prim = SingleRigidPrim(prim_path)
    # # rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
    # # rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
    # rigid_prim.set_linear_velocity(linear_velocity)
    # rigid_prim.set_angular_velocity(angular_velocity)



def _response_to_mesh_in_place(obj: FaceGeometry, prim: pxr.Usd.Prim, linear_velocity: np.ndarray, angular_velocity: np.ndarray) -> None:
    stage = omni.usd.get_context().get_stage()

    t = time.time()

    mesh = pxr.UsdGeom.Mesh(prim)

    # Vertices
    vertices = np.asarray(obj.vertices, dtype=np.float64)
    print(f"t1: {time.time() - t:.09f} s")
    t = time.time()
    vecs = [pxr.Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in vertices]
    print(f"t2: {time.time() - t:.09f} s")
    t = time.time()
    mesh.CreatePointsAttr(pxr.Vt.Vec3fArray(vecs))

    # Faces (assume triangulated — Trimesh always gives triangles after repair)
    faces = np.asarray(obj.faces, dtype=np.int64)
    mesh.CreateFaceVertexCountsAttr(pxr.Vt.IntArray([3] * len(faces)))
    mesh.CreateFaceVertexIndicesAttr(pxr.Vt.IntArray(faces.flatten().tolist()))

    # Normals (vertex normals look best)
    if obj.vertex_normals is not None:
        normals = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*n) for n in obj.vertex_normals])
        mesh.CreateNormalsAttr(normals)
        mesh.SetNormalsInterpolation("vertex")

    print(f"t3: {time.time() - t:.09f} s")
    t = time.time()

    # Optional uniform color
    # if color:
    #     usd_mesh.CreateDisplayColorAttr(pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*color)]))

    # Extents (required for proper bounding box / culling)
    min_pt = vertices.min(axis=0)
    max_pt = vertices.max(axis=0)
    min_vec = pxr.Gf.Vec3f(float(min_pt[0]), float(min_pt[1]), float(min_pt[2]))
    max_vec = pxr.Gf.Vec3f(float(max_pt[0]), float(max_pt[1]), float(max_pt[2]))
    mesh.CreateExtentAttr(pxr.Vt.Vec3fArray([min_vec, max_vec]))

    # === Tell PhysX to rebuild the collision mesh ===
    collision_api = pxr.PhysxSchema.PhysxCollisionAPI(prim)
    if collision_api:
        # This forces PhysX to re-cook the collision geometry
        collision_api.GetEnableAttribute().Set(False)
        stage.Update()                     # important
        collision_api.GetEnableAttribute().Set(True)

    # new_xform = pxr.UsdGeom.Xformable(mesh.GetPrim())
    # new_xform.ClearXformOpOrder()          # start clean
    # new_xform.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.0)) #position)
    # new_xform.AddOrientOp().Set(pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)) #orientation)    # quaternion orientation
    # new_xform.AddScaleOp().Set(pxr.Gf.Vec3d(1.0, 1.0, 1.0)) #scale)


    rigid_prim = SingleRigidPrim(str(prim.GetPath()))
    rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
    rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
    rigid_prim.set_linear_velocity(linear_velocity)
    rigid_prim.set_angular_velocity(angular_velocity)
    # linear_velocity = np.array([0, 0, 0], dtype=np.float32)   # m/s
    # angular_velocity = np.array([angular_velocity[0], angular_velocity[1], angular_velocity[2]], dtype=np.float32)  # deg/s
    # rigid_prim.set_linear_velocity(linear_velocity)
    # rigid_prim.set_angular_velocity(angular_velocity)
    print(f"t4: {time.time() - t:.09f} s")

    #new_xform = pxr.UsdGeom.Xformable(mesh.GetPrim())
    #new_xform.ClearXformOpOrder()          # start clean
    #new_xform.GetTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.0)) #position)
    #new_xform.AddOrientOp().Set(pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)) #orientation)    # quaternion orientation
    #new_xform.AddScaleOp().Set(pxr.Gf.Vec3d(1.0, 1.0, 1.0)) #scale)

    # new_xform = pxr.UsdGeom.Xformable(usd_mesh.GetPrim())
    # new_xform.ClearXformOpOrder()          # start clean
    # new_xform.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.0)) #position)
    # new_xform.AddOrientOp().Set(pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)) #orientation)    # quaternion orientation
    # new_xform.AddScaleOp().Set(pxr.Gf.Vec3d(1.0, 1.0, 1.0)) #scale)

    # rigid_api = pxr.UsdPhysics.RigidBodyAPI.Apply(prim)
    # physx_rigid = pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(prim)

    # pxr.UsdPhysics.CollisionAPI.Apply(prim)
    # mesh_collision_api = pxr.UsdPhysics.MeshCollisionAPI.Apply(prim)
    # mesh_collision_api.CreateApproximationAttr().Set("convexDecomposition")

    return prim


# class CosimClient:

#     def __init__(self):
#         self._client = CoSimulatorClient()

#     def difference(self, workpiece: pxr.Usd.Prim, tool: pxr.Usd.Prim, prim_path: str):
#         if not isinstance(workpiece, pxr.Usd.Prim):
#             raise TypeError("workpiece must be a prim")
#         if not workpiece.IsValid():
#             raise ValueError("workpiece must be a valid prim")
#         if not isinstance(tool, pxr.Usd.Prim):
#             raise TypeError("tool must be a prim")
#         if not tool.IsValid():
#             raise ValueError("tool must be a valid prim")
#         if not isinstance(prim_path, str):
#             raise TypeError("prim_path must be a str")

#         # pos = pxr.Gf.Vec3d(0.0, 0.0, 0.0)
#         # ori = pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)
#         #scale = pxr.Gf.Vec3d(1.0, 1.0, 1.0)

#         # work_xform = pxr.UsdGeom.Xformable(workpiece)
#         # if work_xform:
#         #     pos = work_xform.GetTranslateOp().Get()
#         #     ori = work_xform.GetOrientOp().Get() # get returns quatd, set requires quatf
#         #     ori = pxr.Gf.Quatf(ori)
#             #scale = work_xform.GetScaleOp().Get()
#             # test = work_xform.GetLocalTransformation(pxr.Usd.TimeCode.Default())
#             # pos, rot, scale = work_xform.GetLocalTransformation(pxr.Usd.TimeCode.Default())
#             # ori = rot.GetQuaternion()
#         # work_xform.Is
#         # pos = work_xform.GetTranslateAttr().Get(pxr.Usd.TimeCode.Default())
#         # if pos is None:
#         #     pos = pxr.Gf.Vec3d(0.0, 0.0, 0.0)
#         # ori = work_xform.GetOrientAttr().Get(pxr.Usd.TimeCode.Default())
#         # if ori is None:
#         #     ori = pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)
#         # scale = work_xform.GetScaleAttr().Get(pxr.Usd.TimeCode.Default())
#         # if scale is None:
#         #     scale = pxr.Gf.Vec3d(1.0, 1.0, 1.0)

#         work_matl = None
#         if workpiece.HasAPI(pxr.UsdShade.MaterialBindingAPI):
#             mb_api = pxr.UsdShade.MaterialBindingAPI(workpiece)
#             work_matl, work_matl_prim = mb_api.ComputeBoundMaterial()
#             if not work_matl or not work_matl_prim.IsValid():
#                 work_matl = None

#         rigid_workpiece = SingleRigidPrim(str(workpiece.GetPath()))
#         lin_vel = rigid_workpiece.get_linear_velocity()
#         ang_vel = rigid_workpiece.get_angular_velocity()

#         workpieces = _mesh_to_request(workpiece)
#         tools = _mesh_to_request(tool)
#         response = self._client.difference(GeometryRequest(
#             workpieces=workpieces,
#             tools=tools
#         ))
#         if len(response.workpieces) == 0:
#             pass
#         for i in range(len(response.workpieces)):
#             workpiece_obj = response.workpieces[i]
#             #_response_to_mesh_in_place(workpiece_obj, workpiece, lin_vel, ang_vel)
#             prim = _response_to_new_mesh(workpiece_obj, prim_path, lin_vel, ang_vel)#, pos, ori)
#             if work_matl is not None:
#                 #mb_api = pxr.UsdShade.MaterialBindingAPI(prim)
#                 mb_api = pxr.UsdShade.MaterialBindingAPI.Apply(prim)
#                 mb_api.Bind(work_matl)
#             # _response_to_stage(workpiece_obj, f"/World/Difference_{i:02d}", pos, ori)
#         stage = omni.usd.get_context().get_stage()
#         stage.RemovePrim(str(workpiece.GetPath()))
#         pass

#     def union(self):
#         pass


#     def subtractive_start(self, workpiece: pxr.Usd.Prim, tools: List[pxr.Usd.Prim]) -> str:
#         if not isinstance(workpiece, pxr.Usd.Prim):
#             raise TypeError("workpiece must be a prim")
#         if not workpiece.IsValid():
#             raise ValueError("workpiece must be a valid prim")
#         if not isinstance(tools, list):
#             raise TypeError("tools must be a List[pxr.Usd.Prim]")
#         if not all(isinstance(t, pxr.Usd.Prim) and t.IsValid() for t in tools):
#             raise TypeError("tools must be a List[pxr.Usd.Prim]")
        
#         response = self._client.subtractive_start(GeometryStartRequest(
#             workpiece=_mesh_to_request(workpiece),
#             tools=[_mesh_to_request(tool) for tool in tools]
#         ))
#         return response.workpiece_id
    
#     def subtractive_update(self, workpiece: pxr.Usd.Prim, tools: List[pxr.Usd.Prim], workpiece_id: str) -> None:
#         if not isinstance(workpiece, pxr.Usd.Prim):
#             raise TypeError("workpiece must be a prim")
#         if not workpiece.IsValid():
#             raise ValueError("workpiece must be a valid prim")
#         if not isinstance(tools, list):
#             raise TypeError("tools must be a List[pxr.Usd.Prim]")
#         if not all(isinstance(t, pxr.Usd.Prim) and t.IsValid() for t in tools):
#             raise TypeError("tools must be a List[pxr.Usd.Prim]")
#         if not isinstance(workpiece_id, str):
#             raise TypeError("workpiece_id must be a str")
        
#         workpiece_xform = pxr.UsdGeom.Xformable(workpiece).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
#         workpiece_transform_matrix = np.array(workpiece_xform).reshape(4, 4).T
#         tools_transform_matrices = []
#         for tool in tools:
#             tool_xform = pxr.UsdGeom.Xformable(tool).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
#             tool_transform_matrix = np.array(tool_xform).reshape(4, 4).T
#             tools_transform_matrices.append(tool_transform_matrix)

#         self._client.subtractive_update(GeometryStepRequest(
#             workpiece_id=workpiece_id,
#             workpiece=ObjectTransform(matrix=workpiece_transform_matrix),
#             tools=[ObjectTransform(matrix=ttm) for ttm in tools_transform_matrices]
#         ))

#     def subtractive_ready(self, workpiece_id: str) -> bool:
#         if not isinstance(workpiece_id, str):
#             raise TypeError("workpiece_id must be a str")
        
#         response = self._client.subtractive_ready(GeometryFinalizeRequest(
#             workpiece_id=workpiece_id,
#         ))
#         return response.ready

#     def subtractive_finish(self, workpiece: pxr.Usd.Prim, workpiece_id: str, prim_path: str) -> None:
#         if not isinstance(workpiece, pxr.Usd.Prim):
#             raise TypeError("workpiece must be a prim")
#         if not workpiece.IsValid():
#             raise ValueError("workpiece must be a valid prim")
#         if not isinstance(workpiece_id, str):
#             raise TypeError("workpiece_id must be a str")
#         if not isinstance(prim_path, str):
#             raise TypeError("prim_path must be a str")

#         work_xform = pxr.UsdGeom.Xformable(workpiece)
#         work_world = work_xform.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
#         #translate, rotation, scale = work_xform.GetLocalTransformation(pxr.Usd.TimeCode.Default())
#         pos = work_world.ExtractTranslation()
#         ori = work_world.ExtractRotationQuat()
#         ori = pxr.Gf.Quatf(ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2])
#         #scale = work_world.ExtractScale()

#         response = self._client.subtractive_finish(GeometryFinalizeRequest(
#             workpiece_id=workpiece_id,
#         ))

#         workpiece_objs = response.workpieces
#         for i in range(len(workpiece_objs)):
#             final_path = prim_path
#             if len(workpiece_objs) > 1:
#                 final_path = prim_path + f"_{i:03d}"
#             workpiece_obj = workpiece_objs[i]
#             prim = _response_to_new_mesh(workpiece_obj, final_path)
#             # prim_xform = pxr.UsdGeom.Xformable(workpiece)
#             # prim_xform.GetTranslateOp().Set(pos)
#             # prim_xform.GetOrientOp().Set(ori)
#             rigid_prim = SingleRigidPrim(str(prim.GetPath()))
#             rigid_prim.set_world_pose([pos[0], pos[1], pos[2]], [ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2]])
#             #rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
#             #rigid_prim.set_world_pose([0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
#                 # new_xform = pxr.UsdGeom.Xformable(usd_mesh.GetPrim())
#     # new_xform.ClearXformOpOrder()          # start clean
#     # new_xform.AddTranslateOp().Set(pxr.Gf.Vec3d(0.0, 0.0, 0.0)) #position)
#     # new_xform.AddOrientOp().Set(pxr.Gf.Quatd(1.0, 0.0, 0.0, 0.0)) #orientation)    # quaternion orientation
#     # new_xform.AddScaleOp().Set(pxr.Gf.Vec3d(1.0, 1.0, 1.0)) #scale)

#         # Remove old workpiece
#         stage = omni.usd.get_context().get_stage()
#         stage.RemovePrim(str(workpiece.GetPath()))
        

class SubtractiveCosimulator:

    def __init__(self, workpiece: pxr.Usd.Prim, tools: List[pxr.Usd.Prim]):
        if not isinstance(workpiece, pxr.Usd.Prim):
            raise TypeError("workpiece must be a prim")
        if not workpiece.IsValid():
            raise ValueError("workpiece must be a valid prim")
        self._workpiece = workpiece

        if not isinstance(tools, list):
            raise TypeError("tools must be a List[pxr.Usd.Prim]")
        if not all(isinstance(t, pxr.Usd.Prim) and t.IsValid() for t in tools):
            raise TypeError("tools must be a List[pxr.Usd.Prim] of valid prims")
        self._tools = tools

        # TODO: make sure workpiece and tools are meshes which can be used in cosimulator

        self._client = CoProcessorClient()
        self._workpiece_id: Optional[str] = None
        self._step_thread_exit_flag: bool = False
        self._step_thread: Optional[threading.Thread] = None
        self._step_queue: queue.Queue[ProcessStepRequest] = queue.Queue()
        self._finalize_thread_exit_flag: bool = False
        self._finalize_thread: Optional[threading.Thread] = None
        self._finalize_queue: queue.Queue[List[MeshData]] = queue.Queue()

    @property
    def workpiece(self) -> pxr.Usd.Prim:
        return self._workpiece
    
    @property
    def tools(self) -> List[pxr.Usd.Prim]:
        return list(self._tools)
    
    @property
    def workpiece_id(self) -> Optional[str]:
        return self._workpiece_id

    @property
    def is_finished(self) -> bool:
        if not self._step_queue.empty():
            return False
        return not self._finalize_queue.empty()
    
        if self._workpiece_id is None:
            return False

        response = self._client.subtractive_is_finished(GeometryFinalizeRequest(
            workpiece_id=self._workpiece_id,
        ))
        return response.ready

    def start(self):
        if self._workpiece_id is not None:
            raise Exception("already started")

        response = self._client.subtractive_start(ProcessStartRequest(
            workpiece=_mesh_to_request(self._workpiece),
            tools=[_mesh_to_request(tool) for tool in self._tools]
        ))
        self._workpiece_id = response.workpiece_id

        self._step_thread_exit_flag = False
        self._step_thread = threading.Thread(target=self._step_thread_method_wrapper)
        self._step_thread.start()

    def _step_thread_method_wrapper(self):
        try:
            self._step_thread_method()
        except Exception as ex:
            print(f"Exception running: {ex}") # TODO: log
        
    def _step_thread_method(self):
        while not self._step_thread_exit_flag:
            if self._step_queue.empty():
                time.sleep(0.001)
                continue
            request = self._step_queue.get_nowait()
            self._client.subtractive_step(request)

    def step(self):
        if self._workpiece_id is None:
            raise Exception("not started")

        if self._step_thread is None:
            raise Exception("worker thread not intialized")
        if not self._step_thread.is_alive():
            raise Exception("worker thread is not alive")

        # Only send data if bounding boxes overlap
        bbox_overlap = False
        for tool in self._tools:
            if self._bounding_boxes_overlap(self._workpiece, tool):
                bbox_overlap = True
                break
        if not bbox_overlap:
            return

        workpiece_xform = pxr.UsdGeom.Xformable(self._workpiece).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        workpiece_transform_matrix = np.array(workpiece_xform).reshape(4, 4).T
        tools_transform_matrices = []
        for tool in self._tools:
            tool_xform = pxr.UsdGeom.Xformable(tool).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
            tool_transform_matrix = np.array(tool_xform).reshape(4, 4).T
            tools_transform_matrices.append(tool_transform_matrix)

        self._step_queue.put(ProcessStepRequest(
            workpiece_id=self._workpiece_id,
            workpiece=ObjectTransform(transform_matrix=workpiece_transform_matrix),
            tools=[ObjectTransform(transform_matrix=ttm) for ttm in tools_transform_matrices]
        ))

        # self._client.subtractive_step(GeometryStepRequest(
        #     workpiece_id=self._workpiece_id,
        #     workpiece=ObjectTransform(matrix=workpiece_transform_matrix),
        #     tools=[ObjectTransform(matrix=ttm) for ttm in tools_transform_matrices]
        # ))

    def _bounding_boxes_overlap(self, workpiece: pxr.Usd.Prim, tool: pxr.Usd.Prim) -> bool:
        # Reuse one cache for both queries → faster
        cache = pxr.UsdGeom.BBoxCache(pxr.Usd.TimeCode.Default(), ["default", "render"])
        
        aabb1 = self._get_world_aabb(str(workpiece.GetPath()), cache)
        aabb2 = self._get_world_aabb(str(tool.GetPath()), cache)
        
        overlap: bool = not (
            aabb1[3] < aabb2[0] or aabb1[0] > aabb2[3] or   # X no overlap
            aabb1[4] < aabb2[1] or aabb1[1] > aabb2[4] or   # Y
            aabb1[5] < aabb2[2] or aabb1[2] > aabb2[5]      # Z
        )

        return overlap

    @staticmethod
    def _get_world_aabb(prim_path: str, cache: pxr.UsdGeom.BBoxCache = None) -> np.ndarray:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        
        if not prim.IsValid():
            raise ValueError(f"Prim not found: {prim_path}")
        
        # Create cache if not provided (purpose = "default" or "render" usually)
        if cache is None:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                ["default", "render"]   # or just ["default"]
            )
        
        bound = cache.ComputeWorldBound(prim)
        aligned_range = bound.ComputeAlignedRange()   # axis-aligned
        
        min_pt = aligned_range.GetMin()
        max_pt = aligned_range.GetMax()
        
        return np.array([
            min_pt[0], min_pt[1], min_pt[2],
            max_pt[0], max_pt[1], max_pt[2]
        ], dtype=np.float64)
    
    def stop(self) -> None:
        if self._workpiece_id is None:
            raise Exception("not started")

        # Start the finalize thread
        self._finalize_thread_exit_flag = False
        self._finalize_thread = threading.Thread(target=self._finalize_thread_method_wrapper)
        self._finalize_thread.start()

    def _finalize_thread_method_wrapper(self):
        try:
            self._finalize_thread_method()
        except Exception as ex:
            print(f"Exception running: {ex}") # TODO: log
        
    def _finalize_thread_method(self):
        timeout_time = time.time() + 240.0
        while not self._finalize_thread_exit_flag:
            # Wait for all steps to be transmitted
            if not self._step_queue.empty():
                time.sleep(0.001)
                continue

            # Wait for remote calculations to finish
            ready_response = self._client.subtractive_is_finished(ProcessIdRequest(
                id=self._workpiece_id,
            ))
            if not ready_response.ready:
                if time.time() > timeout_time:
                    raise TimeoutError(f"Workpiece {self._workpiece_id} not ready")
                time.sleep(0.001)
                continue

            response = self._client.subtractive_finalize(ProcessIdRequest(
                id=self.workpiece_id,
            ))
            mesh_datas: List[MeshData] = []
            for workpiece in response.workpieces:
                mesh_datas.append(MeshData.from_object_geometry(workpiece))
            self._finalize_queue.put(mesh_datas)
            self._finalize_thread_exit_flag = True
            break

    def finalize(self, prim_path: str) -> None:
        if not isinstance(prim_path, str):
            raise TypeError("prim_path must be a str")

        if self._workpiece_id is None:
            raise Exception("not started")

        # Exit the threads
        self._step_thread_exit_flag = True

        timeout_end = time.time() + 60.0
        while not self.is_finished:
            time.sleep(0.001)
            if time.time() > timeout_end:
                self._finalize_thread_exit_flag = True
                raise TimeoutError("Workpiece not ready")
        self._finalize_thread_exit_flag = True
        
        # Get the position and orientation of the original workpiece
        work_xform = pxr.UsdGeom.Xformable(self._workpiece)
        work_world = work_xform.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        pos = work_world.ExtractTranslation()
        ori = work_world.ExtractRotationQuat()
        ori = pxr.Gf.Quatf(ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2])

        # response = self._client.subtractive_finalize(GeometryFinalizeRequest(
        #     workpiece_id=self.workpiece_id,
        # ))
        #response = self._finalize_queue.get_nowait()
        #workpiece_objs = response.workpieces
        workpiece_mesh_datas = self._finalize_queue.get_nowait()
        for i in range(len(workpiece_mesh_datas)):
            # Append a suffix to the path if there are multiple workpieces
            final_path = prim_path
            if len(workpiece_mesh_datas) > 1:
                final_path = prim_path + f"_{i:03d}"

            # Create a new workpiece and place it where the original workpiece was
            workpiece_obj = workpiece_mesh_datas[i]
            prim = _response_to_new_mesh(workpiece_obj, final_path)

            rigid_prim = SingleRigidPrim(str(prim.GetPath()))

            rigid_prim.set_world_pose(
                [pos[0], pos[1], pos[2]], 
                [ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2]]
            )

        # Remove old workpiece
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(str(self._workpiece.GetPath()))


class AdditiveCosimulator:

    def __init__(self, workpiece: pxr.Usd.Prim, tools: List[pxr.Usd.Prim]):
        if not isinstance(workpiece, pxr.Usd.Prim):
            raise TypeError("workpiece must be a prim")
        if not workpiece.IsValid():
            raise ValueError("workpiece must be a valid prim")
        self._workpiece = workpiece

        if not isinstance(tools, list):
            raise TypeError("tools must be a List[pxr.Usd.Prim]")
        if not all(isinstance(t, pxr.Usd.Prim) and t.IsValid() for t in tools):
            raise TypeError("tools must be a List[pxr.Usd.Prim] of valid prims")
        self._tools = tools

        # TODO: make sure workpiece and tools are meshes which can be used in cosimulator

        self._client = CoProcessorClient()
        self._workpiece_id: Optional[str] = None
        self._step_thread_exit_flag: bool = False
        self._step_thread: Optional[threading.Thread] = None
        self._step_queue: queue.Queue[ProcessStepRequest] = queue.Queue()
        self._finalize_thread_exit_flag: bool = False
        self._finalize_thread: Optional[threading.Thread] = None
        self._finalize_queue: queue.Queue[List[MeshData]] = queue.Queue()

    @property
    def workpiece(self) -> pxr.Usd.Prim:
        return self._workpiece
    
    @property
    def tools(self) -> List[pxr.Usd.Prim]:
        return list(self._tools)
    
    @property
    def workpiece_id(self) -> Optional[str]:
        return self._workpiece_id

    @property
    def is_finished(self) -> bool:
        return not self._finalize_queue.empty()

    def start(self):
        if self._workpiece_id is not None:
            raise Exception("already started")

        response = self._client.additive_start(ProcessStartRequest(
            workpiece=_mesh_to_request(self._workpiece),
            tools=[_mesh_to_request(tool) for tool in self._tools]
        ))
        self._workpiece_id = response.workpiece_id

        self._step_thread_exit_flag = False
        self._step_thread = threading.Thread(target=self._step_thread_method_wrapper)
        self._step_thread.start()

    def _step_thread_method_wrapper(self):
        try:
            self._step_thread_method()
        except Exception as ex:
            print(f"Exception running: {ex}") # TODO: log
        
    def _step_thread_method(self):
        while not self._step_thread_exit_flag:
            if self._step_queue.empty():
                time.sleep(0.001)
                continue
            request = self._step_queue.get_nowait()
            self._client.additive_step(request)

    def step(self):
        if self._workpiece_id is None:
            raise Exception("not started")

        if self._step_thread is None:
            raise Exception("worker thread not intialized")
        if not self._step_thread.is_alive():
            raise Exception("worker thread is not alive")

        # Only send data if bounding boxes overlap
        bbox_overlap = False
        for tool in self._tools:
            if self._bounding_boxes_overlap(self._workpiece, tool):
                bbox_overlap = True
                break
        if not bbox_overlap:
            return

        workpiece_xform = pxr.UsdGeom.Xformable(self._workpiece).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        workpiece_transform_matrix = np.array(workpiece_xform).reshape(4, 4).T
        tools_transform_matrices = []
        for tool in self._tools:
            tool_xform = pxr.UsdGeom.Xformable(tool).ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
            tool_transform_matrix = np.array(tool_xform).reshape(4, 4).T
            tools_transform_matrices.append(tool_transform_matrix)

        self._step_queue.put(ProcessStepRequest(
            id=self._workpiece_id,
            workpiece=ObjectTransform(transform_matrix=workpiece_transform_matrix),
            tools=[ObjectTransform(transform_matrix=ttm) for ttm in tools_transform_matrices]
        ))

        # self._client.subtractive_step(GeometryStepRequest(
        #     workpiece_id=self._workpiece_id,
        #     workpiece=ObjectTransform(matrix=workpiece_transform_matrix),
        #     tools=[ObjectTransform(matrix=ttm) for ttm in tools_transform_matrices]
        # ))

    def _bounding_boxes_overlap(self, workpiece: pxr.Usd.Prim, tool: pxr.Usd.Prim) -> bool:
        # Reuse one cache for both queries → faster
        cache = pxr.UsdGeom.BBoxCache(pxr.Usd.TimeCode.Default(), ["default", "render"])
        
        aabb1 = self._get_world_aabb(str(workpiece.GetPath()), cache)
        aabb2 = self._get_world_aabb(str(tool.GetPath()), cache)
        
        overlap: bool = not (
            aabb1[3] < aabb2[0] or aabb1[0] > aabb2[3] or   # X no overlap
            aabb1[4] < aabb2[1] or aabb1[1] > aabb2[4] or   # Y
            aabb1[5] < aabb2[2] or aabb1[2] > aabb2[5]      # Z
        )

        return overlap

    @staticmethod
    def _get_world_aabb(prim_path: str, cache: pxr.UsdGeom.BBoxCache = None) -> np.ndarray:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        
        if not prim.IsValid():
            raise ValueError(f"Prim not found: {prim_path}")
        
        # Create cache if not provided (purpose = "default" or "render" usually)
        if cache is None:
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                ["default", "render"]   # or just ["default"]
            )
        
        bound = cache.ComputeWorldBound(prim)
        aligned_range = bound.ComputeAlignedRange()   # axis-aligned
        
        min_pt = aligned_range.GetMin()
        max_pt = aligned_range.GetMax()
        
        return np.array([
            min_pt[0], min_pt[1], min_pt[2],
            max_pt[0], max_pt[1], max_pt[2]
        ], dtype=np.float64)
    
    def stop(self) -> None:
        if self._workpiece_id is None:
            raise Exception("not started")

        # Start the finalize thread
        self._finalize_thread_exit_flag = False
        self._finalize_thread = threading.Thread(target=self._finalize_thread_method_wrapper)
        self._finalize_thread.start()

    def _finalize_thread_method_wrapper(self):
        try:
            self._finalize_thread_method()
        except Exception as ex:
            print(f"Exception running: {ex}") # TODO: log
        
    def _finalize_thread_method(self):
        timeout_time = time.time() + 60.0
        while not self._finalize_thread_exit_flag:
            ready_response = self._client.process_is_complete(ProcessIdRequest(
                id=self._workpiece_id,
            ))
            if not ready_response.ready:
                if time.time() > timeout_time:
                    raise TimeoutError(f"Workpiece {self._workpiece_id} not ready")
                time.sleep(0.001)
                continue

            response = self._client.process_finalize(ProcessIdRequest(
                id=self.workpiece_id,
            ))
            mesh_datas: List[MeshData] = []
            for workpiece in response.workpieces:
                mesh_datas.append(MeshData.from_object_geometry(workpiece))
            self._finalize_queue.put(mesh_datas)
            self._finalize_thread_exit_flag = True
            break

    def finalize(self, prim_path: str) -> None:
        if not isinstance(prim_path, str):
            raise TypeError("prim_path must be a str")

        if self._workpiece_id is None:
            raise Exception("not started")

        # Exit the threads
        self._step_thread_exit_flag = True
        
        timeout_end = time.time() + 60.0
        while not self.is_finished:
            time.sleep(0.001)
            if time.time() > timeout_end:
                self._finalize_thread_exit_flag = True
                raise TimeoutError("Workpiece not ready")
        self._finalize_thread_exit_flag = True
        
        # Get the position and orientation of the original workpiece
        work_xform = pxr.UsdGeom.Xformable(self._workpiece)
        work_world = work_xform.ComputeLocalToWorldTransform(pxr.Usd.TimeCode.Default())
        pos = work_world.ExtractTranslation()
        ori = work_world.ExtractRotationQuat()
        ori = pxr.Gf.Quatf(ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2])

        # response = self._client.subtractive_finalize(GeometryFinalizeRequest(
        #     workpiece_id=self.workpiece_id,
        # ))
        #response = self._finalize_queue.get_nowait()
        #workpiece_objs = response.workpieces
        workpiece_mesh_datas = self._finalize_queue.get_nowait()
        for i in range(len(workpiece_mesh_datas)):
            # Append a suffix to the path if there are multiple workpieces
            final_path = prim_path
            if len(workpiece_mesh_datas) > 1:
                final_path = prim_path + f"_{i:03d}"

            # Create a new workpiece and place it where the original workpiece was
            workpiece_obj = workpiece_mesh_datas[i]
            prim = _response_to_new_mesh(workpiece_obj, final_path)

            rigid_prim = SingleRigidPrim(str(prim.GetPath()))

            rigid_prim.set_world_pose(
                [pos[0], pos[1], pos[2]], 
                [ori.real, ori.imaginary[0], ori.imaginary[1], ori.imaginary[2]]
            )

        # Remove old workpiece
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(str(self._workpiece.GetPath()))
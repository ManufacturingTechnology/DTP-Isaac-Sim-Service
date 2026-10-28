from operator import is_
import os
import asyncio
import tempfile
from typing import Optional, List, Tuple, Dict, ClassVar
from click import Option
from isodate import parse_date
import numpy as np
import threading
import queue
import time
import pathlib
import math
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict
from sympy import EX
import warp as wp
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pxr
import pxr.Gf
import pxr.Usd
import pxr.UsdGeom
import pxr.UsdShade
import pxr.UsdPhysics
import pxr.PhysxSchema
import pxr.Sdf
import pxr.Vt
import omni
import omni.kit
import omni.kit.app
import omni.kit.commands
import omni.usd
import omni.physx
import omni.physx.scripts
import omni.physx.scripts.particleUtils
import isaacsim.core.experimental.materials
from ..isaac5_compatibility import SingleRigidPrim
#from omni.physx import get_physx_simulation_interface

from .core import EntityInstancer
from ..lib.models import WorkpieceModel, MaterialProperties, FaceGeometry, PointGeometry
from ..service.config import ConfigurationService
from ..lib.logging import _logger



class WorkpieceInstancer(EntityInstancer):

    def get_particle_system_path(self) -> str:
        return f"/{self._folder_name}/ParticleSystem"

    def create_if_doesnt_exist(self) -> pxr.Sdf.Path:
        folder_path = super().create_if_doesnt_exist()

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")

        # Create particle system
        particle_system_path = self.get_particle_system_path()
        particle_system_prim = stage.GetPrimAtPath(particle_system_path)
        if particle_system_prim is None or not particle_system_prim.IsValid():
            # Default Values
            # https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/dev_guide/particles/particles.html#offset-autocomputation
            #   Particle Contact Offset (PCO) = 0.05 meters scaled to scene units
            #   Contact Offset = 1.0 * PCO
            #   Rest Offset = 0.99 * PCO
            #   Fluid Rest Offset = 0.99 * 0.6 * PCO
            #   Solid Rest Offset = 0.99 * PCO
            particle_contact_offset = 0.05
            particle_contact_offset = ConfigurationService().configuration.particle_diameter
            #particle_system_path = f"{prim_path}/ParticleSystem"
            omni.physx.scripts.particleUtils.add_physx_particle_system(
                stage=stage,
                particle_system_path=pxr.Sdf.Path(particle_system_path),
                simulation_owner="/World/physicsScene",
                contact_offset=particle_contact_offset * 1.0,
                rest_offset=particle_contact_offset * 0.999,
                particle_contact_offset=particle_contact_offset,
                solid_rest_offset=particle_contact_offset * 0.5,
                fluid_rest_offset=particle_contact_offset * 0.5,
                #solid_rest_offset=particle_contact_offset * 0.999 * 0.51, # * 0.56,
                #fluid_rest_offset=particle_contact_offset * 0.999 * 0.51, # * 0.56,
                #solver_position_iterations=30, # Default 16
                #max_neighborhood=100, # Default 96
            )
            particle_system_prim: pxr.Usd.Prim = stage.GetPrimAtPath(particle_system_path)
            if not particle_system_prim.IsValid():
                raise RuntimeError("created invalid particle_system_prim")

        return folder_path
        

_workpiece_instancer = WorkpieceInstancer("Workpiece", "w")








@dataclass
class WorkpieceCacheData:
    all: List["Workpiece"] = field(default_factory=list)
    by_id: Dict[str, "Workpiece"] = field(default_factory=dict)

class WorkpieceCache:

    def __init__(self):
        self._data: Optional[WorkpieceCacheData] = WorkpieceCacheData()
        self._id_sequence = 0

    @property
    def data(self) -> WorkpieceCacheData:
        return self._data

    def build(self) -> None:
        self._data = WorkpieceCacheData()
        self._id_sequence = 0

    def clear(self) -> None:
        self._data = WorkpieceCacheData()
        self._id_sequence = 0

    def get_all(self) -> List["Workpiece"]:
        return self.data.all

    def get_ids(self) -> List[str]:
        return list(self.data.by_id.keys())

    def get_by_id(self, id: str) -> Optional["Workpiece"]:
        return self.data.by_id.get(id, None)
    
    def contains(self, workpiece: "Workpiece") -> bool:
        if not isinstance(workpiece, Workpiece):
            raise TypeError("workpiece must be a Workpiece")
        
        return workpiece.get_model().id in self.data.by_id
    
    def contains_id(self, id: str) -> bool:
        if not isinstance(id, str):
            raise TypeError("id must be a str")
        return id in self.data.by_id

    def ingest(self, workpiece: "Workpiece") -> None:
        if not isinstance(workpiece, Workpiece):
            raise TypeError("workpiece must be a Workpiece")

        # Check whether workpiece is being defined or already exists
        if workpiece.get_model().id == "[EMPTY]":
            # Workpiece is being defined
            # Add an ID to the model and update it
            self._id_sequence += 1
            wid = f"{self._id_sequence:012d}"
            workpiece.set_model(WorkpieceModel(id=wid))
            # Add the new workpiece
            self._data.all.append(workpiece)
            self._data.by_id[wid] = workpiece
        else:
            # Workpiece exists, replace it
            # Replace the old workpiece with the new workpiece
            if workpiece.get_model().id in self._data.by_id:
                # Remove the old workpiece
                old_workpiece = self._data.by_id[workpiece.get_model().id]
                self._data.all.remove(old_workpiece)
            else:
                pass # Part is new, but user-specified ID
            # Add the new workpiece
            self._data.all.append(workpiece)
            self._data.by_id[workpiece.get_model().id] = workpiece

    def remove(self, workpiece: "Workpiece") -> None:
        if workpiece not in self.data.all:
            raise ValueError("workpiece is not in cache")
            
        self.data.all.remove(workpiece)
        del self.data.by_id[workpiece.get_model().id]


@dataclass
class FaceData:
    vertices: pxr.Vt.Vec3fArray
    face_vertex_counts: pxr.Vt.IntArray
    face_vertex_indices: pxr.Vt.IntArray
    extents: pxr.Vt.Vec3fArray
    vertex_normals: Optional[pxr.Vt.Vec3fArray]

    def to_mesh(self, mesh: pxr.UsdGeom.Mesh | pxr.Usd.Prim) -> None:
        if not isinstance(mesh, (pxr.UsdGeom.Mesh, pxr.Usd.Prim)):
            raise TypeError("mesh must be a pxr.UsdGeom.Mesh | pxr.Usd.Prim")
        if isinstance(mesh, pxr.Usd.Prim):
            if not mesh.IsValid():
                raise ValueError("mesh prim must be valid")
            if not mesh.IsA(pxr.UsdGeom.Mesh):
                raise TypeError("mesh must be a pxr.UsdGeom.Mesh | pxr.Usd.Prim")
            mesh = pxr.UsdGeom.Mesh(mesh)
        mesh: pxr.UsdGeom.Mesh = mesh

        attr: pxr.Usd.Attribute = mesh.CreatePointsAttr(self.vertices)
        mesh.CreateFaceVertexCountsAttr(self.face_vertex_counts)
        mesh.CreateFaceVertexIndicesAttr(self.face_vertex_indices)
        mesh.CreateExtentAttr(self.extents)
        if self.vertex_normals is not None:
            mesh.CreateNormalsAttr(self.vertex_normals)
            mesh.SetNormalsInterpolation("vertex")
            #mesh.SetNormalsInterpolation("faceVarying")

    @staticmethod
    def from_mesh(mesh: pxr.UsdGeom.Mesh | pxr.Usd.Prim) -> "FaceData":
        if not isinstance(mesh, (pxr.UsdGeom.Mesh, pxr.Usd.Prim)):
            raise TypeError("mesh must be a pxr.UsdGeom.Mesh | pxr.Usd.Prim")
        if isinstance(mesh, pxr.Usd.Prim):
            if not mesh.IsValid():
                raise ValueError("mesh prim must be valid")
            if not mesh.IsA(pxr.UsdGeom.Mesh):
                raise TypeError("mesh must be a pxr.UsdGeom.Mesh | pxr.Usd.Prim")
            mesh = pxr.UsdGeom.Mesh(mesh)

        vertex_normals_attr = mesh.GetNormalsAttr()
        vertex_normals = None
        if vertex_normals_attr is not None and vertex_normals_attr.HasValue():
            vertex_normals = vertex_normals_attr.Get()

        # Prefer the authored extent attribute; fall back to computing from vertices
        extents = None
        extent_attr = mesh.GetExtentAttr()
        if extent_attr is not None and extent_attr.HasValue():
            extents = extent_attr.Get()
        else:
            points = mesh.GetPointsAttr().Get()
            if points:
                extents = pxr.UsdGeom.PointBased.ComputeExtent(points)

        return FaceData(
            vertices=mesh.GetPointsAttr().Get() or [],
            face_vertex_counts=mesh.GetFaceVertexCountsAttr().Get() or [],
            face_vertex_indices=mesh.GetFaceVertexIndicesAttr().Get() or [],
            extents=extents,
            vertex_normals=vertex_normals,
        )

    def to_face_geometry(self) -> FaceGeometry:
        vertices = [list(v) for v in self.vertices]
        faces = []
        idx = 0
        for count in self.face_vertex_counts:
            faces.append(list(self.face_vertex_indices[idx:idx + count]))
            idx += count
        normals = [list(n) for n in self.vertex_normals] if self.vertex_normals is not None else None
        return FaceGeometry(vertices=vertices, faces=faces, normals=normals)

    @staticmethod
    def from_face_geometry(face_geometry: FaceGeometry) -> "FaceData":
        vertices = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*v) for v in face_geometry.vertices])
        face_vertex_counts = pxr.Vt.IntArray([len(face) for face in face_geometry.faces])
        face_vertex_indices = pxr.Vt.IntArray([idx for face in face_geometry.faces for idx in face])
        extents = pxr.UsdGeom.PointBased.ComputeExtent(vertices) if vertices else pxr.Vt.Vec3fArray()
        vertex_normals = (
            pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*n) for n in face_geometry.normals])
            if face_geometry.normals is not None
            else None
        )
        return FaceData(
            vertices=vertices,
            face_vertex_counts=face_vertex_counts,
            face_vertex_indices=face_vertex_indices,
            extents=extents,
            vertex_normals=vertex_normals,
        )

    def transform(
        self,
        position: pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float] | np.ndarray,
        orientation: pxr.Gf.Quatf | List[float] | np.ndarray = pxr.Gf.Quatf(1.0, 0.0, 0.0, 0.0),
        transform_normals: bool = True,
    ) -> "FaceData":
        """
        Applies: v' = R(quat) * v + pos_offset to all vertices.
        Recomputes extents.
        If vertex_normals exist and transform_normals=True, rotates them by R(quat) (no translation).

        quat_wxyz may be:
        - Gf.Quatf / Gf.Quatd
        - tuple/list (w,x,y,z)  [wxyz]
        """
        mesh = self

        if not isinstance(position, (pxr.Gf.Vec3d, list, np.ndarray)):
            raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
        if isinstance(position, np.ndarray):
            if len(position) != 3:
                raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
            position = pxr.Gf.Vec3f(*position)
        if isinstance(position, list):
            if not (len(position) == 3 and all(isinstance(f, (float | int)) for f in position)):
                raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
            position = [float(f) for f in position]
            position = pxr.Gf.Vec3f(*position)
        if isinstance(position, pxr.Gf.Vec3d):
            position = pxr.Gf.Vec3f(position[0], position[1], position[2])

        if not isinstance(orientation, (pxr.Gf.Quatf, pxr.Gf.Quatd, list, np.ndarray)):
            raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
        if isinstance(orientation, np.ndarray):
            if len(position) != 4:
                raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
            orientation = pxr.Gf.Quatf(*orientation)
        if isinstance(orientation, list):
            if not (len(orientation) == 4 and all(isinstance(f, (float, int)) for f in orientation)):
                raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
            orientation = [float(f) for f in orientation]
            orientation = pxr.Gf.Quatf(*orientation)
        if isinstance(orientation, pxr.Gf.Quatd):
            imaginary = orientation.GetImaginary()
            orientation = pxr.Gf.Quatf(orientation.GetReal(), imaginary[0], imaginary[1], imaginary[2])

        t = position
        q = orientation

        # -------------------------
        # Transform vertices
        # -------------------------
        new_verts = []

        for v in mesh.vertices:
            # q.Transform(v) rotates; + t translates
            new_verts.append(q.Transform(v) + t)
        new_verts = pxr.Vt.Vec3fArray(new_verts)

        # -------------------------
        # Transform normals (rotate only)
        # -------------------------
        new_normals = None
        if mesh.vertex_normals is not None:
            if transform_normals:
                new_normals = []

                for n in mesh.vertex_normals:
                    nr = q.Transform(n)
                    # normalize (safe)
                    l2 = float(nr[0]) * float(nr[0]) + float(nr[1]) * float(nr[1]) + float(nr[2]) * float(nr[2])
                    eps2 = 1e-24
                    if l2 > eps2:
                        inv_l = 1.0 / math.sqrt(l2)
                        nr = pxr.Gf.Vec3f(float(nr[0]) * inv_l, float(nr[1]) * inv_l, float(nr[2]) * inv_l)
                    new_normals.append(nr)

                new_normals = pxr.Vt.Vec3fArray(new_normals)
            else:
                new_normals = mesh.vertex_normals

        # -------------------------
        # Recompute extents
        # -------------------------
        xs = [v[0] for v in new_verts]
        ys = [v[1] for v in new_verts]
        zs = [v[2] for v in new_verts]
        vmin = pxr.Gf.Vec3f(min(xs), min(ys), min(zs))
        vmax = pxr.Gf.Vec3f(max(xs), max(ys), max(zs))
        new_extents = pxr.Vt.Vec3fArray([vmin, vmax])

        return FaceData(
            vertices=new_verts,
            face_vertex_counts=mesh.face_vertex_counts,
            face_vertex_indices=mesh.face_vertex_indices,
            extents=new_extents,
            vertex_normals=new_normals,
        )

    def split_each_triangle_in_half(
        self,
        split_mode: str = "longest_edge",   # "longest_edge" or "edge01"/"edge12"/"edge20"
        recompute_normals: bool = True,
    ) -> "FaceData":
        """
        Splits each input triangle (i0,i1,i2) into 2 triangles by adding the midpoint of one edge.
        Output remains triangulated (faceVertexCounts are all 3).

        - split_mode="longest_edge": picks the longest edge per triangle
        - split_mode="edge01"/"edge12"/"edge20": always split that edge in the triangle order
        - Deduplicates midpoint vertices across shared edges via an edge cache.
        - If recompute_normals=True, outputs face-varying normals aligned with face_vertex_indices.
        """
        mesh = self
        counts = list(mesh.face_vertex_counts)
        if any(c != 3 for c in counts):
            raise ValueError("This function expects a fully-triangulated mesh (all face_vertex_counts == 3).")

        verts: List[pxr.Gf.Vec3f] = [pxr.Gf.Vec3f(v[0], v[1], v[2]) for v in mesh.vertices]
        idx = list(mesh.face_vertex_indices)
        tri_count = len(idx) // 3
        if len(idx) != tri_count * 3:
            raise ValueError("face_vertex_indices length must be a multiple of 3.")

        # Cache to reuse midpoint vertices on shared edges: key=(min_i, max_i) -> midpoint_index
        edge_mid_cache: Dict[Tuple[int, int], int] = {}

        new_indices: List[int] = []
        new_normals: List[pxr.Gf.Vec3f] = []  # face-varying normals (one per face-vertex)

        def midpoint_index(i_a: int, i_b: int) -> int:
            key = (i_a, i_b) if i_a < i_b else (i_b, i_a)
            if key in edge_mid_cache:
                return edge_mid_cache[key]

            a = verts[i_a]
            b = verts[i_b]
            m = pxr.Gf.Vec3f(
                0.5 * (a[0] + b[0]),
                0.5 * (a[1] + b[1]),
                0.5 * (a[2] + b[2]),
            )
            verts.append(m)
            mi = len(verts) - 1
            edge_mid_cache[key] = mi
            return mi

        def tri_face_normal(i0: int, i1: int, i2: int) -> pxr.Gf.Vec3f:
            p0, p1, p2 = verts[i0], verts[i1], verts[i2]
            e1 = pxr.Gf.Vec3f(p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            e2 = pxr.Gf.Vec3f(p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
            n = pxr.Gf.Cross(e1, e2)
            ln = n.GetLength()
            if ln > 1e-12:
                n = (1.0 / ln) * n
            return n

        def choose_edge(i0: int, i1: int, i2: int) -> str:
            if split_mode != "longest_edge":
                return split_mode

            def len2(a: int, b: int) -> float:
                pa, pb = verts[a], verts[b]
                dx = pa[0] - pb[0]
                dy = pa[1] - pb[1]
                dz = pa[2] - pb[2]
                return dx*dx + dy*dy + dz*dz

            l01 = len2(i0, i1)
            l12 = len2(i1, i2)
            l20 = len2(i2, i0)

            if l01 >= l12 and l01 >= l20:
                return "edge01"
            if l12 >= l01 and l12 >= l20:
                return "edge12"
            return "edge20"

        # Split each triangle
        for t in range(tri_count):
            i0, i1, i2 = idx[3*t + 0], idx[3*t + 1], idx[3*t + 2]
            edge = choose_edge(i0, i1, i2)

            if edge == "edge01":
                im = midpoint_index(i0, i1)
                # Preserve original winding (i0,i1,i2) => (i0,im,i2) and (im,i1,i2)
                tris = [(i0, im, i2), (im, i1, i2)]
            elif edge == "edge12":
                im = midpoint_index(i1, i2)
                tris = [(i1, im, i0), (im, i2, i0)]
            elif edge == "edge20":
                im = midpoint_index(i2, i0)
                tris = [(i2, im, i1), (im, i0, i1)]
            else:
                raise ValueError(f"Unknown split_mode: {split_mode}")

            for a, b, c in tris:
                new_indices.extend([a, b, c])
                if recompute_normals:
                    n = tri_face_normal(a, b, c)
                    new_normals.extend([n, n, n])

        # New counts: 2 triangles per input triangle
        new_counts = [3] * (tri_count * 2)

        # Recompute extents from new vertex list
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        vmin = pxr.Gf.Vec3f(min(xs), min(ys), min(zs))
        vmax = pxr.Gf.Vec3f(max(xs), max(ys), max(zs))
        new_extents = pxr.Vt.Vec3fArray([vmin, vmax])

        out_normals = pxr.Vt.Vec3fArray(new_normals) if recompute_normals else None

        return FaceData(
            vertices=pxr.Vt.Vec3fArray(verts),
            face_vertex_counts=pxr.Vt.IntArray(new_counts),
            face_vertex_indices=pxr.Vt.IntArray(new_indices),
            extents=new_extents,
            vertex_normals=out_normals,
        )

    def split_until_max_edge_length(
        self,
        max_edge_length: float,
        recompute_normals: bool = True,
        max_splits: int = 2_000_000,  # safety cap so you don't accidentally explode memory
    ) -> "FaceData":
        """
        Repeatedly subdivide triangles by splitting the longest edge until every triangle's
        edge lengths are <= max_edge_length.

        - Works on already-triangulated meshes only (all face_vertex_counts == 3).
        - Uses an edge-midpoint cache so adjacent triangles share midpoint vertices.
        - Produces a triangulated mesh (all faceVertexCounts are 3).
        - If recompute_normals=True, outputs face-varying normals (one per face-vertex).
        """

        if max_edge_length <= 0.0:
            raise ValueError("max_edge_length must be > 0.")

        mesh = self
        counts = list(mesh.face_vertex_counts)
        if any(c != 3 for c in counts):
            raise ValueError("This function expects a fully-triangulated mesh (all face_vertex_counts == 3).")

        verts: List[pxr.Gf.Vec3f] = [pxr.Gf.Vec3f(v[0], v[1], v[2]) for v in mesh.vertices]
        idx = list(mesh.face_vertex_indices)
        tri_count = len(idx) // 3
        if len(idx) != tri_count * 3:
            raise ValueError("face_vertex_indices length must be a multiple of 3.")

        # Cache to reuse midpoint vertices on shared edges: key=(min_i, max_i) -> midpoint_index
        edge_mid_cache: Dict[Tuple[int, int], int] = {}

        # --- helpers ---
        def len2(a: int, b: int) -> float:
            pa, pb = verts[a], verts[b]
            dx = pa[0] - pb[0]
            dy = pa[1] - pb[1]
            dz = pa[2] - pb[2]
            return dx * dx + dy * dy + dz * dz

        max_len2 = float(max_edge_length) * float(max_edge_length)

        def longest_edge(i0: int, i1: int, i2: int) -> Tuple[str, float]:
            """
            Returns ("edge01"/"edge12"/"edge20", longest_len2).
            """
            l01 = len2(i0, i1)
            l12 = len2(i1, i2)
            l20 = len2(i2, i0)
            if l01 >= l12 and l01 >= l20:
                return "edge01", l01
            if l12 >= l01 and l12 >= l20:
                return "edge12", l12
            return "edge20", l20

        def midpoint_index(i_a: int, i_b: int) -> int:
            key = (i_a, i_b) if i_a < i_b else (i_b, i_a)
            if key in edge_mid_cache:
                return edge_mid_cache[key]

            a = verts[i_a]
            b = verts[i_b]
            m = pxr.Gf.Vec3f(
                0.5 * (a[0] + b[0]),
                0.5 * (a[1] + b[1]),
                0.5 * (a[2] + b[2]),
            )
            verts.append(m)
            mi = len(verts) - 1
            edge_mid_cache[key] = mi
            return mi

        def tri_face_normal(i0: int, i1: int, i2: int) -> pxr.Gf.Vec3f:
            p0, p1, p2 = verts[i0], verts[i1], verts[i2]
            e1 = pxr.Gf.Vec3f(p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            e2 = pxr.Gf.Vec3f(p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
            n = pxr.Gf.Cross(e1, e2)
            ln = n.GetLength()
            if ln > 1e-12:
                n = (1.0 / ln) * n
            return n

        # --- queue of triangles to process ---
        # Represent each tri as tuple of vertex indices (i0,i1,i2)
        work: List[Tuple[int, int, int]] = []
        for t in range(tri_count):
            work.append((idx[3 * t + 0], idx[3 * t + 1], idx[3 * t + 2]))

        final_tris: List[Tuple[int, int, int]] = []
        splits_done = 0

        while work:
            i0, i1, i2 = work.pop()

            edge, L2 = longest_edge(i0, i1, i2)
            if L2 <= max_len2:
                final_tris.append((i0, i1, i2))
                continue

            splits_done += 1
            if splits_done > max_splits:
                raise RuntimeError(
                    f"Exceeded max_splits={max_splits}. "
                    f"Your max_edge_length={max_edge_length} may be too small for the mesh size."
                )

            # Split along longest edge, preserving winding similarly to your example
            if edge == "edge01":
                im = midpoint_index(i0, i1)
                # (i0,i1,i2) -> (i0,im,i2) and (im,i1,i2)
                work.append((i0, im, i2))
                work.append((im, i1, i2))
            elif edge == "edge12":
                im = midpoint_index(i1, i2)
                # (i0,i1,i2) -> (i1,im,i0) and (im,i2,i0)
                work.append((i1, im, i0))
                work.append((im, i2, i0))
            else:  # "edge20"
                im = midpoint_index(i2, i0)
                # (i0,i1,i2) -> (i2,im,i1) and (im,i0,i1)
                work.append((i2, im, i1))
                work.append((im, i0, i1))

        # Build indices + normals
        new_indices: List[int] = []
        new_normals: List[pxr.Gf.Vec3f] = []

        for a, b, c in final_tris:
            new_indices.extend([a, b, c])
            if recompute_normals:
                n = tri_face_normal(a, b, c)
                new_normals.extend([n, n, n])

        new_counts = [3] * len(final_tris)

        # Recompute extents
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        vmin = pxr.Gf.Vec3f(min(xs), min(ys), min(zs))
        vmax = pxr.Gf.Vec3f(max(xs), max(ys), max(zs))
        new_extents = pxr.Vt.Vec3fArray([vmin, vmax])

        out_normals = pxr.Vt.Vec3fArray(new_normals) if recompute_normals else None

        return FaceData(
            vertices=pxr.Vt.Vec3fArray(verts),
            face_vertex_counts=pxr.Vt.IntArray(new_counts),
            face_vertex_indices=pxr.Vt.IntArray(new_indices),
            extents=new_extents,
            vertex_normals=out_normals,
        )

@dataclass
class PointData:
    diameter: float
    positions: pxr.Vt.Vec3fArray
    velocities: Optional[pxr.Vt.Vec3fArray] = None

    def to_point_geometry(self) -> PointGeometry:
        positions = [list(p) for p in self.positions]
        velocities = [list(v) for v in self.velocities] if self.velocities is not None else None
        return PointGeometry(diameter=self.diameter, positions=positions, velocities=velocities)

    @staticmethod
    def from_point_geometry(point_geometry: PointGeometry) -> "PointData":
        positions = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*p) for p in point_geometry.positions])
        velocities = (
            pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*v) for v in point_geometry.velocities])
            if point_geometry.velocities is not None
            else None
        )
        return PointData(diameter=point_geometry.diameter, positions=positions, velocities=velocities)

    def transform(
        self,
        position: pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float] | np.ndarray,
        orientation: pxr.Gf.Quatf | List[float] | np.ndarray = pxr.Gf.Quatf(1.0, 0.0, 0.0, 0.0),
    ) -> "PointData":
        """
        Applies: v' = R(quat) * v + pos_offset to all vertices.
        Recomputes extents.
        If vertex_normals exist and transform_normals=True, rotates them by R(quat) (no translation).

        quat_wxyz may be:
        - Gf.Quatf / Gf.Quatd
        - tuple/list (w,x,y,z)  [wxyz]
        """

        if not isinstance(position, (pxr.Gf.Vec3f, pxr.Gf.Vec3d, list, np.ndarray)):
            raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
        if isinstance(position, np.ndarray):
            if len(position) != 3:
                raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
            position = pxr.Gf.Vec3f(*position)
        if isinstance(position, list):
            if not (len(position) == 3 and all(isinstance(f, (float | int)) for f in position)):
                raise TypeError("position must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
            position = [float(f) for f in position]
            position = pxr.Gf.Vec3f(*position)
        if isinstance(position, pxr.Gf.Vec3d):
            position = pxr.Gf.Vec3f(position[0], position[1], position[2])

        if not isinstance(orientation, (pxr.Gf.Quatf, pxr.Gf.Quatd, list, np.ndarray)):
            raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
        if isinstance(orientation, np.ndarray):
            if len(position) != 4:
                raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
            orientation = pxr.Gf.Quatf(*orientation)
        if isinstance(orientation, list):
            if not (len(orientation) == 4 and all(isinstance(f, (float, int)) for f in orientation)):
                raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
            orientation = [float(f) for f in orientation]
            orientation = pxr.Gf.Quatf(*orientation)
        if isinstance(orientation, pxr.Gf.Quatd):
            imaginary = orientation.GetImaginary()
            orientation = pxr.Gf.Quatf(orientation.GetReal(), imaginary[0], imaginary[1], imaginary[2])

        t = position
        q = orientation

        # -------------------------
        # Transform positions
        # -------------------------

        new_positions = []
        for p in self.positions:
            # q.Transform(p) rotates; + t translates
            new_positions.append(q.Transform(p) + t)
        new_positions = pxr.Vt.Vec3fArray(new_positions)

        return PointData(
            diameter=self.diameter,
            positions=new_positions,
        )


class MeshConverter(ABC):

    @abstractmethod
    def is_capable(self) -> bool:
        pass

    @abstractmethod
    def convert(self, mesh_data: FaceData, diameter: float) -> PointData:
        pass

class VoxelizeMeshConverter(MeshConverter):

    def is_capable(self) -> bool:
        return True

    def convert(self, mesh_data: FaceData, diameter: float) -> PointData:
        """
        1. Voxelization + Ray Cast Inside/Outside Test ✅ Recommended
        Build a 3D grid over the mesh's extents at spacing = diameter. For each grid point, fire a ray in an arbitrary axis direction and count intersections against triangles derived from face_vertex_counts and face_vertex_indices using the parity (odd = inside) rule. Implementable in pure NumPy.

        ✅ No external dependencies beyond NumPy
        ✅ Uniform diameter by construction — grid spacing == diameter
        ✅ Full volume fill
        ⚠️ Ray-cast parity test is brittle on non-manifold or degenerate meshes (rays hitting edges/vertices exactly). Mitigated by jittering the ray direction slightly
        ❌ O((extents/diameter)³) memory; large meshes at fine diameter can be expensive
        """
        """
        Convert a mesh to a volumetric particle set via voxelization and ray casting.

        Grid points are placed on a regular lattice at spacing `diameter`, offset by
        half a cell so particles sit at voxel centers. Each candidate point is tested
        for interior membership by casting a ray in the +Z direction and counting
        triangle intersections — odd count means inside (Jordan curve theorem).

        Möller–Trumbore intersection terms that are independent of ray origin are
        precomputed once over all triangles, then reused across batched grid points
        to keep peak memory bounded.

        Args:
            mesh_data: Source mesh. `extents` must be a 2-element Vec3fArray
                    [min_corner, max_corner] in the mesh's local frame.
            diameter:  Particle diameter, also controls voxel grid spacing.
                    Smaller values produce denser fills at higher memory cost
                    (scales as O((extent/diameter)^3)).

        Returns:
            PointData with positions of all interior grid points and no velocities.
        """
        import numpy as np
        from pxr import Vt

        # ------------------------------------------------------------------ #
        # 1. Convert USD arrays to numpy                                       #
        # ------------------------------------------------------------------ #
        vertices            = np.array(mesh_data.vertices,            dtype=np.float32)  # (V, 3)
        face_vertex_counts  = np.array(mesh_data.face_vertex_counts,  dtype=np.int32)    # (F,)
        face_vertex_indices = np.array(mesh_data.face_vertex_indices, dtype=np.int32)    # (I,)
        extents             = np.array(mesh_data.extents,             dtype=np.float32)  # (2, 3)

        bbox_min = extents[0]  # (3,)
        bbox_max = extents[1]  # (3,)

        # ------------------------------------------------------------------ #
        # 2. Triangulate: fan decomposition handles tris, quads, and n-gons   #
        # ------------------------------------------------------------------ #
        tri_list = []
        offset = 0
        for count in face_vertex_counts:
            root = face_vertex_indices[offset]
            for k in range(1, count - 1):
                tri_list.append([root,
                                face_vertex_indices[offset + k],
                                face_vertex_indices[offset + k + 1]])
            offset += int(count)

        triangles = np.array(tri_list, dtype=np.int32)  # (T, 3)

        v0 = vertices[triangles[:, 0]]  # (T, 3)
        v1 = vertices[triangles[:, 1]]  # (T, 3)
        v2 = vertices[triangles[:, 2]]  # (T, 3)

        edge1 = v1 - v0  # (T, 3)
        edge2 = v2 - v0  # (T, 3)

        # ------------------------------------------------------------------ #
        # 3. Precompute Möller–Trumbore terms independent of ray origin        #
        #                                                                      #
        # Ray direction is fixed at +Z = (0, 0, 1).                           #
        #   h     = D × edge2                                                  #
        #   a     = edge1 · h   (zero → ray parallel to triangle plane)       #
        #   inv_a = 1 / a       (guarded against divide-by-zero)              #
        # ------------------------------------------------------------------ #
        RAY_DIR = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        EPSILON = 1e-7

        h         = np.cross(RAY_DIR, edge2)           # (T, 3)
        a         = np.einsum('ti,ti->t', edge1, h)    # (T,)
        valid_tri = np.abs(a) > EPSILON                # (T,)  non-parallel triangles

        # Safe reciprocal: parallel triangles get inv_a=0 and are masked out later
        inv_a = np.where(valid_tri, 1.0 / np.where(valid_tri, a, 1.0), 0.0)  # (T,)

        # ------------------------------------------------------------------ #
        # 4. Build candidate grid                                              #
        #                                                                      #
        # Points are offset by diameter/2 so they sit at voxel centers and    #
        # avoid landing exactly on the bounding box faces.                     #
        # A small XY jitter (~1% of diameter) is applied to the grid origin   #
        # to reduce the chance of rays landing exactly on mesh edges/vertices, #
        # which can produce ambiguous parity counts.                           #
        # ------------------------------------------------------------------ #
        rng   = np.random.default_rng(seed=0)
        jitter = rng.uniform(EPSILON, diameter * 0.01, size=2).astype(np.float32)

        xs = np.arange(bbox_min[0] + diameter * 0.5 + jitter[0], bbox_max[0], diameter, dtype=np.float32)
        ys = np.arange(bbox_min[1] + diameter * 0.5 + jitter[1], bbox_max[1], diameter, dtype=np.float32)
        zs = np.arange(bbox_min[2] + diameter * 0.5,              bbox_max[2], diameter, dtype=np.float32)

        gx, gy, gz  = np.meshgrid(xs, ys, zs, indexing='ij')
        grid_points = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=-1)  # (M, 3)
        M           = len(grid_points)

        # ------------------------------------------------------------------ #
        # 5. Batched ray–triangle intersection test (Möller–Trumbore)         #
        #                                                                      #
        # Batching over grid points (axis 0) keeps peak RAM bounded.          #
        # At BATCH=512 and T=10 000 triangles, the dominant (B, T, 3) arrays  #
        # consume ~120 MB per batch; reduce BATCH if memory is constrained.   #
        # ------------------------------------------------------------------ #
        BATCH               = 512
        intersection_counts = np.zeros(M, dtype=np.int32)

        for start in range(0, M, BATCH):
            end     = min(start + BATCH, M)
            origins = grid_points[start:end]  # (B, 3)

            # s = origin - v0  →  (B, T, 3)
            s = origins[:, np.newaxis, :] - v0[np.newaxis, :, :]

            # u = inv_a * (s · h)  →  (B, T)
            u = inv_a[np.newaxis, :] * np.einsum('bti,ti->bt', s, h)

            # q = s × edge1  →  (B, T, 3)
            q = np.cross(s, edge1[np.newaxis, :, :])

            # v = inv_a * (D · q)  →  (B, T)
            v = inv_a[np.newaxis, :] * np.einsum('bti,i->bt', q, RAY_DIR)

            # t_param = inv_a * (edge2 · q)  →  (B, T)
            # Positive t_param means intersection is in the +Z half-space of origin
            t_param = inv_a[np.newaxis, :] * np.einsum('ti,bti->bt', edge2, q)

            hit = (
                valid_tri[np.newaxis, :]    # non-parallel triangle
                & (u >= 0.0) & (u <= 1.0)  # barycentric u ∈ [0, 1]
                & (v >= 0.0)               # barycentric v ≥ 0
                & (u + v <= 1.0)           # within triangle
                & (t_param > EPSILON)      # intersection is ahead of the ray origin
            )  # (B, T)

            intersection_counts[start:end] = hit.sum(axis=1)

        # ------------------------------------------------------------------ #
        # 6. Parity test: odd intersection count → inside mesh                #
        # ------------------------------------------------------------------ #
        inside_positions = grid_points[(intersection_counts % 2) == 1]

        return PointData(
            diameter=diameter,
            positions=Vt.Vec3fArray.FromNumpy(inside_positions),
            velocities=None,
        )

# ── Module-level kernel ──────────────────────────────────────────────────────
# Warp kernels must be defined at module scope so they are compiled once via
# JIT on first invocation and reused across all subsequent calls.  Defining
# inside the function body would trigger recompilation on every call.
#
# One thread == one grid point.  Each thread iterates over all triangles and
# counts +Z ray intersections using Möller–Trumbore, writing its own slot in
# `counts` with no inter-thread communication or atomics required.
#
# Nested if-guards are used instead of `continue` because Warp's handling of
# early loop exits can vary across versions; the nested form is universally safe.
# ────────────────────────────────────────────────────────────────────────────

@wp.kernel
def _ray_cast_kernel(
    origins:   wp.array(dtype=wp.vec3),   # (M,)  candidate grid points
    v0_arr:    wp.array(dtype=wp.vec3),   # (T,)  triangle vertex 0
    edge1_arr: wp.array(dtype=wp.vec3),   # (T,)  v1 - v0
    edge2_arr: wp.array(dtype=wp.vec3),   # (T,)  v2 - v0
    num_tris:  int,
    counts:    wp.array(dtype=wp.int32),  # (M,)  output intersection counts
) -> None:
    tid    = wp.tid()
    origin = origins[tid]
    ray    = wp.vec3(0.0, 0.0, 1.0)   # fixed +Z ray direction
    eps    = float(1e-7)
    n      = int(0)

    for t in range(num_tris):
        e1 = edge1_arr[t]
        e2 = edge2_arr[t]

        # h = D × edge2;  a = edge1 · h
        # |a| ≈ 0 → ray is parallel to the triangle plane, skip
        h = wp.cross(ray, e2)
        a = wp.dot(e1, h)

        if wp.abs(a) > eps:
            inv_a = 1.0 / a

            # Barycentric coordinate u
            s = origin - v0_arr[t]
            u = inv_a * wp.dot(s, h)

            if u >= 0.0 and u <= 1.0:
                # Barycentric coordinate v
                q = wp.cross(s, e1)
                v = inv_a * wp.dot(ray, q)

                if v >= 0.0 and (u + v) <= 1.0:
                    # t_hit > eps → intersection is strictly ahead of the origin
                    t_hit = inv_a * wp.dot(e2, q)

                    if t_hit > eps:
                        n += 1

    counts[tid] = n

class WarpMeshConverter(MeshConverter):

    def is_capable(self) -> bool:
        return wp.is_cuda_available()

    def convert(self, mesh_data: FaceData, diameter: float) -> PointData:
        """
        2. Warp-Accelerated Voxelization
        Same algorithm as above but the inside/outside ray tests are parallelized across all grid points using NVIDIA Warp (import warp), which ships with Isaac Sim.

        ✅ Dramatically faster on large meshes or fine diameters
        ✅ Same output quality as approach 1
        ❌ Warp kernel adds implementation complexity vs. plain NumPy
        ❌ Requires GPU; introduces a Warp dependency (though it's bundled with Isaac Sim)
        """
        """
        Warp-accelerated volumetric mesh-to-particle conversion.

        Identical in algorithm to the NumPy version (voxelization + Möller–Trumbore
        ray cast) but the per-grid-point inside/outside test is parallelised across
        all CUDA cores via a Warp kernel, with one thread per grid point.

        CPU work (triangulation, grid construction, result compaction) remains in
        NumPy/Python.  Only the ray-cast loop — the dominant cost for fine diameters
        or high-poly meshes — is moved to the GPU.

        Memory layout:
        - Grid points, triangle data, and the output count array are allocated as
            Warp CUDA arrays and freed at end of scope.
        - There is no per-batch loop: all M grid points are dispatched in one
            kernel launch.  GPU VRAM must fit:
            4 arrays × M floats × 3 components × 4 bytes  (origins + counts)
            + 4 arrays × T floats × 3 components × 4 bytes  (triangle geometry)
            Reduce diameter if VRAM is tight; the NumPy fallback handles that case.

        One-time JIT cost:
        The first call compiles _ray_cast_kernel to PTX.  On Isaac Sim's bundled
        Warp this takes ~1–2 s and is cached to disk for subsequent runs.

        Args:
            mesh_data: Source mesh.  extents must be a 2-element Vec3fArray
                    [min_corner, max_corner] in the mesh's local frame.
            diameter:  Particle diameter and voxel grid spacing.

        Returns:
            PointData with positions of all interior grid points and no velocities.

        Raises:
            RuntimeError: If no CUDA device is available.
        """

        # ------------------------------------------------------------------ #
        # 0. Verify CUDA availability                                          #
        # ------------------------------------------------------------------ #
        if not wp.is_cuda_available():
            raise RuntimeError(
                "mesh_to_particle_warp requires a CUDA device. "
                "Use mesh_to_particle (NumPy) on CPU-only systems."
            )

        device = "cuda"

        # ------------------------------------------------------------------ #
        # 1. Convert USD arrays to NumPy                                       #
        # ------------------------------------------------------------------ #
        vertices            = np.array(mesh_data.vertices,            dtype=np.float32)  # (V, 3)
        face_vertex_counts  = np.array(mesh_data.face_vertex_counts,  dtype=np.int32)    # (F,)
        face_vertex_indices = np.array(mesh_data.face_vertex_indices, dtype=np.int32)    # (I,)
        extents             = np.array(mesh_data.extents,             dtype=np.float32)  # (2, 3)

        bbox_min = extents[0]
        bbox_max = extents[1]

        # ------------------------------------------------------------------ #
        # 2. Fan triangulation (identical to NumPy version)                   #
        # ------------------------------------------------------------------ #
        tri_list = []
        offset   = 0
        for count in face_vertex_counts:
            root = face_vertex_indices[offset]
            for k in range(1, count - 1):
                tri_list.append([root,
                                face_vertex_indices[offset + k],
                                face_vertex_indices[offset + k + 1]])
            offset += int(count)

        triangles = np.array(tri_list, dtype=np.int32)  # (T, 3)

        v0_np    = vertices[triangles[:, 0]]             # (T, 3)
        edge1_np = vertices[triangles[:, 1]] - v0_np    # (T, 3)
        edge2_np = vertices[triangles[:, 2]] - v0_np    # (T, 3)
        num_tris = len(triangles)

        # ------------------------------------------------------------------ #
        # 3. Build candidate grid with XY jitter                               #
        #                                                                      #
        # The jitter logic is identical to the NumPy version: a small         #
        # fixed-seed offset on X and Y prevents rays from landing exactly on  #
        # mesh edges, which produces ambiguous parity counts.                 #
        # ------------------------------------------------------------------ #
        EPSILON = 1e-7
        rng     = np.random.default_rng(seed=0)
        jitter  = rng.uniform(EPSILON, diameter * 0.01, size=2).astype(np.float32)

        xs = np.arange(bbox_min[0] + diameter * 0.5 + jitter[0], bbox_max[0], diameter, dtype=np.float32)
        ys = np.arange(bbox_min[1] + diameter * 0.5 + jitter[1], bbox_max[1], diameter, dtype=np.float32)
        zs = np.arange(bbox_min[2] + diameter * 0.5,              bbox_max[2], diameter, dtype=np.float32)

        gx, gy, gz   = np.meshgrid(xs, ys, zs, indexing='ij')
        grid_points  = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=-1)  # (M, 3)
        M            = len(grid_points)

        # ------------------------------------------------------------------ #
        # 4. Upload arrays to GPU                                              #
        #                                                                      #
        # wp.array with dtype=wp.vec3 expects a contiguous (N, 3) float32     #
        # numpy array.  np.ascontiguousarray guards against non-contiguous     #
        # slices from meshgrid stacking.                                       #
        # ------------------------------------------------------------------ #
        wp_origins = wp.array(np.ascontiguousarray(grid_points), dtype=wp.vec3, device=device)
        wp_v0      = wp.array(np.ascontiguousarray(v0_np),       dtype=wp.vec3, device=device)
        wp_edge1   = wp.array(np.ascontiguousarray(edge1_np),    dtype=wp.vec3, device=device)
        wp_edge2   = wp.array(np.ascontiguousarray(edge2_np),    dtype=wp.vec3, device=device)
        wp_counts  = wp.zeros(M, dtype=wp.int32, device=device)

        # ------------------------------------------------------------------ #
        # 5. Launch kernel — one thread per grid point                         #
        #                                                                      #
        # wp.launch handles CUDA block/grid decomposition automatically.       #
        # The kernel is JIT-compiled on first call (PTX cached to disk).       #
        # ------------------------------------------------------------------ #
        wp.launch(
            kernel=_ray_cast_kernel,
            dim=M,
            inputs=[wp_origins, wp_v0, wp_edge1, wp_edge2, num_tris],
            outputs=[wp_counts],
            device=device,
        )

        # Synchronise before reading back — ensures kernel completion
        wp.synchronize_device(device)

        # ------------------------------------------------------------------ #
        # 6. Download counts, parity test, pack result                         #
        # ------------------------------------------------------------------ #
        counts_np = wp_counts.numpy()                        # (M,)  back to CPU
        inside    = grid_points[(counts_np % 2) == 1]        # odd count → inside

        return PointData(
            diameter=diameter,
            positions=pxr.Vt.Vec3fArray.FromNumpy(inside),
            velocities=None,
        )

class ParticleClusterer(ABC):

    @abstractmethod
    def is_capable(self) -> bool:
        pass

    @abstractmethod
    def cluster(self, particle_data: PointData) -> List[PointData]:
        pass


class KdtreeParticleClusterer(ParticleClusterer):

    def is_capable(self) -> bool:
        return True

    def cluster(self, particle_data: PointData) -> List[PointData]:
        """
        Option A: KDTree Connected Components (Pure SciPy) ✅ Recommended
        Build a scipy.spatial.KDTree from positions. Query all pairs within 2 * diameter (natural neighbor distance for touching particles), then extract connected components via BFS or union-find. Each component becomes one mesh.

        ✅ No sklearn or external dependency; scipy ships with Isaac Sim
        ✅ diameter maps directly and naturally to the connectivity threshold
        ✅ Works well when clusters have any shape
        ❌ BFS over very large particle sets can be slow in pure Python; consider a union-find (path-compressed) implementation for performance
        """
        """
        Segment an unordered particle set into spatially distinct clusters using
        KDTree neighbour queries and union-find connected components.

        Two particles are considered connected if their centres are within
        `2 * diameter` of each other (i.e. they touch or overlap given the
        uniform diameter). Isolated single particles form their own cluster.

        Velocities, if present, are sliced alongside positions so each returned
        PointData carries the matching velocity subset.

        Args:
            particle_data: Source particles. Must have diameter > 0.

        Returns:
            One PointData per connected component, in arbitrary order.
            The returned list is empty if particle_data contains no positions.
        """
        import numpy as np
        from scipy.spatial import KDTree
        from pxr import Vt

        positions = np.array(particle_data.positions, dtype=np.float32)   # (N, 3)
        N = len(positions)

        if N == 0:
            return []

        velocities = (
            np.array(particle_data.velocities, dtype=np.float32)
            if particle_data.velocities is not None
            else None
        )

        # ------------------------------------------------------------------ #
        # 1. Union-Find with path-halving and union-by-rank                   #
        # ------------------------------------------------------------------ #
        parent = np.arange(N, dtype=np.int32)
        rank   = np.zeros(N, dtype=np.int32)

        def find(x: int) -> int:
            # Path-halving: every other node on the path points to its grandparent.
            # Amortised O(α(N)) per call without recursive stack overhead.
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx == ry:
                return
            # Attach smaller-rank tree under larger-rank root
            if rank[rx] < rank[ry]:
                rx, ry = ry, rx
            parent[ry] = rx
            if rank[rx] == rank[ry]:
                rank[rx] += 1

        # ------------------------------------------------------------------ #
        # 2. Find all neighbouring pairs within the connectivity threshold     #
        #                                                                      #
        # Threshold = 2 * diameter: two particles whose centres are closer     #
        # than this distance are physically touching or overlapping.           #
        # output_type='ndarray' avoids a Python set and is faster to iterate. #
        # ------------------------------------------------------------------ #
        connectivity_radius = 2.0 * particle_data.diameter
        tree  = KDTree(positions)
        pairs = tree.query_pairs(r=connectivity_radius, output_type='ndarray')  # (P, 2)

        for i, j in pairs:
            union(int(i), int(j))

        # ------------------------------------------------------------------ #
        # 3. Group particle indices by their component root                   #
        # ------------------------------------------------------------------ #
        roots        = np.array([find(i) for i in range(N)], dtype=np.int32)
        unique_roots = np.unique(roots)

        clusters: List[PointData] = []
        for root in unique_roots:
            mask = roots == root

            clusters.append(PointData(
                diameter=particle_data.diameter,
                positions=Vt.Vec3fArray.FromNumpy(positions[mask]),
                velocities=(
                    Vt.Vec3fArray.FromNumpy(velocities[mask])
                    if velocities is not None else None
                ),
            ))

        return clusters


class DbscanParticleClusterer(ParticleClusterer):

    def is_capable(self) -> bool:
        return True

    def cluster(self, particle_data: PointData) -> List[PointData]:
        """
        Option B: DBSCAN (SciPy)
        scipy.spatial doesn't include DBSCAN directly, but it can be implemented on top of the KDTree with eps = 2 * diameter and min_samples = 1. Equivalent to Option A in practice for this use case.

        Same tradeoffs as Option A; adds minor implementation overhead with no real benefit here since min_samples = 1 collapses to connected components anyway

        Recommendation: Use Option A (KDTree connected components). DBSCAN's min_samples > 1 would be useful if you need to filter out noise/lone stray particles — keep that in mind as an optional filter before reconstruction.
        """
        """
        Segment an unordered particle set into spatially distinct clusters using
        DBSCAN, implemented on top of a KDTree without sklearn.

        DBSCAN parameters are derived directly from the particle geometry:
        - eps        = 2 * diameter  (connectivity radius, same as union-find version)
        - min_samples = 2            (lone stray particles are labelled as noise
                                        and excluded from output; raise to filter
                                        out larger sparse outlier groups)

        Noise points (label = -1) are silently dropped. If you want strays
        returned as single-particle clusters, set min_samples = 1, which
        degenerates to connected-components behaviour.

        Args:
            particle_data: Source particles. Must have diameter > 0.

        Returns:
            One PointData per DBSCAN cluster, noise particles excluded.
            Returns an empty list if all particles are classified as noise
            or particle_data contains no positions.
        """
        import numpy as np
        from scipy.spatial import KDTree
        from pxr import Vt
        from collections import deque

        positions = np.array(particle_data.positions, dtype=np.float32)  # (N, 3)
        N = len(positions)

        if N == 0:
            return []

        velocities = (
            np.array(particle_data.velocities, dtype=np.float32)
            if particle_data.velocities is not None
            else None
        )

        eps         = 2.0 * particle_data.diameter
        min_samples = 2

        # ------------------------------------------------------------------ #
        # 1. Pre-query all neighbours within eps for every point              #
        #                                                                      #
        # query_ball_tree returns a list-of-lists; convert each to a numpy    #
        # array once here so the BFS inner loop doesn't re-allocate.          #
        # ------------------------------------------------------------------ #
        tree        = KDTree(positions)
        neighbours  = tree.query_ball_tree(tree, r=eps)   # List[List[int]], length N
        neighbours  = [np.array(nb, dtype=np.int32) for nb in neighbours]

        # ------------------------------------------------------------------ #
        # 2. DBSCAN — iterative BFS, no recursion                             #
        #                                                                      #
        # Labels:                                                              #
        #   UNVISITED = -2  (not yet processed)                               #
        #   NOISE     = -1  (visited, failed density test)                    #
        #   >= 0           cluster id                                          #
        # ------------------------------------------------------------------ #
        UNVISITED = -2
        NOISE     = -1

        labels     = np.full(N, UNVISITED, dtype=np.int32)
        cluster_id = 0

        for i in range(N):
            if labels[i] != UNVISITED:
                continue

            nb_i = neighbours[i]

            # Density test: does this point have enough neighbours to be a core point?
            if len(nb_i) < min_samples:
                labels[i] = NOISE
                continue

            # Start a new cluster via BFS from core point i
            labels[i] = cluster_id
            queue      = deque(nb_i.tolist())

            while queue:
                j = queue.popleft()

                if labels[j] == NOISE:
                    # Border point: reachable from a core, absorb into cluster
                    # but do not expand further from it
                    labels[j] = cluster_id
                    continue

                if labels[j] != UNVISITED:
                    continue

                labels[j] = cluster_id
                nb_j = neighbours[j]

                # j is itself a core point — schedule its neighbours for expansion
                if len(nb_j) >= min_samples:
                    queue.extend(nb_j.tolist())

            cluster_id += 1

        # ------------------------------------------------------------------ #
        # 3. Collect particles by cluster label, discard noise                #
        # ------------------------------------------------------------------ #
        clusters: List[PointData] = []

        for cid in range(cluster_id):
            mask = labels == cid

            clusters.append(PointData(
                diameter=particle_data.diameter,
                positions=Vt.Vec3fArray.FromNumpy(positions[mask]),
                velocities=(
                    Vt.Vec3fArray.FromNumpy(velocities[mask])
                    if velocities is not None else None
                ),
            ))

        return clusters

class ParticleConverter(ABC):

    @abstractmethod
    def is_capable(self) -> bool:
        pass

    @abstractmethod
    def convert(self, particle_data: PointData) -> FaceData:
        pass

class LocalConvexhullParticleConverter(ParticleConverter):

    def is_capable(self) -> bool:
        return True

    def convert(self, particle_data: PointData) -> FaceData:
        """
        Option B: Convex Hull per Cluster (Pure SciPy)
        scipy.spatial.ConvexHull on each cluster's positions, converting hull simplices directly into face_vertex_indices.

        ✅ Zero extra dependencies, fast, always produces a valid closed mesh
        ✅ Acceptable if your clusters are roughly convex (e.g., blobs, spheres)
        ❌ Fills all concavities — shape fidelity is poor for non-convex clusters
        ❌ Likely the weakest option if cluster shapes matter
        """
        """
        Reconstruct a surface mesh from a single particle cluster using
        scipy.spatial.ConvexHull.

        The convex hull is computed over particle centres. Hull simplices are
        already triangles in 3D, so face_vertex_counts is uniformly 3.
        Winding order is corrected so all face normals point outward, matching
        the convention expected by USD / Isaac Sim.

        Limitations:
        - All concavities are filled — shape fidelity is poor for non-convex
            clusters. Use the marching cubes implementation when shape matters.
        - Requires at least 4 non-coplanar points for ConvexHull to succeed.
            A ValueError is raised for degenerate inputs.

        Args:
            particle_data: A single cluster with >= 4 non-coplanar positions.

        Returns:
            FaceData representing the convex hull surface, no vertex normals.
        """
        import numpy as np
        from scipy.spatial import ConvexHull
        from pxr import Vt

        positions = np.array(particle_data.positions, dtype=np.float32)  # (N, 3)
        N = len(positions)

        if N < 4:
            raise ValueError(
                f"ConvexHull requires at least 4 non-coplanar points; got {N}. "
                "Consider merging small clusters or using a fallback (e.g. a sphere proxy)."
            )

        # ------------------------------------------------------------------ #
        # 1. Compute convex hull                                               #
        #                                                                      #
        # QHull option 'Qt' (triangulated output) is the default in scipy;    #
        # every simplex in hull.simplices is already a triangle.              #
        # hull.vertices contains only the subset of input points on the hull. #
        # ------------------------------------------------------------------ #
        try:
            hull = ConvexHull(positions)
        except Exception as e:
            raise ValueError(
                f"ConvexHull failed, likely due to degenerate (coplanar) input: {e}"
            )

        hull_vertices = positions[hull.vertices]       # (V_hull, 3) — compacted vertex set
        simplices     = hull.simplices.astype(np.int32)  # (F, 3) — indices into positions

        # ------------------------------------------------------------------ #
        # 2. Re-index simplices from positions-space to hull_vertices-space   #
        #                                                                      #
        # hull.simplices indexes into the original positions array.           #
        # We compact to only the vertices on the hull surface so the output   #
        # FaceData doesn't carry interior particles as dead vertices.         #
        # ------------------------------------------------------------------ #
        old_to_new                    = np.full(N, -1, dtype=np.int32)
        old_to_new[hull.vertices]     = np.arange(len(hull.vertices), dtype=np.int32)
        reindexed_simplices           = old_to_new[simplices]  # (F, 3)

        # ------------------------------------------------------------------ #
        # 3. Correct winding order for outward-facing normals                 #
        #                                                                      #
        # ConvexHull.equations stores [normal | offset] per facet.            #
        # The normal already points outward; we verify that the cross product #
        # of (v1-v0) × (v2-v0) aligns with this normal, and swap v1/v2 when  #
        # it doesn't.  This ensures consistent outward winding across all     #
        # faces — a hard requirement for USD mesh validity and PhysX import.  #
        # ------------------------------------------------------------------ #
        v0      = hull_vertices[reindexed_simplices[:, 0]]  # (F, 3)
        v1      = hull_vertices[reindexed_simplices[:, 1]]  # (F, 3)
        v2      = hull_vertices[reindexed_simplices[:, 2]]  # (F, 3)

        cross        = np.cross(v1 - v0, v2 - v0)           # (F, 3) — unnormalised face normal
        outward_norm = hull.equations[:, :3]                 # (F, 3) — QHull outward normals
        dot          = np.einsum('fi,fi->f', cross, outward_norm)  # (F,)

        # Where dot < 0 the winding is inward — swap columns 1 and 2 to flip it
        needs_flip = dot < 0.0
        reindexed_simplices[needs_flip] = reindexed_simplices[needs_flip][:, [0, 2, 1]]

        # ------------------------------------------------------------------ #
        # 4. Pack into FaceData                                                #
        # ------------------------------------------------------------------ #
        face_vertex_counts  = np.full(len(reindexed_simplices), 3, dtype=np.int32)
        face_vertex_indices = reindexed_simplices.ravel()
        extents             = np.stack(
            [hull_vertices.min(axis=0), hull_vertices.max(axis=0)]
        ).astype(np.float32)

        return FaceData(
            vertices           = Vt.Vec3fArray.FromNumpy(hull_vertices),
            face_vertex_counts = Vt.IntArray.FromNumpy(face_vertex_counts),
            face_vertex_indices= Vt.IntArray.FromNumpy(face_vertex_indices),
            extents            = Vt.Vec3fArray.FromNumpy(extents),
            vertex_normals     = None,
        )


class LocalMarchingCubesParticleConverter(ParticleConverter):

    def __init__(self, voxel_size: float = 0.05, smooth_sigma: float = 1.0, isolevel: float = 0.3):
        """
        Args:
            voxel_size:   Size of each voxel in world units. Smaller = more detail, more memory.
            smooth_sigma: Gaussian blur sigma applied to the density field before extraction.
                          Higher values produce smoother but less faithful surfaces.
            isolevel:     Density threshold for surface extraction (0–1 range after normalisation).
                          Lower values expand the surface outward; higher values shrink it.
        """
        self.voxel_size   = voxel_size
        self.smooth_sigma = smooth_sigma
        self.isolevel     = isolevel

    def is_capable(self) -> bool:
        return True

    def convert(self, particle_data: PointData) -> FaceData:
        """
        Reconstruct a concave surface mesh from a particle cluster using
        marching cubes (PyMCubes).

        Pipeline:
          1. Voxelise particle centres into a 3-D occupancy grid.
          2. Gaussian-blur the grid to produce a smooth density field.
          3. Run marching cubes at `isolevel` to extract the isosurface.
          4. Re-express vertices in world space and pack into FaceData.

        ✅ Faithfully captures concavities and non-convex shapes
        ✅ Smooth, closed, manifold output suitable for USD / PhysX
        ✅ PyMCubes is a lightweight dependency with no compatibility issues
        ❌ Quality depends on voxel_size — too coarse loses fine detail
        ❌ Higher memory usage than ConvexHull for large particle sets

        Args:
            particle_data: Particle cluster with >= 1 position.

        Returns:
            FaceData representing the reconstructed surface, with vertex normals.
        """
        import numpy as np
        import mcubes
        from scipy.ndimage import gaussian_filter
        from pxr import Vt

        positions = np.array(particle_data.positions, dtype=np.float32)  # (N, 3)

        if len(positions) < 1:
            raise ValueError("At least one particle position is required.")

        # ------------------------------------------------------------------ #
        # 1. Build a voxel grid from particle positions                        #
        #                                                                      #
        # We offset by a margin of 2 voxels on each side so the isosurface    #
        # is never clipped at the grid boundary.                               #
        # ------------------------------------------------------------------ #
        margin    = 2
        world_min = positions.min(axis=0) - margin * self.voxel_size
        world_max = positions.max(axis=0) + margin * self.voxel_size

        grid_shape = np.ceil((world_max - world_min) / self.voxel_size).astype(int) + 1

        # Map each particle to its nearest voxel index and splat into grid
        voxel_indices = np.floor((positions - world_min) / self.voxel_size).astype(int)
        voxel_indices = np.clip(voxel_indices, 0, grid_shape - 1)

        density = np.zeros(grid_shape, dtype=np.float32)
        density[voxel_indices[:, 0], voxel_indices[:, 1], voxel_indices[:, 2]] = 1.0

        # ------------------------------------------------------------------ #
        # 2. Smooth the density field                                          #
        #                                                                      #
        # Gaussian blur spreads occupancy across neighbouring voxels,         #
        # bridging small gaps between particles and softening the field so     #
        # marching cubes produces a smooth, manifold surface.                  #
        # ------------------------------------------------------------------ #
        density = gaussian_filter(density, sigma=self.smooth_sigma)
        density /= density.max() if density.max() > 0 else 1.0  # normalise to [0, 1]

        # ------------------------------------------------------------------ #
        # 3. Extract isosurface with marching cubes (PyMCubes)                #
        #                                                                      #
        # mcubes.marching_cubes returns verts in voxel index space —          #
        # multiply by voxel_size and offset by world_min to get world space.  #
        # ------------------------------------------------------------------ #
        verts, faces = mcubes.marching_cubes(density, self.isolevel)

        if len(verts) == 0:
            raise ValueError(
                f"Marching cubes produced no geometry — the density field may be too sparse "
                f"or isolevel {self.isolevel} is out of range. "
                f"Try a smaller voxel_size or lower isolevel."
            )

        verts = (verts * self.voxel_size + world_min).astype(np.float32)  # (V, 3)
        faces = faces.astype(np.int32)                                     # (F, 3)

        # ------------------------------------------------------------------ #
        # 4. Compute vertex normals                                            #
        #                                                                      #
        # mcubes does not provide normals, so we compute per-face normals     #
        # from the cross product and splat them onto vertices, then           #
        # normalise to get smooth per-vertex normals.                         #
        # ------------------------------------------------------------------ #
        v0 = verts[faces[:, 0]]
        v1 = verts[faces[:, 1]]
        v2 = verts[faces[:, 2]]

        face_normals = np.cross(v1 - v0, v2 - v0)                          # (F, 3)
        norms        = np.linalg.norm(face_normals, axis=1, keepdims=True)
        face_normals /= np.where(norms > 0, norms, 1.0)                    # normalise

        vertex_normals = np.zeros_like(verts)
        np.add.at(vertex_normals, faces[:, 0], face_normals)
        np.add.at(vertex_normals, faces[:, 1], face_normals)
        np.add.at(vertex_normals, faces[:, 2], face_normals)

        norms          = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
        vertex_normals = vertex_normals / np.where(norms > 0, norms, 1.0)  # (V, 3)

        # ------------------------------------------------------------------ #
        # 5. Pack into FaceData                                                #
        # ------------------------------------------------------------------ #
        face_vertex_counts  = np.full(len(faces), 3, dtype=np.int32)
        face_vertex_indices = faces.ravel()
        extents             = np.stack(
            [verts.min(axis=0), verts.max(axis=0)]
        ).astype(np.float32)

        return FaceData(
            vertices            = Vt.Vec3fArray.FromNumpy(verts),
            face_vertex_counts  = Vt.IntArray.FromNumpy(face_vertex_counts),
            face_vertex_indices = Vt.IntArray.FromNumpy(face_vertex_indices),
            extents             = Vt.Vec3fArray.FromNumpy(extents),
            vertex_normals      = Vt.Vec3fArray.FromNumpy(vertex_normals),
        )
        

def cluster_to_mesh_marching_cubes(particle_data: PointData) -> FaceData:
    """
    Option A: Marching Cubes on Gaussian Density Field
    Splat each cluster's particles into a local voxel grid using Gaussian kernels with bandwidth ≈ diameter, then extract the isosurface with marching cubes. Available via skimage.measure.marching_cubes.

    ✅ Smooth, closed, reasonable-quality output even from irregular clusters
    ✅ diameter drives both kernel bandwidth and grid resolution naturally
    ❌ Requires scikit-image — present in many Isaac Sim installs but not guaranteed; verify with python -c "import skimage"
    ❌ Per-cluster voxel grid memory scales with cluster bounding box / diameter³
    """
    """
    Reconstruct a surface mesh from a single particle cluster using a
    Gaussian density field and marching cubes.

    Pipeline:
      1. Allocate a voxel grid over the cluster's bounding box, extended by
         one diameter of padding on all sides to prevent surface clipping at
         the boundary.
      2. Splat each particle as an isotropic Gaussian kernel (σ = radius)
         into the grid.  Only voxels within 3σ of the particle centre are
         updated, keeping the loop O(N * cutoff_voxels³) rather than
         O(N * total_voxels).
      3. Run marching cubes at a fixed iso-level calibrated so that an
         isolated particle's surface sits at the threshold.
      4. Pack the resulting triangle soup into FaceData.

    Vertex normals returned by marching cubes (gradient of the density field)
    are stored in FaceData.vertex_normals.

    Args:
        particle_data: A single cluster (ideally the output of one element
                       from particle_to_clusters). Must contain at least 4
                       non-coplanar positions for marching cubes to produce
                       a closed surface.

    Returns:
        FaceData whose triangulated faces form the reconstructed surface.
        Raises ValueError if the density field produces no surface at the
        chosen iso-level (e.g. cluster is too sparse or a single particle).
    """
    # TODO: this doesn't work in isaac sim because of scikit-image dependency non-compatibility
    import numpy as np
    from skimage.measure import marching_cubes # scikit-image
    from pxr import Vt

    positions = np.array(particle_data.positions, dtype=np.float32)  # (N, 3)
    diameter  = particle_data.diameter
    radius    = diameter * 0.5

    # ------------------------------------------------------------------ #
    # 1. Voxel grid layout                                                #
    #                                                                      #
    # VOXELS_PER_DIAMETER = 2 → voxel_size = diameter / 2.               #
    # Finer grids improve surface quality at higher memory cost; coarser  #
    # grids (e.g. = 1) are faster for large clusters.                     #
    #                                                                      #
    # Padding of one full diameter ensures the Gaussian tails of boundary  #
    # particles decay to near-zero before the grid edge, so marching cubes #
    # always sees a closed zero-crossing and doesn't clip the surface.     #
    # ------------------------------------------------------------------ #
    VOXELS_PER_DIAMETER = 2
    voxel_size = diameter / VOXELS_PER_DIAMETER
    sigma      = radius                   # Gaussian bandwidth = particle radius
    padding    = diameter                 # world-space border on each face
    cutoff     = 3.0 * sigma             # beyond this, Gaussian contribution < 0.1%

    bbox_min   = positions.min(axis=0) - padding
    bbox_max   = positions.max(axis=0) + padding
    grid_dims  = (
        np.ceil((bbox_max - bbox_min) / voxel_size).astype(np.int32) + 1
    )  # (3,)  — +1 so the far face of the last voxel is included

    # ------------------------------------------------------------------ #
    # 2. Gaussian density splatting                                        #
    #                                                                      #
    # For each particle, identify the bounding box of voxels within the   #
    # cutoff radius, build a local coordinate grid, and accumulate the     #
    # Gaussian contribution into the global density array.                 #
    # This avoids constructing a full (N, Vx, Vy, Vz) tensor.             #
    # ------------------------------------------------------------------ #
    density        = np.zeros(grid_dims, dtype=np.float32)
    cutoff_voxels  = int(np.ceil(cutoff / voxel_size))
    inv_two_sigma2 = 1.0 / (2.0 * sigma ** 2)

    for pos in positions:
        center_v = (pos - bbox_min) / voxel_size   # particle centre in voxel coords

        lo = np.maximum(0,            np.floor(center_v - cutoff_voxels).astype(np.int32))
        hi = np.minimum(grid_dims - 1, np.ceil(center_v + cutoff_voxels).astype(np.int32))

        xi = np.arange(lo[0], hi[0] + 1, dtype=np.float32)
        yi = np.arange(lo[1], hi[1] + 1, dtype=np.float32)
        zi = np.arange(lo[2], hi[2] + 1, dtype=np.float32)

        # World positions of each voxel in the local patch
        gx, gy, gz = np.meshgrid(
            bbox_min[0] + xi * voxel_size,
            bbox_min[1] + yi * voxel_size,
            bbox_min[2] + zi * voxel_size,
            indexing='ij',
        )

        dist_sq      = (gx - pos[0]) ** 2 + (gy - pos[1]) ** 2 + (gz - pos[2]) ** 2
        contribution = np.exp(-dist_sq * inv_two_sigma2)

        density[lo[0]:hi[0]+1, lo[1]:hi[1]+1, lo[2]:hi[2]+1] += contribution

    # ------------------------------------------------------------------ #
    # 3. Iso-level selection                                               #
    #                                                                      #
    # For an isolated particle (σ = radius), the density at the particle  #
    # surface (r = radius) is exp(-0.5) ≈ 0.607.                          #
    # We use 0.3 — slightly below this — so tightly packed particles       #
    # merge into one continuous surface rather than producing pinch-points.#
    # Raise toward 0.5 for sharper, more separated blobs.                 #
    # ------------------------------------------------------------------ #
    ISO_LEVEL = 0.3

    if density.max() < ISO_LEVEL:
        raise ValueError(
            f"Peak density ({density.max():.4f}) is below ISO_LEVEL ({ISO_LEVEL}). "
            "The cluster may be too sparse or contain too few particles to form a "
            "closed surface. Consider reducing ISO_LEVEL or increasing VOXELS_PER_DIAMETER."
        )

    # ------------------------------------------------------------------ #
    # 4. Marching cubes                                                    #
    #                                                                      #
    # `spacing` scales voxel-index vertices back to world units.           #
    # Adding bbox_min shifts them from grid-local to world coordinates.    #
    # All faces are triangles → face_vertex_counts is uniformly 3.         #
    # ------------------------------------------------------------------ #
    verts, faces, normals, _ = marching_cubes(
        density,
        level=ISO_LEVEL,
        spacing=(voxel_size, voxel_size, voxel_size),
    )

    verts = (verts + bbox_min).astype(np.float32)   # world space  (V, 3)
    faces = faces.astype(np.int32)                  # triangles    (F, 3)

    face_vertex_counts  = np.full(len(faces), 3, dtype=np.int32)
    face_vertex_indices = faces.ravel()

    extents = np.stack([verts.min(axis=0), verts.max(axis=0)]).astype(np.float32)

    return FaceData(
        vertices           = Vt.Vec3fArray.FromNumpy(verts),
        face_vertex_counts = Vt.IntArray.FromNumpy(face_vertex_counts),
        face_vertex_indices= Vt.IntArray.FromNumpy(face_vertex_indices),
        extents            = Vt.Vec3fArray.FromNumpy(extents),
        vertex_normals     = Vt.Vec3fArray.FromNumpy(normals.astype(np.float32)),
    )


class MeshGenerator:

    @staticmethod
    def generate_cube(
        size: pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int] = pxr.Gf.Vec3d(1.0, 1.0, 1.0),
    ) -> FaceData:
        if isinstance(size, (tuple, list)):
            if not (len(size) == 3 and all(isinstance(f, (float, int)) for f in size)):
                raise TypeError("size must be a pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int]")
            size = pxr.Gf.Vec3d(*[float(f) for f in size])
        if not isinstance(size, pxr.Gf.Vec3d):
            raise TypeError("size must be a pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int]")

        vertices = pxr.Vt.Vec3fArray([
            pxr.Gf.Vec3f(-size[0]/2.0, -size[1]/2.0, -size[2]/2.0),  # 0
            pxr.Gf.Vec3f( size[0]/2.0, -size[1]/2.0, -size[2]/2.0),  # 1
            pxr.Gf.Vec3f( size[0]/2.0,  size[1]/2.0, -size[2]/2.0),  # 2
            pxr.Gf.Vec3f(-size[0]/2.0,  size[1]/2.0, -size[2]/2.0),  # 3
            pxr.Gf.Vec3f(-size[0]/2.0, -size[1]/2.0,  size[2]/2.0),  # 4
            pxr.Gf.Vec3f( size[0]/2.0, -size[1]/2.0,  size[2]/2.0),  # 5
            pxr.Gf.Vec3f( size[0]/2.0,  size[1]/2.0,  size[2]/2.0),  # 6
            pxr.Gf.Vec3f(-size[0]/2.0,  size[1]/2.0,  size[2]/2.0),  # 7
        ])

        # Triangulated faces (12 triangles)
        face_vertex_counts = pxr.Vt.IntArray([3] * 12)

        face_vertex_indices = pxr.Vt.IntArray([
            # Bottom (-Z outward)
            0, 3, 2,  0, 2, 1,
            # Top (+Z outward)
            4, 5, 6,  4, 6, 7,
            # Front (-Y)
            0, 1, 5,  0, 5, 4,
            # Right (+X)
            1, 2, 6,  1, 6, 5,
            # Back (+Y)
            2, 3, 7,  2, 7, 6,
            # Left (-X)
            3, 0, 4,  3, 4, 7,
        ])

        extents = pxr.Vt.Vec3fArray([
            pxr.Gf.Vec3f(-size[0]/2.0, -size[1]/2.0, -size[2]/2.0),
            pxr.Gf.Vec3f( size[0]/2.0,  size[1]/2.0,  size[2]/2.0)
        ])

        vertex_normals = pxr.Vt.Vec3fArray(
            [pxr.Gf.Vec3f(0,0,-1)]*3 + 
            [pxr.Gf.Vec3f(0,0,1)]*3 +
            [pxr.Gf.Vec3f(0,-1,0)]*3 + 
            [pxr.Gf.Vec3f(1,0,0)]*3 +
            [pxr.Gf.Vec3f(0,1,0)]*3 + 
            [pxr.Gf.Vec3f(-1,0,0)]*3
        )

        return FaceData(
            vertices=vertices,
            face_vertex_counts=face_vertex_counts,
            face_vertex_indices=face_vertex_indices,
            extents=extents,
            vertex_normals=vertex_normals,
        )

    @staticmethod
    def generate_cylinder(
        diameter: float | int = 1.0,
        length: float | int = 1.0,
        degree_step: float | int = 5.0,
    ) -> "FaceData":
        """
        Generate a triangulated upright cylinder (axis = +Z), centered at origin.

        Args:
            diameter: cylinder diameter (> 0)
            length: cylinder length along Z (> 0)
            degree_step: angular step in degrees around circumference (smaller => finer)

        Notes:
            - Produces side + top cap + bottom cap triangles.
            - vertex_normals are per-vertex (len == len(vertices)).
        """
        if isinstance(diameter, int):
            diameter = float(diameter)
        if not isinstance(diameter, float):
            raise TypeError("diameter must be a float | int")
        if diameter <= 0.0:
            raise Exception("diameter must be >= 0")
        radius = diameter / 2.0

        if isinstance(length, int):
            length = float(length)
        if not isinstance(length, float):
            raise TypeError("length must be a float | int")
        if length <= 0.0:
            raise Exception("length must be >= 0")

        if isinstance(degree_step, int):
            degree_step = float(degree_step)
        if not isinstance(degree_step, float):
            raise TypeError("degree_step must be a float | int")
        if degree_step <= 0:
            raise Exception("degree_step must be > 0")

        z_top = length * 0.5
        z_bot = -length * 0.5

        # Segment count around circumference
        segments = max(3, int(round(360.0 / degree_step)))
        dphi = 2.0 * math.pi / segments

        vertices: List[pxr.Gf.Vec3f] = []
        normals: List[pxr.Gf.Vec3f] = []

        def add_vertex(x: float, y: float, z: float, nx: float, ny: float, nz: float) -> int:
            vertices.append(pxr.Gf.Vec3f(float(x), float(y), float(z)))
            normals.append(pxr.Gf.Vec3f(float(nx), float(ny), float(nz)))
            return len(vertices) - 1

        # --- Build rings for sides ---
        # We'll create TWO rings for sides (top and bottom), each with 'segments' vertices.
        # Side normals are radial (x,y,0) normalized.
        side_top_indices = []
        side_bot_indices = []

        for j in range(segments):
            phi = j * dphi
            c = math.cos(phi)
            s = math.sin(phi)
            x = radius * c
            y = radius * s
            # Normal is radial
            nx, ny, nz = c, s, 0.0

            side_top_indices.append(add_vertex(x, y, z_top, nx, ny, nz))
            side_bot_indices.append(add_vertex(x, y, z_bot, nx, ny, nz))

        # --- Centers for caps ---
        top_center = add_vertex(0.0, 0.0, z_top, 0.0, 0.0, 1.0)
        bot_center = add_vertex(0.0, 0.0, z_bot, 0.0, 0.0, -1.0)

        # --- Cap rings (separate vertices so cap normals are ±Z) ---
        top_cap_ring = []
        bot_cap_ring = []
        for j in range(segments):
            phi = j * dphi
            c = math.cos(phi)
            s = math.sin(phi)
            x = radius * c
            y = radius * s
            top_cap_ring.append(add_vertex(x, y, z_top, 0.0, 0.0, 1.0))
            bot_cap_ring.append(add_vertex(x, y, z_bot, 0.0, 0.0, -1.0))

        face_vertex_indices: List[int] = []

        # --- Side triangles ---
        # For each segment j, connect quad:
        # top[j] -> top[j+1] and bottom[j] -> bottom[j+1]
        # two triangles per quad with outward winding
        for j in range(segments):
            jp = (j + 1) % segments

            t0 = side_top_indices[j]
            t1 = side_top_indices[jp]
            b0 = side_bot_indices[j]
            b1 = side_bot_indices[jp]

            # Two triangles (t0, b0, t1) and (t1, b0, b1)
            face_vertex_indices.extend([t0, b0, t1])
            face_vertex_indices.extend([t1, b0, b1])

        # --- Top cap triangles (fan) ---
        # outward is +Z, so winding should be (center, j, j+1)
        for j in range(segments):
            jp = (j + 1) % segments
            a = top_center
            b = top_cap_ring[j]
            c = top_cap_ring[jp]
            face_vertex_indices.extend([a, b, c])

        # --- Bottom cap triangles (fan) ---
        # outward is -Z, so winding should be (center, j+1, j) to point outward
        for j in range(segments):
            jp = (j + 1) % segments
            a = bot_center
            b = bot_cap_ring[jp]
            c = bot_cap_ring[j]
            face_vertex_indices.extend([a, b, c])

        tri_count = len(face_vertex_indices) // 3
        face_vertex_counts = pxr.Vt.IntArray([3] * tri_count)

        extents = pxr.Vt.Vec3fArray([
            pxr.Gf.Vec3f(-radius, -radius, z_bot),
            pxr.Gf.Vec3f( radius,  radius, z_top),
        ])

        return FaceData(
            vertices=pxr.Vt.Vec3fArray(vertices),
            face_vertex_counts=face_vertex_counts,
            face_vertex_indices=pxr.Vt.IntArray(face_vertex_indices),
            extents=extents,
            vertex_normals=pxr.Vt.Vec3fArray(normals),
        )

    @staticmethod
    def generate_sphere(
        diameter: float | int = 1.0,
        degree_step: float | int = 5.0,
    ) -> FaceData:
        if isinstance(diameter, int):
            diameter = float(diameter)
        if not isinstance(diameter, float):
            raise TypeError("diameter must be a float | int")
        if diameter <= 0.0:
            raise Exception("diameter must be >= 0")
        radius = diameter / 2.0

        if isinstance(degree_step, int):
            degree_step = float(degree_step)
        if not isinstance(degree_step, float):
            raise TypeError("degree_step must be a float | int")
        if degree_step <= 0:
            raise Exception("degree_step must be > 0")

        # Convert "degree step" to segment counts.
        # We round to get something close, then clamp to minimums.
        lon_segments = max(3, int(round(360.0 / degree_step)))
        lat_segments = max(2, int(round(180.0 / degree_step)))

        # Recompute actual step angles (so we land exactly on poles/seam)
        dphi = 2.0 * math.pi / lon_segments          # longitude step
        dtheta = math.pi / lat_segments              # latitude step (0..pi)

        vertices: List[pxr.Gf.Vec3f] = []
        normals: List[pxr.Gf.Vec3f] = []

        # Helper: add vertex and its normal (outward)
        def add_vertex(x: float, y: float, z: float):
            v = pxr.Gf.Vec3f(float(x), float(y), float(z))
            vertices.append(v)
            # Normal = normalized position for a sphere centered at origin
            inv_r = 1.0 / radius
            normals.append(pxr.Gf.Vec3f(float(x * inv_r), float(y * inv_r), float(z * inv_r)))

        # Indices:
        # 0: top pole
        # 1..: rings
        # last: bottom pole
        add_vertex(0.0, 0.0, radius)  # top pole
        top_index = 0

        # Rings (exclude poles): theta in (0, pi)
        # theta = 0 is top pole, theta = pi is bottom pole
        ring_start = 1
        for i in range(1, lat_segments):
            theta = i * dtheta  # (0, pi)
            sin_t = math.sin(theta)
            cos_t = math.cos(theta)

            # z is vertical axis
            z = radius * cos_t
            r_xy = radius * sin_t

            for j in range(lon_segments):
                phi = j * dphi
                x = r_xy * math.cos(phi)
                y = r_xy * math.sin(phi)
                add_vertex(x, y, z)

        bottom_index = len(vertices)
        add_vertex(0.0, 0.0, -radius)  # bottom pole

        def ring_index(i_ring: int, j_lon: int) -> int:
            """
            i_ring: 0..(rings-1) where rings = lat_segments-1
                    (ring 0 is the first ring below the top pole)
            j_lon:  0..(lon_segments-1)
            """
            return ring_start + i_ring * lon_segments + (j_lon % lon_segments)

        rings = lat_segments - 1  # number of rings between poles

        face_vertex_indices: List[int] = []

        # --- Top cap: top pole to ring 0 ---
        # triangles: (top, ring0[j], ring0[j+1])
        if rings >= 1:
            for j in range(lon_segments):
                a = top_index
                b = ring_index(0, j)
                c = ring_index(0, j + 1)
                face_vertex_indices.extend([a, b, c])

        # --- Middle strips between rings ---
        # For each strip between ring r and r+1, make 2 triangles per quad
        # quad corners: a=upper[j], b=upper[j+1], c=lower[j], d=lower[j+1]
        for r in range(rings - 1):
            for j in range(lon_segments):
                a = ring_index(r, j)
                b = ring_index(r, j + 1)
                c = ring_index(r + 1, j)
                d = ring_index(r + 1, j + 1)

                # Two triangles, consistent winding for outward normals
                face_vertex_indices.extend([a, c, b])
                face_vertex_indices.extend([b, c, d])

        # --- Bottom cap: last ring to bottom pole ---
        # triangles: (bottom, lastRing[j+1], lastRing[j]) to keep outward winding
        if rings >= 1:
            last = rings - 1
            for j in range(lon_segments):
                a = bottom_index
                b = ring_index(last, j + 1)
                c = ring_index(last, j)
                face_vertex_indices.extend([a, b, c])

        tri_count = len(face_vertex_indices) // 3
        face_vertex_counts = pxr.Vt.IntArray([3] * tri_count)

        # Extents
        extents = pxr.Vt.Vec3fArray([
            pxr.Gf.Vec3f(-radius, -radius, -radius),
            pxr.Gf.Vec3f( radius,  radius,  radius),
        ])

        return FaceData(
            vertices=pxr.Vt.Vec3fArray(vertices),
            face_vertex_counts=face_vertex_counts,
            face_vertex_indices=pxr.Vt.IntArray(face_vertex_indices),
            extents=extents,
            vertex_normals=pxr.Vt.Vec3fArray(normals),  # per-vertex normals
        )


class ParticleGenerator:

    @staticmethod
    def generate_cube(
        size: pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int] = pxr.Gf.Vec3d(1.0, 1.0, 1.0),
    ) -> PointData:
        if isinstance(size, (tuple, list)):
            if not (len(size) == 3 and all(isinstance(f, (float, int)) for f in size)):
                raise TypeError("size must be a pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int]")
            size = pxr.Gf.Vec3d(*[float(f) for f in size])
        if not isinstance(size, pxr.Gf.Vec3d):
            raise TypeError("size must be a pxr.Gf.Vec3d | Tuple[float, float, float] | Tuple[int, int, int] | List[float] | List[int]")

        particle_diameter = ConfigurationService().configuration.particle_diameter
        if isinstance(particle_diameter, int):
            particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float):
            raise TypeError("particle_diameter must be a float | int")
        
        particle_spacing = particle_diameter * 1.01
        half = [size[0] / 2.0, size[1] / 2.0, size[2] / 2.0]
        local_positions = []

        num_x = int(size[0] / particle_spacing) + 2
        num_y = int(size[1] / particle_spacing) + 2
        num_z = int(size[2] / particle_spacing) + 2

        for i in range(num_x):
            x = -half[0] + i * particle_spacing
            if x < -half[0] or x > half[0]:
                continue
            for j in range(num_y):
                y = -half[1] + j * particle_spacing
                if y < -half[1] or y > half[1]:
                    continue
                for k in range(num_z):
                    z = -half[2] + k * particle_spacing
                    if z < -half[2] or z > half[2]:
                        continue
                    local_positions.append(np.array([x, y, z]))

        world_positions = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*p) for p in local_positions])
        velocities = [pxr.Gf.Vec3f(0, 0, 0)] * len(local_positions)

        return PointData(
            diameter=particle_diameter,
            positions=world_positions,
            velocities=velocities,
        )

    @staticmethod
    def generate_cylinder(
        diameter: float | int = 1.0,
        height: float | int = 1.0,
    ) -> PointData:
        if isinstance(diameter, int):
            diameter = float(diameter)
        if not isinstance(diameter, float):
            raise TypeError("diameter must be a float | int")

        if isinstance(height, int):
            height = float(height)
        if not isinstance(height, float):
            raise TypeError("length must be a float | int")

        particle_diameter = ConfigurationService().configuration.particle_diameter
        if isinstance(particle_diameter, int):
            particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float):
            raise TypeError("particle_diameter must be a float | int")

        # Generate local positions inside the cylinder volume (grid sampling)
        particle_spacing = particle_diameter * 1.01
        radius = diameter / 2.0
        local_positions = []
        num_layers = int(height / particle_spacing) + 2
        for k in range(num_layers):
            z_local = -height / 2.0 + k * particle_spacing
            if z_local < -height / 2.0 or z_local > height / 2.0:
                continue
            num_side = int((radius * 2.0) / particle_spacing) + 3
            for i in range(num_side):
                x = -radius + i * (2.0 * radius) / (num_side - 1)
                for j in range(num_side):
                    y = -radius + j * (2.0 * radius) / (num_side - 1)
                    if np.sqrt(x**2 + y**2) <= radius:
                        local_positions.append(np.array([x, y, z_local]))

        world_positions = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*p) for p in local_positions])
        velocities = [pxr.Gf.Vec3f(0, 0, 0)] * len(local_positions)

        return PointData(
            diameter=particle_diameter,
            positions=world_positions,
            velocities=velocities,
        )

    @staticmethod
    def generate_sphere(
        diameter: float | int = 1.0,
    ) -> PointData:
        if isinstance(diameter, int):
            diameter = float(diameter)
        if not isinstance(diameter, float):
            raise TypeError("diameter must be a float | int")

        particle_diameter = ConfigurationService().configuration.particle_diameter
        if isinstance(particle_diameter, int):
            particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float):
            raise TypeError("particle_diameter must be a float | int")
        
        particle_spacing = particle_diameter * 1.01
        radius = diameter / 2.0
        local_positions = []

        num_side = int((radius * 2.0) / particle_spacing) + 2

        for i in range(num_side):
            x = -radius + i * particle_spacing
            if x < -radius or x > radius:
                continue
            for j in range(num_side):
                y = -radius + j * particle_spacing
                if y < -radius or y > radius:
                    continue
                for k in range(num_side):
                    z = -radius + k * particle_spacing
                    if z < -radius or z > radius:
                        continue
                    if np.sqrt(x**2 + y**2 + z**2) <= radius:
                        local_positions.append(np.array([x, y, z]))

        world_positions = pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*p) for p in local_positions])
        velocities = [pxr.Gf.Vec3f(0, 0, 0)] * len(local_positions)

        return PointData(
            diameter=particle_diameter,
            positions=world_positions,
            velocities=velocities,
        )


class Workpiece(ABC):
    _mesh_converters: List[MeshConverter] = [
        WarpMeshConverter(),
        VoxelizeMeshConverter(),
    ]
    _particle_clusterers: List[ParticleClusterer] = [
        KdtreeParticleClusterer(),
        DbscanParticleClusterer(),
    ]
    _particle_converters: List[ParticleConverter] = [
        LocalConvexhullParticleConverter(),
    ]


    @staticmethod
    def get_workpiece(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> Optional["Workpiece"]:
        if FaceWorkpiece.is_a(prim):
            return FaceWorkpiece.get_workpiece(prim)
        
        if PointWorkpiece.is_a(prim):
            return PointWorkpiece.get_workpiece(prim)
        
        return None

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        if FaceWorkpiece.is_a(prim):
            return True
        
        if PointWorkpiece.is_a(prim):
            return True
        
        return False

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        self._stage = stage
    
        if isinstance(prim, pxr.Sdf.Path):
            prim: pxr.Usd.Prim = stage.GetPrimAtPath(str(prim))
        if isinstance(prim, str):
            prim: pxr.Usd.Prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        self._prim = prim

        if not self.is_a(prim):
            raise ValueError(f"prim must be a {self.__class__.__name__}")

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim
    
    def get_model(self) -> WorkpieceModel:
        return WorkpieceModel.model_validate_json(self.prim.GetCustomDataByKey(WorkpieceModel.custom_data_key))

    def set_model(self, workpiece_model: WorkpieceModel) -> None:
        self._set_model(self.prim, workpiece_model)

    @abstractmethod
    def set_material(self, material_data: MaterialProperties) -> None:
        pass

    def get_material(self) -> MaterialProperties:
        data = self._prim.GetCustomDataByKey("amt:dtp:ise:material")
        model = MaterialProperties.model_validate_json(data)
        return model

    def remove(self):
        # Recursively delete the prim and all of its children
        omni.kit.commands.execute("DeletePrims", paths=[str(self._prim.GetPath())])

    @staticmethod
    def _define_visual_material(root: pxr.Usd.Prim | pxr.Sdf.Path | str) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        if isinstance(root, pxr.Sdf.Path):
            root = stage.GetPrimAtPath(str(root))
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        root_path = str(root.GetPath())

        # Create an OmniPBR visual material
        visual_material_path = f"{root_path}/VisualMaterial"
        visual_material = isaacsim.core.experimental.materials.OmniPbrMaterial(visual_material_path)
        visual_material_prim = stage.GetPrimAtPath(visual_material_path)
        if not visual_material_prim.IsValid():
            raise RuntimeError("created invalid visual_material_prim")

        # Create a OmniGlass visual material
        visual_glass_material_path = f"{root_path}/VisualGlassMaterial"
        visual_glass_material = isaacsim.core.experimental.materials.OmniGlassMaterial(visual_glass_material_path)
        visual_glass_material_prim = stage.GetPrimAtPath(visual_glass_material_path)
        if not visual_glass_material_prim.IsValid():
            raise RuntimeError("created invalid visual_glass_material_prim")

    @staticmethod
    def _has_materials(root: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        if isinstance(root, pxr.Sdf.Path):
            root = stage.GetPrimAtPath(str(root))
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not root.IsValid():
            raise ValueError("root must be a valid prim")
        root_path = str(root.GetPath())

        physics_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_path}/PhysicsMaterial")
        if not physics_material_prim.IsValid():
            return False

        visual_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_path}/VisualMaterial")
        if not visual_material_prim.IsValid():
            return False

        visual_glass_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_path}/VisualGlassMaterial")
        if not visual_glass_material_prim.IsValid():
            return False

        if not root.HasCustomDataKey("amt:dtp:ise:material"):
            return False
        
        return True

    @staticmethod
    def _set_visual_material(
        root: pxr.Usd.Prim | pxr.Sdf.Path | str,
        target: pxr.Usd.Prim | pxr.Sdf.Path | str,
        material_data: MaterialProperties,
    ) -> None:
        """
        Set the visual material of a workpiece.

        :param root: The root prim of the workpiece.
        :param target: The target prim that should be bound to the visual material.
        :param material_data: The material data to set.
        """
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        if isinstance(root, pxr.Sdf.Path):
            root = stage.GetPrimAtPath(str(root))
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not root.IsValid():
            raise ValueError("root must be a valid prim")
        root_path = str(root.GetPath())

        if isinstance(target, pxr.Sdf.Path):
            target: pxr.Usd.Prim = stage.GetPrimAtPath(str(target))
        if isinstance(target, str):
            target: pxr.Usd.Prim = stage.GetPrimAtPath(target)
        if not isinstance(target, pxr.Usd.Prim):
            raise TypeError("target must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not target.IsValid():
            raise ValueError("target must be a valid prim")

        if not isinstance(material_data, MaterialProperties):
            raise TypeError("material_data must be a MaterialData")

        visual_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_path}/VisualMaterial")
        if not visual_material_prim.IsValid():
            raise Exception("root is not a Workpiece")
        visual_material = isaacsim.core.experimental.materials.OmniPbrMaterial(f"{root_path}/VisualMaterial")
        visual_material.set_input_values("diffuse_color_constant", np.array(material_data.visual_color))
        visual_material.set_input_values("reflection_roughness_constant", [material_data.visual_roughness]) # 0.5 default
        visual_material.set_input_values("metallic_constant", [material_data.visual_solid_metallic_constant]) # 0.0 default

        visual_glass_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_path}/VisualGlassMaterial")
        if not visual_glass_material_prim.IsValid():
            raise Exception("root is not a Workpiece")
        visual_glass_material = isaacsim.core.experimental.materials.OmniGlassMaterial(f"{root_path}/VisualGlassMaterial")
        visual_glass_material.set_input_values("glass_color", material_data.visual_color)
        visual_glass_material.set_input_values("glass_ior", [material_data.visual_glass_index_of_refraction])                # Index of Refraction (typical glass ~1.5)
        visual_glass_material.set_input_values("thin_walled", [material_data.visual_glass_thin_walled])              # Most objects use thin-walled for performance
        visual_glass_material.set_input_values("depth", [material_data.visual_glass_volume_absorption_scale])                   # Absorption depth / tint strength
        visual_glass_material.set_input_values("frosting_roughness", [material_data.visual_roughness])        # 0 = perfectly smooth mirror-like glass

        bind_path = str(visual_material_prim.GetPath())
        if material_data.visual_glass:
            bind_path = str(visual_glass_material_prim.GetPath())
        bind_material = pxr.UsdShade.Material.Get(stage, bind_path)
        pxr.UsdShade.MaterialBindingAPI(target).Bind(bind_material)

        Workpiece._set_material(root, material_data)

    @staticmethod
    def _set_material(root: pxr.Usd.Prim | pxr.Sdf.Path | str, material_data: MaterialProperties) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")

        if isinstance(root, pxr.Sdf.Path):
            root = stage.GetPrimAtPath(str(root))
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
        if not root.IsValid():
            raise ValueError("root must be a valid prim")
        
        root.SetCustomDataByKey("amt:dtp:ise:material", material_data.model_dump_json())

    @staticmethod
    def _set_model(root: pxr.Usd.Prim | pxr.Sdf.Path | str, workpiece_model: WorkpieceModel) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        if isinstance(root, pxr.Sdf.Path):
            root = stage.GetPrimAtPath(str(root))
        if isinstance(root, str):
            root = stage.GetPrimAtPath(root)
        if not isinstance(root, pxr.Usd.Prim):
            raise TypeError("root must be a pxr.Usd.Prim | pxr.Sdf.Path | str")

        if not isinstance(workpiece_model, WorkpieceModel):
            raise TypeError("workpiece_model must be a WorkpieceModel")
        
        root.SetCustomDataByKey(WorkpieceModel.custom_data_key, workpiece_model.model_dump_json())

    def _convert_to_particle_data(self) -> "PointData":
        if FaceWorkpiece.is_a(self._prim):
            face_workpiece = FaceWorkpiece.get_workpiece(self._prim)
            converter: MeshConverter = WarpMeshConverter()
            if not converter.is_capable():
                converter: MeshConverter = VoxelizeMeshConverter()
            particle_data = converter.convert(
                mesh_data=face_workpiece.get_mesh_data(), 
                diameter=ConfigurationService().configuration.particle_diameter
            )
            return particle_data

        if PointWorkpiece.is_a(self._prim):
            return PointWorkpiece.get_workpiece(self._prim).get_particle_data()

        raise Exception("Could not identify workpiece type")

    def _convert_to_mesh_data(self) -> List["FaceData"]:
        if FaceWorkpiece.is_a(self._prim):
            return [FaceWorkpiece.get_workpiece(self._prim).get_mesh_data()]

        if PointWorkpiece.is_a(self._prim):
            point_workpiece = PointWorkpiece.get_workpiece(self._prim)
            clusterer = KdtreeParticleClusterer()
            #clusterer = DbscanParticleClusterer()
            clusters = clusterer.cluster(point_workpiece.get_particle_data())
            converter = LocalConvexhullParticleConverter()
            #converter = LocalMarchingCubesParticleConverter()
            mesh_datas: List[FaceData] = []
            for cluster in clusters:
                if len(cluster.positions) < 100:
                    continue
                mesh_datas.append(converter.convert(cluster))
            return mesh_datas

        raise Exception("Could not identify workpiece type")
    
    def convert_to_rigid(self, workpiece_cache: WorkpieceCache) -> List["RigidWorkpiece"]:
        if RigidWorkpiece.is_a(self._prim):
            return [self] # Already a RigidWorkpiece

        workpieces: List[RigidWorkpiece] = []
        first = True
        for mesh_data in self._convert_to_mesh_data():
            # Use the same ID for first workpiece, new IDs for new workpieces
            workpiece_model = None
            if first:
                first = False
                workpiece_model = self.get_model()
            w = RigidWorkpiece.define(
                mesh_data=mesh_data,
                workpiece_cache=workpiece_cache,
                material_data=self.get_material(),
                workpiece_model=workpiece_model,
            )
            workpieces.append(w)
        self.remove()
        return workpieces

    def convert_to_deformable(self, workpiece_cache: WorkpieceCache) -> List["DeformableWorkpiece"]:
        if DeformableWorkpiece.is_a(self._prim):
            return [self] # Already a DeformableWorkpiece
        
        max_element_length = ConfigurationService().configuration.maximum_edge_length

        workpieces: List[DeformableWorkpiece] = []
        first = True
        for mesh_data in self._convert_to_mesh_data():
            # Use the same ID for first workpiece, new IDs for new workpieces
            workpiece_model = None
            if first:
                first = False
                workpiece_model = self.get_model()
            w = DeformableWorkpiece.define(
                mesh_data=mesh_data.split_until_max_edge_length(max_element_length),
                workpiece_cache=workpiece_cache,
                material_data=self.get_material(),
                workpiece_model=workpiece_model,
            )
            workpieces.append(w)
        self.remove()
        return workpieces

    def convert_to_particle(self, workpiece_cache: WorkpieceCache) -> "ParticleWorkpiece":
        if ParticleWorkpiece.is_a(self._prim):
            return self # Already a ParticleWorkpiece
        
        particle_data = self._convert_to_particle_data()
        workpiece = ParticleWorkpiece.define(
                point_data=particle_data,
                workpiece_cache=workpiece_cache,
                material_data=self.get_material(),
                workpiece_model=self.get_model(), # Use the same ID
            )
        self.remove()
        return workpiece

    def convert_to_voxel(self, workpiece_cache: WorkpieceCache) -> "VoxelWorkpiece":
        if VoxelWorkpiece.is_a(self._prim):
            return self # Already a VoxelWorkpiece

        particle_data = self._convert_to_particle_data()
        workpiece = VoxelWorkpiece.define(
                particle_data=particle_data,
                workpiece_cache=workpiece_cache,
                material_data=self.get_material(),
                workpiece_model=self.get_model(), # Use the same ID
            )
        self.remove()
        return workpiece

    def __eq__(self, other: "Workpiece") -> bool:
        if not isinstance(other, Workpiece):
            return False
        if not isinstance(other, type(self)):
            return False
        return self._prim.GetPath() == other._prim.GetPath()

    def __hash__(self) -> int:
        return hash(self._prim.GetPath())
    
    def __repr__(self):
        return f"Workpiece({self._prim.GetPath()})"

class FaceWorkpiece(Workpiece, ABC):

    @staticmethod
    def get_workpiece(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> Optional["FaceWorkpiece"]:
        if RigidWorkpiece.is_a(prim):
            return RigidWorkpiece(prim)
        
        if DeformableWorkpiece.is_a(prim):
            return DeformableWorkpiece(prim)
        
        return None

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        if RigidWorkpiece.is_a(prim):
            return True
        
        if DeformableWorkpiece.is_a(prim):
            return True
        
        return False

    @staticmethod
    @abstractmethod
    def define(workpiece_cache: WorkpieceCache, mesh_data: FaceData, material_data: MaterialProperties, workpiece_model: Optional[WorkpieceModel] = None) -> "Workpiece":
        pass

    # def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
    #     super().__init__(prim)

    @abstractmethod
    def get_mesh_data(self) -> FaceData:
        pass

class PointWorkpiece(Workpiece, ABC):

    @staticmethod
    def get_workpiece(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> Optional["PointWorkpiece"]:
        if ParticleWorkpiece.is_a(prim):
            return ParticleWorkpiece(prim)
        
        if VoxelWorkpiece.is_a(prim):
            return VoxelWorkpiece(prim)
        
        return None

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        if ParticleWorkpiece.is_a(prim):
            return True
        
        if VoxelWorkpiece.is_a(prim):
            return True
        
        return False

    @staticmethod
    @abstractmethod
    def define(workpiece_cache: WorkpieceCache, particle_data: PointData, material_data: MaterialProperties, workpiece_model: Optional[WorkpieceModel] = None) -> "Workpiece":
        pass

    # def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
    #     super().__init__(prim)

    @abstractmethod
    def get_particle_data(self) -> PointData:
        pass

class RigidWorkpiece(FaceWorkpiece):

    @staticmethod
    def define(
        workpiece_cache: WorkpieceCache, 
        mesh_data: FaceData, 
        material_data: MaterialProperties, 
        workpiece_model: Optional[WorkpieceModel] = None,
        position: Optional[Tuple[float, float, float] | List[float] | np.ndarray | pxr.Gf.Vec3f | pxr.Gf.Vec3d] = None,
        orientation: Optional[Tuple[float, float, float, float] | List[float] | np.ndarray | pxr.Gf.Quatf | pxr.Gf.Quatd] = None,
    ) -> "RigidWorkpiece":
        if not isinstance(mesh_data, FaceData):
            raise TypeError("mesh_data must be a FaceData")

        if not isinstance(workpiece_cache, WorkpieceCache):
            raise TypeError("workpiece_cache must be a WorkpieceCache")

        if not isinstance(material_data, MaterialProperties):
            raise TypeError("material_data must be a MaterialData")
        
        if workpiece_model is not None:
            if not isinstance(workpiece_model, WorkpieceModel):
                raise TypeError("workpiece_model must be an Optional[WorkpieceModel]")

        if position is not None:
            if isinstance(position, (tuple, list, np.ndarray)):
                if not len(position) == 3: raise ValueError("position must be 3 long")
                if not all(isinstance(p, float) for p in position): raise TypeError("position must be floats")
                position = pxr.Gf.Vec3d(*[float(f) for f in position])
            if isinstance(position, pxr.Gf.Vec3f):
                position = pxr.Gf.Vec3d(*[float(f) for f in position])
            if not isinstance(position, pxr.Gf.Vec3d):
                raise TypeError("position must be a Sequence[float,3]")
            
        if orientation is not None:
            if isinstance(orientation, (tuple, list, np.ndarray)):
                if not len(orientation) == 4: raise ValueError("orientation must be 4 long")
                if not all(isinstance(p, float) for p in position): raise TypeError("position must be floats")
                orientation = pxr.Gf.Quatf(*[float(f) for f in orientation])
            if isinstance(orientation, pxr.Gf.Quatd):
                imag = orientation.GetImaginary()
                orientation = pxr.Gf.Quatf(orientation.GetReal(), imag[0], imag[1], imag[2])
            if not isinstance(orientation, pxr.Gf.Quatf):
                raise TypeError("position must be a Sequence[float,4]")
        
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        # Create the root prim
        root_path = _workpiece_instancer.get_path()
        mesh: pxr.UsdGeom.Mesh = pxr.UsdGeom.Mesh.Define(stage, root_path)
        mesh_data.to_mesh(mesh)
        root_prim = stage.GetPrimAtPath(root_path)
        rigid_api = pxr.UsdPhysics.RigidBodyAPI.Apply(root_prim)
        physx_rigid = pxr.PhysxSchema.PhysxRigidBodyAPI.Apply(root_prim)
        collision_api = pxr.UsdPhysics.CollisionAPI.Apply(root_prim)
        mesh_collision_api = pxr.UsdPhysics.MeshCollisionAPI.Apply(root_prim)
        mesh_collision_api.CreateApproximationAttr().Set("convexDecomposition")

        # Create physics material
        physics_material_path = f"{root_path}/PhysicsMaterial"
        physics_material = isaacsim.core.experimental.materials.RigidBodyMaterial(physics_material_path)
        physics_material_prim = stage.GetPrimAtPath(physics_material_path)
        if not physics_material_prim.IsValid():
            raise RuntimeError("created invalid physics_material_prim")

        # Bind the physics material to the root prim
        material_rel = root_prim.CreateRelationship("material:binding:physics")
        material_rel.SetTargets([pxr.Sdf.Path(physics_material_path)])

        # Author the initial pose as USD transform ops so the body is created at the
        # target pose. PhysX reads these ops as the body's starting transform on the
        # first step, which avoids teleporting the body via set_world_pose in the
        # frame it is spawned (unreliable before its physics view is initialized).
        if position is not None or orientation is not None:
            xformable = pxr.UsdGeom.Xformable(root_prim)
            if position is not None:
                xformable.AddTranslateOp().Set(position)
            if orientation is not None:
                xformable.AddOrientOp().Set(orientation)

        Workpiece._define_visual_material(root_path)
        Workpiece._set_material(root_prim, material_data)
        Workpiece._set_model(root_prim, workpiece_model or WorkpieceModel(id="[EMPTY]"))
        rw = RigidWorkpiece(root_prim)
        rw.set_material(material_data)
        workpiece_cache.ingest(rw)
        return rw
    
    @staticmethod
    def is_a(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> bool:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
    
        if isinstance(prim, pxr.Sdf.Path):
            prim: pxr.Usd.Prim = stage.GetPrimAtPath(str(prim))
        if isinstance(prim, str):
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        if not prim.HasCustomDataKey(WorkpieceModel.custom_data_key):
            return False
        
        if not prim.IsA(pxr.UsdGeom.Mesh):
            return False

        if not prim.IsA(pxr.UsdGeom.Imageable):
            return False
        
        if not prim.HasAPI(pxr.UsdPhysics.RigidBodyAPI):
            return False

        if not prim.HasAPI(pxr.PhysxSchema.PhysxRigidBodyAPI):
            return False
        
        if not prim.HasAPI(pxr.UsdPhysics.CollisionAPI):
            return False
        
        if not prim.HasAPI(pxr.UsdPhysics.MeshCollisionAPI):
            return False

        if not Workpiece._has_materials(prim):
            return False

        return True
    
    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str):
        super().__init__(prim)

        # self._mesh = pxr.UsdGeom.Mesh(self._prim)
        # self._imageable = pxr.UsdGeom.Imageable(self._prim)
        # self._rigid_body_api: pxr.UsdPhysics.RigidBodyAPI = pxr.UsdPhysics.RigidBodyAPI.Get(self._stage, str(self._prim.GetPath()))
        # self._physx_rigid_body_api: pxr.PhysxSchema.PhysxRigidBodyAPI = pxr.PhysxSchema.PhysxRigidBodyAPI.Get(self._stage, str(self._prim.GetPath()))
        
        self._rigid_prim = SingleRigidPrim(str(self._prim.GetPath()))

    def set_material(self, material_data: MaterialProperties) -> None:
        physics_material_prim: pxr.Usd.Prim = self._stage.GetPrimAtPath(f"{self._prim.GetPath()}/PhysicsMaterial")
        if not physics_material_prim.IsValid():
            raise Exception("parent is not a Workpiece")
        physics_material = isaacsim.core.experimental.materials.RigidBodyMaterial(f"{self._prim.GetPath()}/PhysicsMaterial")

        density = material_data.density
        if material_data.rigid_density is not None:
            density = material_data.rigid_density
        physics_material_prim.GetAttribute("physics:density").Set(density)

        friction = material_data.friction
        if material_data.rigid_static_friction is not None:
            friction = material_data.rigid_static_friction
        physics_material.set_friction_coefficients(static_frictions=[friction])

        friction = material_data.friction
        if material_data.rigid_dynamic_friction is not None:
            friction = material_data.rigid_static_friction
        physics_material.set_friction_coefficients(dynamic_frictions=[friction])

        physics_material.set_restitution_coefficients([material_data.rigid_restitution])

        Workpiece._set_visual_material(self._prim, self._prim, material_data)

    def get_mesh_data_local(self) -> FaceData:
        mesh = pxr.UsdGeom.Mesh(self._prim)

        vertex_normals_attr = mesh.GetNormalsAttr()
        vertex_normals = None
        if vertex_normals_attr is not None and vertex_normals_attr.HasValue():
            vertex_normals = vertex_normals_attr.Get()

        # Prefer the authored extent attribute; fall back to computing from vertices
        extents = None
        extent_attr = mesh.GetExtentAttr()
        if extent_attr is not None and extent_attr.HasValue():
            extents = extent_attr.Get()
        else:
            points = mesh.GetPointsAttr().Get()
            if points:
                extents = pxr.UsdGeom.PointBased.ComputeExtent(points)

        return FaceData(
            vertices=mesh.GetPointsAttr().Get() or [],
            face_vertex_counts=mesh.GetFaceVertexCountsAttr().Get() or [],
            face_vertex_indices=mesh.GetFaceVertexIndicesAttr().Get() or [],
            extents=extents,
            vertex_normals=vertex_normals,
        )

    def get_mesh_data(self) -> FaceData:
        pos, ori = self.get_pose_gf()
        world_mesh = self.get_mesh_data_local().transform(pos, ori)
        return world_mesh
    
    def remove(self) -> None:
        # You can not dynamically remove RigidPrims from the stage while playing. If you do,
        #   and you attempt to dynamically spawn more RigidPrims, the physics engine will
        #   throw errors. So, to avoid this issue, disable physics for this workpiece and
        #   make it invisible so it is effectively removed, but not actually removed, to avoid
        #   this isssue.

                # PhysX forbids waking, re-posing, or re-filtering a body once it has
        #   eDISABLE_SIMULATION set (i.e. once its rigid body physics is disabled):
        #     "PxRigidDynamic::wakeUp: Not allowed if eDISABLE_SIMULATION is set!"
        #     "PxScene::resetFiltering(): Not allowed if eDISABLE_SIMULATION is set!"
        #   So the order matters, and the two steps must not happen in the same frame:
        #
        #   1) While the body is still simulation-enabled, disable its collision. This
        #      is a resetFiltering operation (allowed while enabled) that drops the body
        #      out of the contact graph (e.g. a conveyor's surface-velocity belt, which
        #      otherwise keeps trying to wakeUp contacting bodies every substep) and out
        #      of scene queries (overlap_sphere), so a proximity waypoint won't re-detect
        #      it on subsequent frames. Hide it and strip its Workpiece identity now too.
        # Disable collisions
        pxr.UsdPhysics.CollisionAPI(self._prim).CreateCollisionEnabledAttr().Set(False)
        # Make invisible
        pxr.UsdGeom.Imageable(self._prim).CreateVisibilityAttr().Set(pxr.UsdGeom.Tokens.invisible)
        # Remove custom data so the prim is no longer recognized as a Workpiece
        self._prim.ClearCustomDataByKey(WorkpieceModel.custom_data_key)

        #   2) Disable the rigid body simulation only after the collision change above has
        #      been flushed to PhysX (next app update). By then the body is already out of
        #      every contact pair, so setting eDISABLE_SIMULATION won't provoke a wakeUp or
        #      resetFiltering. Doing this in the same frame would set eDISABLE_SIMULATION
        #      before the still-pending collision change is applied, triggering both errors.
        rigid_prim = self._rigid_prim
        async def _disable_simulation_deferred() -> None:
            await omni.kit.app.get_app().next_update_async()
            rigid_prim.disable_rigid_body_physics()
        asyncio.ensure_future(_disable_simulation_deferred())
    
    def get_pose_gf(self) -> Tuple[pxr.Gf.Vec3d, pxr.Gf.Quatf]:
        """
        Get the pose of the workpiece.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        pos, ori = self._rigid_prim.get_world_pose()
        return (pxr.Gf.Vec3d(*pos.tolist()), pxr.Gf.Quatf(*ori.tolist()))

    def get_pose_np(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get the pose of the workpiece.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        return self._rigid_prim.get_world_pose()

    def get_pose_py(self) -> Tuple[List[float], List[float]]:
        """
        Get the pose of the workpiece.

        :return: The position (x, y, z) and orientation (w, x, y, z)
        """
        pos, ori = self._rigid_prim.get_world_pose()
        pos = [float(pos[0]), float(pos[1]), float(pos[2])]
        ori = [float(ori[0]), float(ori[1]), float(ori[2]), float(ori[3])]
        return pos, ori
    
    def set_pose(
            self, 
            position:  Tuple[float, float, float] | List[float] | np.ndarray | pxr.Gf.Vec3f | pxr.Gf.Vec3d = None,
            orientation:  Tuple[float, float, float, float] | List[float] | np.ndarray | pxr.Gf.Quatf | pxr.Gf.Quatd = None,
        ) -> None:
        if position is not None:
            if isinstance(position, (tuple, list, np.ndarray)):
                if len(position) != 3:
                    raise ValueError("position must have a length of 3")
                if not all(isinstance(p, (float, int)) for p in position):
                    raise TypeError("position must be a Sequence[float,3]")
            if isinstance(position, (tuple, list, pxr.Gf.Vec3f, pxr.Gf.Vec3d)):
                position = np.array([float(position[0]), float(position[1]), float(position[2])], dtype=float)
            if not isinstance(position, np.ndarray) or not np.issubdtype(position.dtype, np.floating):
                raise TypeError("position must be a Sequence[float,3]")

        if orientation is not None:
            if isinstance(orientation, (tuple, list, np.ndarray)):
                if len(orientation) != 4:
                    raise ValueError("orientation must have a length of 4")
                if not all(isinstance(o, (float, int)) for o in orientation):
                    raise TypeError("orientation must be a Sequence[float,4]")
            if isinstance(orientation, (pxr.Gf.Quatf, pxr.Gf.Quatd)):
                imag = orientation.GetImaginary()
                orientation = np.array([float(orientation.GetReal()), float(imag[0]), float(imag[1]), float(imag[2])])
            if isinstance(orientation, (tuple, list, np.ndarray)):
                orientation = np.array([float(orientation[0]), float(orientation[1]), float(orientation[2]), float(orientation[3])], dtype=float)
            if not isinstance(orientation, np.ndarray) or not np.issubdtype(orientation.dtype, np.floating):
                raise TypeError("orientation must be a Sequence[float,4]")

        self._rigid_prim.set_world_pose(position, orientation) # takes np arrays

    def get_transform_gf(self) -> Tuple[pxr.Gf.Vec3d, pxr.Gf.Quatf, pxr.Gf.Vec3d]:
        xformable = pxr.UsdGeom.Xformable(self._prim)

        position = None
        pos_attribute = xformable.GetTranslateOp()
        if pos_attribute is not None:
            position = pos_attribute.Get() # Returns pxr.Gf.Vec3d

        orientation = None
        ori_attribute = xformable.GetOrientOp()
        if ori_attribute is not None:
            orientation = ori_attribute.Get() # Returns pxr.Gf.Quatf

        scale = None
        sca_attribute = xformable.GetScaleOp()
        if sca_attribute is not None:
            scale = sca_attribute.Get() # Returns pxr.Gf.Vec3f

        return (position, orientation, pxr.Gf.Vec3d(*scale)) # Use pxr.Gf.Vec3d for consistency

    def get_transform_np(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        pos, ori, sca = self.get_transform_gf()
        if pos is not None:
            pos = np.array[pos[0], pos[1], pos[2]]
        if ori is not None:
            imaginary = ori.GetImaginary()
            ori = np.array([ori.GetReal(), imaginary[0], imaginary[1], imaginary[2]])
        if sca is not None:
            sca = np.array[sca[0], sca[1], sca[2]]
        return pos, ori, sca

    def set_transform(
        self, 
        position: pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float | int] | np.ndarray = None,
        orientation: pxr.Gf.Quatf | List[float | int] | np.ndarray = None,
        scale: pxr.Gf.Vec3d | List[float | int] | np.ndarray = None,
    ) -> None:
        if position is not None:
            if not isinstance(position, (pxr.Gf.Vec3f, pxr.Gf.Vec3d, list, np.ndarray)):
                raise TypeError("position must be a pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float] | np.ndarray")
            if isinstance(position, np.ndarray):
                if len(position) != 3:
                    raise TypeError("position must be a pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float] | np.ndarray")
                position = pxr.Gf.Vec3d(*position)
            if isinstance(position, list):
                if not (len(position) == 3 and all(isinstance(f, (float | int)) for f in position)):
                    raise TypeError("position must be a pxr.Gf.Vec3f | pxr.Gf.Vec3d | List[float] | np.ndarray")
                position = [float(f) for f in position]
                position = pxr.Gf.Vec3d(*position)
            if isinstance(position, pxr.Gf.Vec3f):
                position = pxr.Gf.Vec3d(position[0], position[1], position[2])

        if orientation is not None:
            if not isinstance(orientation, (pxr.Gf.Quatf, list, np.ndarray)):
                raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
            if isinstance(orientation, np.ndarray):
                if len(position) != 4:
                    raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
                orientation = pxr.Gf.Quatf(*orientation)
            if isinstance(orientation, list):
                if not (len(orientation) == 4 and all(isinstance(f, (float, int)) for f in orientation)):
                    raise TypeError("orientation must be a pxr.Gf.Quatf | List[float] | np.ndarray")
                orientation = [float(f) for f in orientation]
                orientation = pxr.Gf.Quatf(*orientation)

        if scale is not None:
            if not isinstance(scale, (pxr.Gf.Vec3d, list, np.ndarray)):
                raise TypeError("scale must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
            if isinstance(scale, np.ndarray):
                if len(scale) != 3:
                    raise TypeError("scale must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
                scale = pxr.Gf.Vec3d(*scale)
            if isinstance(scale, list):
                if not (len(scale) == 3 and all(isinstance(f, (float | int)) for f in scale)):
                    raise TypeError("scale must be a pxr.Gf.Vec3d | List[float] | np.ndarray")
                scale = [float(f) for f in scale]
                scale = pxr.Gf.Vec3d(*scale)

        xformable = pxr.UsdGeom.Xformable(self._prim)

        if position is not None:
            xformable.GetTranslateOp().Set(position) # Set pxr.Gf.Vec3d or pxr.Gf.Vec3f

        if orientation is not None:
            xformable.GetOrientOp().Set(orientation) # Set pxr.Gf.Quatf

        if scale is not None:
            xformable.GetScaleOp().Set(scale) # Set pxr.Gf.Vec3d or pxr.Gf.Vec3f


class DeformableWorkpiece(FaceWorkpiece):
    # TODO: Isaac 5.1.0 requires extra settings to be enabled for this to be visible in the GUI
    #   Edit > Preferences > Physics > General > Enable Deformable Schema Beta (Requires Restart)

    @staticmethod
    def define(workpiece_cache: WorkpieceCache, mesh_data: FaceData, material_data: MaterialProperties, workpiece_model: Optional[WorkpieceModel] = None, maximum_edge_length: Optional[float] = None) -> "DeformableWorkpiece":
        if not isinstance(workpiece_cache, WorkpieceCache):
            raise TypeError("workpiece_cache must be a WorkpieceCache")

        if not isinstance(mesh_data, FaceData):
            raise TypeError("mesh_data must be a FaceData")

        if not isinstance(material_data, MaterialProperties):
            raise TypeError("material_data must be a MaterialData")

        if workpiece_model is not None:
            if not isinstance(workpiece_model, WorkpieceModel):
                raise TypeError("workpiece_model must be an Optional[WorkpieceModel]")

        # Defaults to the global maximum edge length
        if maximum_edge_length is None:
            maximum_edge_length = ConfigurationService().configuration.maximum_edge_length
        if maximum_edge_length is not None and not isinstance(maximum_edge_length, (float, int)):
            raise TypeError("maximum_edge_length must be an Optional[float]")

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        # Create the root prim
        root_path = _workpiece_instancer.get_path()
        xform = pxr.UsdGeom.Xform.Define(stage, root_path)
        root_prim: pxr.Usd.Prim = xform.GetPrim()
        if not root_prim.IsValid():
            raise RuntimeError("created invalid root_prim")
        pxr.UsdPhysics.CollisionAPI.Apply(root_prim)
        pxr.UsdGeom.Imageable(root_prim).CreatePurposeAttr().Set(pxr.UsdGeom.Tokens.render)
        #root_prim.SetActive(True)

        # Create mesh prim
        mesh_path=f"{root_path}/DeformableMesh"
        mesh: pxr.UsdGeom.Mesh = pxr.UsdGeom.Mesh.Define(stage, mesh_path)
        mesh_data.to_mesh(mesh)
        mesh_prim: pxr.Usd.Prim = stage.GetPrimAtPath(mesh_path)
        if not mesh_prim.IsValid():
            raise RuntimeError("created invalid mesh_prim")

        # Create tet mesh prims
        if not omni.physx.scripts.deformableUtils.create_auto_volume_deformable_hierarchy(
            stage = stage,
            root_prim_path = root_prim.GetPath(),
            simulation_tetmesh_path = f"{root_prim.GetPath()}/SimulationTetMesh",
            collision_tetmesh_path = f"{root_prim.GetPath()}/CollisionTetMesh",
            cooking_src_mesh_path = mesh_path,
            simulation_hex_mesh_enabled = True,
            # TEST: False keeps the exact collision thickness (simplification inflated a 5 mm strip to 6.6 mm).
            #   The earlier fall-through with False was solver iterations, not the collision mesh; revert to True if it regresses.
            cooking_src_simplification_enabled = True, #False,
            set_visibility_with_guide_purpose = True
        ):
            raise RuntimeError("could not create auto volume deformable hierarchy")
        # Must follow create_auto_volume_deformable_hierarchy, which removes PhysxBaseDeformableBodyAPI from the root
        root_prim.ApplyAPI("PhysxBaseDeformableBodyAPI")
        # Per-body solver iterations, range [1, 255], default 16. Effective stiffness and contact are iteration-limited,
        #   so too few makes a stiff strip sag like taffy and sink through narrow supports
        root_prim.GetAttribute("physxDeformableBody:solverPositionIterationCount").Set(255)
        simulation_tetmesh_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_prim.GetPath()}/SimulationTetMesh")
        if not simulation_tetmesh_prim.IsValid():
            raise RuntimeError("created invalid simulation_prim")
        collision_tetmesh_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{root_prim.GetPath()}/CollisionTetMesh")
        if not collision_tetmesh_prim.IsValid():
            raise RuntimeError("created invalid collision_prim")
        physxCollisionAPI = pxr.PhysxSchema.PhysxCollisionAPI.Apply(collision_tetmesh_prim)
        # Rest offset is the gap kept between surfaces, so it must be zero for contact geometry to be exact
        physxCollisionAPI.GetContactOffsetAttr().Set(0.0002)
        physxCollisionAPI.GetRestOffsetAttr().Set(0.0)

        # Size the hexahedral simulation mesh elements to the maximum edge length
        #   resolution = number of hexahedral elements along the longest bounding box edge (0 = automatic)
        if maximum_edge_length is not None and maximum_edge_length > 0.0:
            vertices_array = np.array(mesh_data.vertices)
            longest_edge = float((vertices_array.max(axis=0) - vertices_array.min(axis=0)).max())
            resolution = max(1, math.ceil(longest_edge / maximum_edge_length))
            root_prim.GetAttribute("physxDeformableBody:resolution").Set(resolution)

        # Trigger synchronous generation of simulation mesh data
        omni.physx.get_physx_cooking_interface().cook_auto_deformable_body(str(root_prim.GetPath()))

        # Create physics material
        physics_material_path = f"{root_prim.GetPath()}/PhysicsMaterial"
        physics_material: pxr.UsdShade.Material = pxr.UsdShade.Material.Define(stage, physics_material_path)
        physics_material_prim: pxr.Usd.Prim = physics_material.GetPrim()
        physics_material_prim.ApplyAPI("OmniPhysicsBaseMaterialAPI")
        physics_material_prim.ApplyAPI("OmniPhysicsDeformableMaterialAPI")
        physics_material_prim.ApplyAPI("PhysxDeformableMaterialAPI")

        # Bind the physics material to the simulation prim
        binding_api = pxr.UsdShade.MaterialBindingAPI.Apply(simulation_tetmesh_prim)
        binding_api.Bind(physics_material, pxr.UsdShade.Tokens.weakerThanDescendants, "physics")

        Workpiece._define_visual_material(root_path)
        Workpiece._set_material(root_prim, material_data)
        Workpiece._set_model(root_prim, workpiece_model or WorkpieceModel(id="[EMPTY]"))
        dw = DeformableWorkpiece(root_prim)
        dw.set_material(material_data)
        workpiece_cache.ingest(dw)
        return dw
    
    @staticmethod
    def is_a(prim: pxr.Usd.Prim | str) -> bool:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
    
        if isinstance(prim, str):
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        if not prim.HasCustomDataKey(WorkpieceModel.custom_data_key):
            return False
        
        if not prim.IsA(pxr.UsdGeom.Xform):
            return False
        
        if not prim.IsA(pxr.UsdGeom.Imageable):
            return False
        
        deformable_mesh_prim = stage.GetPrimAtPath(f"{prim.GetPath()}/DeformableMesh")
        if deformable_mesh_prim is None or not deformable_mesh_prim.IsValid():
            return False
        
        simulation_tetmesh_prim = stage.GetPrimAtPath(f"{prim.GetPath()}/SimulationTetMesh")
        if simulation_tetmesh_prim is None or not simulation_tetmesh_prim.IsValid():
            return False

        collision_tetmesh_prim = stage.GetPrimAtPath(f"{prim.GetPath()}/CollisionTetMesh")
        if collision_tetmesh_prim is None or not collision_tetmesh_prim.IsValid():
            return False

        Workpiece._has_materials(prim)
        
        return True

    def __init__(self, prim: pxr.Usd.Prim | str):
        super().__init__(prim)

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")

        self._deformable_mesh_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{self._prim.GetPath()}/DeformableMesh")
        if not self._deformable_mesh_prim.IsValid():
            raise Exception("deformable_mesh_prim must be valid")
        self._deformable_mesh = pxr.UsdGeom.Mesh(self._deformable_mesh_prim)

    def set_material(self, material_data: MaterialProperties) -> None:
        physics_material_prim = self._stage.GetPrimAtPath(f"{self._prim.GetPath()}/PhysicsMaterial")
        if not physics_material_prim.IsValid():
            raise Exception("prim is not a Workpiece")
        def set_physical_material_attr(name: str, value_type: pxr.Sdf.ValueTypeName, value):
            attr = physics_material_prim.GetAttribute(name)
            if attr is None or not attr.IsValid():
                attr = physics_material_prim.CreateAttribute(name, value_type)
            attr.Set(value)

        density = material_data.density
        if material_data.deformable_density is not None:
            density = material_data.deformable_density
        set_physical_material_attr("omniphysics:density", pxr.Sdf.ValueTypeNames.Float, density)

        friction = material_data.friction
        if material_data.deformable_static_friction is not None:
            friction = material_data.deformable_static_friction
        set_physical_material_attr("omniphysics:staticFriction", pxr.Sdf.ValueTypeNames.Float, friction)
        
        friction = material_data.friction
        if material_data.deformable_dynamic_friction is not None:
            friction = material_data.deformable_dynamic_friction
        set_physical_material_attr("omniphysics:dynamicFriction", pxr.Sdf.ValueTypeNames.Float, friction)
        
        set_physical_material_attr("omniphysics:youngsModulus", pxr.Sdf.ValueTypeNames.Float, material_data.deformable_youngs_modulus)
        set_physical_material_attr("omniphysics:poissonsRatio", pxr.Sdf.ValueTypeNames.Float, material_data.deformable_poissons_ratio)
        set_physical_material_attr("physxDeformableMaterial:elasticityDamping", pxr.Sdf.ValueTypeNames.Float, material_data.deformable_elasticity_damping)

        Workpiece._set_visual_material(self._prim, self._deformable_mesh_prim, material_data)
        
    def get_mesh_data(self) -> FaceData:

        vertices: pxr.Vt.Vec3fArray = self._deformable_mesh.GetPointsAttr().Get() or []
        if vertices is None or not isinstance(vertices, pxr.Vt.Vec3fArray) or len(vertices) == 0:
            raise Exception("deformable_mesh returned invalid/empty vertices")

        vertex_counts: pxr.Vt.IntArray = self._deformable_mesh.GetFaceVertexCountsAttr().Get() or []
        if vertex_counts is None or not isinstance(vertex_counts, pxr.Vt.IntArray) or len(vertex_counts) == 0:
            raise Exception("deformable_mesh returned invalid/empty vertex_counts")

        vertex_indices: pxr.Vt.IntArray = self._deformable_mesh.GetFaceVertexIndicesAttr().Get() or []
        if vertex_indices is None or not isinstance(vertex_indices, pxr.Vt.IntArray) or len(vertex_indices) == 0:
            raise Exception("deformable_mesh returned invalid/empty vertex_indices")
        
        extents: Optional[pxr.Vt.Vec3fArray] = None
        vertices_array = np.array(vertices)
        min_extent = vertices_array.min(axis=0)
        max_extent = vertices_array.max(axis=0)
        extents: pxr.Vt.Vec3fArray = pxr.Vt.Vec3fArray([
            pxr.Gf.Vec3f(float(min_extent[0]), float(min_extent[1]), float(min_extent[2])),
            pxr.Gf.Vec3f(float(max_extent[0]), float(max_extent[1]), float(max_extent[2])),
        ])

        vertex_normals_attr = self._deformable_mesh.GetNormalsAttr()
        vertex_normals: Optional[pxr.Vt.Vec3fArray] = None
        if vertex_normals_attr is not None and vertex_normals_attr.HasValue():
            vertex_normals = vertex_normals_attr.Get()
        if vertex_normals is not None:
            if not isinstance(vertex_normals, pxr.Vt.Vec3fArray) or len(vertex_normals) == 0:
                raise Exception("deformable_mesh returned invalid vertex_normals")

        return FaceData(
            vertices=vertices,
            face_vertex_counts=vertex_counts,
            face_vertex_indices=vertex_indices,
            extents=extents,
            vertex_normals=vertex_normals,
        )
    
        return FaceData.from_mesh(self._deformable_mesh_prim)
        

class ParticleWorkpiece(PointWorkpiece):

    @staticmethod
    def define(workpiece_cache: WorkpieceCache, point_data: PointData, material_data: MaterialProperties, workpiece_model: Optional[WorkpieceModel] = None) -> "ParticleWorkpiece":
        if not isinstance(workpiece_cache, WorkpieceCache):
            raise TypeError("workpiece_cache must be a WorkpieceCache")
        
        if not isinstance(point_data, PointData):
            raise TypeError("particle_data must be a PointData")

        if not isinstance(material_data, MaterialProperties):
            raise TypeError("material_data must be a MaterialData")

        if workpiece_model is not None:
            if not isinstance(workpiece_model, WorkpieceModel):
                raise TypeError("workpiece_model must be an Optional[WorkpieceModel]")

        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")

        # Make sure global particle system exists
        _workpiece_instancer.create_if_doesnt_exist()
        particle_system_path = _workpiece_instancer.get_particle_system_path()
        particle_system_prim = stage.GetPrimAtPath(particle_system_path)
        if not particle_system_prim.IsValid():
            raise RuntimeError("particle system does not exist")

        # Create a particle set as the root prim
        root_path = _workpiece_instancer.get_path()
        particle_diameters = [point_data.diameter] * len(point_data.positions)
        if point_data.velocities is not None:
            particle_velocities = point_data.velocities
        else:
            particle_velocities = [pxr.Gf.Vec3f(0, 0, 0)] * len(point_data.positions)
        omni.physx.scripts.particleUtils.add_physx_particleset_points(
            stage=stage,
            path=pxr.Sdf.Path(root_path),
            positions_list=pxr.Vt.Vec3fArray(point_data.positions),
            velocities_list=pxr.Vt.Vec3fArray(particle_velocities),
            widths_list=particle_diameters,
            particle_system_path=pxr.Sdf.Path(str(particle_system_prim.GetPath())),
            self_collision=True,
            fluid=material_data.particle_fluid,
            particle_group=0,
            particle_mass=1.0,
            density=1.0,
        )
        root_prim = stage.GetPrimAtPath(root_path)
        if not root_prim.IsValid():
            raise RuntimeError("created invalid particle_set_prim")

        # Create physics material
        particle_physics_material_path = f"{root_path}/PhysicsMaterial"
        omni.physx.scripts.particleUtils.add_pbd_particle_material(
            stage=stage,
            path=pxr.Sdf.Path(particle_physics_material_path),
        )
        physics_material_prim = stage.GetPrimAtPath(particle_physics_material_path)
        if not physics_material_prim.IsValid():
            raise RuntimeError("created invalid particle_physics_material_prim")

        # Bind the physics material to the Particle Set
        material_binding = pxr.UsdShade.MaterialBindingAPI(root_prim)
        material_binding.Bind(pxr.UsdShade.Material(physics_material_prim), pxr.UsdShade.Tokens.weakerThanDescendants, "physics")

        Workpiece._define_visual_material(root_path)
        Workpiece._set_material(root_prim, material_data)
        Workpiece._set_model(root_prim, workpiece_model or WorkpieceModel(id="[EMPTY]"))
        pw = ParticleWorkpiece(root_prim)
        pw.set_material(material_data)
        workpiece_cache.ingest(pw)
        return pw
    
    @staticmethod
    def is_a(prim: pxr.Usd.Prim | str) -> bool:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
    
        if isinstance(prim, str):
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")
        
        if not prim.HasCustomDataKey(WorkpieceModel.custom_data_key):
            return False

        particle_set_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{prim.GetPath()}")
        if particle_set_prim is None or not particle_set_prim.IsValid():
            return False
        if not particle_set_prim.HasAPI(pxr.PhysxSchema.PhysxParticleSetAPI):
            return False

        if not Workpiece._has_materials(prim):
            return False
        
        return True

    def __init__(self, prim: pxr.Usd.Prim | str):
        super().__init__(prim)

    def set_material(self, material_data: MaterialProperties) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        physics_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{self._prim.GetPath()}/PhysicsMaterial")
        if not physics_material_prim.IsValid():
            raise Exception("prim is not a Workpiece")
        
        particle_set_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{self._prim.GetPath()}")
        if not particle_set_prim.IsValid():
            raise Exception("particle_set_prim must be a valid prim")

        density = material_data.density
        if material_data.particle_density is not None:
            density = material_data.particle_density
        particle_set_prim.GetAttribute("physics:density").Set(density)

        friction = material_data.friction
        if material_data.particle_friction is not None:
            friction = material_data.particle_friction
        physics_material_prim.GetAttribute("physxPBDMaterial:friction").Set(friction)

        physics_material_prim.GetAttribute("physxPBDMaterial:viscosity").Set(material_data.particle_viscosity)
        physics_material_prim.GetAttribute("physxPBDMaterial:cohesion").Set(material_data.particle_cohesion)
        physics_material_prim.GetAttribute("physxPBDMaterial:surfaceTension").Set(material_data.particle_surface_tension)
        physics_material_prim.GetAttribute("physxPBDMaterial:damping").Set(material_data.particle_damping)

        Workpiece._set_visual_material(self._prim, particle_set_prim, material_data)

    def get_particle_data(self) -> PointData:
        return PointData(
            diameter=self.get_widths_py()[0],
            positions=self.get_positions_vt(),
            velocities=self.get_velocities_vt(),
        )

    def get_positions_vt(self) -> pxr.Vt.Vec3fArray:
        return self._prim.GetAttribute("points").Get()

    def get_positions_np(self) -> np.ndarray:
        vecs = self.get_positions_vt()
        values: np.ndarray = np.zeros((len(vecs), 3), dtype=np.float64)
        for i in range(len(vecs)):
            vec: pxr.Gf.Vec3f = vecs[i]
            values[i,0] = vec[0]
            values[i,1] = vec[1]
            values[i,2] = vec[2]
        return values

    def get_velocities_vt(self) -> pxr.Vt.Vec3fArray:
        return self._prim.GetAttribute("velocities").Get()
    
    def get_velocities_np(self) -> np.ndarray:
        vecs = self.get_velocities_vt()
        values: np.ndarray = np.zeros((len(vecs), 3), dtype=np.float64)
        for i in range(len(vecs)):
            vec: pxr.Gf.Vec3f = vecs[i]
            values[i,0] = vec[0]
            values[i,1] = vec[1]
            values[i,2] = vec[2]
        return values
    
    def get_velocities_gf(self) -> List[pxr.Gf.Vec3f]:
        return list(self.get_velocities_vt())

    def get_widths_vt(self) -> pxr.Vt.FloatArray:
        return self._prim.GetAttribute("widths").Get()
    
    def get_widths_np(self) -> np.ndarray:
        vecs = self.get_widths_vt()
        return np.array(vecs, dtype=np.float64)
    
    def get_widths_py(self) -> List[float]:
        return list(self.get_widths_vt())


class VoxelWorkpiece(Workpiece):

    @staticmethod
    def define(workpiece_cache: WorkpieceCache, particle_data: PointData, material_data: MaterialProperties, workpiece_model: Optional[WorkpieceModel] = None) -> "VoxelWorkpiece":
        if not isinstance(workpiece_cache, WorkpieceCache):
            raise TypeError("workpiece_cache must be a WorkpieceCache")
        
        if not isinstance(particle_data, PointData):
            raise TypeError("particle_data must be a PointData")
        
        if not isinstance(material_data, MaterialProperties):
            raise TypeError("material_data must be a MaterialData")

        if workpiece_model is not None:
            if not isinstance(workpiece_model, WorkpieceModel):
                raise TypeError("workpiece_model must be an Optional[WorkpieceModel]")
        
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
        
        # Create a PointInstancer which creates/contains instances of a prototype
        root_path = _workpiece_instancer.get_path()
        root_prim = stage.DefinePrim(root_path, "PointInstancer")
        instancer = pxr.UsdGeom.PointInstancer(root_prim)
        
        # A prototype must be visible for instances of the prototype to be visible,
        #   but the prototype shouldn't be visible on the stage. To make the prototype
        #   invisible on the stage but visible itself, wrap the prototype in a scope
        #   and make the scope invisible.
        scope_path = f"{root_path}/InvisibilityScope"
        scope_prim = stage.DefinePrim(scope_path, "Scope")
        pxr.UsdGeom.Imageable(scope_prim)

        # Create a prototype for the instancer to instance
        proto_path = f"{scope_path}/Prototype"
        proto_prim = stage.DefinePrim(proto_path, "Sphere")
        sphere = pxr.UsdGeom.Sphere(proto_prim)
        sphere.CreateRadiusAttr().Set(particle_data.diameter / 2.0)
        pxr.UsdPhysics.CollisionAPI.Apply(proto_prim)
        mesh_collision_api = pxr.UsdPhysics.MeshCollisionAPI.Apply(proto_prim)
        mesh_collision_api.CreateApproximationAttr().Set("boundingSphere")

        # Link instancer to prototype
        proto_rel = instancer.CreatePrototypesRel()
        proto_rel.SetTargets([pxr.Sdf.Path(proto_path)])

        # Set instance indices (all use prototype index 0)
        indices = list(range(len(particle_data.positions)))  # [0, 0, ..., 0] since single prototype
        indices = [0] * len(particle_data.positions)
        instancer.CreateProtoIndicesAttr().Set(indices)

        instancer.CreatePositionsAttr().Set(particle_data.positions)
        #instancer.CreateOrientationsAttr().Set(Vt.QuathArray([Gf.Quath(0, 0, 0, 1)] * num_spheres))
        #instancer.CreateScalesAttr().Set(Vt.Vec3fArray([Gf.Vec3f(1, 1, 1)] * num_spheres))
        #instancer.CreateVelocitiesAttr().Set(pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(0, 0, 0)] * len(positions)))
        #instancer.CreateAngularVelocitiesAttr().Set(pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(0, 0, 0)] * len(positions)))

        #pxr.UsdPhysics.CollisionAPI.Apply(instancer_prim)
        pxr.UsdPhysics.RigidBodyAPI.Apply(root_prim)

        # Create physics material
        physics_material_path = f"{root_path}/PhysicsMaterial"
        physics_material = isaacsim.core.experimental.materials.RigidBodyMaterial(physics_material_path)
        physics_material_prim = stage.GetPrimAtPath(physics_material_path)
        if not physics_material_prim.IsValid():
            raise RuntimeError("created invalid physics_material_prim")

        # Bind the physics material to the mesh prim
        material_rel = root_prim.CreateRelationship("material:binding:physics")
        material_rel.SetTargets([pxr.Sdf.Path(physics_material_path)])

        Workpiece._define_visual_material(root_prim)
        Workpiece._set_material(root_prim, material_data)
        Workpiece._set_model(root_prim, workpiece_model or WorkpieceModel(id="[EMPTY]"))
        vw = VoxelWorkpiece(root_prim)
        vw.set_material(material_data)
        workpiece_cache.ingest(vw)
        return vw

    @staticmethod
    def is_a(prim: pxr.Usd.Prim | str) -> bool:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")
    
        if isinstance(prim, str):
            prim = stage.GetPrimAtPath(prim)
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim | str")
        if not prim.IsValid():
            raise ValueError("prim must be valid")

        if not prim.HasCustomDataKey(WorkpieceModel.custom_data_key):
            return False
        
        if not prim.IsA(pxr.UsdGeom.PointInstancer):
            return False

        if not Workpiece._has_materials(prim):
            return False
        
        return True

    def __init__(self, prim: pxr.Usd.Prim | str):
        super().__init__(prim)

        self._instancer = pxr.UsdGeom.PointInstancer(self._prim)

        if not self.is_a(prim):
            raise Exception("prim must be a VoxelWorkpiece")

        self._scope_prim: pxr.Usd.Prim = self._stage.GetPrimAtPath(f"{self._prim.GetPath()}/InvisibilityScope")
        if not self._scope_prim.IsValid():
            raise ValueError(f"'{self._scope_prim.GetPath()}' must be a valid prim")
        
        self._prototype_prim: pxr.Usd.Prim = self._stage.GetPrimAtPath(f"{self._scope_prim.GetPath()}/Prototype")
        if not self._scope_prim.IsValid():
            raise ValueError(f"'{self._prototype_prim.GetPath()}' must be a valid prim")
        self._prototype = pxr.UsdGeom.Sphere(self._prototype_prim)

    def set_material(self, material_data: MaterialProperties) -> None:
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        if not isinstance(stage, pxr.Usd.Stage):
            raise TypeError("stage must be a pxr.Usd.Stage")

        physics_material_prim: pxr.Usd.Prim = stage.GetPrimAtPath(f"{self._prim.GetPath()}/PhysicsMaterial")
        if not physics_material_prim.IsValid():
            raise Exception("parent is not a Workpiece")
        physics_material = isaacsim.core.experimental.materials.RigidBodyMaterial(f"{self._prim.GetPath()}/PhysicsMaterial")

        density = material_data.density
        if material_data.rigid_density is not None:
            density = material_data.rigid_density
        physics_material_prim.GetAttribute("physics:density").Set(density)

        friction = material_data.friction
        if material_data.rigid_static_friction is not None:
            friction = material_data.rigid_static_friction
        physics_material.set_friction_coefficients(static_frictions=[friction])

        friction = material_data.friction
        if material_data.rigid_dynamic_friction is not None:
            friction = material_data.rigid_static_friction
        physics_material.set_friction_coefficients(dynamic_frictions=[friction])

        physics_material.set_restitution_coefficients([material_data.rigid_restitution])

        Workpiece._set_visual_material(self._prim, self._prim, material_data)

    def get_particle_data(self) -> PointData:
        particle_data = PointData(
            diameter=self.get_diameter_py(),
            positions=self.get_positions_vt(),
            velocities=self.get_velocities_vt(),
        )

        pos, ori, scale = self.get_transform_gf()
        particle_data = particle_data.transform(pos, ori)

        return particle_data

    def get_transform_gf(self) -> Tuple[pxr.Gf.Vec3d, pxr.Gf.Quatf, pxr.Gf.Vec3d]:
        xformable = pxr.UsdGeom.Xformable(self._prim)

        position = None
        pos_attribute = xformable.GetTranslateOp()
        if pos_attribute is not None:
            position = pos_attribute.Get() # Returns pxr.Gf.Vec3d

        orientation = None
        ori_attribute = xformable.GetOrientOp()
        if ori_attribute is not None:
            orientation = ori_attribute.Get() # Returns pxr.Gf.Quatf

        scale = None
        sca_attribute = xformable.GetScaleOp()
        if sca_attribute is not None:
            scale = sca_attribute.Get() # Returns pxr.Gf.Vec3f

        return (position, orientation, pxr.Gf.Vec3d(*scale)) # Use pxr.Gf.Vec3d for consistency

    def get_positions_vt(self) -> pxr.Vt.Vec3fArray:
        positions = self._instancer.GetPositionsAttr().Get()
        return positions
        return pxr.Vt.Vec3fArray(self.get_positions_gf())

    def get_positions_np(self) -> np.ndarray:
        return np.array(self.get_positions_gf())

    def get_positions_gf(self) -> List[pxr.Gf.Vec3f]:
        return list(self.get_positions_vt())
        test = self._instancer.GetPositionsAttr().Get()
        transforms = self._instancer.ComputeInstanceTransformsAtTime(time, pxr.Usd.TimeCode.Default())
        positions = [[m[3][0], m[3][1], m[3][2]] for m in transforms]
        return positions

    def get_diameter_py(self) -> float:
        return self._prototype.GetRadiusAttr().Get() * 2.0
    
    def get_velocities_vt(self) -> pxr.Vt.Vec3fArray:
        return self._instancer.GetVelocitiesAttr().Get(pxr.Usd.TimeCode.Default())


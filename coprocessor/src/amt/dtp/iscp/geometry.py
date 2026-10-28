import trimesh
from typing import List, Optional
import numpy as np

from .lib.coprocessorclient import *

# Trimesh default units are mm
# Isaac default units are m

# def load_stl(file_path: str) -> trimesh.Trimesh:
#     mesh = trimesh.load_mesh(file_path)
#     if isinstance(mesh, trimesh.Scene):  # multi-mesh STL
#         mesh = mesh.dump(concatenate=True)  # combine into one mesh
#     return mesh
#
# def save_stl(mesh: trimesh.Trimesh, file_path: str) -> None:
#     mesh.export(file_path)
#
#
# def repair_mesh(mesh: trimesh.Trimesh):
#     # def repair_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
#     """Make mesh suitable for boolean operations."""
#     m = mesh.copy()  # never modify original
#     #m.remove_degenerate_faces()
#     #m.remove_duplicate_faces()
#     #m.remove_infinite_faces()
#     m.fill_holes()  # critical for watertight
#     #m.fix_normals()
#     #m.merge_vertices(angular_tolerance=0.01)
#     m.update_faces(m.area_faces > 1e-8)  # remove tiny slivers
#     return m
#
# def difference(workpiece: trimesh.Trimesh, tool: trimesh.Trimesh) -> trimesh.Trimesh:
#     result = workpiece.difference(tool, engine="manifold", check_volume=True)
#     return repair_mesh(result)
#
# def union(substrate: trimesh.Trimesh, material: trimesh.Trimesh) -> trimesh.Trimesh:
#     result = substrate.union(material, "manifold", check_volume=True)
#     return repair_mesh(result)

def object_to_tmesh(obj: FaceGeometry) -> trimesh.Trimesh:
    if not isinstance(obj, FaceGeometry):
        raise TypeError("obj must be a FaceGeometry")

    vertices = np.array(obj.vertices, dtype=np.float64)
    faces = np.array(obj.faces, dtype=np.int64)
    vertex_normals = None
    if obj.vertex_normals is not None:
        vertex_normals = np.array(obj.vertex_normals, dtype=np.float64)
    tmesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=vertex_normals, process=True)
    if obj.transform_matrix is not None:
        transform_matrix = np.array(obj.transform_matrix, dtype=np.float64)
        tmesh = tmesh.apply_transform(transform_matrix)
    return tmesh

def objects_to_tmesh(objs: List[FaceGeometry]) -> trimesh.Trimesh:
    if not isinstance(objs, list):
        raise TypeError("objs must be a List[FaceGeometry]")
    if len(objs) == 0:
        raise ValueError("objs can not be an empty list")
    meshes = [object_to_tmesh(obj) for obj in objs]
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else \
        meshes[0]

def geometry_to_tmesh(obj: FaceGeometry) -> trimesh.Trimesh:
    if not isinstance(obj, FaceGeometry):
        raise TypeError("obj must be a FaceGeometry")

    vertices = np.array(obj.vertices, dtype=np.float64)
    faces = np.array(obj.faces, dtype=np.int64)
    vertex_normals = None
    if obj.normals is not None:
        vertex_normals = np.array(obj.normals, dtype=np.float64)
    tmesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=vertex_normals, process=True)
    # if obj.transform_matrix is not None:
    #     transform_matrix = np.array(obj.transform_matrix, dtype=np.float64)
    #     tmesh = tmesh.apply_transform(transform_matrix)
    return tmesh

def combine_tmeshes(meshes: List[trimesh.Trimesh]) -> trimesh.Trimesh:
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else \
        meshes[0]

def geometries_to_tmesh(objs: List[FaceGeometry]) -> trimesh.Trimesh:
    if not isinstance(objs, list):
        raise TypeError("objs must be a List[FaceGeometry]")
    if len(objs) == 0:
        raise ValueError("objs can not be an empty list")
    meshes = [geometry_to_tmesh(obj) for obj in objs]
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else \
        meshes[0]

def repair_mesh(mesh: trimesh.Trimesh, name: str = None) -> trimesh.Trimesh:
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError("mesh must be a Trimesh")
    prefix = ""
    if isinstance(name, str):
        prefix = f"{name} "

    m = mesh.copy()

    print(f"{prefix}Before Repair:")
    print(f"     is_empty: {m.is_empty}")
    print(f"     is_watertight: {m.is_watertight}")
    print(f"     volume: {m.volume:.6f}")
    print(f"     vertices: {len(m.vertices)}")
    print(f"     faces: {len(m.faces)}")

    # Remove degenerate / broken geometry
    #m.remove_degenerate_faces()
    #m.remove_duplicate_faces()
    #m.remove_infinite_faces()

    m.remove_unreferenced_vertices()
    m.remove_infinite_values()

    if not m.is_watertight:

        # Try to close holes
        m.fill_holes()


        # Fix inconsistent winding / normals
        m.fix_normals()

        # Merge close vertices (helps a lot with CAD → mesh conversions)
        #m.merge_vertices(epsilon=1e-5)  # adjust epsilon based on your scale
        m.merge_vertices()

        # Remove zero-area faces
        m.update_faces(m.area_faces > 1e-8)

    print(f"{prefix}After Repair:")
    print(f"     is_empty: {m.is_empty}")
    print(f"     is_watertight: {m.is_watertight}")
    print(f"     volume: {m.volume:.6f}")
    print(f"     vertices: {len(m.vertices)}")
    print(f"     faces: {len(m.faces)}")

    return m

from abc import abstractmethod
import asyncio
import os
import pathlib
from typing import List

import carb
import isaacsim.gui.components.element_wrappers
import isaacsim.gui.components.menu
import omni.kit
import omni.kit.menu.utils
import omni.ui
import omni.usd
import pxr
import pxr.UsdGeom
import pxr.Sdf
import pxr.Usd
import pxr.Vt

from .core import ExtensionMenu
#from ..entity.__waypoint import WaypointDataEntry


class CreateMenu(ExtensionMenu):

    def __init__(self, ext_id: str):

        submenu_items = [
            # omni.kit.menu.utils.MenuItemDescription(
            #     name="MySensor2",
            #     onclick_fn=self._create_custom_prim,
            # ),
            omni.kit.menu.utils.MenuItemDescription(
                name="Marker",
                onclick_fn=self._create_marker_prim
            ),
            omni.kit.menu.utils.MenuItemDescription(
                name="Pointer",
                onclick_fn=self._create_pointer_prim
            ),
            # omni.kit.menu.utils.MenuItemDescription(
            #     name="Hide Waypoint",
            #     onclick_fn=self._hide_waypoint_prim,
            # ),
            # omni.kit.menu.utils.MenuItemDescription(
            #     name="Show Waypoint",
            #     onclick_fn=self._show_waypoint_prim,
            # ),
        ]

        extension_path = pathlib.Path(__file__).parent.parent.parent.parent.resolve()
        glyph_path = os.path.join(extension_path, "data", "icon.svg")
        items = [
            omni.kit.menu.utils.MenuItemDescription(
                name="AMT",
                sub_menu=submenu_items,
                glyph=glyph_path,
            )
        ]

        super().__init__(ext_id, "Create", items)

    def _hide_marker_prim(self):
        stage = omni.usd.get_context().get_stage()
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
        # Get selected prim
        prim_path = selection[0] if selection else None
        prim = stage.GetPrimAtPath(pxr.Sdf.Path(prim_path))
        if prim is None:
            return

        imageable = pxr.UsdGeom.Imageable(prim)
        if imageable is None:
            return

        imageable.MakeInvisible()

    def _show_marker_prim(self):
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
        # Get selected prim
        prim_path = selection[0] if selection else None
        prim = stage.GetPrimAtPath(pxr.Sdf.Path(prim_path))
        if prim is None:
            return

        imageable = pxr.UsdGeom.Imageable(prim)
        if imageable is None:
            return

        prim.GetAttribute("visibility").Set("inherited")
        #imageable.MakeVisible()

    def _create_marker_prim(self):
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
        if selection is None or len(selection) != 1:
            parent_path = ""
            parent_prim = stage.GetPseudoRoot()
        else:
            parent_prim: pxr.Usd.Prim = stage.GetPrimAtPath(pxr.Sdf.Path(selection[0]))
            while parent_prim.GetTypeName() != "Xform":
                if parent_prim is None or parent_prim == stage.GetPseudoRoot():
                    break
                parent_prim = parent_prim.GetParent()
            parent_path_path: pxr.Sdf.Path = parent_prim.GetPath()
            parent_path: str = parent_path_path.pathString
        if parent_path == "/":
            parent_path = "" # Avoid starting path with "//"

        sibling_maker_count = 0
        for prim in parent_prim.GetChildren():
            sibling_path: pxr.Sdf.Path = prim.GetPath()
            sibling_name = sibling_path.pathString.split("/")[-1]
            if sibling_name.startswith("Marker_"):
                sibling_maker_count += 1

        # Path to new prim
        prim_path = f"{parent_path}/Marker_{sibling_maker_count + 1:02d}"

        #prim: pxr.Usd.Prim = stage.DefinePrim(new_path, "AMT-DTP-Waypoint")  # Your custom type
        #prim: pxr.Usd.Prim = stage.DefinePrim(new_path)
        # prim = isaacsim.core.utils.prims.create_prim(
        #     prim_path=new_path,
        #     prim_type="Xform",
        #     semantic_label="MyCustomGroup",
        #     semantic_type="class",
        #     )
        #isaacsim.core.utils.semantics.add_semantics(prim, "MyCustomGrou", "class")

        sphere: pxr.UsdGeom.Sphere = pxr.UsdGeom.Sphere.Define(stage, prim_path)
        prim: pxr.Usd.Prim = sphere.GetPrim()

        # Set radius
        #sphere.GetRadiusAttr().Set(0.0762) # R = 3 in, D = 6 in
        #sphere.GetRadiusAttr().Set(0.0125) # R = 0.5 in, D = 1 in
        sphere.GetRadiusAttr().Set(0.025) # R = 1 in, D = 2 in

        # Set color
        display_color_attr = prim.CreateAttribute(
            "primvars:displayColor",
            pxr.Sdf.ValueTypeNames.Color3fArray
        )
        display_color_attr.Set([(1.0, 1.0, 0.0)])  # RGB

        # Optional: opacity
        display_opacity_attr = prim.CreateAttribute(
            "primvars:displayOpacity",
            pxr.Sdf.ValueTypeNames.FloatArray
        )
        display_opacity_attr.Set([1.0])

        # Ensure it's visible in viewport
        prim.GetAttribute("visibility").Set("inherited")  # or omit (default)
        #cube.GetPrim().SetMetadata("purpose", "default")  # Critical for visibility



        if not prim.IsValid():
            print("Failed to create Marker")
            stage.RemovePrim(prim.GetPath())
            return


        # # Write blank metadata
        # waypoint_data = WaypointDataEntry(True, "", [])
        # waypoint_data.get_editor(prim).write(waypoint_data)

        # Select the newly created prim
        context = omni.usd.get_context()
        sel = context.get_selection()
        sel.clear_selected_prim_paths()
        sel.set_selected_prim_paths([prim.GetPath().pathString])
        sel.set_expand_in_stage_tree(True)

    def _pyramid_geometry(self, direction, width: float, color):
        """Build the geometry for one square-based pyramid.

        direction: vector from base center to apex; its length is the pyramid height.
        width: side length of the square base (both base dimensions).
        color: RGB triplet in [0, 1] applied uniformly to all 5 faces.

        Returns (points, face_vertex_counts, face_vertex_indices, face_normals, face_colors).
        Indices are local (0..4) — the caller offsets them when concatenating.
        """
        direction = pxr.Gf.Vec3f(direction)
        axis = direction.GetNormalized()

        def _cross(a, b):
            return pxr.Gf.Vec3f(
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            )

        # Pick a reference not parallel to axis, then build two in-plane axes.
        ref = pxr.Gf.Vec3f(0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else pxr.Gf.Vec3f(1.0, 0.0, 0.0)
        u = _cross(axis, ref).GetNormalized()
        v = _cross(axis, u)

        half = width / 2.0
        p0 = (-u - v) * half
        p1 = ( u - v) * half
        p2 = ( u + v) * half
        p3 = (-u + v) * half
        apex = direction

        points = [p0, p1, p2, p3, apex]
        face_vertex_counts = [3, 3, 3, 3, 4]
        face_vertex_indices = [
            0, 1, 4,    # Side 1
            1, 2, 4,    # Side 2
            2, 3, 4,    # Side 3
            3, 0, 4,    # Side 4
            0, 3, 2, 1, # Base (outward normal opposite to axis)
        ]
        face_normals = [
            _cross(p1 - p0, apex - p0).GetNormalized(),
            _cross(p2 - p1, apex - p1).GetNormalized(),
            _cross(p3 - p2, apex - p2).GetNormalized(),
            _cross(p0 - p3, apex - p3).GetNormalized(),
            -axis,
        ]
        face_colors = [tuple(color)] * len(face_vertex_counts)

        return points, face_vertex_counts, face_vertex_indices, face_normals, face_colors

    def _create_pointer_prim(self):
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
        if selection is None or len(selection) != 1:
            parent_path = ""
            parent_prim = stage.GetPseudoRoot()
        else:
            parent_prim: pxr.Usd.Prim = stage.GetPrimAtPath(pxr.Sdf.Path(selection[0]))
            while parent_prim.GetTypeName() != "Xform":
                if parent_prim is None or parent_prim == stage.GetPseudoRoot():
                    break
                parent_prim = parent_prim.GetParent()
            parent_path_path: pxr.Sdf.Path = parent_prim.GetPath()
            parent_path: str = parent_path_path.pathString
        if parent_path == "/":
            parent_path = "" # Avoid starting path with "//"

        sibling_pointer_count = 0
        for prim in parent_prim.GetChildren():
            sibling_path: pxr.Sdf.Path = prim.GetPath()
            sibling_name = sibling_path.pathString.split("/")[-1]
            if sibling_name.startswith("Pointer_"):
                sibling_pointer_count += 1

        # Path to new prim
        prim_path = f"{parent_path}/Pointer_{sibling_pointer_count + 1:02d}"

        pyramids = [
            (pxr.Gf.Vec3f(0.05, 0.0, 0.0), 0.0125, (1.0, 0.0, 0.0)),  # X: red
            (pxr.Gf.Vec3f(0.0, 0.05, 0.0), 0.0125, (0.0, 1.0, 0.0)),  # Y: green
            (pxr.Gf.Vec3f(0.0, 0.0, 0.05), 0.0125, (0.0, 0.0, 1.0)),  # Z: blue
        ]

        all_points = []
        all_face_vertex_counts = []
        all_face_vertex_indices = []
        all_face_normals = []
        all_face_colors = []
        index_offset = 0
        for direction, width, color in pyramids:
            points, face_counts, face_indices, face_normals, face_colors = \
                self._pyramid_geometry(direction, width, color)
            all_points.extend(points)
            all_face_vertex_counts.extend(face_counts)
            all_face_vertex_indices.extend(i + index_offset for i in face_indices)
            all_face_normals.extend(face_normals)
            all_face_colors.extend(face_colors)
            index_offset += len(points)

        mesh_schema = pxr.UsdGeom.Mesh.Define(stage, prim_path)
        prim: pxr.Usd.Prim = mesh_schema.GetPrim()
        mesh = pxr.UsdGeom.Mesh(prim)

        mesh.CreatePointsAttr(pxr.Vt.Vec3fArray(all_points))
        mesh.CreateFaceVertexCountsAttr(pxr.Vt.IntArray(all_face_vertex_counts))
        mesh.CreateFaceVertexIndicesAttr(pxr.Vt.IntArray(all_face_vertex_indices))
        mesh.CreateSubdivisionSchemeAttr().Set("none")
        mesh.CreateNormalsAttr(pxr.Vt.Vec3fArray(all_face_normals))
        mesh.SetNormalsInterpolation("uniform")

        # Per-face color so each pyramid gets its own color (5 faces per pyramid).
        display_color_primvar = mesh.CreateDisplayColorPrimvar(interpolation="uniform")
        display_color_primvar.Set(pxr.Vt.Vec3fArray([pxr.Gf.Vec3f(*c) for c in all_face_colors]))

        display_opacity_attr = prim.CreateAttribute(
            "primvars:displayOpacity",
            pxr.Sdf.ValueTypeNames.FloatArray,
        )
        display_opacity_attr.Set([1.0])

        prim.GetAttribute("visibility").Set("inherited")

        if not prim.IsValid():
            print("Failed to create Pointer")
            stage.RemovePrim(prim.GetPath())
            return

        # # Write blank metadata
        # waypoint_data = WaypointDataEntry(True, "", [])
        # waypoint_data.get_editor(prim).write(waypoint_data)

        # Select the newly created prim
        context = omni.usd.get_context()
        sel = context.get_selection()
        sel.clear_selected_prim_paths()
        sel.set_selected_prim_paths([prim.GetPath().pathString])
        sel.set_expand_in_stage_tree(True)

    def _create_custom_prim(self):
        stage = omni.usd.get_context().get_stage()
        selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
        parent_path = selection[0] if selection else "/World"  # Use selected or default parent
        new_path = parent_path + "/MySensor_" + str(len(stage.GetPrimAtPath(parent_path).GetChildren()) + 1)

        prim: pxr.Usd.Prim = stage.DefinePrim(new_path, "MySensor")  # Your custom type

        cube_path = new_path + "/Visual"
        cube = pxr.UsdGeom.Cube.Define(stage, cube_path)

        # Set size (20cm cube)
        cube.GetSizeAttr().Set(0.2)

        # Set color (red)
        display_color_attr = cube.GetPrim().CreateAttribute(
            "primvars:displayColor",
            pxr.Sdf.ValueTypeNames.Color3fArray
        )
        display_color_attr.Set([(1.0, 0.0, 0.0)])  # Red

        # Optional: opacity
        display_opacity_attr = cube.GetPrim().CreateAttribute(
            "primvars:displayOpacity",
            pxr.Sdf.ValueTypeNames.FloatArray
        )
        display_opacity_attr.Set([1.0])

        # Ensure it's visible in viewport
        cube.GetPrim().GetAttribute("visibility").Set("inherited")  # or omit (default)
        #cube.GetPrim().SetMetadata("purpose", "default")  # Critical for visibility

        # Hide it
        cube_prim = cube.GetPrim()
        visibility_attr = cube_prim.GetAttribute("visibility")
        if not visibility_attr:
            visibility_attr = pxr.UsdGeom.Imageable(cube_prim).CreateVisibilityAttr()

        visibility_attr.Set("invisible")
        print(f"Hidden cube at {cube_prim.GetPath()}")

        pxr.UsdGeom.Imageable(cube.GetPrim()).CreateVisibilityAttr().Set("inherited")
        cube.GetPrim().SetMetadata("purpose", "default")

        #api = prim.ApplyAPI("MySensor")

        extension_path = pathlib.Path(__file__).parent.parent.resolve()
        usd_path = os.path.join(extension_path, "data", "dummy.usda")
        # prim = isaacsim.core.utils.prims.create_prim(
        #     prim_path=new_path,
        #     usd_path=usd_path,
        # )
        # isaacsim.core.utils.prims.add_reference_to_stage(
        #     prim_path=new_path,
        #     usd_path=usd_path,
        # )
        if prim.IsValid():
            print(f"Created MySensor at {new_path}")
        else:
            print("Failed to create prim")

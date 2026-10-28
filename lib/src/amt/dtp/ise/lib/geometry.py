import math
from typing import List, Optional, Tuple, Dict

from .models import FaceGeometry, PointGeometry


def _cross(a: List[float] | Tuple[float, ...], b: List[float] | Tuple[float, ...]) -> List[float]:
    return [
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    ]


def _quat_rotate(q_wxyz: List[float] | Tuple[float, ...], v: List[float] | Tuple[float, ...]) -> List[float]:
    w, qx, qy, qz = q_wxyz
    vx, vy, vz = v[0], v[1], v[2]
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return [
        vx + w * tx + (qy * tz - qz * ty),
        vy + w * ty + (qz * tx - qx * tz),
        vz + w * tz + (qx * ty - qy * tx),
    ]


class FaceGenerator:

    @staticmethod
    def generate_cube(
        x: float | int,
        y: float | int,
        z: float | int,
    ) -> FaceGeometry:
        if isinstance(x, int): x = float(x)
        if not isinstance(x, float): raise TypeError("x must be a float | int")
        if isinstance(y, int): y = float(y)
        if not isinstance(y, float): raise TypeError("y must be a float | int")
        if isinstance(z, int): z = float(z)
        if not isinstance(z, float): raise TypeError("z must be a float | int")

        hx, hy, hz = x / 2.0, y / 2.0, z / 2.0

        vertices = [
            [-hx, -hy, -hz],  # 0
            [ hx, -hy, -hz],  # 1
            [ hx,  hy, -hz],  # 2
            [-hx,  hy, -hz],  # 3
            [-hx, -hy,  hz],  # 4
            [ hx, -hy,  hz],  # 5
            [ hx,  hy,  hz],  # 6
            [-hx,  hy,  hz],  # 7
        ]

        faces = [
            [0, 3, 2], [0, 2, 1],  # Bottom (-Z)
            [4, 5, 6], [4, 6, 7],  # Top (+Z)
            [0, 1, 5], [0, 5, 4],  # Front (-Y)
            [1, 2, 6], [1, 6, 5],  # Right (+X)
            [2, 3, 7], [2, 7, 6],  # Back (+Y)
            [3, 0, 4], [3, 4, 7],  # Left (-X)
        ]

        normals = (
            [[0,  0, -1]] * 2 +
            [[0,  0,  1]] * 2 +
            [[0, -1,  0]] * 2 +
            [[1,  0,  0]] * 2 +
            [[0,  1,  0]] * 2 +
            [[-1, 0,  0]] * 2
        )

        return FaceGeometry(vertices=vertices, faces=faces, normals=normals)

    @staticmethod
    def generate_cylinder(
        diameter: float | int,
        length: float | int,
        degree_step: float | int,
    ) -> FaceGeometry:
        if isinstance(diameter, int): diameter = float(diameter)
        if not isinstance(diameter, float): raise TypeError("diameter must be a float | int")
        if diameter <= 0.0: raise ValueError("diameter must be > 0")
        radius = diameter / 2.0

        if isinstance(length, int): length = float(length)
        if not isinstance(length, float): raise TypeError("length must be a float | int")
        if length <= 0.0: raise ValueError("length must be > 0")

        if isinstance(degree_step, int): degree_step = float(degree_step)
        if not isinstance(degree_step, float): raise TypeError("degree_step must be a float | int")
        if degree_step <= 0: raise ValueError("degree_step must be > 0")

        z_top = length * 0.5
        z_bot = -length * 0.5
        segments = max(3, int(round(360.0 / degree_step)))
        dphi = 2.0 * math.pi / segments

        vertices: List[List[float]] = []
        normals: List[List[float]] = []

        def add_vertex(x, y, z, nx, ny, nz) -> int:
            vertices.append([float(x), float(y), float(z)])
            normals.append([float(nx), float(ny), float(nz)])
            return len(vertices) - 1

        side_top_indices = []
        side_bot_indices = []
        for j in range(segments):
            phi = j * dphi
            c, s = math.cos(phi), math.sin(phi)
            vx, vy = radius * c, radius * s
            side_top_indices.append(add_vertex(vx, vy, z_top, c, s, 0.0))
            side_bot_indices.append(add_vertex(vx, vy, z_bot, c, s, 0.0))

        top_center = add_vertex(0.0, 0.0, z_top, 0.0, 0.0, 1.0)
        bot_center = add_vertex(0.0, 0.0, z_bot, 0.0, 0.0, -1.0)

        top_cap_ring, bot_cap_ring = [], []
        for j in range(segments):
            phi = j * dphi
            c, s = math.cos(phi), math.sin(phi)
            vx, vy = radius * c, radius * s
            top_cap_ring.append(add_vertex(vx, vy, z_top, 0.0, 0.0,  1.0))
            bot_cap_ring.append(add_vertex(vx, vy, z_bot, 0.0, 0.0, -1.0))

        faces: List[List[int]] = []

        for j in range(segments):
            jp = (j + 1) % segments
            t0, t1 = side_top_indices[j], side_top_indices[jp]
            b0, b1 = side_bot_indices[j], side_bot_indices[jp]
            faces.append([t0, b0, t1])
            faces.append([t1, b0, b1])

        for j in range(segments):
            jp = (j + 1) % segments
            faces.append([top_center, top_cap_ring[j], top_cap_ring[jp]])

        for j in range(segments):
            jp = (j + 1) % segments
            faces.append([bot_center, bot_cap_ring[jp], bot_cap_ring[j]])

        return FaceGeometry(vertices=vertices, faces=faces, normals=normals)

    @staticmethod
    def generate_sphere(
        diameter: float | int,
        degree_step: float | int,
    ) -> FaceGeometry:
        if isinstance(diameter, int): diameter = float(diameter)
        if not isinstance(diameter, float): raise TypeError("diameter must be a float | int")
        if diameter <= 0.0: raise ValueError("diameter must be > 0")
        radius = diameter / 2.0

        if isinstance(degree_step, int): degree_step = float(degree_step)
        if not isinstance(degree_step, float): raise TypeError("degree_step must be a float | int")
        if degree_step <= 0: raise ValueError("degree_step must be > 0")

        lon_segments = max(3, int(round(360.0 / degree_step)))
        lat_segments = max(2, int(round(180.0 / degree_step)))
        dphi = 2.0 * math.pi / lon_segments
        dtheta = math.pi / lat_segments

        vertices: List[List[float]] = []
        normals: List[List[float]] = []

        def add_vertex(x, y, z):
            vertices.append([float(x), float(y), float(z)])
            inv_r = 1.0 / radius
            normals.append([float(x * inv_r), float(y * inv_r), float(z * inv_r)])

        add_vertex(0.0, 0.0, radius)
        top_index = 0

        ring_start = 1
        for i in range(1, lat_segments):
            theta = i * dtheta
            sin_t, cos_t = math.sin(theta), math.cos(theta)
            z = radius * cos_t
            r_xy = radius * sin_t
            for j in range(lon_segments):
                phi = j * dphi
                add_vertex(r_xy * math.cos(phi), r_xy * math.sin(phi), z)

        bottom_index = len(vertices)
        add_vertex(0.0, 0.0, -radius)

        def ring_index(i_ring, j_lon):
            return ring_start + i_ring * lon_segments + (j_lon % lon_segments)

        rings = lat_segments - 1
        faces: List[List[int]] = []

        if rings >= 1:
            for j in range(lon_segments):
                faces.append([top_index, ring_index(0, j), ring_index(0, j + 1)])

        for r in range(rings - 1):
            for j in range(lon_segments):
                a, b = ring_index(r, j), ring_index(r, j + 1)
                c, d = ring_index(r + 1, j), ring_index(r + 1, j + 1)
                faces.append([a, c, b])
                faces.append([b, c, d])

        if rings >= 1:
            last = rings - 1
            for j in range(lon_segments):
                faces.append([bottom_index, ring_index(last, j + 1), ring_index(last, j)])

        return FaceGeometry(vertices=vertices, faces=faces, normals=normals)


class PointGenerator:

    @staticmethod
    def generate_cube(
        x: float | int,
        y: float | int,
        z: float | int,
        particle_diameter: float | int,
    ) -> PointGeometry:
        if isinstance(x, int): x = float(x)
        if not isinstance(x, float): raise TypeError("x must be a float | int")
        if isinstance(y, int): y = float(y)
        if not isinstance(y, float): raise TypeError("y must be a float | int")
        if isinstance(z, int): z = float(z)
        if not isinstance(z, float): raise TypeError("z must be a float | int")
        if isinstance(particle_diameter, int): particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float): raise TypeError("particle_diameter must be a float | int")

        particle_spacing = particle_diameter * 1.01
        hx, hy, hz = x / 2.0, y / 2.0, z / 2.0
        positions = []

        num_x = int(x / particle_spacing) + 2
        num_y = int(y / particle_spacing) + 2
        num_z = int(z / particle_spacing) + 2

        for i in range(num_x):
            px = -hx + i * particle_spacing
            if px < -hx or px > hx: continue
            for j in range(num_y):
                py = -hy + j * particle_spacing
                if py < -hy or py > hy: continue
                for k in range(num_z):
                    pz = -hz + k * particle_spacing
                    if pz < -hz or pz > hz: continue
                    positions.append([px, py, pz])

        velocities = [[0.0, 0.0, 0.0]] * len(positions)
        return PointGeometry(diameter=particle_diameter, positions=positions, velocities=velocities)

    @staticmethod
    def generate_cylinder(
        diameter: float | int,
        height: float | int,
        particle_diameter: float | int,
    ) -> PointGeometry:
        if isinstance(diameter, int): diameter = float(diameter)
        if not isinstance(diameter, float): raise TypeError("diameter must be a float | int")
        if isinstance(height, int): height = float(height)
        if not isinstance(height, float): raise TypeError("length must be a float | int")
        if isinstance(particle_diameter, int): particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float): raise TypeError("particle_diameter must be a float | int")

        particle_spacing = particle_diameter * 1.01
        radius = diameter / 2.0
        positions = []

        num_layers = int(height / particle_spacing) + 2
        for k in range(num_layers):
            z_local = -height / 2.0 + k * particle_spacing
            if z_local < -height / 2.0 or z_local > height / 2.0: continue
            num_side = int((radius * 2.0) / particle_spacing) + 3
            for i in range(num_side):
                px = -radius + i * (2.0 * radius) / (num_side - 1)
                for j in range(num_side):
                    py = -radius + j * (2.0 * radius) / (num_side - 1)
                    if math.sqrt(px**2 + py**2) <= radius:
                        positions.append([px, py, z_local])

        velocities = [[0.0, 0.0, 0.0]] * len(positions)
        return PointGeometry(diameter=particle_diameter, positions=positions, velocities=velocities)

    @staticmethod
    def generate_sphere(
        diameter: float | int,
        particle_diameter: float | int,
    ) -> PointGeometry:
        if isinstance(diameter, int): diameter = float(diameter)
        if not isinstance(diameter, float): raise TypeError("diameter must be a float | int")
        if isinstance(particle_diameter, int): particle_diameter = float(particle_diameter)
        if not isinstance(particle_diameter, float): raise TypeError("particle_diameter must be a float | int")

        particle_spacing = particle_diameter * 1.01
        radius = diameter / 2.0
        positions = []

        num_side = int((radius * 2.0) / particle_spacing) + 2
        for i in range(num_side):
            px = -radius + i * particle_spacing
            if px < -radius or px > radius: continue
            for j in range(num_side):
                py = -radius + j * particle_spacing
                if py < -radius or py > radius: continue
                for k in range(num_side):
                    pz = -radius + k * particle_spacing
                    if pz < -radius or pz > radius: continue
                    if math.sqrt(px**2 + py**2 + pz**2) <= radius:
                        positions.append([px, py, pz])

        velocities = [[0.0, 0.0, 0.0]] * len(positions)
        return PointGeometry(diameter=particle_diameter, positions=positions, velocities=velocities)


class FaceTransformer:

    @staticmethod
    def transform(
        face_geometry: FaceGeometry,
        position: List[float] | Tuple[float, float, float],
        orientation: Optional[List[float] | Tuple[float, float, float, float]] = None,  # [w,x,y,z], defaults to identity
        transform_normals: bool = True,
    ) -> FaceGeometry:
        """
        Transform geometry based on a offset position and rotation orientation.

        :param point_geometry: The geometry to transform.
        :param position: The position to offset by.
        :param orientation: The orientation to rotate by, defaults to None
        :param defaultstoidentitytransform_velocities: Whether to transform normals, defaults to True
        :return: The transformed geometry.
        """
        if isinstance(position, tuple): position = list(position)
        if not (isinstance(position, list) and len(position) == 3
                and all(isinstance(f, (float, int)) for f in position)):
            raise TypeError("position must be a List[float] of length 3")
        pos = [float(f) for f in position]

        if orientation is None:
            orientation = [1.0, 0.0, 0.0, 0.0]
        if isinstance(orientation, tuple): orientation = list(orientation)
        if not (isinstance(orientation, list) and len(orientation) == 4
                and all(isinstance(f, (float, int)) for f in orientation)):
            raise TypeError("orientation must be a List[float] [w,x,y,z] of length 4")
        q = [float(f) for f in orientation]

        new_verts = []
        for v in face_geometry.vertices:
            rv = _quat_rotate(q, v)
            new_verts.append([rv[0] + pos[0], rv[1] + pos[1], rv[2] + pos[2]])

        new_normals = None
        if face_geometry.normals is not None:
            if transform_normals:
                new_normals = []
                for n in face_geometry.normals:
                    nr = _quat_rotate(q, n)
                    l2 = nr[0]*nr[0] + nr[1]*nr[1] + nr[2]*nr[2]
                    if l2 > 1e-24:
                        inv_l = 1.0 / math.sqrt(l2)
                        nr = [nr[0]*inv_l, nr[1]*inv_l, nr[2]*inv_l]
                    new_normals.append(nr)
            else:
                new_normals = face_geometry.normals

        return FaceGeometry(vertices=new_verts, faces=face_geometry.faces, normals=new_normals)


class PointTransformer:

    @staticmethod
    def transform(
        point_geometry: PointGeometry,
        position: List[float] | Tuple[float, ...],
        orientation: Optional[List[float] | Tuple[float, ...]] = None,  # [w,x,y,z], defaults to identity
        transform_velocities: bool = True,
    ) -> PointGeometry:
        """
        Transform geometry based on a offset position and rotation orientation.

        :param point_geometry: The geometry to transform.
        :param position: The position to offset by.
        :param orientation: The orientation to rotate by, defaults to None
        :param defaultstoidentitytransform_velocities: Whether to transform velocities, defaults to True
        :return: The transformed geometry.
        """
        if isinstance(position, tuple): position = list(position)
        if not (isinstance(position, list) and len(position) == 3
                and all(isinstance(f, (float, int)) for f in position)):
            raise TypeError("position must be a List[float] of length 3")
        pos = [float(f) for f in position]

        if orientation is None:
            orientation = [1.0, 0.0, 0.0, 0.0]
        if isinstance(orientation, tuple): orientation = list(orientation)
        if not (isinstance(orientation, list) and len(orientation) == 4
                and all(isinstance(f, (float, int)) for f in orientation)):
            raise TypeError("orientation must be a List[float] [w,x,y,z] of length 4")
        q = [float(f) for f in orientation]

        new_positions = []
        for p in point_geometry.positions:
            rp = _quat_rotate(q, p)
            new_positions.append([rp[0] + pos[0], rp[1] + pos[1], rp[2] + pos[2]])

        new_velocities = None
        if point_geometry.velocities is not None:
            if transform_velocities:
                new_velocities = [_quat_rotate(q, v) for v in point_geometry.velocities]
            else:
                new_velocities = point_geometry.velocities

        return PointGeometry(
            diameter=point_geometry.diameter,
            positions=new_positions,
            velocities=new_velocities,
        )

class FaceRefiner:

    @staticmethod
    def refine(
        face_geometry: FaceGeometry,
        maximum_edge_length: float,
        recompute_normals: bool = True,
        max_splits: int = 10_000_000,
    ) -> FaceGeometry:
        """
        Refine the face mesh, splitting triangles in half until all edge lengths
        of all triangles are less than a specified maximum edge length.

        :param face_geometry: The face geometry to refine.
        :param maximum_edge_length: The maximum edge length.
        :param recompute_normals: Whether normals should be recomputed, defaults to True
        :param max_splits: The maximum number of splits to cap memory usage, defaults to 2_000_000
        :return: The refined face mesh.
        """
        if maximum_edge_length <= 0.0:
            raise ValueError("maximum_edge_length must be > 0.")
        if any(len(f) != 3 for f in face_geometry.faces):
            raise ValueError("Mesh must be fully triangulated (all faces must have 3 vertices).")

        verts: List[List[float]] = [list(v) for v in face_geometry.vertices]
        work: List[Tuple[int, int, int]] = [tuple(f) for f in face_geometry.faces]
        edge_mid_cache: Dict[Tuple[int, int], int] = {}
        max_len2 = float(maximum_edge_length) ** 2

        def len2(a: int, b: int) -> float:
            pa, pb = verts[a], verts[b]
            dx, dy, dz = pa[0]-pb[0], pa[1]-pb[1], pa[2]-pb[2]
            return dx*dx + dy*dy + dz*dz

        def longest_edge(i0: int, i1: int, i2: int) -> Tuple[str, float]:
            l01, l12, l20 = len2(i0, i1), len2(i1, i2), len2(i2, i0)
            if l01 >= l12 and l01 >= l20: return "edge01", l01
            if l12 >= l01 and l12 >= l20: return "edge12", l12
            return "edge20", l20

        def midpoint_index(i_a: int, i_b: int) -> int:
            key = (i_a, i_b) if i_a < i_b else (i_b, i_a)
            if key in edge_mid_cache:
                return edge_mid_cache[key]
            a, b = verts[i_a], verts[i_b]
            verts.append([0.5*(a[0]+b[0]), 0.5*(a[1]+b[1]), 0.5*(a[2]+b[2])])
            mi = len(verts) - 1
            edge_mid_cache[key] = mi
            return mi

        def tri_face_normal(i0: int, i1: int, i2: int) -> List[float]:
            p0, p1, p2 = verts[i0], verts[i1], verts[i2]
            e1 = [p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]]
            e2 = [p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2]]
            n = _cross(e1, e2)
            ln = math.sqrt(n[0]*n[0] + n[1]*n[1] + n[2]*n[2])
            if ln > 1e-12:
                inv_ln = 1.0 / ln
                n = [n[0]*inv_ln, n[1]*inv_ln, n[2]*inv_ln]
            return n

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
                    f"maximum_edge_length={maximum_edge_length} may be too small for the mesh size."
                )

            if edge == "edge01":
                im = midpoint_index(i0, i1)
                work.append((i0, im, i2))
                work.append((im, i1, i2))
            elif edge == "edge12":
                im = midpoint_index(i1, i2)
                work.append((i1, im, i0))
                work.append((im, i2, i0))
            else:  # edge20
                im = midpoint_index(i2, i0)
                work.append((i2, im, i1))
                work.append((im, i0, i1))

        new_faces = [[a, b, c] for a, b, c in final_tris]
        new_normals = [tri_face_normal(a, b, c) for a, b, c in final_tris] if recompute_normals else None

        return FaceGeometry(vertices=verts, faces=new_faces, normals=new_normals)

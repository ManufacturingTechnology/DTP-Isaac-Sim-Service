---
title: 'Workpieces'
---


# Taxonomy

A taxonomy of workpieces (types and inheritance) are illustrated in the diagram below.

``` mermaid
flowchart BT
    subgraph "Abstract Classes"
        W[Workpiece]
        F[Face] --> W
        Po[Point] --> W
    end
    subgraph "Concrete Phases"
        R[Rigid] --> F
        D[Deformable] --> F
        P[Particle] --> Po
        V[Voxel] --> Po
    end
```

Workpieces fall into one of two classes:

1. Workpieces represented by a mesh of triangular faces
1. Workpieces represented by a collection of spherical points

During the lifecycle of a workpiece, it may undergo transformations into different
phases as illustrated in the diagram below. 
Geometry and phase transformations are orchestrated by manufacturing equipment in
the environment, not through an API.

``` mermaid
flowchart LR
    subgraph "Face"
        R[Rigid]
        D[Deformable]
    end
    subgraph "Point"
        P[Particle]
        V[Voxel]
    end

    R <--> D
    R <--> P
    R <--> V
    D <--> P
    D <--> V
    P <--> V
```

In addition to the geometry of the workpiece, material properties are also defined:

??? "Material Properties"

    ```python title="amt.dtp.ise.lib.models.py"
    --8<-- "lib/src/amt/dtp/ise/lib/models.py:MaterialProperties"
    ```

## Workpiece

A piece of material that is in the process of being worked on or produced in discrete manufacturing.

Material properties are stored in the Isaac Sim `amt:dtp:ise:material` custom property.

All workpieces use an Isaac Sim `OmniPBR` and `OmniGlass` for visual representation.
Which visual representation is utilized depends on the `visual_glass` material property.

## Face (Workpiece)

A workpiece who's representation is based on a surface mesh of triangular faces.

While Isaac Sim tesselations support faces with more than three points,
the ISE restricts workpiece geometry to faces with three points (triangles only)
for compatibility with computational geometry libraries.

## Point (Workpiece)

A workpiece who's representation is based on spherical points.

## Rigid (Face)

A rigid solid who's representation is based on triangular faces.

This phase uses an Isaac Sim `Mesh` and `Rigid Body Material` physics material.

## Deformable (Face)

A deformable solid who's representation is based on triangular faces.

When a deformable workpiece is defined, the triangular faces of the surface geometry
are converted into a 3D tetrahedral (tet) mesh for backend FEA solver calculations. 
The resolution of the tet mesh (how fine or coarse it is) defines the resolution
of simulation results. For example, small workpiece features require a fine mesh in
order to accurately represent them. Since the input triangular faces define the
resolution of the tet mesh, and the tet mesh may not be fine enough for a given
use case, the input triangular faces may need to be refined so that the resulting
tet mesh is finer. The process of refining triangular faces takes time and may
negatively impact the performance of the simulation, causing it to freeze while
calculations occur. To avoid negative impacts on performance, a design decision
was made for the ISE: Before any face workpiece is spawned in an environment, its 
faces must be refined such that the length of all edges of a face triangle must be
less than a global `maximum_face_length` parameter defined the ISE configuration.

This phase uses an Isaac Sim `Xform` container, `Mesh` for visual representation,
a `TetMesh` for simulation, a `TetMesh` for collision, and `DeformableMaterial` physics material.

## Particle (Point)

A deformable collection of particles who's representation is based on spherical points.

The material property `particle_fluid` dictates whether it behaves like a fluid or a non-fluid (e.g., sand).

In the context of fluid simulation in Isaac Sim, a `ParticleSet` is a collection of 3D positions
and diameters of particles in the set, and `ParticleSystem` is a collection of `ParticleSet`s.
In order for `ParticleSet`s to interact with each other, they must be part of the same `ParticleSystem`.

How particles interact with other particles and non-particles is configured through a
number of parameters which are documented in the [Omni Physics Documentation](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/dev_guide/particles/particles.html#particle-system-configuration). 
In particular, there are parameters which specify when particles are in contact or at rest, determined by the distance between them.
In effect, these parameters define how and when particles push against each other.
These parameters are set at the `ParticleSystem` level and apply to all `ParticleSet`s in that system.

A byproduct is that while individual particles may be different diameters, the criteria (distance parameter)
for determining whether they are in contact or at rest is the same for all particles regardless of diameter.
As a result, this can lead to undesireable behavior such as tiny particles visibly not being near each other
but repelling each other due to being in contact, or large particles visibly intersecting but not being in
contact. To avoid this type of behavior, a design decision was made for ISE: All particles must be the
same diameter, as defined by the `particle_diameter` parameter in the ISE configuration. Futhermore,
all particles must be part of the same global `ParticleSystem`.

In addition, visual and physical material properties are set at the `ParticleSet` level, and can not be
modified at the particle level. As a result, changing material properties based on individual particles
and/or location (e.g., changing viscosity based on location or other parameters like 'temperature') is
not possible.

This phase uses an Isaac Sim `ParticleSystem` (global), `ParticleSet` collection of particles,
and `PBD Particle Material` physics material.

## Voxel (Point)

A rigid collection of particles who's representation is based on spherical points.

This phase uses an Isaac Sim `Sphere` prototype, a `Scope` wrapping the prototype to make it invisible,
a `PointInstancer` instantiating instances of the prototype, and a `Rigid Body Material` physics material.


# Tutorial

The following example demonstrates how to spawn various workpieces via API.

```python
#! pip install amt-dtp-ise-lib
import time
from amt.dtp.ise.lib.apiclient import *
from amt.dtp.ise.lib.geometry import FaceGenerator, PointGenerator

client = ApiClient("http://127.0.0.1:8012/")
client.environment_clear()
client.environment_upload("cup.zip")
client.environment_open("cup")
time.sleep(2)
client.timeline_play()

client.workpiece_spawn_rigid_cube(
    WorkpieceSpawnCubeRequest(
        material=MaterialProperties(
            visual_color=[255, 0, 0],
        ),
        x=1.0,
        y=1.0,
        z=1.0,
        position=[0.0, 0.0, 3.0],
    )
)

time.sleep(1)

client.workpiece_spawn_deformable_cylinder(
    WorkpieceSpawnCylinderRequest(
        material=MaterialProperties(
            visual_color=[0, 255, 0],
        ),
        xy_diameter=1.0,
        z_length=1.0,
        position=[0.0, 0.0, 3.0],
    )
)

time.sleep(1)

client.workpiece_spawn_rigid_arbitrary(
    WorkpieceSpawnFaceRequest(
        material=MaterialProperties(
            visual_color=[0, 0, 255],
        ),
        geometry=FaceGenerator.generate_sphere(1.0, 5.0),
        position=[0.0, 0.0, 4.0],
    )
)

time.sleep(1)

client.workpiece_spawn_particle_cube(
    WorkpieceSpawnCubeRequest(
        material=MaterialProperties(
            visual_color=[255, 255, 0],
        ),
        x=1.0,
        y=1.0,
        z=1.0,
        position=[0.0, 0.0, 4.0],
    )
)

time.sleep(1)

particle_diameter = client.extension_configuration_resolved().particle_diameter
client.workpiece_spawn_particle_arbitrary(
    WorkpieceSpawnPointRequest(
        material=MaterialProperties(
            visual_color=[0, 255, 255],
        ),
        geometry=PointGenerator.generate_sphere(1.0, particle_diameter),
        position=[0.0, 0.0, 4.0],
    )
)

time.sleep(1)

time.sleep(2)
client.timeline_stop()
time.sleep(2)
client.environment_close()
```
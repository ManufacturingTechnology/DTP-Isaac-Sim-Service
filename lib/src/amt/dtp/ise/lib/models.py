import os
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Tuple, Literal, ClassVar


# --8<-- [start:ApplicationConfiguration]
class ApplicationConfiguration(BaseModel):
    environments_directory_path: Optional[str] = Field(
        None,
        title="Environments Directory Path",
        description="Path to directory where environments are stored",
    )
    assets_directory_path: Optional[str] = Field(
        None,
        title="Assets Directory Path",
        description="Path to directory where assets are stored",
    )
    particle_diameter: Optional[float] = Field(
        None,
        title="Particle Diameter",
        description="The global diameter of particles in particle workpieces in meters",
    )
    maximum_edge_length: Optional[float] = Field(
        None,
        title="Maximum Edge Length",
        description="The global maximum length of face workpiece edges in meters",
    )
    autostart_coprocessor: Optional[bool] = Field(
        None,
        title="Autostart CoProcessor",
        description="Whether the coprocessor should be automatically started with the ISE.",
    )
    log_level: Optional[Literal["trace", "debug", "info", "warning", "error", "critical"]] = Field(
        None,
        title="Log Level",
        description="The log level",
    )
    hide_waypoints_on_play: Optional[bool] = Field(
        None,
        title="Hide Waypoints on Play",
        description="Whether waypoints should be automatically hidden on play and automatically shown on stop."
    )
    debug_draw_enabled: Optional[bool] = Field(
        None,
        title="Debug Draw Enabled",
        description="Whether debug draw lines and points should be shown",
    )
    kinematics_service_port: Optional[int] = Field(
        None,
        ge=1,
        le=65535,
        title="Kinematics Service Port",
        description="The TCP port of the kinematics service (robot kinematics over a REST API; read when the extension starts).",
    )
# --8<-- [end:ApplicationConfiguration]

# --8<-- [start:ApplicationConfigurationDefault]
    @staticmethod
    def get_default_configuration() -> "ApplicationConfiguration":
        return ApplicationConfiguration(
            environments_directory_path="environments",
            assets_directory_path="assets",
            particle_diameter=0.0254, # one inch
            maximum_edge_length=0.0254, # one inch
            autostart_coprocessor=True,
            log_level="warning",
            hide_waypoints_on_play=False,
            debug_draw_enabled=True,
            kinematics_service_port=8014,
        )
# --8<-- [end:ApplicationConfigurationDefault]


# --8<-- [start:MaterialProperties]
class MaterialProperties(BaseModel):
    # Global
    density: float = Field(
        1_000.0, # Water
        description="The global density in kg/m^3."
    )
    friction: float = Field(
        0.001,
        description="The global friction coefficient (unitless)."
    )

    # Rigid Workpiece
    rigid_density: Optional[float] = Field(
        None,
        description="The density when a rigid workpiece in kg/m^3. " \
        "This overrides the global density."
    )
    rigid_static_friction: Optional[float] = Field(
        None,
        description="The static friction coefficient when a rigid workpiece (unitless). " \
        "This overrides the global friction coefficient."
    )
    rigid_dynamic_friction: Optional[float] = Field(
        None,
        description="The dynamic friction coefficient when a rigid workpiece (unitless). " \
        "This overrides the global friction coefficient."
    )
    rigid_restitution: float = Field(
        0.0,
        description="(unitless)"
    )
    #damping: float = 0.0

    # Deformable Workpiece
    deformable_density: Optional[float] = Field(
        None,
        description="The density when a rigid workpiece in kg/m^3. " \
        "This overrides the global density."
    )
    deformable_static_friction: Optional[float] = Field(
        None,
        description="The static friction coefficient when a rigid workpiece (unitless). " \
        "This overrides the global friction coefficient."
    )
    deformable_dynamic_friction: Optional[float] = Field(
        None,
        description="The dynamic friction coefficient when a rigid workpiece (unitless). " \
        "This overrides the global friction coefficient."
    )
    deformable_youngs_modulus: float = Field(
        1e6,
        description="(Pa)"
    )
    deformable_poissons_ratio: float = Field(
        0.49, # unitless, 0.0 to 0.5
        description=""
    )
    deformable_elasticity_damping: float = Field(
        0.05, # unitless
        description=""
    )

    # Particle Workpiece
    particle_density: Optional[float] = Field(
        None,
        description="The density when a rigid workpiece in kg/m^3. " \
        "This overrides the global density."
    )
    particle_friction: Optional[float] = Field(
        None,
        description="The friction coefficient when a rigid workpiece (unitless). " \
        "This overrides the global friction coefficient."
    )
    particle_fluid: bool = Field(
        True,
        description="Whether the material is a fluid [true] or non-fluid [false] (e.g., sand)."
    )
    particle_viscosity: float = Field(
        8.0,
        description=""
    )
    particle_cohesion: float = Field(
        0.0,
        description=""
    )
    particle_surface_tension: float = Field(
        0.004,
        description=""
    )
    particle_damping: float = Field(
        0.003,
        description=""
    )

    # Visual Properties
    visual_color: Tuple[float, float, float] = Field(
        (0.15, 0.15, 0.15), # Isaac default is (0.2, 0.2, 0.2), use slightly different to differentiate
        description="The RGB color values from 0.0 to 1.0"
    ) 
    visual_roughness: float = Field(
        0.5, # 0.0 to 1.0
        description=""
    )  
    # Visual Properties Solid Only
    visual_solid_metallic_constant: float = Field(
        0.0, # 0.0 to 1.0
        description=""
    ) 
    # Visual Properties Glass Only
    visual_glass: bool = Field(
        False,
        description="Whether the material is glass [true] or normal/opaque [false]."
    )
    visual_glass_index_of_refraction: float = Field(
        1.491, # 0.0 to 4.0
        description=""
    ) 
    visual_glass_volume_absorption_scale: float = Field(
        0.001, # 0.0 to 1.0
        description=""
    )
    visual_glass_thin_walled: bool = Field(
        False,
        description=""
    )
# --8<-- [end:MaterialProperties]


class WorkpieceModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    model_name: ClassVar[str] = "Workpiece"
    custom_data_key: ClassVar[str] = "amt:dtp:workpiece:data"

    id: str = Field(
        #None,
        title="ID",
        description="A unique identifier of a workpiece instance. None if the workpiece hasn't been defined yet.",
    )
    kind_id: Optional[str] = Field(
        None,
        title="Part Kind ID",
        description="A unique identifier for the type of workpiece.",
    )
    state_id: Optional[str] = Field(
        None,
        title="Part State ID",
        description="The state of the workpiece, such as 'raw', 'finished', or 'op20'.",
    )


class FaceGeometry(BaseModel):
    vertices: List[List[float]] = Field(
        description="An array of position vectors representing the position of every vertex in the object",
    )
    faces: List[List[int]] = Field(
        description="An array of connections which represent the indexes of vertexes which form a face."
    )
    normals: Optional[List[List[float]]] = Field(
        default=None,
        description="An array of vectors which represent the normal direction of faces at each vertex."
    )


class PointGeometry(BaseModel):
    diameter: float = Field(
        description="The diameter of every particle in the object.",
    )
    positions: List[List[float]] = Field(
        description="An array of position vectors representing the position of every particle in the object.",
    )
    velocities: Optional[List[List[float]]] = Field(
        default=None,
        description="An array of velocity vectors representing the velocity of every particle in the object.",
    )

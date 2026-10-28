---
title: 'Transducers'
---

# Transducers

!!! info "In this chapter"

    What a transducer is (a sensor, an actuator, or both, on a prim), the base classes, a complete sensor,
    models built per instance, where errors are caught; then the joint and articulation transducers in
    detail, writing your own, and adding configuration.

    **Builds on:** [Behavior Scripts](020-Behavior-Scripts.md), [Models](010-Models.md).

A transducer is a `Sensor`, an `Actuator`, or both, attached to a prim. Transducers are the I/O points of an asset: everything a controller can read or command goes through one.

## Base Classes

```python title="amt.dtp.ise.transducer.core (simplified)"
class Sensor(Transducer, Generic[T]):

    @property
    @abstractmethod
    def actual_model_type(self) -> Type[BaseModel]:
        """The model of the values this sensor measures."""

    @abstractmethod
    def sensor_read(self) -> Optional[T]:
        """Read the actual values (None when no sample is available this step)."""

class Actuator(Transducer, Generic[T]):

    @property
    @abstractmethod
    def command_model_type(self) -> Type[BaseModel]:
        """The model of the commands this actuator accepts."""

    @abstractmethod
    def actuator_write(self, value: T) -> None:
        """Apply a command (its targets)."""

    @abstractmethod
    def actuator_read(self) -> T:
        """Read back the targets in effect."""
```

Both may also offer configuration: `config_model_type` (None by default), `config_read()`, and
`config_write(value)` (see [Add Configuration](#add-configuration)).

Both derive from `Transducer`, whose constructor takes the prim (a `pxr.Usd.Prim`, `pxr.Sdf.Path`, or
path string) and an optional `name`. The name is the prefix of the transducer's dynamically created
model classes (see [Articulations](030-Transducers.md#joints-and-articulations)); without one, the prefix comes from the prim
path, and an [AutomationComponent](040-Automation-Components.md) names an unnamed transducer after its id.

## Sensor Example

```python title="Distance Sensor and Model"
from typing import Optional, Type
from pydantic import BaseModel, Field
from amt.dtp.ise.transducer.core import Sensor

class DistanceModel(BaseModel):
    distance: float = Field(
        title="Distance",
        description="The measured distance in meters.",
    )

class DistanceSensor(Sensor[DistanceModel]):

    @property
    def actual_model_type(self) -> Type[BaseModel]:
        return DistanceModel

    def sensor_read(self) -> Optional[DistanceModel]:
        # Some sensors, such as video cameras, don't provide a value
        #   on every simulation step. This can return None.
        if not self._sample_available():
            return None
        
        d: float = self._get_sample()
        return DistanceModel(
            distance=d,
        )
```

## Models That Depend on the Instance

`actual_model_type` and `command_model_type` are properties of the instance, so a transducer can
return a model built for its own prim. The built-in joint transducers do this: a position-driven
joint's command model carries the joint's limits (from `physics:lowerLimit` / `physics:upperLimit`),
and an articulation's models have one field per joint. Build such models lazily and cache them with
`Transducer._dynamic_model(key, build)`, so an AutomationComponent can name the transducer before the class names
are fixed, and name them with `dynamic_model_name(self.model_prefix, ...)`.

```python
from amt.dtp.ise.lib.control.component import constrained_subclass, dynamic_model_name

@property
def command_model_type(self) -> Type[BaseModel]:
    return self._dynamic_model("actuator", lambda: constrained_subclass(
        dynamic_model_name(self.model_prefix, "Commands"),   # e.g. "Press_Commands"
        PrismaticJointPositionModel,
        "position",
        ge=self._lower_limit,
        le=self._upper_limit,
    ))
```

`constrained_subclass` returns a subclass that also accepts plain `PrismaticJointPositionModel`
instances (converting them and applying the constraints), so code that builds the plain model keeps working.

## Errors: Schema First, Then `actuator_write`

A command can be rejected in two places, and the first is much better for the people controlling the
machine:

1. **By the model (the schema).** A rule in the model (a type, a `Field` limit, a `Literal`) is checked
   wherever the model is used: in the API when a batch of frames is written (the whole batch is rejected,
   nothing runs), in generated client code before anything is sent, and in Swagger's documentation.
2. **By `actuator_write`, when the command is applied.** Raise an exception (e.g. `ValueError` with a
   message saying what to change) for what only the simulation can know: whether an IK target is
   reachable, whether the physics view exists, whether a tool is seated. In a
   [segment API](050-Segments-and-Control-APIs.md#what-is-validated-and-when), the message is reported
   in the response frame of the step that applied the command (the component's `errors`, naming the
   transducer), the frames before it have already run, and the queue
   [halts](050-Segments-and-Control-APIs.md#a-failed-frame-halts-the-queue). To give the error a
   machine-readable code, raise an exception class with an `error_code` attribute (e.g.
   `class GripperJammed(RuntimeError): error_code = "GRIPPER_JAMMED"`); otherwise it is `WRITE_FAILED`.

So put every rule you can into the model, and keep `actuator_write` checks for state that only exists
at runtime. Never fail silently: if a command can't be applied, raise (or, like the robot actuators'
`raise_when_unreachable=False` default, document clearly that it is skipped).
## Joints and Articulations

This guide is for asset builders who need to **read state from** and **write commands to**
joints when building reusable assets. It covers:

- The `Sensor` / `Actuator` / `Transducer` abstractions and how each maps to reading vs. writing.
- Which **stand-alone joints** support reading and/or writing, and which do not.
- Which joints are supported **inside an articulation**, and which are not.
- The units and conventions you must follow.

!!! abstract "The one-sentence model"

    A joint is a piece of hardware. A **`Sensor`** *reads* its state, an **`Actuator`** *writes*
    its command, and a **`Transducer`** does both. Whether a given joint supports reading,
    writing, or neither depends on the joint type **and** on whether it is stand-alone or part
    of an articulation.

### Sensor, Actuator, Transducer

All joint access goes through the abstract base classes in `amt.dtp.ise.transducer.core`.
`Sensor` and `Actuator` are each generic over a [Pydantic](https://pydantic.dev/docs/validation/latest/concepts/models/)
model `T` that describes their data; a joint that can be both sensed and driven derives from both.

``` mermaid
classDiagram
    class Transducer {
        +prim : Usd.Prim
        +name : str
    }
    class Sensor~T~ {
        +actual_model_type : Type[T]
        +sensor_read() T
    }
    class Actuator~T~ {
        +command_model_type : Type[T]
        +actuator_write(value: T)
        +actuator_read() T
    }
    Transducer <|-- Sensor
    Transducer <|-- Actuator
```

| Class | Method | Direction | What it does |
|-------|--------|-----------|--------------|
| `Sensor[T]`   | `sensor_read()`      | **read**  | Returns the joint's *measured state* (position, velocity, effort, reaction forces). |
| `Actuator[T]` | `actuator_write(v)`  | **write** | Commands the joint's drive *target* (a position or velocity command). |
| `Actuator[T]` | `actuator_read()`    | **read**  | Reads back the *commanded target* (not a measurement). |

!!! info "Command vs. state — a critical distinction"

    `sensor_read()` returns **what the joint is actually doing** — a physics measurement.
    `actuator_read()` returns **what the drive was told to do** — the last authored
    target. A joint with no drive can still be *sensed*; a joint that can't be measured can still
    hold a *command*. Keep the two straight.

Every concrete transducer is constructed from a joint prim (a `Usd.Prim`, `Sdf.Path`, or path
string) and exposes an `is_a(prim)` classmethod so you can check compatibility before
constructing.

### Two ways to use a joint

The **same** `UsdPhysics` joint type behaves very differently depending on how it is wired:

- **Stand-alone (maximal-coordinate):** a joint that directly constrains two rigid bodies,
  not part of an articulation tree. Solved in maximal coordinates.
- **In an articulation (reduced-coordinate):** a joint that is a *tree edge* of a
  `UsdPhysics.ArticulationRootAPI` hierarchy. Solved in reduced coordinates.

This distinction determines what data is available:

!!! warning "Stand-alone joints have no measured effort or reaction force"

    A **stand-alone** joint exposes **no** joint-state readback from PhysX. Position and velocity
    can only be *computed* from the two bodies' poses/velocities, and **effort / reaction
    force / torque are unavailable entirely**. A joint **inside an articulation** exposes the
    full measured state (position, velocity, effort, and per-link reaction forces) via the
    physics tensor API.

### Stand-alone joints

| Joint | Read (`Sensor`) | Write (`Actuator`) | Class |
|-------|:---------------:|:------------------:|-------|
| **Revolute**  | ✗ *(not provided)* | ✓ drive target | `RevoluteJointActuator` |
| **Prismatic** | ✗ *(not provided)* | ✓ drive target | `PrismaticJointActuator` |
| **Spherical** | ✓ position + velocity *(computed)* | ✗ **cannot be driven** | `SphericalJointSensor` |
| **D6 (generic)** | ✓ position + velocity *(computed)* | ✓ drive target (per axis) | `D6JointTransducer` |
| **Fixed**     | ✗ no readback | — | `FixedJoint` *(not a Sensor/Actuator)* |
| **Distance**  | — | — | *(not supported)* |
| **Gear**      | — | — | *(not supported)* |
| **Rack & pinion** | — | — | *(not supported)* |

#### Revolute & Prismatic actuators

`RevoluteJointActuator` and `PrismaticJointActuator` are **actuators only**: they write and read the
joint's drive *target*. Which command they take depends on how the drive is configured, which is read
from the drive gains when the actuator is constructed
([UsdPhysics.DriveAPI](https://openusd.org/release/api/class_usd_physics_drive_a_p_i.html):
the drive applies `stiffness * (targetPosition - position)` plus `damping * (targetVelocity - velocity)`):

| Drive gains | Drive mode | Revolute model | Prismatic model |
|---|---|---|---|
| `stiffness > 0` | position | `RevoluteJointPositionModel` (degrees) | `PrismaticJointPositionModel` (meters) |
| `stiffness == 0`, `damping > 0` | velocity | `RevoluteJointVelocityModel` (degrees/s) | `PrismaticJointVelocityModel` (meters/s) |
| both 0 | none | *construction raises: nothing to drive* | |

```python
from amt.dtp.ise.transducer.articulation import RevoluteJointActuator, RevoluteJointPositionModel

hinge = RevoluteJointActuator("/World/Robot/HingeJoint")       # requires a pre-existing drive
hinge.drive_mode                                                 # "position"
hinge.actuator_write(RevoluteJointPositionModel(position=45.0))  # target 45 degrees
current_target = hinge.actuator_read()
```

For a position-driven joint, the command model carries the joint's limits
(`physics:lowerLimit` / `physics:upperLimit`, in degrees for a revolute joint per
[UsdPhysics.RevoluteJoint](https://openusd.org/release/api/class_usd_physics_revolute_joint.html)), so a
target outside them is rejected, both by the actuator and by the [segment API](050-Segments-and-Control-APIs.md#segment-api):

```python
hinge.command_model_type                                        # HingeJoint_Commands (subclass of RevoluteJointPositionModel)
hinge.actuator_write(RevoluteJointPositionModel(position=500))   # ValueError: less than or equal to 90
```

!!! note "A drive must already exist in the scene"

    These actuators require the joint's `UsdPhysics.DriveAPI` (`"angular"` for revolute,
    `"linear"` for prismatic) to be **authored in the scene**. Constructing one over a joint
    with no drive, or with a drive whose stiffness and damping are both 0, raises. The drive mode is
    read once, at construction; changing the gains later isn't reflected in the model.

#### Spherical — sense only

`SphericalJointTransducer` is a **Sensor only**. PhysX's integration does **not** support drives
on spherical joints (stand-alone *or* in an articulation), so there is no actuator. `sensor_read()`
returns the three angular axes (`rotX`, `rotY`, `rotZ`) computed from the bodies.

!!! tip "Need to *drive* three rotational DOFs?"

    Use a **D6 joint** with `rotX`/`rotY`/`rotZ` drives (`D6JointTransducer`), or actuate the
    spherical joint externally with a **spatial tendon**. A spherical joint itself is only ever
    a *sensed* constraint (ball joint with a cone limit).

#### D6 — the fully general stand-alone joint

`D6JointTransducer` is both a `Sensor` and an `Actuator`. It **drives** the axes that already
have a `DriveAPI` and **senses** per-axis position/velocity (computed). Axes use the tokens
`transX/Y/Z` (linear) and `rotX/Y/Z` (angular).

#### Fixed — no readback stand-alone

A stand-alone `FixedJoint` is a weld. It is **not** a `Sensor`/`Actuator`: PhysX exposes no
reaction force for a maximal-coordinate fixed joint in this build. The `FixedJoint` helper class
provides `fix()` / `unfix()` / `is_fixed` for welding bodies together, not data access.

!!! bug "Why stand-alone joints report no effort or reaction forces"

    Reaction forces and effort require the reduced-coordinate solver. For any stand-alone joint,
    only kinematics (position/velocity, derived from body transforms) are recoverable — and even
    those are computed, not read from a joint-state API.

### Joints inside an articulation

An articulation is accessed as a whole through **`ArticulationTransducer`**, which is both a
`Sensor` and an `Actuator` over the entire articulation. Construct it from the articulation **root**
prim (the one carrying `UsdPhysics.ArticulationRootAPI`).

Its models are built for the articulation, with **one field per joint**:

```python
from amt.dtp.ise.transducer.articulation import ArticulationTransducer, PrismaticJointPositionModel

if ArticulationTransducer.is_a(root_path):
    mill = ArticulationTransducer(root_path, name="Mill")

    state = mill.sensor_read()                     # Mill_Actuals
    state.X_Axis.position, state.Vise.forces

    mill.actuator_write({"X_Axis": {"position": 0.1}, "Spindle": {"velocity": 3600}})
    mill.actuator_write(mill.command_model_type(X_Axis=PrismaticJointPositionModel(position=0.1)))
    mill.actuator_read()                           # Mill_Commands
```

| Model | Fields |
|---|---|
| `Mill_Actuals` | One per DOF: `RevoluteJointSensorModel` (angular) or `PrismaticJointSensorModel` (linear), with `drive_mode`, `position`, `velocity`, `effort`, `forces`, `torques`. One per fixed joint: `FixedJointSensorModel` (`forces`, `torques`). |
| `Mill_Commands` | One per drivable DOF. Position-driven: a `Mill_<joint>_Commands` subclass of the `*JointPositionModel` (field `position`) carrying the joint's limits. Velocity-driven: `*JointVelocityModel` (field `velocity`). Joints that are omitted (or `None`) are left unchanged. |

`Mill_Actuals` and `Mill_Commands` subclass `ArticulationSensorModel` and `ArticulationActuatorModel`
(from `amt.dtp.ise.lib.control.transducer.models`), so code can recognize any articulation's models with
`isinstance`.

Field names are the DOF names. A DOF name that isn't a valid Python identifier, such as a spherical
joint's `Spherical:0`, is converted like a prim path (`Spherical_0`). Field names must be unique across
the articulation's moving and fixed joints; construction raises otherwise.

For code that works with joint names directly (such as the robot actuators), `write_joint_positions({name: position})`
and `read_joint_position_targets()` set and get position targets by DOF name.

#### Supported vs. unsupported articulation tree joints

`ArticulationTransducer.is_a()` accepts an articulation only if **every tree joint** is a
supported type. Joints flagged `excludeFromArticulation` are loop constraints (not tree joints)
and are ignored by the check.

| Tree joint | `sensor_read()` (sense) | `actuator_write` / `actuator_read` (drive) | Notes |
|------------|:----------------:|:------------------------------------------:|-------|
| **Revolute**  | ✓ full state | ✓ | position confined to **±360° (±2π)** |
| **Prismatic** | ✓ full state | ✓ | |
| **Spherical** | ✓ full state (3 DOF) | ✗ **not in the actuator model** | can't be driven; still sensed |
| **Fixed**     | ✓ reaction only *(see below)* | — | no DOF |
| **D6 (generic)** | ✗ | ✗ | **rejected** — not an articulation tree joint |
| **Gear / Rack & pinion / Distance** | ✗ | ✗ | **rejected** — couplings, not tree joints |

!!! danger "Generic D6 joints cannot be articulation tree joints"

    PhysX reduced-coordinate articulations only support **fixed, revolute, prismatic, and
    spherical** tree joints. A generic **D6** placed in an articulation tree is silently dropped
    by the parser — its DOFs never appear in the articulation, so it cannot be read or written
    through `ArticulationTransducer`. To use a D6 with an articulation, author it
    `excludeFromArticulation = True` (an external driver/coupling) and access it with the
    stand-alone `D6JointTransducer`.

#### Spherical joints: sensed, not driven

Spherical joints contribute their 3 angular DOFs to the sensor model (you get their measured state),
but they are **not in the actuator model**, consistent with the fact that spherical joints cannot be
driven in this integration. Joints whose drive gains are both 0 are left out of the actuator model too.

#### Fixed joints: reported as reactions

A fixed joint has **no DOF**, but inside an articulation its child link's **reaction force/torque is
measurable**. Each fixed joint has a `FixedJointSensorModel` field in the sensor model, next to the
DOF fields:

``` mermaid
flowchart LR
    R["ArticulationTransducer.sensor_read()"] --> M["Mill_Actuals"]
    M --> J["one field per DOF<br/>(revolute, prismatic, spherical)"]
    M --> FJ["one field per fixed joint<br/>(reaction only)"]
```

### Units and conventions

!!! warning "Angular values are in **degrees**"

    | Quantity | Angular (revolute, spherical, D6 `rot*`) | Linear (prismatic, D6 `trans*`) |
    |----------|------------------------------------------|---------------------------------|
    | Position | **degrees** | **meters** |
    | Velocity | **degrees / second** | **meters / second** |

    Every model in the joint API uses these units. The PhysX tensor API used internally by
    `ArticulationTransducer` is in radians for angular DOFs; the transducer converts at its
    boundary so **you always work in degrees**.

Additional conventions:

- **Each joint takes the command its drive honors:** a position model for a position-driven joint,
  a velocity model for a velocity-driven one.
- **Articulation revolute positions are confined to ±360° (±2π)**, in addition to the joint's own
  limits. Targets outside them are rejected.
- **Reaction forces/torques** in `sensor_read()` are 6-D per-joint reactions expressed in the child
  link's frame; a multi-DOF joint's DOFs all report the same reaction.

### Quick reference

``` mermaid
flowchart TD
    Q{Where is the joint?} -->|Stand-alone| SA
    Q -->|In an articulation| AR

    subgraph SA["Stand-alone joint"]
        RA["Revolute / Prismatic<br/>→ Actuator (drive only)"]
        SP["Spherical<br/>→ Sensor (read only)"]
        D6["D6<br/>→ Transducer (read + drive)"]
        FX["Fixed<br/>→ FixedJoint helper (no data)"]
    end

    subgraph AR["Articulation"]
        AT["ArticulationTransducer<br/>(read + write the whole articulation)"]
        note["Supported tree joints:<br/>fixed, revolute, prismatic, spherical.<br/>D6 / gear / rack / distance: not supported."]
        AT --- note
    end
```

!!! success "Rules of thumb"

    - **Reading full state (with effort & reaction forces)?** The joint must be in an
      articulation — use `ArticulationTransducer`.
    - **Driving a single joint standalone?** Revolute → `RevoluteJointActuator`,
      Prismatic → `PrismaticJointActuator`, arbitrary axes → `D6JointTransducer`. Author a
      `DriveAPI` first.
    - **Spherical joints are never drivable** — sense them, and drive rotation with a D6 or a
      tendon instead.
    - **D6 belongs outside the articulation tree** — as an external (`excludeFromArticulation`)
      driver accessed with `D6JointTransducer`.

## Write a Custom Transducer

How to write an actuator, or a combined sensor and actuator, for a prim. The basics are in [Transducers](030-Transducers.md).

### Actuator Example

```python title="Solenoid Actuator and Model"
from typing import Type
from pydantic import BaseModel, Field
from amt.dtp.ise.transducer.core import Actuator

class SolenoidModel(BaseModel):
    energized: bool = Field(
        title="Energized",
        description="Whether the solenoid is energized.",
    )

class SolenoidActuator(Actuator[SolenoidModel]):

    @property
    def command_model_type(self) -> Type[BaseModel]:
        return SolenoidModel

    def actuator_write(self, value: SolenoidModel) -> None:
        position = 0.0
        if value.energized:
            position = 0.1
        self._prismatic_joint.set_target_position(position)

    def actuator_read(self) -> SolenoidModel:
        position: float = self._prismatic_joint.read_target_position()
        e: bool = position > 0.05
        return SolenoidModel(
            energized=e,
        )
```

### Sensor and Actuator Example

```python title="Motor Transducer and Model"
from typing import Optional, Type
from pydantic import BaseModel, Field
from amt.dtp.ise.transducer.core import Sensor, Actuator

class EncoderModel(BaseModel):
    speed: float = Field(
        title="Speed",
        description="The speed of the motor in RPM.",
    )

class DriveModel(BaseModel):
    speed: float = Field(
        title="Speed",
        description="The speed of the motor in RPM.",
        ge=0.0,
        le=3000.0,
    )

class MotorTransducer(Sensor[EncoderModel], Actuator[DriveModel]):

    @property
    def actual_model_type(self) -> Type[BaseModel]:
        return EncoderModel

    @property
    def command_model_type(self) -> Type[BaseModel]:
        return DriveModel

    def sensor_read(self) -> Optional[EncoderModel]:
        velocity: float = self._revolute_joint.read_velocity()
        return EncoderModel(
            speed=self._convert_deg_s_to_rpm(velocity),
        )

    def actuator_write(self, value: DriveModel) -> None:
        velocity: float = self._convert_rpm_to_deg_s(value.speed)
        self._revolute_joint.set_target_velocity(velocity)

    def actuator_read(self) -> DriveModel:
        velocity: float = self._revolute_joint.get_target_velocity()
        return DriveModel(
            speed=self._convert_deg_s_to_rpm(velocity),
        )
```

## Add Configuration

How to give a transducer acyclic configuration (parameters read and written on request). Why configuration is separate from process data: [Segments and the Control APIs](050-Segments-and-Control-APIs.md#cyclic-and-acyclic-data).

Besides sensor values and commands, which are exchanged every simulation step (**cyclic** data), a
transducer can have a **configuration**: parameters read and changed on request (**acyclic** data),
such as a velocity limit, drive gains, or a tool offset. Configuration is optional, so unlike the sensor
and actuator members these are not abstract: `config_model_type` returns `None` by default, and a
transducer with a configuration overrides it, `config_read`, and `config_write`.

```python
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class ConveyorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_velocity: Optional[float] = Field(None, gt=0, title="Maximum velocity", description="In m/s.")

class LimitedConveyor(ConveyorActuator):

    def __init__(self, prim, name=None):
        super().__init__(prim, name)
        self._max_velocity = 1.0

    @property
    def config_model_type(self) -> type[BaseModel]:
        return ConveyorConfig

    def config_read(self) -> ConveyorConfig:
        return ConveyorConfig(max_velocity=self._max_velocity)

    def config_write(self, value: ConveyorConfig) -> None:
        if "max_velocity" in value.model_fields_set:      # apply only what was sent
            self._max_velocity = value.max_velocity
```

- **The schema is fixed, the values are not.** Return the same model class every time (build a dynamic
  one with `_dynamic_model("config", ...)`); only the values change while the simulation plays. A
  setting that would change a sensor or actuator model (e.g. switching a joint from position to velocity
  control, which changes its command model) is not configuration: it changes the contract with clients,
  so it can only change while stopped.
- **Partial writes.** Give the fields a default of `None` and apply only `value.model_fields_set`, so a
  client can send just what changes.
- An [AutomationComponent](040-Automation-Components.md#configuration) collects the configurable
  transducers into an optional `{Prefix}Config` model (`config_model`, `config_read()`,
  `config_write()`); an AutomationComponent without configurable transducers has none.

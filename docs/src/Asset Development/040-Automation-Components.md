---
title: 'Automation Components'
---

# Automation Components

!!! info "In this chapter"

    How transducers are composed into one controllable machine, an **automation component**: declaratively
    or with a dict, how ids work, how its models are built, reading and writing, and configuration.

    **Builds on:** [Transducers](030-Transducers.md).

An **automation component** (`AutomationComponent`) is a piece of machinery made of [transducers](030-Transducers.md)
(sensors and actuators), each with an **id**. Creating one builds two models from its transducers:

- **`{Name}Actuals`**: one field per sensor id, typed with that sensor's model.
- **`{Name}Commands`**: one field per actuator id, typed with that actuator's model. When writing,
  actuators that are omitted (or `null`) are left unchanged.

These models are what the [segment API](050-Segments-and-Control-APIs.md#segment-api) validates, documents in Swagger, and generates
client code from, so defining an AutomationComponent is all it takes to get a remote API and a typed client for it.
One API can serve several components.

``` mermaid
flowchart LR
    T["Transducers<br/>(Sensor / Actuator models)"] --> E["AutomationComponent<br/>AmtVmA1Actuals / AmtVmA1Commands"]
    E2["Other components"] --> S
    E --> S["SegmentApi<br/>(Swagger + models.py + assets)"]
    S -->|"GET /api/segment/models.py"| C["machining_cell.py<br/>Client (MachiningCellClient)"]
```

## Defining a Component

### Declaratively

Subclass `AutomationComponent` and declare each transducer as an annotated attribute. The attribute name is the
id and the annotation is the transducer class:

```python
from amt.dtp.ise.transducer.core import AutomationComponent, transducer, ref, asset
from amt.dtp.ise.transducer.articulation import ArticulationTransducer, PrismaticJointActuator
from amt.dtp.ise.transducer.misc import ConveyorActuator, DistanceSensor

class MillCell(AutomationComponent):
    """A 3-axis mill with an infeed conveyor."""   # the description
    name = "Mill Cell"                               # defaults to the class name
    version = "0.2.0"                                # defaults to 0.1.0

    mill: ArticulationTransducer = transducer("Mill", name="Mill")
    slide: PrismaticJointActuator = transducer("Slide", description="Part slide")
    infeed: ConveyorActuator = transducer("../Conveyors/Infeed")
    probe: DistanceSensor = transducer("/World/Shared/Probe", maximum_distance=0.5)

cell = MillCell(prim=self.prim_path)
cell.mill.sensor_read()   # each transducer is also an attribute
```

- **Paths** follow USD's [`Sdf.Path`](https://openusd.org/release/api/class_sdf_path.html) rules: a path
  starting with `/` is absolute; any other path is relative to the AutomationComponent's `prim` (`..` goes up a
  level, and `.` is the prim itself).
- **`name=`** sets the prefix of the transducer's model class names (`Mill_Commands`); it defaults to the id.
- **`description=`** is shown in the API documentation for that field.
- **Model class names** start with the class name (`MillCell` → `MillCellActuals`, `MillCellCommands`),
  so every instance of the class (e.g. two identical machines) has the same class names.
- Other keyword arguments are passed to the transducer's constructor. `ref("attribute")` passes another
  transducer declared earlier (e.g. `tool_changer: ToolChanger = transducer(robot=ref("robot"))`), and
  `asset("file.xrdf")` a file next to the module that defines the AutomationComponent.

### With a dict

```python
component = AutomationComponent("Mill Cell", items={
    "mill": ArticulationTransducer(f"{self.prim_path}/Mill"),
    "infeed": ConveyorActuator(f"{self.prim_path}/Conveyor"),
}, description="A 3-axis mill", version="0.2.0")
```

A sequence of transducers also works; each id is then its prim path with `/` replaced by `_` and the
leading `_` dropped (`/World/Mill` becomes `World_Mill`).

### Ids

Ids become model field names, JSON keys, and attributes in generated Python code, so each must be a
valid Python identifier that isn't a keyword, doesn't start with `_` (pydantic would silently drop it)
or `model_`, and isn't the name of a pydantic `BaseModel` attribute (such as `json` or `copy`). Ids must
be unique among the sensors and among the actuators; a transducer that is both a sensor and an actuator
has the same id in both models.

Every problem is reported at once, with how to fix it:

```text
ComponentDefinitionError: AutomationComponent 'Mill Cell' has 2 problems:
  1. id of PrismaticJointActuator at /World/Cell/Slide: 'class' is a Python keyword, so generated
     client code could not use it as an attribute.
     Fix: choose another id.
  2. two different model classes are named 'Same_Commands': ...
```

Model class names must also be unique within an AutomationComponent, since generated client code has one
namespace. Give one of the transducers a different `name=` to fix a collision.

## Reading and Writing

```python
sensors = cell.sensor_read()        # MillCellActuals
actuators = cell.actuator_read()    # MillCellCommands

cell.actuator_write({"mill": {"X_Axis": {"position": 0.1}}, "infeed": {"velocity": 0.2}})
cell.actuator_write(cell.command_model(slide=PrismaticJointPositionModel(position=0.05)))
```

## Configuration

Transducers with a [configuration](030-Transducers.md#add-configuration) are collected
into an optional `{Prefix}Config` model, one optional field per configurable transducer. An AutomationComponent
without configurable transducers has no configuration (`config_model` is `None`).

```python
cell.config_model                                   # MillCellConfig (if e.g. infeed is a LimitedConveyor), or None
cell.config_read()                                  # MillCellConfig(infeed=ConveyorConfig(max_velocity=1.0))
cell.config_write({"infeed": {"max_velocity": 0.5}})  # only what is sent changes
```

Like `actuator_write`, `config_write` tries every transducer in the value and then raises a
`ComponentWriteError` naming the ones that failed; the others keep their new configuration.

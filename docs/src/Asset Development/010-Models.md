---
title: 'Models'
---

# Models

!!! info "In this chapter"

    Why every value that crosses an asset's boundary is a typed model, the model features this project
    relies on, and the models that ship with the library. Everything later builds on this.

Every value that crosses an asset's boundary (a joint target, a sensor reading, a configuration) is a
[pydantic](https://pydantic.dev/docs/validation/latest/concepts/models/) model. This page explains why, and the
few model features this project depends on.

## Why models drive everything

A model is a schema: it validates data, converts it to and from JSON, and describes itself as a JSON
Schema. One definition therefore produces:

- **validation** wherever the model is used: in transducers, in the API when a batch of frames is written,
  and in client code before anything is sent;
- **API documentation:** Swagger shows every field, unit, and limit;
- **generated client code:** clients download typed Python models for the exact machine;
- a **schema fingerprint**, so a client knows when a machine's interface has changed.

```python
from pydantic import BaseModel, Field

class DistanceModel(BaseModel):
    distance: float = Field(
        title="Distance",
        description="The distance in meters.",
    )

DistanceModel(distance=0.25)                              # validated
DistanceModel.model_validate_json('{"distance": 0.25}')   # parsed from JSON
DistanceModel(distance="far")                             # raises ValidationError
DistanceModel.model_json_schema()                         # the JSON Schema
```

## Rules this project relies on

!!! tip "Put rules in `Field`, not in validators"

    Constraints written with `Field(ge=..., le=..., pattern=...)` or `Literal[...]` are part of the JSON
    Schema, so they appear in the API documentation and in
    [generated client code](050-Segments-and-Control-APIs.md#python-client). Logic in a
    `@field_validator` only runs on the server: generated client code can't include it (a warning is
    logged when a model's validators or methods are left out).

!!! tip "Forbid unknown fields"

    Add `model_config = ConfigDict(extra="forbid")` to your models, as the built-in models do. Otherwise
    pydantic ignores a misspelt field, and a command with a typo silently leaves the actuator unchanged.
    A segment API logs a warning naming each of its models that doesn't forbid unknown fields.

- **Optional means "unchanged".** In commands, every level above a single value is optional: an omitted
  component, actuator, or joint is left as it is. A single value (a joint position, a pose) is sent whole.
- **Models built per instance.** A joint's model carries that joint's USD limits, and an articulation's
  model has one field per joint. These classes are created at runtime (`create_model`), when the
  transducer is built, and are fixed while the simulation plays.
- **Subclasses accept parent instances.** A per-joint subclass (e.g. a revolute position model limited to
  ±90°) accepts an instance of its parent class and applies its own limits (`from_attributes`), so code
  that builds the plain built-in model keeps working.

## Built-in Models

The models that ship with `amt-dtp-ise-lib` (`amt.dtp.ise.lib.control.transducer.models`).

The built-in models are in `amt.dtp.ise.lib.control.transducer.models`, which is part of the
`amt-dtp-ise-lib` package, so client applications can use them too. They are also importable from the
transducer modules that use them (e.g. `amt.dtp.ise.transducer.articulation`).

```python title="Joint command models"
--8<-- "lib/src/amt/dtp/ise/lib/control/transducer/models/joint.py:JointActuatorModels"
```

```python title="Articulation models"
--8<-- "lib/src/amt/dtp/ise/lib/control/transducer/models/articulation.py:ArticulationModels"
```

```python title="Color model"
--8<-- "lib/src/amt/dtp/ise/lib/control/transducer/models/misc.py:ColorModel"
```

`ColorModel()` (all channels `None`) means colorless: the `ColorActuator` removes its color override.

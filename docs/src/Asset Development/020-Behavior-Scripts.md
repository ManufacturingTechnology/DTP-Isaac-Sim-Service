---
title: 'Behavior Scripts'
---

# Behavior Scripts

!!! info "In this chapter"

    Your first behavior script (a slide that moves back and forth), then the lifecycle in detail: when your
    code runs inside Isaac Sim, where each kind of work belongs, and how to give an asset typed
    configuration that people can edit without touching code.

    **Builds on:** [Models](010-Models.md).

## Your First Script: Make a Slide Move

!!! warning "Draft"

    The menu paths below were collected from the existing robot rigging notes. Ones marked ❓ have not been
    checked step by step in Isaac Sim 6.

### 1. Build the slide

1. Create a new stage. Add a **Cube** named `Base` and scale it into a long, flat rail (e.g. 1 × 0.1 × 0.05 m).
2. Add a second **Cube** named `Carriage` on top of the rail. Select it and add
   **Add > Physics > Rigid Body with Colliders Preset**.
3. Select `Base`, then `Carriage`, and create **Create > Physics > Joint > Prismatic Joint** ❓. Name it
   `Slide`, and set its axis to `X`.
4. On the joint, set **lower limit** 0 and **upper limit** 0.5 (meters), and add a **linear drive** with a
   stiffness above 0 (e.g. 10000) and some damping (e.g. 1000). A stiffness above 0 makes it a
   *position*-driven joint.

### 2. Write a behavior script

Save this as `slide.py` next to your stage:

```python
import math
from amt.dtp.ise.scripting import BehaviorScript
from amt.dtp.ise.transducer.articulation import PrismaticJointActuator, PrismaticJointPositionModel

class Slide(BehaviorScript):

    def on_play(self):
        # The transducer: an actuator on the prismatic joint.
        self._slide = PrismaticJointActuator(f"{self.prim_path}/Slide")

    def on_physics(self, current_time: float, delta_time: float):
        # Move between 0 and 0.5 m, once every 4 seconds.
        position = 0.25 + 0.25 * math.sin(2 * math.pi * current_time / 4.0)
        self._slide.actuator_write(PrismaticJointPositionModel(position=position))
```

### 3. Attach it and play

1. Select the prim that contains the joint (the parent of `Slide`). Add the **Python Scripting** component
   to it ❓ and add `slide.py` to its **Scripts** list. The extension loads the `BehaviorScript` classes
   from the scripts listed there.
2. Press **Play**. The carriage slides back and forth.

### What just happened

- The **behavior script** (`Slide`) is your code's entry point. Isaac Sim constructs it when you press
  Play; `on_play` runs once, and `on_physics` runs once per physics step.
- The **transducer** (`PrismaticJointActuator`) is the I/O point: it knows the joint, reads its limits from
  USD (so a target outside 0–0.5 m is rejected), and writes the drive target.
- The **model** (`PrismaticJointPositionModel`) is the value you write: a typed, validated position in
  meters.

Next, the same slide becomes something a *remote* program can control: transducers are grouped into an
automation component and served over a control API.

A **behavior script** is a Python class attached to a prim. Isaac Sim's behavior scripting runs its
methods at points in the simulation's life. This extension's `BehaviorScript`
(`amt.dtp.ise.scripting.BehaviorScript`) extends Isaac Sim's with two things: a **physics-step hook**
(`on_physics`) and **typed configuration** (`config`).

## Lifecycle

| Method | When | Put here |
|---|---|---|
| `__init__` / `on_init()` | when the script is constructed: on Play, **and also while stopped** (e.g. when the configuration editor inspects the script) | cheap setup only; no physics objects (articulations can't be built while stopped) |
| `on_play()` | each time the simulation starts playing; the script is constructed anew from the stage first | build transducers, automation components, and APIs; start servers |
| `on_physics(current_time, delta_time)` | once per **physics step** | per-step control: apply commands, step a segment (`api.step(...)`) |
| `on_update(current_time, delta_time)` | once per **app update** (rendering frame), which can span zero or several physics steps | UI-rate work; not control |
| `on_pause()` / `on_stop()` | when the simulation pauses / stops | stop servers, release resources |

**Rule of thumb:** construct in `on_play`, act in `on_physics`, clean up in `on_stop`. A segment API must be
stepped exactly once per physics step, so its `step()` call belongs in `on_physics`, not `on_update`.

## Typed configuration

Assets often need per-instance settings: an API port, a speed limit, a home position. A behavior script
declares them as a [pydantic](010-Models.md) model, and people edit them in Isaac Sim without
changing code:

```python
from typing import Optional, Type
from pydantic import BaseModel, Field
from amt.dtp.ise.scripting import BehaviorScript

class MillConfig(BaseModel):
    api_port: Optional[int] = Field(
        8020,
        title="Segment API Port",
        description="The port of the segment API (http://localhost:<port>/api/docs).",
    )

class Mill(BehaviorScript[MillConfig]):

    @property
    def config_type(self) -> Optional[Type[MillConfig]]:
        return MillConfig

    def on_play(self):
        port = self.config.api_port          # typed, validated
```

How it works:

- The configuration is stored on the prim as JSON, in custom data under the key
  `amt:dtp:behavior:config`. It travels with the USD file, so each instance of an asset can have its own.
- **Edit Config** in the extension's entity panel opens a form generated from the model (titles,
  descriptions, and limits come from the `Field`s). The button turns red when the stored configuration
  is invalid.
- If the prim has no configuration, the model's **defaults** are used. If the model has required fields
  without defaults, playing fails with a message to create one.
- The configuration is read when the script is constructed, so `__init__` and `on_init` can use it.
  It is validated **strictly** just before `on_play`, and it can't be edited while playing.

### Field types the form supports

| Field type | Shown as |
|---|---|
| `str`, `int`, `float`, `bool` | a text box, a number drag (limits from `ge` / `le`), a checkbox |
| `Literal[...]`, an `Enum`, `FuncEnum[fn]` | a dropdown (`FuncEnum` options come from a function, at the time the form opens) |
| `Path` | a text box with a file picker; `json_schema_extra={"is_directory": True}` for a folder picker, `{"extensions": [...]}` to filter, `{"must_exist": True}` to require it |
| a pydantic model | a collapsible group of its own fields (nested to any depth) |
| `List[T]` (and `Tuple[T, ...]`) of any type above, including models and lists | a list: one row per item, with **+ Add item**, a red **X** to delete, **Up** / **Down** on each row (or select an item by its index and use **Move Up** / **Move Down**), a drag handle (drop an item on another row to move it there), and **Copy** to duplicate an item |
| `Optional[T]` for any type above (including `Optional[List[...]]`, and list items such as `List[Optional[int]]`) | a **checkbox before the field**: unchecked means no value (`None`); the field greys out and keeps what you typed, so checking it again restores it |

Lists check their limits as you edit: `Field(min_length=..., max_length=...)` on the list, and item limits
on the item type (`List[Annotated[int, Field(ge=0, le=10)]]`). An invalid item's index turns red. Options
for a list go in `json_schema_extra`:

| Key | Default | Effect |
|---|---|---|
| `duplicate` | `True` | show the **Copy** button on each item |
| `item_title_field` | none | for lists of models: the field whose value titles each item (e.g. `"name"` shows `Waypoint "home"`) |
| `reorder` | `"both"` | where the move buttons are: `"row"` (on each item), `"list"` (for the selected item), or `"both"` |
| `drag` | `True` | show drag handles |

```python
class CellConfig(BaseModel):
    waypoints: List[Waypoint] = Field(
        default_factory=list,
        title="Waypoints",
        json_schema_extra={"item_title_field": "name"},
    )
    speeds: List[Annotated[float, Field(ge=0, le=2)]] = Field(default_factory=lambda: [1.0], max_length=4)
```

Every change (an edit, an added, deleted, or moved item) is validated against the whole model at once and
saved when the model is valid. The **AMT › Model Editor Example** window shows every supported type on a
test model that is never saved, to try them out.

!!! note "Planned: endpoint descriptors"

    A second declaration, similar to the configuration, will describe the network endpoints an asset serves
    (e.g. the address and port of its control API, or an industrial protocol). The extension will collect
    them from all loaded behavior scripts and present them to an orchestration layer: network discovery for
    the devices in a digital factory.

---
title: 'Machines'
---

# Machines

!!! info "In this chapter"

    Everything so far, put together in one fully worked example: the AMT VM-A1 3-axis mill, from its USD
    model to a remote client.

    **Builds on:** [Segments and the Control APIs](050-Segments-and-Control-APIs.md).

The finished asset lives in the alpha testbeds repository, `Assets/AMT VM-A1/`: the USD file, the behavior
script `AMT_VM_A1.py`, and `client/vm_a1_client.py`. The code below is taken from it.

## 1. The model (USD)

The mill is one articulation (`/Mill`) with three **prismatic joints** (`X_Axis`, `Y_Axis`, `Z_Axis`),
each with a linear position drive (stiffness > 0) and limits in meters (X 0…1, Y 0…2, Z −0.1…1). The
transducers read those limits, and they become the limits in the API's models.

## 2. The automation component

The mill exposes one transducer, `axes`: an `ArticulationTransducer` on the mill prim itself (`"."`), so
the model has one field per joint.

```python
from amt.dtp.ise.transducer.core import AutomationComponent, transducer
from amt.dtp.ise.transducer.articulation import ArticulationTransducer

class AmtVmA1(AutomationComponent):
    """A basic 3-axis vertical mill."""
    name = "AMT VM-A1"
    version = "0.1.0"

    axes: ArticulationTransducer = transducer(
        ".",
        name="MillX",
        description="The X, Y, and Z axes: position commands and measured state, in meters.",
    )
```

What this produces:
- **Models:** `AmtVmA1Commands` (field `axes`, typed `MillX_Commands`, with fields `X_Axis`, `Y_Axis`,
  `Z_Axis`, each a prismatic position model limited to that axis's USD limits) and `AmtVmA1Actuals`
  (measured position, velocity, effort, and reaction forces per axis).
- **Names:** the class name gives the component's model prefix (`AmtVmA1`); `name="MillX"` gives the
  transducer's model names (`MillX_Commands`).

## 3. The behavior script

Configuration (the API port), construction on Play, one segment step per physics step, and cleanup on
Stop:

```python
from pathlib import Path
from typing import Optional, Type
from pydantic import BaseModel, Field

from amt.dtp.ise.scripting import BehaviorScript
from amt.dtp.ise.lib.control.segment.server import SegmentApi

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

    def on_init(self):
        self._api: Optional[SegmentApi] = None

    def on_play(self):
        # Built on Play: articulations need the physics simulation.
        self._component = AmtVmA1(prim=self.prim_path)
        self._component.axes.write_joint_positions({"X_Axis": 0.5, "Y_Axis": 1.0, "Z_Axis": 1.0})  # home

        self._api = None
        if self.config.api_port is not None:
            self._api = SegmentApi(
                self._component,
                port=self.config.api_port,
                assets=[Path(__file__).parent / "AMT VM-A1.usda"],   # served to clients
            )
            self._api.start()

    def on_physics(self, current_time: float, delta_time: float):
        if self._api is not None:
            # One queued frame per physics step, then publish the mill's state.
            self._api.step(physics_time_step=delta_time, timeline_time=current_time)

    def on_stop(self):
        if self._api is not None:
            self._api.stop()
            self._api = None
```

Every asset defaults to port 8020; a testbed with several assets gives each its own port through the
configuration.

## 4. Play and look

Press Play, then open `http://localhost:8020/api/docs` (Swagger). You'll find:
- the request and response models, with the axis limits;
- `GET /api/segment/models.py`, the generated client module;
- `GET /api/core/assets/list`, the USD file you passed as an asset.

## 5. A remote client

A client outside Isaac Sim downloads the generated models (typed, with the limits), checks the fingerprint,
and queues one frame per physics step to mill a square. Condensed from `client/vm_a1_client.py`:

```python
from amt.dtp.ise.lib.control.segment.client import download_client_module

download_client_module("http://127.0.0.1:8020", ".", "amt_vm_a1_controller.py")
from amt_vm_a1_controller import *   # Client, CommandFrame, ComponentCommands, AmtVmA1Commands, ...

client = Client("http://127.0.0.1:8020")            # raises if the mill changed since generation
dt = client.segment_info().physics_time_step or 1 / 60

def frame(x, y, z, i):
    return CommandFrame(frame_id=i, components=ComponentCommands(
        AMT_VM_A1=AmtVmA1Commands(axes=MillX_Commands(
            X_Axis=PrismaticJointPositionModel(position=x),
            Y_Axis=PrismaticJointPositionModel(position=y),
            Z_Axis=PrismaticJointPositionModel(position=z)))))

path = [...]                                           # interpolate the square at the feed rate, one point per step
client.write([frame(x, y, z, i) for i, (x, y, z) in enumerate(path)])
with client.stream(executed_only=True) as stream:
    for response in stream:
        if response.halted:                            # a frame failed (see response.components.AMT_VM_A1.errors)
            break
        if response.frame_id == len(path) - 1:
            break
```

## Check your understanding

- Why is the automation component built in `on_play` rather than `on_init`?
- What happens to a frame whose `X_Axis` target is 1.5 m? (When, and where is the error reported? Does the
  queue halt?)
- What changes in the generated models if you rename the transducer from `MillX` to `Axes`?

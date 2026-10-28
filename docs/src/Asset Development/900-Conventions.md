---
title: 'Conventions'
---

# Conventions

The conventions every asset follows: units, rotations, frames, and names. Each section links to the page
with the details.

## Units

!!! warning "Today: meters and degrees. Under review."

    | Quantity | Unit on the APIs today |
    |---|---|
    | prismatic (linear) position / velocity | meters / meters per second |
    | revolute (angular) position / velocity | **degrees** / degrees per second |
    | conveyor surface velocity | meters per second |

    USD authors joint limits and drive targets in degrees, and the transducers convert to Isaac Sim's
    radians internally. Whether the wire APIs should switch to SI (radians), keep degrees, or use
    millimeters and degrees like industrial controllers is an **open decision**; see
    [Robot Control Architecture: Units](900-Conventions.md#units).

Principles that hold regardless of that decision:
- **Units are never implicit.** Every field states its unit in its description.
- **Convert at boundaries.** USD I/O, device adapters (e.g. FANUC, igus, MTConnect), and displays convert;
  the APIs use one system.
- **Linear values are in stage units.** The models say meters; that is only correct while the stage's
  `metersPerUnit` is 1, which it is for every current asset.

## Rotations and poses

- Poses use named models: `Vector3(x, y, z)`, `Quaternion(w, x, y, z)` (normalized; zero is rejected),
  `Pose`, `FramedPose`. Nothing on the wire is a bare 4-element array.
- Libraries disagree on the order: USD, Isaac Sim, and cuMotion use (w, x, y, z); NVIDIA Warp, SciPy, and ROS
  use (x, y, z, w). Convert explicitly with `to_wxyz()` / `to_xyzw()` (and `from_wxyz` / `from_xyzw`); see
  [Robot Control: Pose Models](070-Manipulator-Robots.md#pose-models).

## Robot frames

The robot base and tool conventions (+X forward, +Z up; tool +Z = approach, Y = jaw axis; `tool0` = the
flange face) are in [Robot Assets and Conventions](070-Manipulator-Robots.md#robot-conventions).

## Names and ids

- **Transducer ids** within an automation component must be valid Python identifiers, without a leading
  underscore or a `model_` prefix, and must not shadow pydantic attributes. Ids derived from prim paths
  replace `/` with `_`. See
  [Composing an Automation Component: Ids](040-Automation-Components.md#ids).
- **Model class names** are derived from names: an automation component's name gives its prefix
  (`AMT VM-A1` → `AmtVmA1Commands`), and a transducer's name gives its model names (`MillX_Commands`).
  Two different classes with the same name in one API are an error.
- **Generated client modules** use fixed aliases (`Client`, `CommandFrame`, `ComponentCommands`, …) so
  client code survives a change of the API's title. See
  [Segments and the Control APIs](050-Segments-and-Control-APIs.md#python-client).

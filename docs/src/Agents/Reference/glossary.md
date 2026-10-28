---
title: 'Glossary'
audience: both
---

# Glossary

Overloaded and project-specific terms, with the meaning used in this repo. The user-facing terminology
(definitions, standards cross-walk) is in the user docs, Asset Development, "Control Terminology"; this
page adds agent-relevant notes and code names. Verify against the code; may lag.

| Term | Meaning here | Watch out for |
|---|---|---|
| **AutomationComponent** ("component") | The unit that owns a set of transducers exclusively and takes part in one segment with a fixed process-data layout (ADR-0007). | Not USD `kind = component`, not an MTConnect `Component`, not a "Kit component" |
| **Segment** | One synchronization domain: one frame per physics step, one writer per component (ADR-0006). `SegmentEngine` + `SegmentApi`. | Not a network segment or a fieldbus segment |
| **CoreApi** | Base of every machine-control API (`/api/core`): layer composition, self-description (ADR-0007); built on `ApiHost`, which does the hosting and the thread bridge (ADR-0024). | Not Isaac Sim's `isaacsim.core` |
| **Transducer** | A group of I/O points on a prim: `Sensor`, `Actuator`, or both (e.g. an articulation's joints). | — |
| **Command / target / actual** | Values written each cycle / what a joint command sets / values read back (ADR-0007). A joint command's fields are `position` / `velocity`, like the actuals: the command model implies "target" (ADR-0007). | "Setpoint" is reserved for held values |
| **State** | A component's state in a response frame: status + actuals + command readback + typed errors. | Not the same as "actual" |
| **Status** (of a component) | `applied` / `failed` / `not_addressed`, per response frame (the working-counter role). | Not the segment's state (`running` / `halted`) |
| **Halted** | The segment stopped draining its queue after a failed frame, until resume or clear (ADR-0021). | Not paused: steps still run and publish |
| **Process data** | Cyclic data (commands, actuals). | Not "telemetry" (that's only the measurement direction) |
| **Configuration** | Acyclic parameters, read and written on request; schema fixed while playing (ADR-0005). | — |
| **Frame** | One row of process data for every component in a segment; request (commands) or response (states). | — |
| **`frame_id`** | Optional, client-chosen label on a request frame, echoed in the response that applied it. | Not unique across clients |
| **`sequence`** | Server-assigned, outbound-only number on every sequenced event (`frame`, `config`, `status`; ADR-0008). | Not passed to writes |
| **`instance_id`** | Changes whenever sequence numbering restarts (every play). | — |
| **Retransmission buffer** | Small fixed-size buffer of recent events, only for repairing or resuming streams (`buffer_size`, default 600). | Not "replay", not a historian; not the per-subscriber queue (`subscriber_queue_size`) |
| **Kinematics service** | The scene-free web API over kinematics solvers created from uploaded URDF + XRDF (ADR-0010): `KinematicsApi` (an `ApiHost`, not a `CoreApi`), `/api/kinematics/...`, the extension's `KinematicsService` on port 8014. Plans around library-neutral obstacles sent to it, never Isaac Sim's scene; one request at a time. Planning that needs a scene stays in process (`MotionPlanner` in the RobotAPI). | Not the RobotAPI (no segment, no execution); a RobotApi serves the same routes for its robots, read-only |
| **Solver** (in the kinematics service) | One `KinematicsSolver` instance with an id, a lock, an active tool frame, and a TCP offset; one robot (end-effector combination). | Not a motion planner |
| **RobotAPI** | Robot-level API that compiles TCP targets into segment frames (ADR-0011); `RobotApi(SegmentApi)`, `/api/robot`. | — |
| **Robot command** / **move** | One row of a robot program (a move per robot + pass-through component commands + dwell), compiled to one or more frames / `set`, `ptp`, `lin`, `circ`, or `plan` (ADR-0023). | "Frame" stays one physics step; a robot command is not a frame |
| **KinematicsSolver / MotionPlanner** | FK/IK (batched) / collision-aware planning that uses a solver and a world of obstacles (ADR-0023). | — |
| **Neutral obstacle** | A library-neutral obstacle shape with a pose (cuboid, sphere, cylinder, capsule, mesh), as in ROS `shape_msgs` and cuRobo's world config (ADR-0010); the kinematics service accepts them, the RobotAPI reads them from the stage. | Not a USD prim |
| **Termination** (`fine` / `cnt`) | How a move ends: stop at the target, or blend into the next move (common industrial robot terms; deferred). | Not "termination" of a connection or process |
| **Pause** / **halt** | The client paused the segment's queue (`pause`; deferred, `preview_features` only) / a frame or a monitor failed (ADR-0021); `resume` releases both. | A pause is not a failure; response frames' `halted` means only a halt |
| **ApiHost** | The server-on-its-own-thread base of every API (ADR-0024); `CoreApi` is the machine-control core above it. | Not an "asset shell" or a layer |
| **End-effector combination** | A robot plus a specific set of end effectors (e.g. tool changer + gripper), with its own merged XRDF and solver (ADR-0012). | — |
| **Asset** | A reusable simulated machine or robot package (USD + scripts + descriptions). | Also: USD asset files; I4.0 "asset" (the physical thing); the `/assets` description-file endpoints |
| **Testbed** | A scene composed from assets; lives in the alpha testbeds repo (ADR-0018). | — |
| **Agent** | An AI agent (Claude). | MTConnect "Agent" is the HTTP server that publishes device data |
| **Device** | Avoid as a class name. | CUDA `device` (e.g. `cuda:0`) |
| **Node** | Avoid as a class name. | OmniGraph nodes |
| **Service** | Loosely: one of the decomposed web APIs (segment, kinematics, robot); in the extension, also a `Service` subclass started and stopped with it (`service/`). | Many other "services" exist (Omniverse, OS); the user rejected `ServiceApi` for that reason |

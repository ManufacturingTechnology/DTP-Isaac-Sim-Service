---
title: 'Decisions (ADR log)'
audience: both
---

# Decisions (ADR log)

Architecture decision records for deliberate, non-obvious choices, especially ones a future agent
might be tempted to "fix". Add an entry whenever a real design fork is decided. Verify against the code;
may lag.

## How to use this file

- **Before reversing a deliberate design, read its entry.** If your change conflicts with it, surface
  the conflict and the entry's **Revisit if** condition to the user, rather than silently overriding or
  silently complying. Re-*evaluating* when an assumption has changed is healthy; re-*litigating* a
  settled decision without new information is not.
- Each entry: **Context · Decision · Alternatives · Revisit if · Confidence · Status**.
- **Confidence:** how firm the decision is (high: defend it; low: stay open). If the user decides
  something without stating confidence and it can't be inferred, ask.
- **Only decided entries live here** (Accepted, or Superseded-by-`<id>`). Proposed, deferred, or parked
  decisions, and details an agent chose that the user hasn't confirmed, stay in the user's local `Scratch/`
  until decided, even when the code already implements them; the specs mark such parts. On reversal, don't
  delete: mark the old entry Superseded and add a new one referencing it.
- Detailed rationale and sources are in `Specs/` (segment protocol, robot control, end-effector package,
  package validation) and the user docs (Asset Development).

---

## ADR-0001: Typed component models, compiled per API, with generated clients
- **Context:** Remote clients need typed models for a specific machine, Swagger needs real schemas, and bad
  input must be a 422, not a 500.
- **Decision:**
  - Each `AutomationComponent` builds dynamic pydantic models (`{Prefix}Actuals` / `{Prefix}Commands`, one
    field per transducer id), and each API compiles them into command and response frames.
  - The API serves a generated `models.py` client module: built-in models are imported, custom ones rendered.
  - Every generated module ends with **fixed aliases** (`Client`, `CommandFrame`, `ResponseFrame`,
    `CommandResponse`, `ComponentCommands`, `ComponentStates`, `ComponentConfigs`, `Configuration`), so client
    code survives a change of the API's title.
  - A schema **fingerprint** (sha256 of the canonical schema, documentation stripped) is checked when a
    client connects.
- **Alternatives:** LLM-generated clients (rejected); building models at client runtime from JSON Schema
  (doesn't work well); hand-written per-machine models.
- **Revisit if:** codegen can't represent a needed type, or pydantic changes break dynamic models.
- **Confidence:** High. **Status:** Accepted. See `Reference/control-apis.md`.

## ADR-0002: Frame-queue control API (one frame per physics step)
- **Context:** Remote control of simulated machines needs synchronized, deterministic application of
  commands.
- **Decision:**
  - Clients write **lists** of frames into a queue; each physics step applies one frame and publishes a
    response frame.
  - A batch is validated all-or-nothing (422 names the frame index); nothing of a bad batch is queued.
  - `clear` empties the queue; actuators keep their last commands.
  - `execute` is a separate blocking endpoint (rather than a `?wait` flag on write).
  - The stream is Server-Sent Events.
- **Alternatives:** direct writes per request.
- **Revisit if:** latency requirements outgrow a queue (e.g. a streaming servo mode).
- **Confidence:** High. **Status:** Accepted.

## ADR-0003: Frames carry deltas; an empty frame is a delay; unknown names are rejected
- **Context:** Clients should send only what changes, and be able to wait.
- **Decision:**
  - Every container level (component, transducer, joint) is optional; omitted or null means unchanged.
  - A single setpoint (joint position, pose, color) is atomic.
  - A frame with no commands takes one step (a delay).
  - All models use `extra="forbid"`, so a typo can't silently become a no-op delay; the API warns about
    custom models that allow extras.
- **Revisit if:** a protocol needs full-image frames (e.g. an adapter to a fieldbus).
- **Confidence:** High. **Status:** Accepted.

## ADR-0004: `step()` runs in `on_physics`; components and APIs are built in `on_play`
- **Context:** `on_update` can span zero or several physics steps; the config editor constructs
  behavior scripts while stopped, when articulations can't be built.
- **Decision:** Call `step(physics_time_step=…, timeline_time=…)` from exactly one behavior script's
  `on_physics`; construct components and APIs in `on_play`. Response frames carry `physics_timestamp`,
  `timeline_timestamp`, and `wall_timestamp`.
- **Confidence:** High. **Status:** Accepted.

## ADR-0005: Cyclic process data vs acyclic configuration
- **Context:** Fieldbuses (EtherCAT, CANopen, PROFINET, EtherNet/IP, IO-Link) and UR RTDE all separate
  cyclic process data from acyclic parameters. "Telemetry" names only the measurement direction (its
  counterpart is telecommand, ECSS).
- **Decision:**
  - Cyclic data is **process data** (the fieldbus term): routes `/process/read|write|clear|execute`,
    `/config/read|write`, `/stream`, under `/api/segment/`.
  - Schemas (process data and configuration) are **fixed while playing**; configuration *values* can
    change while playing.
  - Configuration is optional per transducer: `config_model_type` returns None by default, with
    `config_read()` / `config_write()`. One configuration model per transducer, not split by direction.
  - Large or irregular data (video, files) gets its own channel.
- **Revisit if:** a use case needs schema changes at runtime (then it needs a reconfiguration state, as
  fieldbuses do).
- **Confidence:** High. **Status:** Accepted.

## ADR-0006: Segment (sync domain), with an engine and a `SegmentApi`
- **Context:** The server plays the EtherCAT MainDevice role: one cycle, one frame per cycle, a sorted
  process image.
- **Decision:**
  - A **segment** is one synchronization domain: one frame per physics step, one writer per component.
  - `SegmentEngine` holds the queue, the process image, and `step()`; `SegmentApi` is its HTTP API.
  - Flexibility for distributed (CPPS) control comes from composing segments and services, not from
    loosening the cycle.
- **Confidence:** High. **Status:** Accepted.

## ADR-0007: Names: `AutomationComponent`, command / target / actual, `CoreApi`
- **Context:**
  - "Device" is nebulous and collides with CUDA's `device`; "Node" collides with OmniGraph; bare "Component"
    collides with USD `kind = component` and MTConnect `Component`.
  - IEC 60050-351 "command variable", Sercos "command value", and CiA 402 "target" / "actual value" name the
    directions; "setpoint" fits held values.
  - The API base hosts the server, composes API layers, and self-describes; "controller" would name the wrong
    side (the client is the controller).
- **Decision:**
  - **`AutomationComponent`** (OPC UA FX's term) is the class; "component" is the short word in prose. A
    component **owns its transducers exclusively**. Granularity is free: one component per machine, or one per
    smart sensor.
  - Frames carry **commands**; a joint command's value is a **target** (in prose); read-back values are
    **actual**. "Setpoint" is reserved for held values (a temperature, a configuration parameter). **State** is
    the supertype: a component's state = actuals + command readback + errors.
  - Methods use physical nouns: `actuator_write`, `actuator_read` (readback of commanded targets),
    `sensor_read`, `config_read`, `config_write`. Models use value names: `command_model_type`,
    `actual_model_type`, `config_model_type`.
  - Joint command models name their fields **`position`** and **`velocity`**, like the actual values ("target"
    is implied by being a command).
  - **`CoreApi`** is the machine-control core API (`/api/core/...`); its overlap with Isaac Sim's
    `isaacsim.core` naming is contained by the module path.
  - No deprecated aliases: there are no released users to protect.
- **Alternatives:** Device, Station, Node, Machine, Equipment; `target_position` / `target_velocity` (mirrors
  CiA 402 and USD `targetPosition`, but repeats what the command model already says); ServiceApi, EndpointApi,
  GatewayApi, BaseApi for the core.
- **Revisit if:** a model needs both a target and an actual value in one command (then the distinction must be
  in the field names).
- **Confidence:** High. **Status:** Accepted. Full name list: `Specs/segment-protocol.md` "Names".

## ADR-0008: Outbound `sequence`, `instance_id`, and a retransmission buffer
- **Context:** Streams can drop events or disconnect; clients need to know what they missed and
  continue from there.
- **Decision:**
  - Every response carries a server-assigned **`sequence`** (outbound only; distinct from the client's
    `frame_id`).
  - An **`instance_id`** changes whenever numbering restarts.
  - A small, fixed-size, configurable, optional **retransmission buffer** exists **only to repair or
    resume streams**: streaming is primary, and querying is for repair (the opposite of MTConnect, where
    querying `sample` is primary). The closest match is OPC UA's retransmission queue and Republish.
  - Details as in `Specs/segment-protocol.md`: SSE id `instance_id:sequence`, resume via `Last-Event-ID` or
    `?resume_after=`, `GET /api/segment/retransmit` by list (with `missing`) or range, `OUT_OF_RANGE` with an
    MTConnect-style header, heartbeats with `next_sequence`, the buffer size set in the constructor (0
    disables); per-component `status`, typed `SegmentError`s, configuration as a confirmed service, stream
    events `frame` / `config` / `error` / `status`.
- **Alternatives:** "replay" semantics (wrong intent); MTConnect-style query-first acquisition.
- **Confidence:** High. **Status:** Accepted.

## ADR-0009: Asset ports are assigned at the testbed level
- **Decision:** Every asset may default to the same port (8020). A testbed assigns the real ports
  through each script's configuration (`api_port`).
- **Confidence:** High. **Status:** Accepted.

## ADR-0010: The kinematics service: scene-free, uploaded solvers, port 8014
- **Context:** The user: the robot kinematics API "is intended to be a service similar to MoveIt behind a REST
  API where a controller can use it without even using Isaac Sim", and it shouldn't depend on Isaac Sim's scene.
- **Decision (the user):**
  - `KinematicsApi` (`/api/kinematics/...`) is an Isaac Sim extension service, started and stopped with the
    extension, on its own port **8014** (`kinematics_service_port`), Swagger at `/api/docs`. It is an `ApiHost`,
    not a `CoreApi` (ADR-0024).
  - Clients create, list, and delete `CuMotionKinematicsSolver`s from uploaded URDF/XRDF files (in memory only;
    a registry separate from the testbeds' RobotAPIs), then call batch FK/IK, trajectory sampling (at a
    client-given dt, no execution), tool frames / TCP, self-collision, and other scene-free routes.
  - **Scene-free:** it works with no stage open and no simulation playing. Obstacles **from the USD stage**
    stay in process (the RobotAPI, ADR-0023); **library-neutral** obstacles that external libraries use and
    cuMotion accepts are accepted, with collision-aware planning and checks.
  - **Threading:** its own server thread handles requests **strictly one at a time**; each cuMotion call also
    holds the process-wide `CUMOTION_LOCK` shared with Isaac-thread users.
  - Limits are high by default (1024 solvers, 256 MiB per file, 1 000 000 items per batch, 10 000 000
    trajectory steps), and each can be `None` (no limit). A failure to start (e.g. port in use) is logged,
    shown in the extension's error window, and doesn't stop the extension.
- **Claude's choices** (confirmed by the user): one kinematics wire API (a `RobotApi` serves the same routes for
  its robots as **read-only** solvers, ids = robot ids, writes answer 409 `READ_ONLY`); routes per solver in the
  path (`/api/kinematics/solvers/{solver}/fk`); uploads as multipart or JSON (multipart needs
  `python-multipart`, which Isaac Sim bundles; without it multipart answers 415 and JSON works); the solver id is
  the optional `name`, else generated; extra routes (reachability, Jacobian + manipulability, path check,
  collision spheres, file download, service info); trajectory sampling reuses the RobotAPI's `compile_move`.
- **Evidence:** no cuMotion source states thread safety ❓; pybind11 doesn't release the GIL implicitly, and a
  probe found no errors across 8 threads × 200 iterations (MR-A1, UR10, Franka), so the design relies on its own
  locks. `kinematics_service_probe.py` (12/12): concurrent client requests ran one at a time with answers
  identical to serial; obstacles sent over the API block a straight move and a planned move avoids them; no
  `omni`, `pxr`, `carb`, `isaacsim`, or `warp` module was imported.
- **Alternatives:** a scene/world upload API coupled to USD (rejected by the user); threaded requests with
  per-solver locks; persistent solvers.
- **Revisit if:** one-at-a-time throughput is too low (then a worker per solver); cuMotion documents thread
  safety or releases the GIL; clients need per-request worlds or collision-aware IK.
- **Confidence:** High. **Status:** Accepted.

## ADR-0011: The RobotAPI compiles to segment frames at write time
- **Decision:**
  - TCP targets become joint frames when written; IK runs over the whole batch, rejecting it all-or-nothing.
  - Use batched solving where a backend offers it (parallel seeds plus continuity selection). cuMotion
    1.1.0a2 has no batched plain IK, so its backend solves sequentially with continuity seeding (≈ 28–80 µs
    per pose; `Reference/robots.md`).
  - TCP *definitions* belong to the `KinematicsSolver`; the RobotAPI owns *selection* and the cyclic values.
  - The robot stream is separate and carries the segment frame plus the kinematic data **in one message**.
  - Multi-machine synchronization: other components' commands pass through in the same rows.
  - The planner is part of the RobotAPI ("plan and move", ADR-0023), not a layer above it.
- **Confidence:** High. **Status:** Accepted. Compile details: `Specs/robot-control.md`.

## ADR-0012: End effectors: one solver per combination; XRDF fragments merged at load time
- **Context:** No standard exists for merging description fragments; practice is compose-before-solve
  (xacro + MoveIt configs, Drake model directives, SDFormat includes, per-combination cuRobo configs).
- **Decision:**
  - End effectors ship XRDF fragments, merged with the robot's XRDF at load time (Drake-directive-like).
  - The mount pose comes from the composition; names are prefixed; cross-boundary self-collision ignores are
    generated; the merged XRDF is written out.
  - One solver per combination, built ahead of time. Format: `Specs/end-effector-package.md`.
- **Claude's choices** (confirmed by the user):
  - **Separator `/`** (`g1/tcp`): cuMotion accepts `/`, `_`, `.`, and `:` in XRDF frame names (probe); `/` reads
    like a path. Configurable per combination set.
  - **Generated ignores:** each fragment's mount and one-hop frames ↔ the parent frame, the parent's URDF link,
    the links rigidly attached to it, **and the link across the joint that moves it** (as MoveIt's Setup
    Assistant disables adjacent links); when stacked on another fragment, ↔ that fragment's whole body. Without
    these, a probe's UR10 gripper sphere touched `wrist_2_link`.
  - **Spheres join the robot's collision geometry groups:** into a group with `spheres`, skipping a group that
    already clones it; a group made only of `clone`s gets an extra `<group>_end_effectors` group; a robot without
    spheres gets a `collision_spheres` group for world and self collision.
  - **Written out** deterministically by a small YAML emitter in the lib (no PyYAML dependency), with a header
    listing each source's SHA-256; an unchanged file isn't rewritten.
  - **Combinations** are declared in Python or a combinations file and **selected through the RobotAPI
    configuration** (`RobotConfig.combination`); switching resets the tool frame to the combination's active TCP.
  - **The kinematics service accepts fragments** (JSON `fragments` + `combination`, or multipart files).
- **Revisit if:** cuRobo runtime attachments make one solver per robot sufficient.
- **Confidence:** Medium-high. **Status:** Accepted.

## ADR-0013: The joint-jump check is velocity feasibility from URDF limits
- **Decision:** Reject a compiled path whose per-step joint change exceeds the URDF velocity limit × the
  physics step × a margin; joints without limits fall back to a configured absolute threshold.
- **Revisit if:** real trajectories show false rejections or missed flips. **The user asked to re-evaluate this
  later.**
- **Confidence:** Medium. **Status:** Accepted (`motion.trajectory.check_steps`, `RobotBinding.max_steps`).

## ADR-0014: Named pose models
- **Context:** Isaac Sim's own libraries disagree on quaternion order (USD, Isaac core, cuMotion, Isaac Lab use
  wxyz; Warp, ROS, and SciPy use xyzw), verified by probe.
- **Decision:** Classes, not aliases: `Vector3`, `Quaternion(w, x, y, z)` (normalized, zero rejected), `Pose`, a
  framed pose; converters `to_gf` / `to_wxyz` / `to_xyzw` / `from_rotvec`. No bare 4-element arrays
  (`ManipulatorRobotActuatorPose.from_numpy()` is the explicit numpy path).
- **Confidence:** High. **Status:** Accepted (`transducer.models.pose`).

## ADR-0015: Swagger examples and the D6 axis model
- **Decision:**
  - Constrained numeric fields carry `examples` (0 if allowed, else the midpoint, else the nearest bound),
    because Swagger UI otherwise shows the minimum.
  - The D6 actuator model's `axis` is a `Literal` of the driven axes.
  - `JointAxisActuatorModel` requires exactly one of position or velocity.
- **Confidence:** High. **Status:** Accepted.

## ADR-0016: Robot IK failure is silent by default
- **Decision:** `raise_when_unreachable=False` by default on the Lula and cuMotion actuators (behavior-script
  routines rely on silent skips); APIs that need failure reporting set it True. Through the RobotAPI an
  unreachable target is rejected at write time (422 `IK_UNREACHABLE`, nothing queued, no halt); the
  segment-level `IK_UNREACHABLE` + halt (ADR-0021) applies only to actuators with `raise_when_unreachable=True`.
- **Revisit if:** the Lula actuators are removed (expected) or the routines change.
- **Confidence:** Medium. **Status:** Accepted.

## ADR-0017: Agent knowledge base, Scratch, and personal reminders
- **Decision:**
  - Shared technical knowledge lives in `docs/src/Agents/` (tracked, hidden from the public docs build, one-way
    links); see `Agents/index.md`. Shared Claude machinery (`.claude/settings.json`, skills, hooks) is committed.
  - **`docs/src/Agents/Scratch/` is the user's local playground, gitignored on purpose:** exploration, drafts,
    plans, harnesses, and research in progress, worked out with the user before anything is committed. Settled
    results are **promoted** into tracked files (decisions here, designs into `Specs/`, as-built facts into
    `Reference/`, finished research into `Research/`, user-facing knowledge into the user docs). Scratch files
    are not listed in `AGENTS.md`, `CLAUDE.md`, or the tracked indexes; a tracked doc may cite one as a source but must stand
    on its own (a clone has no `Scratch/`).
  - No project management in the repo (no backlog, ledger, priorities, or owners); GitHub Projects and issues
    will handle task tracking. Agents' working plans stay local.
  - **Personal reminders live in each person's Claude memory**, never in repo files.
- **Confidence:** High. **Status:** Accepted.

## ADR-0018: Repository split
- **Decision:**
  - This repo holds the code.
  - Testbeds and demo assets live in `DTP-Isaac-Sim-Service-Alpha-Testbeds` (alpha).
  - **No tutorial or demo files in the docs** (the user, 2026-10-27): tutorial starting files live in the testbeds
    repository under `Assets/<name>/Tutorial/` (e.g. `AMT MR-A1`, its end effectors, and `AMT VM-A1` stripped to
    geometry and colors), demo scenes under `Demos/` (the cup scene: `Demos/cup-1/main.usd`). The user docs link
    to their raw GitHub URLs on `main` (the testbeds repository is public).
  - Later: per-testbed repos, shared git-based asset repositories, and build/bundle tooling.
- **Confidence:** High (direction); the tooling is future. **Status:** Accepted.

## ADR-0019: Docs lead the code; specs match the code
- **Decision:**
  - Two **leading** documents drive development: the **user docs** (`docs/src/` outside `Agents/`: the experience and
    workflow end users will have) and **`Agents/Specs/`** (the technical design behind it: formats, protocols,
    internals). Both are written with decided names, as if the design had landed.
  - **Specs match what is implemented**, with planned, deferred, and parked parts clearly marked; when code lands
    and differs, the docs are corrected to what was built, unless they have moved further ahead.
  - `Agents/Reference/` stays **as-built** (the agent's record of what the code does now), so the gap between
    "projected" and "built" is always findable.
  - Asset Development chapters are **one long page each**, building on each other.
- **Alternatives:** docs as-built only, with specs leading; docs matching the code with a separate design doc set.
- **Revisit if:** released users are confused by planned content (then publish versioned docs), or the docs drift
  too far ahead of the code to be useful.
- **Confidence:** High (from the user). **Status:** Accepted.

## ADR-0020: Uploads enforce package validation
- **Decision:**
  - `validate_directory` (so `archive()`, `extract()`, `POST /environment/upload`, `POST /asset/upload`, and the
    client) **fails on any error-severity issue**, listing each (rule, file, line, path, fix). Warnings and info
    never fail.
  - The entry layer is `main` with **any USD extension** (`.usd`, `.usda`, `.usdc`, `.usdz`).
  - **No two USD files in one folder may share a name stem** (`USD-STEM-DUPLICATE`, error).
  - Non-portable paths in **`delete` list ops** don't fail (info, `PATH-DELETE-LIST-OP`): they don't affect
    loading.
  - The behavior-script loader's working-directory lookup is kept as is.
- **Alternatives:** warnings only during a transition; enforce only in CI and the packaging tool.
- **Revisit if:** too many existing testbeds are blocked, or the testbeds repo's `default.*` entry naming is
  adopted (open in the spec).
- **Confidence:** High (from the user). **Status:** Accepted. Details: `Specs/package-validation.md`.

## ADR-0021: A failed frame halts the segment's queue
- **Context:** If each component failed on its own and the queue went on, a trajectory would keep running after a
  command had failed (e.g. an unreachable robot target).
- **Decision:**
  - When any component fails to apply its part of a frame (status `failed`), the step finishes (the other
    components are still written and every component is read), the response frame records the failure with
    `halted: true`, and the segment **stops draining the queue**: remaining frames stay queued and actuators hold
    their last commands. Steps keep publishing response frames (`halted: true`), and configuration writes still
    apply.
  - The segment emits a `status` event (`halted`, `frame_failed`) and reports `halted` / `halt` in `info`.
  - The client **resumes** (`POST /api/segment/process/resume`) or **clears** the queue.
- **Alternatives:** continue; clear the queue on failure (loses the client's remaining plan without its consent); a
  per-component or per-frame policy (more to explain and test).
- **Revisit if:** **the user wants to revisit this policy**, e.g. to choose per component, per frame, or per API
  between halt and continue, or to add a watchdog action.
- **Confidence:** Medium. **Status:** Accepted. Details: `Specs/segment-protocol.md` § "A failed frame halts the
  queue".

## ADR-0022: Meters and degrees on the wire, for now
- **Decision:** The wire uses meters and degrees; solvers work in radians, converted at the boundary and stated in
  the schemas.
- **Revisit if:** the general wire-units decision is made (still open).
- **Confidence:** High (from the user) as an interim choice. **Status:** Accepted.

## ADR-0023: Collision-aware planning: a `MotionPlanner` used in process, and a `plan` move
- **Context:** "plan and move" in the RobotAPI with the scene as its world (ADR-0011, ADR-0010).
- **Decision:**
  - **Interface:** `MotionPlanner` (lib `motion/planning.py`) *uses* a `KinematicsSolver` and holds a world of
    library-neutral obstacles: `plan(start, goal or target, frame, attempt)` returns joint waypoints;
    `collisions(q)` / `check_path(q)` check configurations against the obstacles and the robot's own spheres;
    `set_obstacles(obstacles, base_pose)`. Backend: `CuMotionMotionPlanner` (extension `robot/planning.py`):
    cuMotion's graph-based planner (`plan_to_cspace_target`; JtRRT `plan_to_pose_target`) with a cuMotion `World`
    in the robot base frame. Its world comes from the stage through `stage_obstacles(stage, roots, exclude)`
    (Cube, Sphere, Capsule, Cylinder, Mesh colliders, meters), read on Isaac's thread by the behavior script.
  - **A `plan` move** (`PlanMove`: `joints` or `target`, velocity/acceleration scaling, `smooth`, `attempts`) in
    the RobotAPI's commands, plus `POST /api/robot/process/plan` (plan without queueing). Waypoints are timed as a
    **chain of synchronized ptp segments**, blended through the corners when `smooth`, and **every physics-step
    sample is collision-checked**: a blended chain that collides falls back to stopping at each corner; a plan that
    still collides is re-planned with another seed, up to `attempts` (3), then 422 `PATH_IN_COLLISION`. Other
    codes: `NO_PLANNER`, `PLAN_FAILED`, `START_IN_COLLISION`, `GOAL_IN_COLLISION`.
  - The binding holds the planner (`RobotBinding(planner=...)`, one, or one per end-effector combination).
- **Evidence:** cuMotion plans the UR10 around a 15 cm box in ≈ 1–6 ms (probe), ≈ 30 ms per attempt including
  planner creation; its own straight segments graze obstacles by < 1 mm (probe: −0.6 mm with one seed), hence the
  per-step check; planning releases the GIL, and `CUMOTION_LOCK` is held for the call.
- **Alternatives:** the cuMotion trajectory optimizer (smoother but needs tuning); timing the
  planner's dense path at constant speed (velocity jumps at the corners); trusting the planner's path without
  per-step checks.
- **Revisit if:** grazing makes re-plans frequent (add a safety margin), a smoother time parameterization is wanted
  (TOTG / cuMotion's `CSpaceTrajectoryGenerator`), or planning must not hold the lock.
- **Confidence:** Medium. **Status:** Accepted (not yet run inside Isaac Sim).

## ADR-0024: `ApiHost` below `CoreApi`
- **Context:** `CoreApi` is the core for *machine control* (assets, segment layering); the kinematics service
  provides no assets and isn't machine control, but shares the hosting pattern.
- **Decision (the user):** **`ApiHost`** (`lib/src/amt/dtp/ise/lib/control/host.py`) owns the common pattern:
  FastAPI + uvicorn on its own thread with its own asyncio loop, start/stop, `/api/docs`, `/api/redoc`,
  `/api/openapi.json`, error handling and logging, `add_router()`, and an optional bridge to Isaac's main loop
  (`run_coro_on_main_loop`). `CoreApi` and the ISE API's `ApiController` are `ApiHost` subclasses;
  `KinematicsApi` is one too and never uses the bridge. `CoreClient` and `KinematicsClient` share `HttpClient`.
- **Claude's choices** (confirmed by the user): `start()` waits until the server accepts connections (≤ 10 s); the
  ISE API keeps `run_coro_on_isaac_loop` as an alias and logs (doesn't raise) a failure to start. Verified in
  Isaac Sim: the ISE API's docs on port 8012 come up.
- **Confidence:** High. **Status:** Accepted. See `Reference/threading-model.md`.

## ADR-0025: Apache-2.0, with a repo-level NOTICE crediting AMT
- **Decision (the user):**
  - The repo is licensed under Apache-2.0 (`LICENSE.TXT`); both Python packages declare `Apache-2.0`, the Apache
    classifier, and AMT as author (`technology@amtonline.org`).
  - A `NOTICE` file at the repo root names AMT - The Association For Manufacturing Technology (copyright
    2025-2026) as the origin. Apache-2.0 §4(d) requires distributed derivative works to carry its attribution
    notices; the licensing guidance asks for NOTICE to stay short and hold only required notices
    ([ASF licensing how-to](https://infra.apache.org/licensing-howto.html), V 2026-10-26). One NOTICE for the
    repo; no per-file headers.
  - Forks keep `NOTICE` as is and may add their own attribution below it (§4(d) allows additions that don't
    modify the license); it is not a template to fill in.
- **Alternatives:** MIT (shorter and GPLv2-compatible, but no patent grant and no NOTICE mechanism). The FSF lists
  Apache-2.0 as compatible with GPLv3 but not GPLv2 ([FSF license list](https://www.gnu.org/licenses/license-list.html),
  V 2026-10-26).
- **Packages:** both `pyproject.toml` files declare the license as an SPDX expression (`license = "Apache-2.0"`,
  `license-files = ["LICENSE.TXT", "NOTICE"]`, PEP 639; build floor `setuptools>=77`; the older table form and license
  classifier were deprecated with a 2027-02-18 deadline, changed 2026-10-27). The `lib` and `coprocessor` builds
  (`make build`) copy `LICENSE.TXT` and `NOTICE` from the repo root into the package folder for the build, so each
  wheel carries them in `.dist-info/licenses/` and its metadata says `License-Expression: Apache-2.0` (checked by
  building both wheels), then remove the copies; an editable install works without them.
- **Third-party content (the user, 2026-10-27, after a license audit):**
  - Only what we **ship** carries other people's license obligations; dependencies that users install (pip, NVIDIA's
    installer) stay under their own terms. Shipped today: paho-mqtt in the extension's `pip_prebundle`
    (redistributed under the Eclipse Distribution License 1.0, its BSD-style option) and the documentation site's
    theme assets. Each subproject has a `THIRD-PARTY-NOTICES.md` (`extension/`, `docs/`, `coprocessor/`, `lib/`,
    `container/`) with two sections: what its artifact **includes** and what it only **references**. The extension
    and docs builds copy theirs, with `LICENSE.TXT` and `NOTICE`, into the extension zip and the site.
  - The docs build excludes the theme's non-English search files (lunr-languages: MPL-1.1; wordcut: LGPL) from the
    site (`docs/hooks/exclude_theme_assets.py`, in `on_files`): the site is English-only, so the search worker never
    loads them (read in the worker; search checked in a browser).
  - `cascadio` (STEP import for trimesh; its wheel bundles OpenCASCADE, LGPL-2.1 with an exception, while declaring
    MIT) is the coprocessor's opt-in `step` extra, not a default dependency. Without it a STEP upload answers 422.
  - **License gate:** `make` (root) ends with `license-check` (`.github/scripts/check-licenses.py`): it installs the
    lib wheel with `server`, the coprocessor wheel, and the extension's prebundle requirements into a throwaway venv
    and fails on any license that isn't permissive, unless the package is in the script's reviewed list (certifi,
    numpy, scipy, each with its reason). `cascadio` is forbidden outright. CI runs `make`, so CI and local builds
    fail the same way.
  - NVIDIA's runtime (Isaac Sim, Kit, cuMotion) is required but never shipped; the install page says so. The
    container image is built locally from NVIDIA's image; `container/readme.md` notes, with the date, that NVIDIA's
    license doesn't permit redistributing Isaac Sim and links it: whether to share a built image is the user's
    call, not ours to forbid. The user accepts NVIDIA's EULA at run time (`ACCEPT_EULA` is no longer baked into the
    image).
  - The user docs carry no screenshots (removed 2026-10-27 to keep binaries out of git); only `docs/src/assets/`
    (banner, logo) remains.
  - The testbeds repository (`DTP-Isaac-Sim-Service-Alpha-Testbeds`) is Apache-2.0 for code **and** assets (the
    user, 2026-10-27, after weighing CC BY 4.0 for assets: Apache-2.0 has a patent grant and one license fits
    folders that mix scripts, generated clients, and USD; CC BY can be revisited for a media-only asset pack). Its
    `AGENTS.md` requires a provenance and license check, and a warning to the user, before anything is added.
  - **Independent review and cleanup (2026-10-27):** a second agent re-examined everything above (report in the
    licensing Scratch folder). Fixed the same day: a window-docking block in `ui/core.py` that had been taken from
    Isaac Sim's extension template without its Apache-2.0 notice (rewritten) and an unused class from the same source
    (deleted); vendor manual titles and controller command/option identifiers in the user docs (genericized);
    placeholder readmes, keywords and extension metadata; the `NOTICE` years; wrong or missing rows in the notices
    files (no `opentelemetry-api` in the closure; `material-design-color`, the unpkg shim and polyfill in the docs site;
    Roboto is served under OFL-1.1; `srl.spacemouse` carries an NVIDIA proprietary sub-notice; `spacenavd`); the
    container readme's NVIDIA note (the Additional license grants no distribution right; NVIDIA's Omniverse terms
    changed in 2026); a trademark statement on the install page; a docs footer with the license. Still open: whether
    "Isaac Sim" stays inside the product title (nominative use vs NVIDIA's brand guidance) and shrinking
    `container/build/user.config.json`.
  - **Docs site self-hosts its assets (the user, 2026-10-27):** Material's `privacy` plugin bundles Roboto (OFL-1.1),
    Mermaid (MIT) and the worker shim (MIT) into the site at build time, so a page view sends nothing to Google or
    unpkg (GDPR exposure of dynamic Google Fonts: LG München I, 3 O 17493/20). The GitHub API badge call (`repo_url`)
    stays. Details: `dev-environment.md`, "Docs".
  - **Contribution terms (the user, 2026-10-27):** inbound = outbound under Apache-2.0 §5, stated in `CONTRIBUTING.md`
    ("Licensing of contributions"), plus a Developer Certificate of Origin sign-off on every commit as a provenance
    attestation; no CLA (AMT needs no relicensing rights). Enforcement (the GitHub DCO app as a required check) is a
    repository setting, not yet configured.
- **Confidence:** High (from the user). **Status:** Accepted.

## ADR-0026: Confidential and NVIDIA-owned content is kept out by rule and by check
- **Decision (the user, 2026-10-27):** an agent-enforced rule, broader than any one vendor: confidential or
  proprietary information (non-public vendor manuals and specifications, material covered by a nondisclosure
  agreement, customer or partner information, internal business information, personal data, credentials, unreleased
  product details) never enters a tracked file. Private sources may inform the design; tracked files carry only
  generalized conclusions. Research drawing on private sources stays in Scratch (`confidential: true`) until a
  sanitizing pass the user confirms. Standards are cited (body, number, edition, clause, link) with publisher
  attribution; paywalled standards are summarized, never reproduced. NVIDIA material is referenced, never included
  (ADR-0025). Rule text: `AGENTS.md`, "Vendor-confidential material stays out" and "NVIDIA material is referenced".
- **Enforcement, in layers (one script, `.github/scripts/check-content.py`):**
  1. **Agent hook:** PreToolUse on Write/Edit/MultiEdit/NotebookEdit (`.claude/settings.json`) blocks (exit 2) a write
     that would put a match into a file git would track, in this repo or a sibling repository (the testbeds
     repository) written from the same session. Gitignored paths (Scratch) are exempt.
  2. **`make content-check`,** part of `make all`, so CI runs it on every build; and an opt-in pre-commit hook
     (`.githooks/pre-commit`, `git config core.hooksPath .githooks`) that scans the staged versions.
  3. **Skills:** `flush` and `garden` include a confidentiality sweep before anything is promoted out of Scratch.
- **What matches:** generic confidentiality and export-control markings and the stamp of paid standards (in the
  script), NVIDIA copyright notices, `.mdl` files, and a **local denylist** of sensitive terms
  (`.claude/confidential-terms.txt`, gitignored, and `~/.claude/confidential-terms.txt`), which never enters git, so
  CI checks only the generic patterns. Reviewed exceptions go into the script's `ALLOW` with a reason.
- **Known gaps:** binary files (USD crate files, images, CAD) aren't scanned, and shell commands bypass the agent
  hook (not `make` or the pre-commit hook); the agent rules cover both. The 2026-10-27 review's probes added: archives
  and UTF-16 files are skipped; a hook error (e.g. bad JSON) exits 1, which Claude Code treats as non-blocking;
  the stamp list is uppercase-only (`Confidential`, `Proprietary`, `Restricted`, `Not for distribution` pass).
  Fixed since: hook commands run from `$CLAUDE_PROJECT_DIR` (a changed working directory made Python exit 2 and
  block every write), the hook reads stdin as UTF-8, and empty or invalid `re:` denylist lines are skipped with a
  warning. No secrets scanner in CI for now (the user, 2026-10-27).
- **Confidence:** High (from the user). **Status:** Accepted.

## ADR-0027: `AGENTS.md` is the agent hub; `CLAUDE.md` imports it
- **Decision (the user, 2026-10-27):** the repository's agent guidance (map, routing, rules, ask-first list,
  environment) lives in `AGENTS.md`, the cross-tool convention read by many coding agents
  ([agents.md](https://agents.md/), stewarded by the Agentic AI Foundation under the Linux Foundation; V
  2026-10-27). `CLAUDE.md` holds only `@AGENTS.md` plus the Claude Code specifics (skills, hooks, memory). Claude
  Code reads `CLAUDE.md` and pulls in `AGENTS.md` through the import
  ([Claude Code memory docs, "AGENTS.md"](https://code.claude.com/docs/en/memory); V 2026-10-27). The testbeds
  repository uses `AGENTS.md` alone (it has no Claude-specific setup; Claude Code reads `AGENTS.md` when there is no
  `CLAUDE.md`).
- **Why:** contributors using other agents get the same rules without duplication; the Claude-only machinery stays
  out of the shared file. The skills' `SKILL.md` checklists are plain Markdown any agent can follow.
- **Confidence:** High (from the user). **Status:** Accepted.

## ADR-0028: Native SpaceMouse support: a clean-room HID reader in the lib
- **Decision (the user, 2026-10-27):** replace the external `srl.spacemouse` extension with our own reader, written
  clean-room (no third-party SpaceMouse code read or copied) from the USB HID 1.11 specification and Usage Tables,
  Microsoft's SetupAPI and HID client documentation, and the Linux hidraw documentation; standard library only (no
  `hidapi`, whose compiled module is tied to the interpreter version), Apache-2.0.
  - **In the lib** (`amt.dtp.ise.lib.devices.spacemouse`), as a `SpaceMouse` class any program can use, so a device on
    one machine can later drive a remote Isaac Sim instance (`SpaceMouseState` is a pydantic model). The extension
    adapts it to camera navigation.
  - **Generic decoding** from each device's own HID descriptor (Generic Desktop / Multi-axis Controller; X/Y/Z,
    Rx/Ry/Rz; Button page), so every model is supported; `TESTED_DEVICES` in the code records what was actually
    tested, with OS and, on Windows, the driver bound to the device and the 3Dconnexion software present. Untested
    models are not listed (they work through the same path and are logged as untested).
  - **Backend selection is a code constant** (`SPACEMOUSE_BACKEND`, default `"native"`), not configuration: `srl`
    stays only until the user has verified the native backend, then is removed.
  - **3Dconnexion's driver is not required:** a laptop without it reads the device through Windows' generic HID
    driver (V, 2026-10-27); with 3DxWare installed, the device is bound to its `3dxhid` driver and shared reads still
    work (V).
  - Windows is tested first; Linux is implemented and unit-tested, to be checked on the user's Linux machine.
  - Package name `devices` (not `input`, which would shadow a built-in name); each backend owns its axis mapping and
    signs (the native mapping is derived from the HID axis meanings and is unverified until tested; srl keeps its
    verified mapping).
- **Confidence:** High (from the user); the native axis signs are unverified. **Status:** Accepted; native backend
  awaiting the user's test.

## ADR-0029: Venvs are named per OS; `clean` keeps them, `distclean` removes them
- **Context:** `lib/` and `coprocessor/` each had one `venv/` that both the Windows `.bat` scripts and the WSL
  makefiles wrote. Windows `clean.bat` deleted it (against the 2026-10-27 rule that venvs are kept), Windows Python ran
  `-m venv` over the Linux venv, and the extension's dev mode looked for a Windows layout inside a Linux venv. Each
  platform broke or deleted the other's venv (found 2026-10-28).
- **Decision (the user, 2026-10-28):** every venv in the repo is named for its OS: `venv-linux/` (makefiles, WSL, CI)
  and `venv-windows/` (`.bat` scripts); test venvs `venv-linux-test/` and `venv-windows-test/`. The coprocessor
  installed into the extension already used `venv-linux/` / `venv-windows/`; dev mode now uses the same folders.
  `clean` (make target or `clean.bat`) removes build outputs only; `distclean` (make target or the new
  `distclean.bat`) also removes **every venv, both OSes'** and the pre-ADR names (`venv/`, `venv-test/`, for older
  clones), so a distclean from either OS leaves a fresh checkout. The per-OS names protect the everyday commands
  (`clean`, `dev`, `test`) from clobbering the other OS's venv; distclean is deliberate and removes everything.
  Removing the other OS's venv works both ways (probed 2026-10-28: Windows `RD /S /Q` removes a WSL-created venv,
  symlinks included; WSL `rm -rf` removes a Windows venv). Test venvs were first named `venv-test-linux/` /
  `venv-test-windows/` and renamed the same day.
- **Alternatives:** one shared `venv/` (the problem); a venv outside the repo per user (harder to find and clean).
- **Revisit if:** another OS needs its own venvs (macOS), or the Windows scripts stop being maintained.
- **Confidence:** High (from the user). **Status:** Accepted.

## ADR-0030: The coprocessor's venv is created on first start, never at build time
- **Context:** the coprocessor runs in its own venv because its packages conflict with Isaac Sim's environment. The
  Windows build (`extension/build.bat`) installed `venv-windows` into the build folder after the zip was made, so it was
  never shipped (found 2026-10-28).
- **Decision (the user, 2026-10-28):** the build never creates `venv-windows/` or `venv-linux/` for the coprocessor;
  the extension ships the coprocessor's wheel and installers, and `app.py` runs `install.bat` / `install.sh` on the
  coprocessor's first start (and again if the venv or the package is missing). The build-time tooling is kept,
  separate and experimental: `extension/build-prebuilt-venv.bat` and the makefile's `package-prebuilt-venv` target,
  run by neither `build.bat` nor `make`.
- **Why:** the extension doesn't ship every package the coprocessor depends on; the whole build runs on Linux; and a
  venv records the absolute path of the Python that created it (`home` in `pyvenv.cfg`), so a venv built on the build
  machine may not point at a valid Python on the user's machine (Python's install location varies, especially on
  Windows).
- **Known cost:** the first start needs Python and network access, and the install (~2 minutes) runs during the
  extension's start-up, so Isaac Sim is unresponsive meanwhile. Accepted for now ("good enough", the user, 2026-10-28);
  moving it off the main loop is a follow-up.
- **Revisit if:** a relocatable Python, or a check of the venv's Python on first start, makes a pre-built venv
  reliable.
- **Confidence:** High (from the user). **Status:** Accepted.

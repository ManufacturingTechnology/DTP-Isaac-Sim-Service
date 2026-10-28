---
title: 'Robots (as built)'
audience: agent
---

# Robots: RobotAPI, kinematics service, cuMotion, tool changers, conventions (as built)

As-built notes for robot control: the robot motion stack (pose models, `KinematicsSolver` and its cuMotion
backend, interpolation and timing, the planner, execution semantics, end-effector combinations, the RobotAPI),
the kinematics service (ADR-0010), the extension's `ManipulatorRobotCuMotionActuator` (TCP, flange override,
tool frames, on the solver), `ToolChanger`, base-frame and convention decisions, and findings about Isaac Sim
library end effectors. Decisions: ADR-0010 to ADR-0016, ADR-0022 to ADR-0024; design: `Specs/robot-control.md`,
`Specs/end-effector-package.md`. Verify against the code; may lag.

## Modules

| Module | Contents |
|---|---|
| lib `control.host` | `ApiHost` (ADR-0024): the server-on-its-own-thread base of `CoreApi`, `KinematicsApi`, and the extension's ISE `ApiController` |
| lib `control.transducer.models.pose` (re-exported by `transducer.models`) | `Vector3`, `Quaternion` (after-validator normalizes; zero and non-finite rejected), `Pose`, `FramedPose`, `poses_to_arrays`; converters and algebra (spec § Pose Models; ADR-0014) |
| lib `control.transducer.models.robot` | `ManipulatorRobotActuatorPose` with `position: Vector3`, `orientation: Quaternion` (numpy arrays are rejected on the wire and in Python); `to_numpy()`, `from_numpy(position, wxyz, frame_of_reference)` |
| lib `control.motion.kinematics` | `KinematicsSolver` (ABC; SI units; `fk`, `ik` with continuity seeding, unwrap, FK-measured errors; `set_frames`, `revision`; `jacobian` (numerical by default, `_jacobian` hook), `self_collision` / `collision_spheres` (`_self_collision` / `_collision_spheres` hooks, `UNSUPPORTED` by default), `capabilities`; `joints_from_wire` / `joints_to_wire`; `limit_violation`), `JointInfo`, `IkSolution`, `KinematicsError(code)`, `PLACEHOLDER_VELOCITY = 1000`, `to_wire` / `from_wire`, `plausible_limit` |
| lib `control.motion.trajectory` | `time_scaling` (trapezoid, or double-S with `jerk=` via `_scurve`; rounded up to whole steps, stretched; `max_steps` raises `TooManySteps` before allocating; `TimeScaling.ramp` / `ramp_steps`), `joint_time_scaling`, `ptp_samples`, `lin_samples`, `circ_samples`, `CartesianPath.line/arc` (`at`, `displacement`, `body_rotation`, `time_scaling`), `slerp`, `check_steps` (jump check, ADR-0013), `blend_overlaps`, `chain_starts`, `joint_chain_samples`, `cartesian_chain_samples`, `DurationTooShort`, `InvalidCircle`, `TimeScaling`, `StepViolation` |
| lib `control.motion.planning` | `MotionPlanner` (ABC: `set_obstacles(obstacles, base_pose)`, `set_world`, `plan(start, goal/target, frame, attempt)`, `collisions`, `check_path`, `description`, `lock`, `revision`), `PlanningError`, neutral obstacles `CuboidObstacle`, `SphereObstacle`, `CylinderObstacle`, `CapsuleObstacle`, `MeshObstacle` (discriminated `Obstacle`), `PlanningWorld{reference, obstacles, base_pose}`, `obstacles_in_base` |
| lib `control.motion.endeffector` | `XrdfFragment` (+ `FragmentFrame`, `FixedTransform`, `Sphere`, ...), `StackEntry`, `Combination`, `CombinationSet`, `FragmentError`, `load_yaml` (JSON without PyYAML), `load_fragment`, `load_combinations`, `merge_xrdf(robot, fragments, combination, separator, robot_frames, urdf_text)`, `urdf_link_parents`, `render_xrdf` (own deterministic YAML emitter), `write_merged_xrdf`, `merged_file_name`, `default_joint_positions` |
| lib `control.motion.models` / `.trajectory_models` | wire models: `RobotError`, `RobotErrorCode` (+ the service's codes), `JointDescription`, `KinematicsDescription`, `SolverDescription`, `SolverFile`, `KinematicsServiceInfo` (+ `planning_backends`), `ServiceLimits`, `SolverCreateRequest` (+ `fragments`, `combination`), `FkRequest/Response`, `IkRequest/Result/Response`, `ReachableResponse`, `JacobianRequest`, `Jacobian`, `JacobianResponse`, `SolverFrames`, `SolverConfig`, `SelfCollisionRequest/Result/Response`, `CollisionSpheresRequest`, `CollisionSphere(s)`, `WorldDescription`, `WorldCollisionRequest/Result/Response`; `TrajectoryLimits`, `TrajectoryRequest` (uses the RobotAPI's `RobotMove`, hence its own module), `TrajectoryResponse`, `PathCheckRequest/Response` |
| lib `control.motion.registry` | `SolverRegistry` (factories by backend, `planner_factories=`, `planning_backends`, limits, `create(..., fragments=, combination=)` (merged on upload; files `robot_xrdf`, `fragment`, `combination`) / `get` / `use` (entry with its lock held) / `delete` / `clear`, `from_bindings` for a RobotApi), `SolverEntry` (solver (follows a RobotApi robot's active combination), `RLock`, active `tool_frame`, `flange_frame`, `tcp_offset`, files, temp `directory`, `read_only` via a `RobotBinding`, `planner` (lazy), `world` / `set_world`; `describe`, `configure`, `limits_binding`), `SolverSource`, `SolverFactory`, `PlannerFactory`, `RegistryError(code, status)`, `SOLVER_ID_PATTERN` |
| lib `control.motion.server` / `.client` | `KinematicsApi(ApiHost)` (default port 8014; `planner_factories=`; `stop()` also clears the registry; limits `max_solvers` 1024, `max_upload_bytes` 256 MiB, `max_batch` 1 000 000, `max_samples` 10 000 000, each `None` for no limit), `kinematics_router(registry, max_batch, max_samples, serial=)` (create/delete only when the registry has factories; World routes), `coded_error`, `joint_description`, `kinematics_description`, `jacobian_to_wire`; `KinematicsClient(HttpClient)` (`create_solver(fragments=, combination=)`, `solvers`, `solver`, `delete_solver`, `download_solver_file`, `fk`, `ik`, `reachable`, `jacobian`, `sample_trajectory`, `check_path`, `frames`, `solver_config`, `configure_solver`, `self_collision`, `collision_spheres`, `world`, `set_world`, `clear_world`, `world_collision`, `kinematics_info`) |
| lib `control.robot.models` | `SetTcp` / `PtpMove` / `LinMove` / `CircMove` / `PlanMove` (discriminated `RobotMove`; `type` always serialized), `TerminationMixin` (`termination`, `cnt`), `CartesianLimits` (+ `jerk`, `angular_jerk`), `SampledMove`, `RobotCommandBase[TC]`, `RobotResponseFrameBase[TF]`, `RobotCompileRequestBase`, `RobotCompileResponseBase[TF]` (`queue_revision` / `end` only as preview fields, `COMPILE_PREVIEW_FIELDS`), `RobotPlanRequest/Response`, `RobotClearResponse`, `RobotState` (+ `combination`), `CompiledCommand` (+ `blended`), `RobotCommandResponse`, `RobotApiInfo`, `RobotDescription` (+ combination(s), planner, tolerances), `PlannerDescription`, `RobotJointDescription`, `RobotConfig` (+ `combination`), `RobotConfiguration` |
| lib `control.robot.compiler` | `RobotBinding` (dataclass; effective limits and sources, `max_steps(dt)`, `timing_limits`, `jerk_limits`, `path_tolerance`, `goal_tolerance`, `goal_time`, `start_tolerance`, `combinations`, `combination`, `planner`; `select_combination`, `active_planner`, `jerk_limit`, `jerk_limits_si`, `tolerance`, `tolerance_wire`), `compile_chain` (blends; overlaps halved then dropped on JOINT_JUMP/IK failure), `plan_move` → `PlannedMove`, `compile_move(..., max_steps=None)` (all moves), `blends_into`, `compile_commands` (blend runs) → `CompiledBatch`, `predict_start`, `check_start_state`, `read_joints`, `read_component_joints`, `frame_addresses`, `fold_commands`, `deceleration_ramp`, `RobotCompileError` (`code`, `status`, `detail`) |
| lib `control.robot.server` / `.client` | `RobotApi(SegmentApi)` (routes `/api/robot/...`, incl. `POST /process/plan`, `DELETE /process/clear?stop=`, + the kinematics routes over `SolverRegistry.from_bindings` (read-only; `kinematics=False` to omit); `compile(commands, start, dt)`, `write`, `_submit`, `plan`, `clear_robots(stop)`, `robot_states`, `robot_frame`, config; the `_monitor` step monitor (tolerances; the last two commanded positions per robot for the decelerating clear); the combination in config, info, and states), `build_robot_models(prefix, engine, preview_features)`; `RobotClient(SegmentClient, KinematicsClient)` (`robot_*` methods incl. `robot_compile(start=, dt=)`, `robot_plan`, `robot_clear`) |
| lib `control.segment` | what the compiler uses (`queue_revision`, `prediction_snapshot()`, `expected_revision`, `submit_many()`, batch-aware `withdraw`, `add_monitor`, `clear(replacement=)`, `merge_frames`) and the robot parts of codegen: `Reference/control-apis.md` |
| extension `amt.dtp.ise.robot.kinematics` | `CuMotionKinematicsSolver` (URDF + XRDF or their text; joint types from the URDF; `set_frames` → XRDF `add_frame` + `tool_frames`, dependency-ordered, rolled back on failure; `solve_ik_native`; `ik_config`, `last_ik_results`; `cumotion_robot` only inside Isaac Sim; analytic `_jacobian`; `_self_collision` / `_collision_spheres` via a world-less `RobotWorldInspector`, rebuilt after frame changes; `robot_world_inspector(world_view=None)`, the planner's extension point), `CUMOTION_LOCK` (process-wide `RLock` around every cuMotion call, also taken by the actuator's direct FK), `cumotion_solver_factory(SolverSource)`. Imports `isaacsim.robot_motion.cumotion` if available (DLL directory, URDF normalizer), else plain `cumotion` |
| extension `amt.dtp.ise.robot.planning` | `CuMotionMotionPlanner(solver, tool_frame, config, mesh_max_overshoot)` (lazy world rebuild on obstacle or frame changes; seed per attempt; `min_distance`), `cumotion_planner_factory`, `stage_obstacles(stage, roots, exclude, colliders_only)` |
| extension `amt.dtp.ise.robot.endeffector` | `build_combination_solvers(urdf, xrdf, combinations, fragments, cache_dir, include_bare="none")` → `(solvers, merged paths)`, `validate_combination(solver, sites, q, tolerances)`, `usd_site_poses(stage, robot_root, names, base_pose)` |
| extension `amt.dtp.ise.robot.base` | `usd_base_pose(prim)`: world pose of the robot root prim from USD (fixed bases) |
| extension `amt.dtp.ise.service.kinematics` | `KinematicsService(Service)`: started after `ApiService` in `extension.py`; serves `KinematicsApi({"cumotion": cumotion_solver_factory}, planner_factories={"cumotion": cumotion_planner_factory})` on `kinematics_service_port` (lib `ApplicationConfiguration`, default 8014); a start failure is logged, shown in a `MessageWindow` ("Error Report", scheduled for the next update; the UI modules are imported only then, so the service imports nothing from Omniverse), and the extension continues; stop deletes every solver and its files |
| extension `api/core.py` | `ApiController(ApiHost)` (ADR-0024): routes under `/api` from a `ServiceAPIRouter`, the static docs at `/`, `run_coro_on_isaac_loop` = `run_coro_on_main_loop`, a start failure logged |

**Preview features (deferred):** `RobotApi(..., preview_features=False)` by default: the compile route
takes only `[RobotCommand]`, `RobotApi.compile(start=/dt=)` raises `RobotCompileError` (400), and the compile answer
has no `queue_revision` / `end` (`build_robot_models(..., preview_features)` adds them from
`robot.models.COMPILE_PREVIEW_FIELDS`; the generated client then emits them as a subclass's fields). The step monitor
(tolerances) and the internal `expected_revision` on `write` stay. Tests: `test_preview_features_are_off_by_default`;
the other upper-layer tests construct with `preview_features=True`. Execution semantics are deferred;
collision-aware planning follows ADR-0023; the compile semantics are proposed and parked.

## cuMotion findings (verified)

Probes and `Scratch/harnesses/` runs with cuMotion 1.1.0a2 (Isaac Sim 6.0.1):

- **No batched plain IK:** `solve_ik` is per pose; `CollisionFreeIkSolver.solve_array` needs a world. Timings
  (probe + `kinematics_benchmark.py`, MR-A1): FK ≈ 1 µs (6 µs through the base class), seeded IK ≈ 35 µs raw /
  ≈ 80 µs through `KinematicsSolver.ik`, unreachable ≈ 2 ms, 600 path poses ≈ 16.5 ms; a 106-step LIN compile ≈ 6 ms.
- **Limits:** cuMotion doesn't expose joint types (parsed from the URDF). It reports the XRDF's acceleration
  (10 rad/s²) and jerk (10000) limits and the URDF's velocity limit, which for the MR-A1 is a placeholder (1e6
  rad/s); hence `PLACEHOLDER_VELOCITY` and the 5°/step fallback (the alpha testbeds' MR-A1 script sets 180 deg/s).
- **Orientation tolerance** is per axis; the base class checks the rotation angle (up to ~1.22× the per-axis
  error), so the backend asks cuMotion for 0.8× the angle tolerance.
- **Jacobian:** `Kinematics.jacobian` is 6 × n, linear rows then angular-velocity rows in the base frame, equal to
  central differences of FK within 1e-9.
- **Self-collision:** `create_robot_world_inspector(description)` without a world gives self-collision
  (`frames_in_self_collision` returns each pair once); Isaac Sim's bundled UR10 has 38 self-collision spheres, the
  Franka 57. `load_robot_from_memory` raises `RuntimeError` for a bad URDF or XRDF (mapped to 422
  `INVALID_DESCRIPTION`).
- **Planning:** a graph-based `MotionPlanner` (`create_default_motion_planner_config(description, tool_frame,
  world_view)`, `create_motion_planner`, `plan_to_cspace_target`, `plan_to_pose_target`; `Results.path`,
  `interpolated_path`), a `TrajectoryOptimizer`, a `CSpaceTrajectoryGenerator` (time-optimal with jerk limits), a
  `World` (`create_obstacle(CUBOID|SPHERE|CAPSULE|SDF)`, `add_obstacle`, `add_world_view`), and
  `generate_collision_spheres(vertices, triangles)` (read in `_cumotion.pyi`). The world is in the robot base frame.
  `MotionPlannerConfig.set_param` accepts `seed`, `step_size`, `max_iterations` (not `collision_buffer`).
- **Planning the UR10 around a 15 cm box:** ≈ 1–6 ms per call (probe), ≈ 30 ms per attempt including planner
  creation (harness); the planner's own segments can graze an obstacle (probe, seed 2: min distance −0.6 mm along
  a segment), hence the per-step check and re-planning; `plan_to_*` releases the GIL; cuMotion's other calls
  measured so far hold it.
- **XRDF:** frame names may contain `/`, `_`, `.`, `:`; an `ignore` entry naming an unknown frame is accepted;
  geometry groups can `clone` others (Isaac Sim's UR10 XRDF). `generate_collision_spheres` fails for a flat mesh
  (zero-volume grid): flat meshes become thin boxes.
- An in-memory USD stage defaults to `metersPerUnit = 0.01`; the actuator scales by it (harness gotcha).
- The all-zero MR-A1 pose is stretched out (singular): a LIN from it fails the jump check; start from a ready pose
  (e.g. `[0, -20, 60, 0, 50, 0]` deg).

## The kinematics service (ADR-0010)

A scene-free, MoveIt-like REST service: clients upload URDF + XRDF (`POST /api/kinematics/solvers`, multipart or JSON),
get a solver id, and call FK, IK, reachable, Jacobian, trajectory sampling (`set`/`ptp`/`lin`/`circ`/`plan` through
the RobotAPI's `compile_move`; `plan` needs a planning backend, from a client-given start and `dt`), the path check, frames / TCP configuration,
self-collision, collision spheres, and the world; tags "Solvers", "Kinematics", "Trajectories", "Tool Frames",
"Collision". Routes, models, errors, and limits: `Specs/robot-control.md` ("The kinematics service"). In the
extension: `KinematicsService`, port 8014, `/api/docs`. A `RobotApi` serves the same routes for its robots
(read-only: no create/delete (405), config writes 409 `READ_ONLY`, active tool frame = the robot's).

- **Uploads:** multipart needs `python-multipart` (bundled with
  Isaac Sim 6.0.1: `python_multipart` 0.0.26 in `omni.kit.pip_archive`; not declared by the lib); without it
  multipart answers 415 and JSON works. The body is read with a cap of 2 × `max_upload_bytes` + 64 KiB before
  parsing (413; no cap when `max_upload_bytes` is `None`); files are stored in a `tempfile.mkdtemp` directory per
  solver (`amt-kinematics-<id>-...`), deleted on delete and on stop.
- **Concurrency (ADR-0010; evidence: `Scratch/robots/supporting/2026-10-23_cumotion-thread-safety.md`):** the
  service runs on its own thread (an `ApiHost`, not a `CoreApi`: no `/api/core` routes, ADR-0024) and handles
  requests **one at a time**: `kinematics_router(serial=True)` registers every handler as a coroutine that runs the
  solver inline on the server's event-loop thread (no thread pool). `SolverRegistry.use` still holds the entry's
  `RLock` (the RobotAPI compiler takes the same lock); `CUMOTION_LOCK` around each cuMotion call (≈ 112 ns
  uncontended; FK 6.3 µs through the base class). A RobotApi registers the same routes with `serial=False`
  (FastAPI's thread pool).
- **Scene-free (verified by probe):** the probe's process imported no `omni`, `pxr`, `carb`, `isaacsim`, or `warp`
  module; `KinematicsService` reads only its port (configuration default and global layers exist without a stage;
  the port is read once at startup) and imports `robot.kinematics` / `robot.planning` (cuMotion only;
  `stage_obstacles` imports `pxr` lazily and is never called by the service).
- **World and fragments (ADR-0010, ADR-0012):** `GET/PUT/DELETE /solvers/{solver}/world`, `POST
  /solvers/{solver}/collision/world`; a `plan` move through trajectory sampling; fragments merged on create.

## Verification status

- **lib tests** (all pass; counts in `Reference/control-apis.md`): `lib/tests/control/test_pose.py`,
  `test_trajectory.py`, `test_robot_api.py` (incl. HTTP end to end, the generated module, the robot stream, the
  read-only kinematics routes; with `robot_fakes.py`: an XYZ gantry + ZYX wrist solver whose pitch limit ±90°
  forces a real wrist flip, a perfectly tracking robot component, and a fake planner), `test_robot_upper.py` (jerk,
  CIRC, blending, plan, world frames, compile-then-merge, pause, tolerances, start check, decelerating clear,
  combinations, the routes end to end), `test_endeffector.py`, `test_api_host.py`, `test_kinematics_service.py`
  (fake backend; world and planning, unsupported without a planner, fragments; one request at a time on the server
  thread; the multipart test or the 415 test skips depending on python-multipart).
- **Offline with real cuMotion** (`Scratch/harnesses/`, local): `kinematics_benchmark.py` (7 checks + timings);
  `extension_harness.py` with the MR-A1 asset folder as third argument (the actuator on the solver and a RobotApi on
  a real `AutomationComponent`); `kinematics_service_probe.py` (12 checks over HTTP, incl. the extension
  `KinematicsService` with the configuration stubbed); `upper_layer_harness.py` (8 checks: planner, shapes, GIL,
  `stage_obstacles` on an in-memory stage, the compiler on the UR10, UR10 and MR-A1 combinations with USD-Site
  validation, the ISE `ApiController` with `omni.services` stubbed); `demo_harness.py` runs the alpha testbeds'
  MR-A1 script and client (a RobotApi with a cuMotion solver and a stage-obstacle planner; a planner setup failure
  is raised from `on_play`, so playing stops with the error window, the user's choice; the client shows blended
  PTP, LIN, CIRC, IK checks, a rejected unreachable target, and a planned move) and passes.
- **Not yet run inside Isaac Sim:** planning in a live scene, the ISE API on `ApiHost` in Kit, the kinematics
  service and the other services starting in a real Kit session (and its Swagger page there), the actuator's
  solver-backed FK/IK and `set_flange_frame` (verified offline only).

## MR-A1 asset limits (the asset files are the user's to fix)

- **The XRDF has no collision spheres**, so cuMotion sees **no robot geometry at all**: `plan` moves avoid
  neither scene obstacles nor self-collision, and self-collision checks always pass (probe: a 30 cm box at the
  tool frame reported no collision, minimum distance `DBL_MAX`). Add spheres with Isaac Sim's robot description
  editor.
- **URDF velocity limits are placeholders** (`1000000` rad/s); the script overrides them from its config (180
  deg/s). The URDF material `Dark` has no `rgba` (cuMotion logs a warning on every load).
- At the ready pose the tool frame is at x = −0.170 m, behind the base: possibly the rotated-base-frame issue
  (see "Key decisions" below) ❓.
- In a scene, every collider outside the robot's prim becomes an obstacle (e.g. a separately placed gripper or
  the floor); once spheres exist, a mounted end effector outside the robot prim may need excluding or modelling
  as a combination.
- Reach (IK probe): max x 0.6 m at z 0.3 m with the tool horizontal; 0.275 m top-down. The G1 gripper's jaw
  limits are 0.02 m each, so a gripped ball must be < 0.04 m across (or the jaw travel increased).

## User docs

`docs/src/Asset Development/070-Manipulator-Robots.md` (robot control, rigging, kinematics, tool changer, library
robots) and `900-Conventions.md`. Research notes on library end effectors and outstanding issues are local in
`Scratch/robots/2026-10-19_robots-page-research-notes.md`. This file holds working state and decisions the user docs
do not.

## Actuators and the tool changer (user confirmed in Isaac Sim 6.0.1)

- `ManipulatorRobotCuMotionActuator` in `extension/exts/amt.dtp.ise/amt/dtp/ise/transducer/robot.py`.
  `ManipulatorRobotLulaActuator` is retained, deprecated (Isaac Sim 6.0 has no drop-in `LulaKinematicsSolver`
  replacement).
  - Its FK/IK and frames come from a `CuMotionKinematicsSolver` (`.solver`); `set_tcp` / `set_flange_frame` call
    `solver.set_frames` (stage units × `metersPerUnit`), `ik_config` / `last_ik_results` / `cumotion_robot` delegate
    to it, and `_solve_ik` uses `solver.solve_ik_native`. Verified offline (extension harness), not yet in Isaac Sim.
  - XRDF `tool_frames` are mandatory; the first is the flange frame and should be `tool0`, the flange face.
  - `set_tcp(position, orientation=None)` / `clear_tcp()` add or move an XRDF `add_frame` named `tcp` under the
    flange frame, then reload (~0.2 ms). The user chose `set_tcp`/`clear_tcp` over separate add/set methods.
  - `actuator_write` takes `frame_of_reference` "local" (robot base) or "global" (world).
  - `set_flange_frame(position, orientation=None)` / `clear_flange_frame()` (user-requested): override `tool0`
    with an XRDF `add_frame` `flange_override` under the XRDF flange frame. `flange_frame` /
    `flange_frame_world_pose()` return the override while set; the TCP is parented to it (moves with it);
    `tool_frame` is derived (tcp > override > tool0). This is the chosen answer to the tool changer stack height
    question. Tested offline (a kinematic probe), not yet in Isaac Sim.
- `ToolChanger` (Sensor + Actuator) in the same file, with one bool model, `ToolChangerModel.attached`. The user
  specified it should be "dumb":
  - It knows nothing about end effectors.
  - The sensor is a proximity "present" signal: it reads True after detach until the robot retreats. Scripts
    check presence after each retreat.
  - Attach uses a session-layer `UsdPhysics.FixedJoint` at `<body>/ToolChangerFixedJoint` with
    `excludeFromArticulation`. The body is found by `overlap_sphere` at `tool0`.
  - The workpiece `FixedJoint` in `articulation.py` is separate, at the user's request. Do not merge the two.
- `ArticulationTransducer.is_a` (`articulation.py`) skips world-anchoring joints (an empty body0 or body1).
- The ball-routine demo (user docs, "Use a Tool Changer" › "Demo: Ball Routine": three end effectors picked from a
  rack, then again through a second `ToolChanger` with `set_flange_frame((0, 0, 0.02))`; rack entered and left by
  straight lines through a point 0.15 m in front of each slot) passed one full cycle (101 steps) in a kinematic
  harness; not yet run in Isaac Sim, so that user-doc section is unverified.

## Key decisions / findings (do not re-derive)

- **Base frame rule:** the robot base = the root link's physics pose × the authored USD offset (root link → asset
  root prim). The asset root prim is the articulation root prim, or its nearest Xformable ancestor.
  - Link frames are wrong for this: the AMT `BaseLink` is rotated 180°, and the UR10 USD links differ from its URDF.
  - The root link is found from the joint with exactly one empty body, else the articulation root body.
- **Conventions:**
  - Base: +X forward, +Z up.
  - Tool/TCP: +Z = approach, Y = jaw axis.
  - `tool0` = the flange face (not REP-199 `flange`, which is X-out).
- **Recommendation for the AMT robot:** add a `tool0` Xform (optionally `IsaacSiteAPI`), not a UR-style massless
  `ee_link` physics body. The user has not decided.
- **Library end effectors fly away (cause inferred, not reproduced) ❓:**
  - All four inspected grippers (Schunk EGU 50 / EGK 25, Robotiq 2F-140 / 2F-85) carry their own world anchor: a
    fixed joint with an empty body0 and localPos0 = (0, 0, 0).
  - Placed away from the origin, they snap back to it at Play.
  - Check the console for "disjointed body transforms".
- **Tool changer stack height:** decided, `set_flange_frame()` (see above).

## Open work (assets)

- Regenerate `AMT MR-A1.xrdf` (keep the `tool0` `add_frame` on Link6, (0, 0, 0.06), and `tool_frames: ["tool0"]`)
  and the URDF after rotating the model. Re-check the `Joint1` origin.
- Choose a tool changer, and which end effectors, for a multi-end-effector UR10 demo (candidates from Schunk's
  Vention listings: PR-SK-001-0059, the Schunk collaborative electric gripper for UR; PR-SK-106-0101, the EGK 40,
  a variant for another robot brand).
- Never edit testbed/scene USD files; ask the user to make the changes.

## Tooling

See `Reference/dev-environment.md` (portable setup: Isaac Sim Python, offline `pxr` and cuMotion, docs builds).

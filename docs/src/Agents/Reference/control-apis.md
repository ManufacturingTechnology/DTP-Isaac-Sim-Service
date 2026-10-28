---
title: 'Control APIs (as built)'
audience: agent
---

# Control APIs (as built)

The machine-control stack as implemented: typed `AutomationComponent` models, the `ApiHost` and `CoreApi`
bases, the segment (`SegmentEngine` + `SegmentApi`) with per-component status, typed errors, the
halt-on-failure policy, confirmed configuration writes, sequence numbers and the retransmission buffer, and
generated client modules. The RobotAPI and the kinematics service build on it (`Reference/robots.md`).
Design: `Specs/segment-protocol.md`, `Specs/robot-control.md`; decisions: `Reference/decisions.md`. Verify
against the code; may lag.

## Modules (lib, `amt.dtp.ise.lib.control`)

| Module | Contents |
|---|---|
| `component` | `AutomationComponentBase` (abstract: `actual_model`, `command_model`, `sensor_read`, `actuator_read`, `actuator_write`; optional `config_model` / `config_read` / `config_write`), `ComponentDefinitionError`, `ComponentWriteError(errors: {transducer id: exception})`, ids (`id_problem`, `path_to_id`), names (`to_class_name`, `dynamic_model_name`), `build_container_model`, `constrained_subclass`, `schema_fingerprint`, `find_class_name_conflicts`, `models_accepting_unknown_fields`, `GENERATED_MODULE_RESERVED_NAMES` |
| `host` | `ApiHost` (ADR-0024): FastAPI + uvicorn on its own thread with its own loop; `add_router`, start/stop (start waits until it accepts connections), `/api/docs`, `/api/redoc`, `/api/openapi.json`, the unhandled-error handler, `call_soon_on_server_loop`, `server_thread`, and the main-loop bridge (`main_loop`, captured by `start()`; `run_coro_on_main_loop`). No routes of its own. Also the base of the kinematics service and of the extension's ISE `ApiController` |
| `core.server` / `core.client` / `core.models` | `CoreApi(ApiHost)` (the machine-control core: `/api/core/info`, `/api/core/assets/list`, `/api/core/assets/download/{name}`; `layers` (from `CoreApi` down; `ApiHost` isn't a layer)), `HttpClient` (session, `/api` URLs, coded errors; no routes; the base of `KinematicsClient`), `CoreClient(HttpClient)`, `CoreApiError` (`.code` for coded errors), `CoreInfo` (`layers`), `AssetInfo` |
| `segment.merge` | `merge_frames(*sources, offsets, frame_id)` (compile-then-merge; a deferred preview feature, built and kept), `MergeConflict` |
| `segment.models` | `SegmentError`, `ErrorCode`, `error_code(ex, default)`, the generics `CommandFrameBase` / `ComponentStateBase` / `ResponseFrameBase` / `CommandResponseBase` / `ConfigurationBase`, `ConfigEvent` / `ConfigOutcome`, `StatusEvent` / `SegmentStatus` / `HaltInfo`, `BufferHeader`, `ErrorEvent`, `ResetEvent`, `Heartbeat`, `RetransmitResponse`, `SegmentInfo` / `SegmentPreviewInfo`, `build_segment_models` → `SegmentModels` |
| `segment.engine` | `SegmentEngine` (no fastapi/asyncio): queue, halt, config requests, `step()`, sequence numbering, retransmission buffer, listeners; `QueueFullError`, `QueueClearedError`, `QueueChangedError`, `SegmentHaltedError`, `OutOfRangeError`, `SegmentEvent` |
| `segment.server` | `SegmentApi(CoreApi)`: routes under `/api/segment`, stream subscribers on the server loop |
| `segment.client` | `SegmentClient(CoreClient)`, `SegmentStream`, `SegmentStreamError`, `Retransmission`, `SchemaMismatch`, `download_client_module` |
| `segment.codegen` | `generate_client_module(..., robot_models=None)`, `FIXED_ALIASES`, `ROBOT_FIXED_ALIASES`, `RobotModels`, `IMPORTABLE_MODULES` (transducer, segment, and robot models) |
| `motion.*` | `KinematicsSolver` interface, interpolation and timing, `MotionPlanner`, end-effector fragment merging, and the kinematics service (ADR-0010): `SolverRegistry`, `KinematicsApi` (`/api/kinematics/...`, port 8014 in the extension's `KinematicsService`), `KinematicsClient`; see `Reference/robots.md` |
| `robot.*` | `RobotBinding`, the compiler, `RobotApi(SegmentApi)` (`/api/robot/...` + the kinematics routes for its robots, read-only), `RobotClient`; see `Reference/robots.md` |
| `codegen` | `render_models` (live pydantic classes → source), `require_builtins`, `generator` / `set_generator` (the generating software named in client module headers) |

Extension (`amt.dtp.ise.transducer.core`): `Transducer`, `Sensor` (`actual_model_type`, `sensor_read`),
`Actuator` (`command_model_type`, `actuator_write`, `actuator_read`), `AutomationComponent` (declarative or
dict; models `{Name}Actuals` / `{Name}Commands` / `{Name}Config`), `transducer()`, `ref()`, `asset()`.
Transducer model names: `{name}_Actuals`, `{name}_Commands`, per joint `{name}_{joint}_Commands` (one field
per articulation joint), D6 `{name}_Axis_Commands` (the axis is a `Literal`, ADR-0015). Joint command models
and sensor models both name their fields `position` / `velocity` (ADR-0007). MTConnect's map resolution
recognizes joint commands by class (`issubclass` of the four `*Joint{Position,Velocity}Model`s), not by field
names, since a conveyor's `VelocityModel` also has a lone `velocity` field. `IKUnreachableError` (robot
actuators with `raise_when_unreachable=True`) and `PhysicsViewUnavailable` carry `error_code`.

## Model rules

- **Typed and strict (ADR-0001, ADR-0003):** every model uses `extra="forbid"`, so unknown names are rejected
  (`models_accepting_unknown_fields` warns about custom models that would accept them). A frame carries deltas:
  every container level is optional, and an empty frame is a one-step delay. One frame may address several
  components.
- **Write errors** are per actuator (`ComponentWriteError` maps transducer ids to exceptions); constrained
  numbers get Swagger examples (ADR-0015); transducers and components can have configuration (ADR-0005).
- **Still checked only at runtime:** IK reachability (unless `raise_when_unreachable`), the physics view,
  ToolChanger attach with nothing seated, D6 axis mode vs drive, USD/material operations, custom checks.
- **Gotcha:** prismatic joint values are really **stage units** (the models say meters); that is correct only
  while the stage's `metersPerUnit = 1`.

## The segment

- **One step** (`SegmentEngine.step(physics_time_step, timeline_time=None, physics_time=None)`, called from
  `on_physics`, ADR-0004):
  1. take all pending configuration writes and, unless halted, the next queued frame (under the lock);
  2. apply the configuration writes (`config_write` per component; `ComponentWriteError` → per-transducer
     `CONFIG_WRITE_FAILED`; the snapshot is re-read);
  3. apply the frame: each addressed component's `actuator_write`; status `applied` or `failed` (errors
     per transducer from `ComponentWriteError`, codes from `error_code`), others `not_addressed`;
  4. read every component (`sensor_read`, `actuator_read`); a failure is a `READ_FAILED` error with null
     actuals/commands, logged once until it recovers, and doesn't change the status;
  5. under the lock: emit `config` events (one sequence each), then the `frame` event; if any component
     failed, set the halt and emit a `status` event (`halted`, `frame_failed`); then resolve the waiting
     futures (config writes get their `ConfigEvent`, `execute` its response frame).
- **Halt (ADR-0021):** while halted, `step()` doesn't pop frames; frames are `halted: true`; `enqueue`
  still accepts (the `CommandResponse` says `halted`); `submit` (execute) raises `SegmentHaltedError` → 409
  `HALTED`; `resume()` (idempotent) and `clear()` release it, each emitting a `status` event.
- **Preview features (deferred):** pause and optimistic writes are served only with
  `SegmentApi(..., preview_features=True)` (off by default; `SegmentApi.preview_features`). Without it there is
  no `POST /process/pause` route, `SegmentApi.pause()` raises `RuntimeError`, `/info` is a `SegmentInfo` without
  `paused` / `queue_revision` (with it, a `SegmentPreviewInfo`), and `write?expected_revision=` answers 400 (the
  parameter is hidden from the schema). `SegmentClient.segment_info()` parses either. The engine keeps everything.
- **Pause** (preview only): `pause()` / `POST /process/pause` stops draining from the next step (state
  `paused`, `reason: paused`); writes queue, `execute` waits; `resume()` releases a pause and a halt; `clear()`
  keeps a pause. `SegmentStatus.paused`, `SegmentInfo.paused`; response frames unchanged (fingerprints
  unchanged).
- **Step monitors** (`add_monitor`; not a preview feature): called each step after the reads, outside the engine lock; their
  errors are added to the step's component errors and halt the segment (`HaltInfo` with them, `reason:
  monitor_failed`; not repeated while already halted). The RobotAPI's tolerance monitor uses it.
- **`clear(replacement=)`** queues frames computed under the lock in the same atomic step (the RobotAPI's
  decelerating clear). **`write?expected_revision=`** (409 `QUEUE_CHANGED`); `SegmentInfo.queue_revision`,
  `SegmentInfo.stop_policy` (`hold`). `SegmentClient.pause()`, `write(..., expected_revision=)`.
- **For compilers (the RobotAPI):** `queue_revision` increments on every enqueue, submit, clear, and withdrawal
  (not on `step()` pops); `prediction_snapshot()` returns `(latest response frame, [frame being applied, *queued
  frames], queue_revision)` atomically (`step()` keeps the popped entry in `_applying` until its frame event
  is emitted); `enqueue(frames, expected_revision=)` and `submit_many(frames, expected_revision=)` raise
  `QueueChangedError` on a mismatch; `submit_many` appends all frames (one batch marker) with a future on the
  last; `withdraw(future)` removes every still-queued frame of that batch.
- **Events and sequences (ADR-0008):** every `frame`, `config`, and `status` event takes the next sequence and
  goes into the retransmission buffer (`deque(maxlen=buffer_size)`, default 600, 0 = none); listeners are
  called under the engine's `RLock`, so they see events in sequence order. `instance_id` is a uuid4 hex per
  engine. A `SegmentEvent` serializes its model once (`.json`).
- **Configuration:** `config_read()` returns a snapshot (taken at construction and after every write);
  `config_write(value)` validates against `{Prefix}ComponentConfigs` and returns a
  `concurrent.futures.Future`. `config_revision` increments when at least one transducer was configured.
  `effective_sequence` is the frame emitted in the same step.
- **Thread model:** the engine resolves `concurrent.futures.Future`s; the API awaits them with
  `asyncio.wrap_future` + `shield` + `wait_for` (timeout → withdraw → 504). Stream subscribers live on the
  server loop; the engine listener schedules `_publish` with `call_soon_threadsafe`.
- **Streams:** live streams start with the latest frame. Resume points come from `?resume_after=` (with
  optional `instance_id=`) or `Last-Event-ID` (`instance:sequence`); the replay is taken and the subscriber
  registered without an await in between, and the subscriber drops events with a sequence it already saw,
  so replay and live never duplicate or gap. Filters (`every`, `executed_only`) apply to frames only, also
  in the replay; the "latest frame" counts toward `every`. Overflow (`subscriber_queue_size`, default 256)
  sends an `error` event (STREAM_OVERFLOW) and ends. `reset` events for INSTANCE_CHANGED and clamped
  OUT_OF_RANGE; `heartbeat` events every 15 s (`HEARTBEAT_INTERVAL`); a `stopped` status event before the
  API closes the streams.
- **Retransmit:** `GET /api/segment/retransmit` by `sequences=` (found + `missing`) or `from`/`to`/`count`
  (`truncated`; OUT_OF_RANGE 404 unless `clamp`); `instance_id=` mismatch → 409. Coded HTTP errors use
  `detail = {code, message, header?}`.

## Generated client modules

`GET /api/segment/models.py` (file name from the title, e.g. `machining_cell.py`; ETag = fingerprint). Its header
names the API title and version and the generating software and version (`codegen.generator()`: the lib by
default; the extension calls `codegen.set_generator("AMT DTP Isaac Sim Extension (ISE)", __version__)` at import,
since its embedded lib copy carries no version). No timestamp, so the output stays deterministic, and the
fingerprint hashes the schema, not the file text. Contents:
built-in models imported from `amt.dtp.ise.lib.control.transducer.models` and `...segment.models`, custom
and per-instance models rendered, `API_TITLE` / `API_DESCRIPTION` / `API_VERSION` / `SCHEMA_FINGERPRINT` /
`COMPONENTS`, a `require_builtins` check, the typed `{Prefix}Client(SegmentClient)`, and the fixed aliases
(ADR-0001) `Client`, `CommandFrame`, `ResponseFrame`, `CommandResponse`, `ComponentCommands`,
`ComponentStates`, `ComponentConfigs`, `Configuration`. The fingerprint covers the command frame, the response
frame, and the configuration models (documentation stripped), so it changes with the lib's frame format too.

`{Prefix}` is the API's `model_prefix`: by default the title as a class name, plus `Segment` when that default
would name a class the same as one of a component's (e.g. a declarative `JointPaloozaComponent` has
`JointPaloozaComponentCommands`, as does a segment titled "Joint Palooza"). A given `model_prefix` is never
changed; a collision then raises `ComponentDefinitionError`, and the error says to pass another one.

A `RobotApi`'s module also renders `{Prefix}RobotCommand`, `{Prefix}RobotResponseFrame`,
`{Prefix}RobotCompileResponse`, bases its client class on `RobotClient` (with `robot_*_model` class
attributes), imports `SetTcp`, `PtpMove`, `LinMove`, `CircMove`, `PlanMove`, `RobotState`, `RobotConfig`,
`Vector3`, `Quaternion`, `Pose`, `FramedPose`, and adds the aliases `RobotCommand`, `RobotResponseFrame`,
`RobotCompileResponse`. The robot models don't change the segment fingerprint; `require_builtins` also checks
the robot models it imports. `SegmentApi._generate_client_source()` is the hook; `SegmentApi._open_stream(...,
render=)` serves both streams (the robot stream renders each `frame` event as a robot frame, cached per
sequence).

## Verification status

- **lib suite** (`lib/tests`): **271 passed, 4 skipped** in a Python 3.13 venv with `pytest fastapi uvicorn
  requests` (the skips need `pxr` (2), PyYAML, or python-multipart); **274 passed, 1 skipped** on Isaac Sim's
  `kit/python` (3.12) with its bundled packages and `pxr` (the skip is the 415 test, which needs python-multipart
  absent). Both runs: `pytest lib/tests` with `lib/src` on the path.
- **Offline harnesses** (`Scratch/harnesses/`, local; real USD stage, omni/PhysX stubbed):
  `extension_harness.py` 27/27 with the MR-A1 asset folder (the halt on a real `AutomationComponent`,
  configuration through a `SegmentEngine`, real cuMotion IK through the robot actuator, a `RobotApi` on a real
  component); `kinematics_benchmark.py` 7/7; `model_editor_smoke.py` 36/36; `kinematics_service_probe.py` and
  `upper_layer_harness.py` (see `Reference/robots.md`). `demo_harness.py` passes the alpha testbeds' VM-A1
  and MR-A1 demo scripts and their client examples. A local prototype that emulates a vendor's cyclic streaming
  interface on top of the segment passes its tests.
- **Not yet run in Isaac Sim:** the testbeds (jointpalooza, the MTConnect shadows, test7), the demo assets,
  real cuMotion IK through the `tcp` actuator in a live scene. The MR-A1 USD has no behavior customData, so the
  user must attach `AMT_MR-A1.py` to `/Robot` (USD is user-edited).

## Open work

Not decided: wire units, timing modes and a reactive/lockstep mode, target timestamps, a watchdog, a
configurable halt policy, and the deferred RobotAPI and segment additions (`Specs/segment-protocol.md`
"Planned (not built)"; `Specs/robot-control.md`).

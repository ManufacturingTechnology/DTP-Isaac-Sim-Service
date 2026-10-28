---
title: 'Threading model (as built)'
audience: agent
---

# Threading model (as built)

> **Abstract.** How the extension's APIs use threads: each API runs its HTTP server on its own thread, and
> every call that touches Isaac Sim is scheduled as a coroutine on **Isaac Sim's main event loop**. The goal
> is to keep Isaac's loop (rendering, timeline, physics callbacks, behavior scripts) as fast as possible and
> do everything else off it. The kinematics service is the one exception. A key design rule, set by the
> user. Verify against the code; may lag.

## The rule

1. **Isaac Sim's main thread runs Kit's asyncio event loop**, and that loop drives everything in the
   simulation: rendering, the timeline, physics-step callbacks, behavior scripts (`on_play`, `on_physics`,
   `on_update`), the USD stage, and `omni.ui`. Many Isaac Sim and Kit libraries must be called **on that
   loop** (from a behavior-script or timeline/physics callback, or a coroutine scheduled on it); calling them
   from another thread is unsupported. Sources: the user: the documentation says to call Isaac
   Sim SDKs and libraries on the Isaac thread, not another thread, and the user's own experiments crashed or
   stalled Isaac Sim when this was broken (the exact documentation page is not yet cited ❓). An NVIDIA staff
   reply on the developer forum: USD authoring from a secondary thread (e.g. `stage.DefinePrim()`) "is not
   thread-safe", and coroutines are recommended instead of threads
   ([forum](https://forums.developer.nvidia.com/t/multi-threading-in-omniverse-extensions/236553), V(2°)). USD's
   own threading model allows many readers or one writer at a time, without locks in its API
   ([Animal Logic, "Filament Concurrency"](https://animallogic.com/wp-content/uploads/2024/08/Filament_Concurrency.pdf),
   secondary ❓).
2. **Every API runs its HTTP server on its own thread** (uvicorn with its own asyncio loop), so parsing,
   validation, JSON encoding, code generation, and streaming fan-out never cost Isaac's loop any time.
3. **Every call that touches Isaac Sim is wrapped in a coroutine and scheduled on Isaac's main loop**, and
   the request handler awaits its result. The server thread never calls Isaac, omni, USD-stage, or physics
   APIs directly.
4. **Work scheduled on Isaac's loop must be short.** Anything heavy that doesn't need Isaac (computation,
   I/O, encoding) stays on the server thread, or another thread, and only the Isaac-touching part goes to
   the loop.

**Why:** Isaac's loop is the simulation's frame and physics budget. Everything that runs on it delays the
next frame or physics step, so the APIs do as much as possible elsewhere and hand the loop only what must run
there.

## How it's built

**One host class (ADR-0024):** `ApiHost` (`lib/src/amt/dtp/ise/lib/control/host.py`) owns the pattern every API
shares: a FastAPI app served by uvicorn on **its own thread** with its own asyncio loop, start/stop, `/api/docs`,
`/api/redoc`, `/api/openapi.json`, the unhandled-error handler, `add_router()`, `call_soon_on_server_loop()`, and the
**optional bridge to Isaac's loop**: `start()` captures the event loop of the calling thread (Isaac's main loop when
called from the extension or a behavior script), and `await run_coro_on_main_loop(coro)` runs a coroutine there via
`asyncio.run_coroutine_threadsafe` and awaits it. The APIs build on it:

| API | Server thread | How it reaches Isaac's loop |
|---|---|---|
| ISE API (`extension/exts/amt.dtp.ise/amt/dtp/ise/api/core.py`, `ApiController`, port 8012) | an `ApiHost` | route handlers call `await ApiController().run_coro_on_isaac_loop(run())` (`api/routers.py`), an alias of `run_coro_on_main_loop`; its routes are an omni.services `ServiceAPIRouter` mounted under `/api`, and the static docs site at `/` |
| `CoreApi` and its layers (`lib/src/amt/dtp/ise/lib/control/core/server.py`) | an `ApiHost` ("API: <title>") | `await self.run_coro_on_main_loop(coro)` (inherited) |
| `SegmentApi` / `RobotApi` (`lib/.../segment/`, `lib/.../robot/`) | its own thread (a `CoreApi`) | the reverse direction: Isaac's physics callback calls `step()` (`on_physics`), which does O(1) work (take one queued frame, write, read, run the step monitors (the RobotAPI's tolerance check: a few array comparisons), build the response) and hands the result to the server loop with `call_soon_threadsafe`; HTTP handlers only touch the queue (under a lock) and never call Isaac. Compiling (IK, planning) runs in a worker thread (`asyncio.to_thread`) and uses cuMotion, a plain library, never the stage; a planner's obstacles are read from the stage by the behavior script on Isaac's thread (`stage_obstacles` in `on_play`) |
| Kinematics service (`KinematicsApi`, `lib/.../motion/server.py`; port 8014) | an `ApiHost` ("API: Kinematics Service"), **not** a `CoreApi` | never: see the exception below |
| Co-processor (`coprocessor/`, port 8013) | a separate process | not on Isaac's loop at all |

## The exception: the kinematics service

The kinematics service (port 8014, ADR-0010) **touches no Isaac state**: it works with no stage open and no
simulation playing, and it is usable by external services that never use Isaac Sim; Isaac Sim only hosts the
process. It therefore does **not** schedule anything on Isaac's loop. It runs its server on its own thread and
handles requests **one at a time** (the user's choice), because cuMotion isn't documented as
thread-safe. Each cuMotion call also takes the process-wide `CUMOTION_LOCK`, because Isaac's thread uses
cuMotion too (robot actuators in physics callbacks). Details: `Reference/robots.md`, `Specs/robot-control.md`.

**As built:** every kinematics route is a coroutine that runs the solver inline on the server's
event-loop thread (`kinematics_router(..., serial=True)`), so no thread pool is involved and at most one request
computes at a time (only network I/O, e.g. receiving an upload, overlaps). Verified by
`lib/tests/control/test_kinematics_service.py` (peak concurrency 1, every call on the server thread) and
`Scratch/harnesses/kinematics_service_probe.py` (the same with real cuMotion, 8 client threads; and no Omniverse,
USD, PhysX, or Isaac module is imported: the service ran with no stage at all). A planning call (cuMotion's
graph planner) **releases the GIL** (probe, `upper_layer_harness.py`) but holds `CUMOTION_LOCK` for its duration
(≈ 30 ms per attempt for the UR10 around a box), so an Isaac-thread cuMotion call can wait that long behind it.
The `RobotApi` serves the same kinematics routes from FastAPI's thread pool instead (a long request there doesn't
stall its streams).

## Checklist for new APIs

- Build the API on `ApiHost` (or `CoreApi` for a machine-control API): it starts the server on its own
  thread (never uvicorn on Isaac's loop) and captures Isaac's loop when `start()` is called on Isaac's thread.
- Wrap every Isaac/omni/USD/physics call in a coroutine and run it on Isaac's loop
  (`await api.run_coro_on_main_loop(coro)`; the ISE API's alias is `run_coro_on_isaac_loop`); keep those
  coroutines short.
- Do everything else (validation, computation, encoding, streaming) on the server thread.
- If the API needs per-physics-step behavior, do it in a physics callback with O(1) work and pass results
  to the server loop with `call_soon_threadsafe`, as the segment does.
- If the API never touches Isaac (like the kinematics service), say so explicitly and document its own
  concurrency policy.

## Open: a study of what can be called off Isaac's thread

Not done yet (noted by the user). A study would find the official NVIDIA statement, then probe, in
a throwaway Isaac Sim session, each kind of call from a non-main thread (USD stage reads and writes, `omni.usd`
context, timeline, physics views and tensor APIs, `omni.ui`, behavior-script APIs, cuMotion), recording which
crash, stall, corrupt state, or work. The result would replace the unsourced parts of rule 1 and tell us
which work can safely move off the loop.

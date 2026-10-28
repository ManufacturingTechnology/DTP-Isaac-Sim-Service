---
title: 'Specs'
audience: both
---

# Specs

The **leading technical design** (ADR-0019): formats, protocols, and internals that the code will follow.
Specs are written ahead of the code, with decided names as if they had landed, and corrected to what was
built when the implementation differs (unless the spec has moved further ahead).

**Split with the user docs:** the user docs describe what a user sees and does (concepts, workflow, API
usage); a spec describes how it works inside and what an implementer must honor (wire details, merge
rules, state machines, acceptance tests). The user docs can't link here (one-way rule), so each spec
names the user-docs page it backs, not the other way round.

Each spec starts with an abstract and a status line: `draft` (under discussion), `accepted` (decided,
not built), `implemented` (matches the code; `Reference/` says so), or `partially implemented`.

## Contents

| Spec | Backs (user docs) | Status |
|---|---|---|
| `segment-protocol.md`: names, acknowledgements and errors, the halt-on-failure policy, pause, step monitors, the stop policy, optimistic writes, configuration writes, stream events, sequence numbers, the retransmission buffer, the engine/API split | Asset Development › Segments and the Control APIs | implemented; units, timing modes open |
| `robot-control.md`: kinematics service, RobotAPI, batch IK, jump check, interpolation and timing, the MotionPlanner, execution semantics (blending, CIRC, jerk, tolerances, stop policy), compile-then-merge, TCPs and solvers, pose models, streams, several robots per segment | Asset Development › Manipulator Robots | implemented; servo mode planned |
| `end-effector-package.md`: package layout, XRDF fragment schema v0.1, merge rules, validation | Asset Development › Manipulator Robots (Package an End Effector) | implemented v0.1 |
| `package-validation.md`: rule catalog (relative paths, script paths, entry layer, archives), issue format, where validation runs, USD list-op and binary-layer handling | Getting Started › Environments (Rules) | partially implemented (path and USD-stem rules, CLI, enforced on upload) |

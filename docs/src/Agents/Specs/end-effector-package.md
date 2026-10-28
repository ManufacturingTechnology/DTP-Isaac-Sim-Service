---
title: 'End-effector package format'
audience: both
---

# End-effector package format

> **Abstract.** The package layout, the XRDF fragment schema (v0.1), combinations, merge rules, and the
> validation that serves as the implementation's acceptance tests. Backs the user docs page Asset Development ›
> "Manipulator Robots" (Package an End Effector). Verify against the code; may lag.

**Status:** v0.1, **implemented**; this page describes the implementation (the code wins where they differ).
Decision: ADR-0012, **accepted** (fragments merged at load time, one solver per combination, and the
implementation's choices: the `/` separator, the generated ignores, the collision-group handling, the YAML writer,
combinations selected through the RobotAPI configuration, fragments in the kinematics service). Items marked **not built** are design only. The lib's `amt.dtp.ise.lib.control.motion.endeffector`
parses fragments and combinations and merges (pure Python); the extension's `amt.dtp.ise.robot.endeffector` builds
one cuMotion solver per combination and validates it; the RobotAPI selects the active combination through its
configuration; the kinematics service accepts fragments when creating a solver. Tests:
`lib/tests/control/test_endeffector.py`; with real cuMotion and a USD stage: `Scratch/harnesses/upper_layer_harness.py`.

## Why fragments

Robots and end effectors are developed separately but combined at runtime, and a robot can carry
different combinations (a tool changer plus a gripper, a pointer, …). Each combination needs one
kinematic and collision description for the solver.

Elsewhere the common practice is to **compose descriptions before solving**: xacro and a MoveIt
configuration per cell, Drake model directives, SDFormat includes. XRDF (the format cuMotion and cuRobo
read) has no include mechanism
([XRDF spec](https://nvidia-isaac-ros.github.io/concepts/manipulation/xrdf.html)). So each end effector
ships an **XRDF fragment**, and fragments are **merged** with the robot's XRDF when the simulation
starts: the same idea as Drake's directives, at the XRDF level.

## Package layout

```text
AMT MR-A1-G1/                      # one folder per end effector (may become its own asset repository)
  AMT MR-A1-G1.usda                # geometry and physics; Sites (IsaacSiteAPI) named like the frames below
  AMT MR-A1-G1.fragment.yaml       # the XRDF fragment (this page)
  AMT MR-A1-G1.urdf                # optional: only if the end effector has moving joints that must be modeled (not built)
```

The file names are a convention; nothing in the code requires them (fragment files are passed by path or text).
Combining a fragment with its own URDF (moving joints) is **not built**: see [Limits](#limits).

## Fragment schema (v0.1)

A fragment describes the end effector **relative to its own mount frame** and never states where it is
mounted: the composition supplies that, as in Drake (`add_frame` + `add_weld`).

```yaml
format: xrdf-fragment
format_version: 0.1
name: AMT MR-A1-G1                  # unique end-effector name
mount_frame: mount                  # the fragment's root frame (its mounting face)
frames:                             # fixed frames, in order; parent is mount_frame or an earlier frame
  - frame_name: tcp
    parent_frame_name: mount
    fixed_transform:
      position: [0.0, 0.0, 0.1]     # meters
      orientation: {w: 1.0, xyz: [0.0, 0.0, 0.0]}
tool_frames: [tcp]                  # TCPs this end effector offers
collision:
  spheres:                          # keyed by this fragment's frames, as in XRDF geometry groups
    mount: [{center: [0.0, 0.0, 0.02], radius: 0.03}]
    tcp:   [{center: [0.0, 0.0, -0.02], radius: 0.015}]
  buffer_distance: {mount: 0.005}   # optional, per frame, meters
self_collision:
  ignore: {mount: [tcp]}            # pairs within the fragment
locked_joints: {}                   # moving joints represented as locked (see "Limits")
```

Field meanings follow XRDF where they share a name (`frame_name`, `parent_frame_name`, `fixed_transform`,
`tool_frames`, spheres, `buffer_distance`, `ignore`).

As implemented (`XrdfFragment`, unknown keys rejected): `name` is required; `format` defaults to `xrdf-fragment`;
`format_version` must be `0.1`; `mount_frame` defaults to `mount`; every other section is optional (empty by
default); a `fixed_transform` defaults to identity (its quaternion is normalized; zero is rejected); sphere radii must
be positive. Every frame named in `tool_frames`, `collision`, and `self_collision.ignore` must be one of the fragment's
own frames. A fragment is read from YAML or JSON text, a file, or a dict (JSON works without PyYAML; YAML needs it,
and Isaac Sim bundles it). `locked_joints` is parsed but **not used** by the merge yet (no effect).

## Combinations

A robot declares which combinations it supports, in a combinations file (or the same structure as a dict or a
`CombinationSet` in Python) that the behavior script passes to `build_combination_solvers`; the RobotAPI's
configuration selects the active one (`{"robots": {"arm": {"combination": "changer_gripper"}}}`; ADR-0012,
accepted). The file has the `robot` name (used in the merged files' names), an optional `separator` for the whole
set (default `/`), and the `combinations`. Each combination has a `stack` (each entry: `fragment` (its name),
`prefix`, `parent`, optional `offset`, identity by default), an optional `active_tcp` (a prefixed tool frame; without
it the robot's own first tool frame stays first), and optional extra `ignore` pairs. The kinematics service takes
one combination with its upload (no `robot` or `separator`: the default `/` applies).

```yaml
robot: AMT MR-A1                    # its XRDF exposes the attach frame (tool0)
separator: /                        # optional (default "/")
combinations:
  changer_gripper:
    stack:                          # mounted in order; each parent is a robot frame or the previous fragment's frame
      - {fragment: AMT MR-A1-TC1, prefix: tc, parent: tool0, offset: {position: [0, 0, 0]}}
      - {fragment: AMT MR-A1-G1,  prefix: g1, parent: tc/clamp, offset: {position: [0, 0, 0.02]}}
    active_tcp: g1/tcp
  pointer:
    stack:
      - {fragment: AMT MR-A1-P1, prefix: p1, parent: tool0}
    active_tcp: p1/tcp
```

## Merge rules

Produces one XRDF per combination:

1. **Start** from the robot's XRDF.
2. **Prefix** every fragment frame name with its stack entry's `prefix` and the separator, **`/` by default**
   (`g1/tcp`). cuMotion accepts `/`, `_`, `.`, and `:` in XRDF frame names (probe with cuMotion: each loaded, with FK and
   self-collision working), so the separator is a style choice; `/` reads like a path. Prefixes match
   `[A-Za-z][A-Za-z0-9_]{0,31}`. Name collisions after prefixing, and a prefix used twice, are errors.
3. **Mount:** add an `add_frame` modifier for the fragment's `mount_frame`, with `parent_frame_name` = the
   stack entry's `parent` and `fixed_transform` = its `offset` (identity if omitted).
4. **Frames:** append the fragment's `frames` as `add_frame` modifiers, in order (`joint_type: FIXED`;
   `joint_name` = `<prefixed frame>_joint`).
5. **Collision:** merge the fragment's spheres, keyed by prefixed frame names, into the robot's collision geometry:
   the group(s) named by `world_collision.geometry` and `self_collision.geometry`. A group that holds `spheres` gets
   them; a group that already clones that group is skipped; a group made only of `clone`s gets a new
   `<group>_end_effectors` group added to its clones; a robot without collision geometry gets a `collision_spheres`
   group used for both. (Isaac Sim's bundled UR10 XRDF has three groups, one of them a `clone` of the other two, so a robot
   XRDF can have several groups.) Merge `buffer_distance` into both sections.
6. **Self-collision:** union the `ignore` lists, and **generate cross-boundary pairs** between the fragment frames
   within one hop of the mount (the mount included) and: the parent frame; the parent's URDF link; the links rigidly
   attached to it through fixed joints; **the link across the joint that moves it** (MoveIt's Setup Assistant
   disables collisions between such adjacent links); and, when stacked on an earlier fragment, **that fragment's
   frames** (one rigid body). Extra pairs can be listed in the combination. (cuRobo's hand-written UR5e + Robotiq
   configuration shows such pairs are needed; the probe found a UR10 gripper sphere touching `wrist_2_link`, two links
   up, and a gripper touching the tool changer below it.)
7. **Tool frames:** `active_tcp` first (cuMotion uses the first entry as the end-effector frame), then the
   robot's tool frames, then the other fragments' tool frames (without duplicates).
8. **Output:**
   - written to a file, `<robot>__<combination>.xrdf` (characters outside `[A-Za-z0-9 _.-]` replaced by `_`), in a
     cache folder (`build_combination_solvers`' `cache_dir`); in the kinematics service, the solver's `xrdf` file;
   - deterministic YAML from a small emitter in the lib (no PyYAML needed to write), with a header comment listing
     the source files and their SHA-256; an unchanged file isn't rewritten;
   - meant to be usable unchanged by any XRDF consumer. Verified with cuMotion (it loads the merged files: harness);
     cuRobo through its XRDF converter and MoveIt through `isaac_ros_cumotion` are **not verified** ❓.

## Validation (acceptance tests for the implementation)

- The merged XRDF loads in cuMotion offline (no Isaac Sim stage needed). **Built** (`build_combination_solvers`;
  harness: UR10 with a tool changer and a gripper, the MR-A1 with a gripper).
- Forward kinematics of each TCP at the default pose matches the corresponding USD Site within a
  tolerance. **Built** (`validate_combination(solver, sites, q=None, position_tolerance=1 mm, angle_tolerance=0.5°)`,
  which returns the list of problems; `usd_site_poses(stage, robot_root, names, base_pose=None)` finds each frame's
  prim by its last name part under a prim named like its prefix, preferring `IsaacSiteAPI` prims, and gives its pose
  in the robot base frame; harness: a synthetic assembly). The demo end effectors' USD files have **no Sites yet**
  (an asset change for the user).
- No self-collision is reported at the default joint positions (the XRDF's `default_joint_positions`, else zeros
  clipped to the limits). **Built** (`validate_combination`, when the solver can check self-collision).
- All frame names are unique, and every `parent_frame_name` exists. **Built** (`load_fragment`, `merge_xrdf`): inside a
  fragment, parents must be the mount or an earlier frame; a stack entry's `parent` must be a frame of an earlier entry,
  or, when the robot's frames are known (`build_combination_solvers` and the kinematics service pass the URDF), one of
  the robot's links or XRDF frames. Also checked: unknown fragments, a prefix used twice, frame names that already
  exist, an `active_tcp` that isn't a tool frame of the combination, extra `ignore` pairs naming unknown frames, and (in
  the kinematics service) two fragments with the same name. Every problem is listed (`FragmentError`,
  `INVALID_FRAGMENT` in the kinematics service).

## Limits

- **Fixed frames only.** XRDF `add_frame` creates fixed joints only, so an end effector's moving joints
  (fingers) are either **locked** and represented by spheres, or need a combined URDF. Today the fingers' spheres are
  simply fixed frames' spheres in the fragment; `locked_joints` and combined URDFs are **not built**.
- **Held parts** (a workpiece in the gripper) are not fragments; the design is to attach them at runtime through an
  attach API, without changing the combination. **Not built:** no solver or planner has an attach API yet.
- **(open)** Whether cuRobo's runtime attachments could replace one-solver-per-combination for some cases.

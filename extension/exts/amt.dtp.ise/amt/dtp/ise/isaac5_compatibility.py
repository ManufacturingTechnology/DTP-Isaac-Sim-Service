"""Single-object prim adapters built on the Isaac Sim 6.0 experimental APIs.

Isaac Sim 6.0 deprecated ``isaacsim.core.{prims,api}`` in favour of
``isaacsim.core.experimental.*``. The experimental prim wrappers are *batched*
Warp-tensor views (``get_world_poses``/``set_world_poses``, regex paths) with no
single-object equivalent for the old ``SingleRigidPrim`` / ``SingleXFormPrim`` /
``SingleArticulation`` classes this codebase relies on.

These thin adapters wrap a single prim path with the batched experimental API and
re-expose the singular methods the existing call sites use, so the deprecated
extensions can be dropped without rewriting every call site. The batched<->single
conversion lives here, in one place.

NOTE: Migrated for Isaac Sim 6.0 but NOT yet validated against a running
simulation. Pose/velocity reads require the physics tensor view to be initialized
(i.e. the timeline playing), matching the behaviour of the old single-prim wrappers.
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, Union

import numpy as np

# Anything that ``np.asarray`` accepts: a numpy array, a Warp array, or a
# nested Python sequence of numbers.
ArrayLike = Union[np.ndarray, Sequence[float], Sequence[Sequence[float]]]

#import omni.timeline
import isaacsim.core.experimental.prims as _xprims

# >>> ISAAC6-DISPOSAL-FIX (revertible) — see docs/src/Agents/Scratch/performance/isaac6-performance-strategy.md §7.1.
# Added to restore SingleRigidPrim.disable_rigid_body_physics() for Workpiece.remove().
# To revert: delete this import + the _get_prim() helper + the two methods marked
# with the same ISAAC6-DISPOSAL-FIX banner below.
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema
# <<< ISAAC6-DISPOSAL-FIX
# >>> ISAAC6-STALE-VIEW-FIX (revertible) — used by _physics_sim_view_valid() below.
from isaacsim.core.simulation_manager import SimulationManager as _SimulationManager
# <<< ISAAC6-STALE-VIEW-FIX


# >>> ISAAC6-DISPOSAL-FIX (revertible)
def _get_prim(prim_path: str) -> Usd.Prim:
    """Resolve a prim path to a USD prim on the current stage."""
    stage = omni.usd.get_context().get_stage()
    return stage.GetPrimAtPath(prim_path)
# <<< ISAAC6-DISPOSAL-FIX


# class World:
#     """Minimal replacement for the deprecated ``isaacsim.core.api.World`` singleton.

#     Only the surface this codebase uses is implemented, backed by the supported
#     timeline interface and :class:`isaacsim.core.simulation_manager.SimulationManager`.
#     Constructed cheaply on each call site (the old ``World()`` was also a singleton
#     accessor), so ``World()`` keeps working unchanged.
#     """

#     @property
#     def current_time(self) -> float:
#         return omni.timeline.get_timeline_interface().get_current_time()

#     def is_stopped(self) -> bool:
#         return omni.timeline.get_timeline_interface().is_stopped()

#     def is_playing(self) -> bool:
#         return omni.timeline.get_timeline_interface().is_playing()

#     def get_physics_dt(self) -> float:
#         return _SimulationManager.get_physics_dt()


def _to_numpy(value: Optional[ArrayLike]) -> Optional[np.ndarray]:
    """Convert a Warp array (or anything array-like) to a contiguous numpy array.

    Returns ``None`` unchanged when ``value`` is ``None``.
    """
    if value is None:
        return None
    numpy_fn = getattr(value, "numpy", None)
    if callable(numpy_fn):
        value = numpy_fn()
    return np.asarray(value)


@dataclass
class ArticulationAction:
    """Drop-in replacement for ``isaacsim.core.utils.types.ArticulationAction``.

    Only the fields used by this codebase are modelled. Consumed by
    :meth:`SingleArticulation.apply_action`.
    """

    joint_positions: Optional[Sequence[float]] = None
    joint_velocities: Optional[Sequence[float]] = None
    joint_efforts: Optional[Sequence[float]] = None
    joint_indices: Optional[Sequence[int]] = None


# >>> ISAAC6-STALE-VIEW-FIX (revertible) — see docs/src/Agents/Scratch/performance/isaac6-performance-strategy.md §7.2.
class PhysicsViewUnavailable(RuntimeError):
    """A tensor op could not complete because the physics tensor view is unavailable.

    Raised by :meth:`SingleXFormPrim._view_call` when the op fails and either the global
    physics sim view is invalid (transient: a rigid body was spawned/removed this frame,
    between stop/play, or before the first physics step) or a rebind+retry still failed.
    Callers reading/writing tensor state in a per-frame loop should catch this and
    **skip that frame**; it recovers once physics re-initializes.
    """
    error_code = "PHYSICS_VIEW_UNAVAILABLE"  # the code a segment reports it with


def _physics_sim_view_valid() -> bool:
    """Whether the global physics tensor sim view is currently valid.

    Mirrors the guard in the experimental prims' ``_on_physics_ready``. When this is
    False, *constructing* a prim view won't initialize its physics tensor entity (it
    logs "Invalid physics simulation view" and leaves the entity None), so rebuilding is
    pointless and noisy — callers should skip the frame instead.
    """
    try:
        view = _SimulationManager._physics_sim_view__warp
        return view is not None and view.is_valid
    except Exception:
        return False
# <<< ISAAC6-STALE-VIEW-FIX


class SingleXFormPrim:
    """Single-prim view over :class:`isaacsim.core.experimental.prims.XformPrim`."""

    _view_cls = _xprims.XformPrim

    def __init__(self, prim_path: str, name: Optional[str] = None) -> None:
        self._prim_path = prim_path
        self._view = self._view_cls(prim_path)

    @property
    def prim_path(self) -> str:
        return self._prim_path

    # >>> ISAAC6-STALE-VIEW-FIX (revertible) — see docs/src/Agents/Scratch/performance/isaac6-performance-strategy.md §7.2.
    # The experimental views bind their physics tensor view at construction and go STALE
    # when rigid bodies are spawned/removed mid-simulation (which rebuilds the
    # SimulationManager sim view); subsequent tensor reads/writes then fail at the backend
    # ("Failed to get rigid body transforms from backend", "Failed to get DOF actuation
    # forces from backend", ...). A freshly-constructed view rebinds to the current sim
    # view, so one rebuild+retry recovers. If the retry also fails, the original error
    # propagates. All tensor ops below route through this.
    # To revert: delete this method and change each ``self._view_call("foo", ...)`` call
    # back to ``self._view.foo(...)``.
    def _view_call(self, _method: str, *args, **kwargs):
        # Pre-check: if the global sim view is invalid (e.g. a prim was just deleted,
        # which invalidates it), don't even attempt the op — calling it makes the C++
        # tensor backend log "Simulation view object is invalidated and cannot be used".
        # Skip cleanly; the view re-initializes once physics is ready again.
        if not _physics_sim_view_valid():
            raise PhysicsViewUnavailable(
                f"{type(self).__name__}({self._prim_path}).{_method}(): physics sim view invalid."
            )
        try:
            return getattr(self._view, _method)(*args, **kwargs)
        except Exception as err:
            # Sim view is valid but this view may be stale -> rebuild + retry once.
            try:
                self._view = self._view_cls(self._prim_path)
                return getattr(self._view, _method)(*args, **kwargs)
            except Exception as err2:
                raise PhysicsViewUnavailable(
                    f"{type(self).__name__}({self._prim_path}).{_method}() failed after rebuild."
                ) from err2
    # <<< ISAAC6-STALE-VIEW-FIX

    def get_world_pose(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return ``(position (3,), orientation quaternion (4,))`` for this prim."""
        positions, orientations = self._view_call("get_world_poses")
        return _to_numpy(positions)[0], _to_numpy(orientations)[0]

    def set_world_pose(
        self,
        position: Optional[ArrayLike] = None,
        orientation: Optional[ArrayLike] = None,
    ) -> None:
        """Set this prim's world pose. ``position`` is ``(3,)``, ``orientation`` a ``(4,)`` quaternion."""
        positions = None if position is None else np.asarray(position, dtype=np.float32).reshape(1, 3)
        orientations = None if orientation is None else np.asarray(orientation, dtype=np.float32).reshape(1, 4)
        self._view_call("set_world_poses", positions=positions, orientations=orientations)

    def apply_visual_material(self, visual_material) -> None:
        # Experimental API takes a single material or a list; broadcast over the 1 prim.
        self._view.apply_visual_materials(visual_material)


class SingleRigidPrim(SingleXFormPrim):
    """Single-prim view over :class:`isaacsim.core.experimental.prims.RigidPrim`."""

    _view_cls = _xprims.RigidPrim

    def get_linear_velocity(self) -> np.ndarray:
        """Return the linear velocity ``(3,)`` of this prim."""
        linear, _ = self._view_call("get_velocities")
        return _to_numpy(linear)[0]

    def get_angular_velocity(self) -> np.ndarray:
        """Return the angular velocity ``(3,)`` of this prim."""
        _, angular = self._view_call("get_velocities")
        return _to_numpy(angular)[0]

    def set_linear_velocity(self, velocity: ArrayLike) -> None:
        """Set the linear velocity from a ``(3,)`` array-like."""
        self._view_call(
            "set_velocities",
            linear_velocities=np.asarray(velocity, dtype=np.float32).reshape(1, 3),
        )

    def set_angular_velocity(self, velocity: ArrayLike) -> None:
        """Set the angular velocity from a ``(3,)`` array-like."""
        self._view_call(
            "set_velocities",
            angular_velocities=np.asarray(velocity, dtype=np.float32).reshape(1, 3),
        )

    # >>> ISAAC6-DISPOSAL-FIX (revertible) — see docs/src/Agents/Scratch/performance/isaac6-performance-strategy.md §7.1.
    # To revert: delete these two methods (and the import/helper banner near the top).
    def disable_rigid_body_physics(self) -> None:
        """Park a disposed body in place WITHOUT removing it from the solver.

        The Isaac-5 method set ``physics:rigidBodyEnabled = False`` (eDISABLE_SIMULATION).
        On Isaac 6 that toggle *restructures the physics scene and invalidates the global
        physics tensor sim view*, breaking every tensor op for a frame (see
        docs/src/Agents/Scratch/performance/isaac6-performance-strategy.md §7.1/§8). Instead we use a *soft* per-body property that
        doesn't restructure: disable gravity, so the (collision-already-disabled) body
        stops falling and sleeps in place. Velocity is zeroed best-effort so it doesn't
        coast (gravity off != momentum gone).

        NOTE: this keeps the body in the solver (cheap once asleep) rather than removing
        it like the old eDISABLE_SIMULATION; pooling/reuse is the real perf fix (§3.4/§7.1).
        """
        prim = _get_prim(self._prim_path)
        PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr().Set(True)
        try:
            self.set_linear_velocity(np.zeros(3, dtype=np.float32))
            self.set_angular_velocity(np.zeros(3, dtype=np.float32))
        except PhysicsViewUnavailable:
            pass

    def enable_rigid_body_physics(self) -> None:
        """Undo :meth:`disable_rigid_body_physics` — re-enable gravity (for a reused body)."""
        prim = _get_prim(self._prim_path)
        PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr().Set(False)
    # <<< ISAAC6-DISPOSAL-FIX


class SingleArticulation(SingleXFormPrim):
    """Single-robot view over :class:`isaacsim.core.experimental.prims.Articulation`."""

    _view_cls = _xprims.Articulation

    def initialize(self, physics_sim_view=None) -> None:
        # No-op: experimental prim views initialize their physics tensor view lazily
        # once the timeline is playing. Retained for call-site compatibility with the
        # old SingleArticulation.initialize().
        return None

    @property
    def dof_names(self) -> Sequence[str]:
        return self._view.dof_names

    @property
    def dof_types(self) -> Sequence[str]:
        """Per-DOF motion type in DOF order: ``"angular"`` (rotation) or ``"linear"``
        (translation).

        Derived from the experimental view's ``dof_types`` (``omni.physics.tensors.DofType``),
        which expands multi-DOF joints (e.g. a spherical joint -> 3 angular DOFs). Resolved
        via the enum's name to avoid a hard dependency on its integer values.
        """
        result: list[str] = []
        for dof_type in self._view.dof_types:
            name = getattr(dof_type, "name", str(dof_type)).lower()
            if "rot" in name:
                result.append("angular")
            elif "trans" in name:
                result.append("linear")
            else:
                raise ValueError(f"Unsupported DOF type: {dof_type!r}")
        return result

    @property
    def dof_paths(self) -> Sequence[str]:
        """Joint prim path for each DOF, in DOF order.

        A multi-DOF joint (e.g. spherical) repeats its joint path once per DOF. Used to map
        a DOF back to its joint (and hence its child link's force/torque row).
        """
        paths = self._view.dof_paths
        # Experimental views return a per-articulation nested list (N, D); unwrap the
        # single wrapped robot if so.
        if len(paths) > 0 and isinstance(paths[0], (list, tuple)):
            return [str(p) for p in paths[0]]
        return [str(p) for p in paths]

    @property
    def link_paths(self) -> Sequence[str]:
        """Link prim path for each row of :meth:`get_measured_joint_forces_and_torques`,
        in link order."""
        paths = self._view.link_paths
        if len(paths) > 0 and isinstance(paths[0], (list, tuple)):
            return [str(p) for p in paths[0]]
        return [str(p) for p in paths]

    def get_joint_positions(self) -> np.ndarray:
        """Return joint positions ``(num_dofs,)``."""
        return _to_numpy(self._view_call("get_dof_positions"))[0]

    def get_joint_velocities(self) -> np.ndarray:
        """Return joint velocities ``(num_dofs,)``."""
        return _to_numpy(self._view_call("get_dof_velocities"))[0]

    def get_measured_joint_efforts(self) -> np.ndarray:
        # old: SingleArticulation.get_measured_joint_efforts() -> (num_dofs,)
        return _to_numpy(self._view_call("get_dof_efforts"))[0]

    def get_measured_joint_forces_and_torques(self) -> Tuple[np.ndarray, np.ndarray]:
        # old: SingleArticulation.get_measured_joint_forces() -> (num_links + 1, 6),
        # where columns [0:3] are forces and [3:6] are torques, one row per link
        # (row 0 = base link's incoming joint). The experimental API splits this into
        # get_link_incoming_joint_force() -> (forces (N, L, 3), torques (N, L, 3));
        forces, torques = self._view_call("get_link_incoming_joint_force")
        forces2 = _to_numpy(forces)[0]    # (L, 3)
        torques2 = _to_numpy(torques)[0]  # (L, 3)
        return forces2, torques2

    # def get_measured_joint_forces(self) -> np.ndarray:
    #     # old: SingleArticulation.get_measured_joint_forces() -> (num_links + 1, 6),
    #     # where columns [0:3] are forces and [3:6] are torques, one row per link
    #     # (row 0 = base link's incoming joint). The experimental API splits this into
    #     # get_link_incoming_joint_force() -> (forces (N, L, 3), torques (N, L, 3));
    #     # recombine into (L, 6) for the single wrapped prim to preserve call-site indexing.
    #     forces, torques = self._view.get_link_incoming_joint_force()
    #     forces2 = _to_numpy(forces)[0]    # (L, 3)
    #     torques2 = _to_numpy(torques)[0]  # (L, 3)
    #     return np.concatenate([forces2, torques2], axis=1)  # (L, 6)

    def set_joint_positions(
        self, positions: ArrayLike, dof_indices: Optional[Sequence[int]] = None
    ) -> None:
        """Teleport joints to ``positions`` ``(num_dofs,)`` (or the subset given by ``dof_indices``)."""
        positions = np.asarray(positions, dtype=np.float32).reshape(1, -1)
        self._view_call("set_dof_positions", positions, dof_indices=dof_indices)

    def get_dof_positions(self) -> np.ndarray:
        """Return joint positions ``(num_dofs,)``."""
        return _to_numpy(self._view_call("get_dof_positions"))[0]

    def set_dof_position_targets(
        self, positions: ArrayLike, dof_indices: Optional[Sequence[int]] = None
    ) -> None:
        """Set position-drive targets ``(num_dofs,)`` (or the subset given by ``dof_indices``)."""
        positions2 = np.asarray(positions, dtype=np.float32).reshape(1, -1)
        self._view_call("set_dof_position_targets", positions2, dof_indices=dof_indices)

    def set_dof_velocity_targets(
        self, velocities: ArrayLike, dof_indices: Optional[Sequence[int]] = None
    ) -> None:
        """Set velocity-drive targets ``(num_dofs,)`` (or the subset given by ``dof_indices``)."""
        velocities2 = np.asarray(velocities, dtype=np.float32).reshape(1, -1)
        self._view_call("set_dof_velocity_targets", velocities2, dof_indices=dof_indices)

    def get_dof_position_targets(self) -> np.ndarray:
        """Return the commanded position-drive targets (setpoints) ``(num_dofs,)``.

        These are the last-applied drive *targets*, not the measured joint positions
        (see :meth:`get_joint_positions` for the sensed values).
        """
        return _to_numpy(self._view_call("get_dof_position_targets"))[0]

    def get_dof_velocity_targets(self) -> np.ndarray:
        """Return the commanded velocity-drive targets (setpoints) ``(num_dofs,)``.

        These are the last-applied drive *targets*, not the measured joint velocities
        (see :meth:`get_joint_velocities` for the sensed values).
        """
        return _to_numpy(self._view_call("get_dof_velocity_targets"))[0]

    def get_dof_gains(self) -> Tuple[np.ndarray, np.ndarray]:
        """Return the drive gains ``(stiffnesses (num_dofs,), dampings (num_dofs,))``.

        Used to determine each DOF's drive mode: a position drive has stiffness > 0,
        a pure velocity drive has stiffness == 0 and damping > 0.
        """
        stiffnesses, dampings = self._view_call("get_dof_gains")
        return _to_numpy(stiffnesses)[0], _to_numpy(dampings)[0]

    def apply_action(self, control_actions: ArticulationAction) -> None:
        """Map a (single-robot) ArticulationAction onto the batched DOF target setters."""
        if control_actions.joint_positions is not None:
            self.set_dof_position_targets(
                control_actions.joint_positions, dof_indices=control_actions.joint_indices
            )
        if control_actions.joint_velocities is not None:
            self.set_dof_velocity_targets(
                control_actions.joint_velocities, dof_indices=control_actions.joint_indices
            )

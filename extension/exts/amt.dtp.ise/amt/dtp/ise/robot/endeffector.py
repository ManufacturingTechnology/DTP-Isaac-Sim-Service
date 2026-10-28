"""End-effector combinations with cuMotion (ADR-0012): merge a robot's XRDF with end-effector XRDF fragments into one
XRDF per combination (the lib's :mod:`~..lib.control.motion.endeffector`), write each to a cache folder, build one
:class:`~.kinematics.CuMotionKinematicsSolver` per combination **ahead of time**, and validate them (the spec's
acceptance checks): the merged XRDF loads in cuMotion; each TCP's forward kinematics matches its USD Site (where Sites
are given); no self-collision at the default joint positions.

A behavior script passes the solvers to the RobotAPI (``RobotBinding(solver, combinations=solvers)``) and switches the
active one through the robot configuration (``{"combination": "gripper"}``). Design:
``docs/src/Agents/Specs/end-effector-package.md``.
"""
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from ..lib.control.motion.endeffector import (
    CombinationSet,
    FragmentError,
    XrdfFragment,
    default_joint_positions,
    load_combinations,
    load_fragment,
    load_yaml,
    merge_xrdf,
    write_merged_xrdf,
)
from ..lib.control.transducer.models.pose import Pose, Quaternion, Vector3
from .kinematics import CuMotionKinematicsSolver

PathLike = Union[str, Path]


def _urdf_links(urdf_text: str) -> List[str]:
    from xml.etree import ElementTree  # noqa: PLC0415
    return [link.get("name") for link in ElementTree.fromstring(urdf_text).iter("link")]


def build_combination_solvers(
    urdf_path: PathLike,
    xrdf_path: PathLike,
    combinations: Union[PathLike, Mapping[str, Any], CombinationSet],
    fragments: Union[Sequence[PathLike], Mapping[str, Union[PathLike, XrdfFragment]]],
    cache_dir: PathLike,
    include_bare: Optional[str] = "none",
) -> Tuple[Dict[str, CuMotionKinematicsSolver], Dict[str, Path]]:
    """One solver per combination, built now: ``({combination: solver}, {combination: merged XRDF path})``.

    :param combinations: A combinations file (YAML/JSON), a dict, or a :class:`CombinationSet`.
    :param fragments: Fragment files (or ``{name: file or XrdfFragment}``).
    :param cache_dir: Where the merged XRDFs are written (``<robot>__<combination>.xrdf``).
    :param include_bare: Also add the robot without end effectors under this name (None: don't).
    Raises :class:`~..lib.control.motion.endeffector.FragmentError` (a bad fragment or combination) or the backend's
    error (a merged XRDF cuMotion can't load)."""
    urdf_path, xrdf_path = Path(urdf_path), Path(xrdf_path)
    xrdf_text = xrdf_path.read_text(encoding="utf-8")
    robot_xrdf = load_yaml(xrdf_text)
    combo_set = combinations if isinstance(combinations, CombinationSet) else load_combinations(combinations)
    loaded: Dict[str, XrdfFragment] = {}
    sources: Dict[str, Tuple[str, str]] = {}
    items = fragments.items() if isinstance(fragments, Mapping) else [(None, f) for f in fragments]
    for key, item in items:
        if isinstance(item, XrdfFragment):
            fragment, text, name = item, item.model_dump_json(), f"{item.name} (in memory)"
        else:
            path = Path(item)
            text = path.read_text(encoding="utf-8")
            fragment, name = load_fragment(load_yaml(text)), path.name
        loaded[key or fragment.name] = fragment
        sources[key or fragment.name] = (name, text)
    links = _urdf_links(urdf_path.read_text(encoding="utf-8"))
    solvers: Dict[str, CuMotionKinematicsSolver] = {}
    paths: Dict[str, Path] = {}
    if include_bare:
        solvers[include_bare] = CuMotionKinematicsSolver(str(urdf_path), str(xrdf_path))
        paths[include_bare] = xrdf_path
    for name, combination in combo_set.combinations.items():
        merged = merge_xrdf(robot_xrdf, loaded, combination, separator=combo_set.separator, robot_frames=links,
                            urdf_text=urdf_path.read_text(encoding="utf-8"))
        used = [sources[e.fragment] for e in combination.stack]
        path = write_merged_xrdf(cache_dir, combo_set.robot, name, merged,
                                 sources=[(xrdf_path.name, xrdf_text)] + used)
        solvers[name] = CuMotionKinematicsSolver(str(urdf_path), str(path))
        paths[name] = path
    return solvers, paths


def validate_combination(solver: CuMotionKinematicsSolver, sites: Optional[Mapping[str, Pose]] = None,
                         q: Optional[Sequence[float]] = None, position_tolerance: float = 1e-3,
                         angle_tolerance: float = math.radians(0.5)) -> List[str]:
    """The spec's acceptance checks for a built combination (the merged XRDF already loaded): each frame in ``sites``
    (its USD Site's pose in the robot base frame at ``q``) matches FK within the tolerances, and there is no
    self-collision at ``q`` (default: the XRDF's ``default_joint_positions``, else zeros clipped to the limits).
    Returns the problems (empty: valid)."""
    problems: List[str] = []
    if q is None:
        q = default_joint_positions(solver.xrdf, solver.joint_names)
    if q is None:
        q = [min(max(0.0, j.lower), j.upper) for j in solver.joints]
    q = np.asarray(q, dtype=np.float64)
    for frame, expected in (sites or {}).items():
        if frame not in solver.frame_names:
            problems.append(f"Site '{frame}' has no frame in the merged XRDF (frames: {solver.frame_names})")
            continue
        p, o = solver.fk(q, frame)
        position_error = float(np.linalg.norm(p[0] - expected.position.to_numpy()))
        angle = Quaternion.from_wxyz(o[0]).angle_to(expected.orientation)
        if position_error > position_tolerance or angle > angle_tolerance:
            problems.append(f"frame '{frame}': FK is {position_error * 1000:.2f} mm and {math.degrees(angle):.2f} deg from its "
                            f"USD Site (tolerance {position_tolerance * 1000:g} mm, {math.degrees(angle_tolerance):g} deg)")
    if "self_collision" in solver.capabilities:
        pairs = solver.self_collision(q)[0]
        if pairs:
            problems.append(f"self-collision at the default pose: {pairs} (add ignore pairs or move spheres)")
    return problems


def usd_site_poses(stage: Any, robot_root: str, names: Sequence[str], base_pose: Optional[Pose] = None,
                   separator: str = "/") -> Dict[str, Pose]:
    """The poses (in the robot base frame, meters) of the USD prims that mark frames: for each frame name (e.g.
    ``g1/tcp``), the prim named like its last part (``tcp``) under the prim named like its prefix (``g1``, or anywhere
    under ``robot_root`` for an unprefixed name), preferring prims with ``IsaacSiteAPI``. ``base_pose``: the robot base
    in the world (default: ``robot_root``'s world pose)."""
    from pxr import Usd, UsdGeom  # noqa: PLC0415
    meters = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    root = stage.GetPrimAtPath(robot_root)

    def world_pose(prim: Any) -> Pose:
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=np.float64)
        rotation = m[:3, :3].T
        rotation = rotation / np.linalg.norm(rotation, axis=0)
        return Pose(position=Vector3.from_numpy(m[3, :3] * meters), orientation=Quaternion.from_matrix(rotation))

    base = base_pose if base_pose is not None else world_pose(root)
    inverse = base.inverse()
    prims = list(Usd.PrimRange(root))
    result: Dict[str, Pose] = {}
    for name in names:
        prefix, _, leaf = name.rpartition(separator)
        candidates = [p for p in prims if p.GetName() == leaf and (
            not prefix or any(a.GetName() == prefix for a in _ancestors(p, root)))]
        candidates.sort(key=lambda p: "IsaacSiteAPI" not in [str(s) for s in p.GetAppliedSchemas()])
        if candidates:
            result[name] = inverse.compose(world_pose(candidates[0]))
    return result


def _ancestors(prim: Any, stop: Any) -> List[Any]:
    result = []
    parent = prim.GetParent()
    while parent and parent.IsValid() and parent != stop:
        result.append(parent)
        parent = parent.GetParent()
    return result


__all__ = ["build_combination_solvers", "validate_combination", "usd_site_poses", "FragmentError"]

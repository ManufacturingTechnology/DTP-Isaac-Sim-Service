"""End-effector packages: XRDF fragments (format v0.1), combinations, and the merge into one XRDF per
combination (ADR-0012; design and rules: ``docs/src/Agents/Specs/end-effector-package.md``).

- :class:`XrdfFragment`: an end effector described **relative to its own mount frame** (frames, tool frames,
  collision spheres, self-collision ignores); it never says where it is mounted.
- :class:`Combination`: a stack of fragments, each mounted on a robot frame or a frame of the fragment before
  (with an offset), plus the active TCP; :class:`CombinationSet` holds a robot's combinations.
- :func:`merge_xrdf`: robot XRDF + combination → merged XRDF (a dict): prefixed frames as ``add_frame``
  modifiers, the mount pose from the composition, spheres merged into the collision geometry, the ignore lists
  unioned with generated cross-boundary pairs, the active TCP first in ``tool_frames``.
- :func:`render_xrdf` writes it as deterministic YAML with a header comment listing the sources (SHA-256), and
  :func:`write_merged_xrdf` caches it as ``<robot>__<combination>.xrdf``.

Pure Python: dicts in, dicts out. Reading YAML text (:func:`load_yaml`) needs PyYAML, which Isaac Sim bundles and the
library doesn't declare (JSON text works without it); writing uses the small emitter here.
"""
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


FORMAT = "xrdf-fragment"
FORMAT_VERSION = "0.1"
DEFAULT_SEPARATOR = "/"
_PREFIX_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


class FragmentError(ValueError):
    """A fragment, combination, or merge is invalid (``INVALID_FRAGMENT``); ``problems`` lists every issue."""
    error_code = "INVALID_FRAGMENT"

    def __init__(self, problems: Union[str, Sequence[str]]):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


# ---- models ----------------------------------------------------------------------------------------------

class XrdfOrientation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    w: float = 1.0
    xyz: Tuple[float, float, float] = (0.0, 0.0, 0.0)


class FixedTransform(BaseModel):
    """XRDF's ``fixed_transform``: position (m) and orientation ``{w, xyz}``."""
    model_config = ConfigDict(extra="forbid")
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    orientation: XrdfOrientation = Field(default_factory=XrdfOrientation)

    @model_validator(mode="after")
    def _normalized(self) -> "FixedTransform":
        norm = math.sqrt(self.orientation.w ** 2 + sum(v * v for v in self.orientation.xyz))
        if not norm > 1e-9 or not all(math.isfinite(v) for v in (*self.position, norm)):
            raise ValueError("the orientation must be a non-zero quaternion and the values finite")
        self.orientation = XrdfOrientation(w=self.orientation.w / norm, xyz=tuple(v / norm for v in self.orientation.xyz))
        return self

    def to_xrdf(self) -> Dict[str, Any]:
        return {"position": [float(v) for v in self.position],
                "orientation": {"w": float(self.orientation.w), "xyz": [float(v) for v in self.orientation.xyz]}}


class FragmentFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_name: str
    parent_frame_name: str
    fixed_transform: FixedTransform = Field(default_factory=FixedTransform)


class Sphere(BaseModel):
    model_config = ConfigDict(extra="forbid")
    center: Tuple[float, float, float]
    radius: float = Field(gt=0)


class FragmentCollision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spheres: Dict[str, List[Sphere]] = Field(default_factory=dict)
    buffer_distance: Dict[str, float] = Field(default_factory=dict)


class FragmentSelfCollision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ignore: Dict[str, List[str]] = Field(default_factory=dict)


class XrdfFragment(BaseModel):
    """An end effector's XRDF fragment (format v0.1), relative to its ``mount_frame``."""
    model_config = ConfigDict(extra="forbid")
    format: Literal["xrdf-fragment"] = FORMAT
    format_version: str = FORMAT_VERSION
    name: str = Field(min_length=1)
    mount_frame: str = Field("mount", min_length=1)
    frames: List[FragmentFrame] = Field(default_factory=list)
    tool_frames: List[str] = Field(default_factory=list)
    collision: FragmentCollision = Field(default_factory=FragmentCollision)
    self_collision: FragmentSelfCollision = Field(default_factory=FragmentSelfCollision)
    locked_joints: Dict[str, float] = Field(default_factory=dict)

    @field_validator("format_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> str:
        value = str(value)
        if value != FORMAT_VERSION:
            raise ValueError(f"format_version {value} is not supported (supported: {FORMAT_VERSION})")
        return value

    @property
    def frame_names(self) -> List[str]:
        return [self.mount_frame] + [f.frame_name for f in self.frames]

    def problems(self) -> List[str]:
        """Rule violations inside the fragment (unique names, known parents, known frames)."""
        problems: List[str] = []
        known = [self.mount_frame]
        for frame in self.frames:
            if frame.frame_name in known:
                problems.append(f"fragment '{self.name}': frame '{frame.frame_name}' is defined twice")
            if frame.parent_frame_name not in known:
                problems.append(f"fragment '{self.name}': the parent '{frame.parent_frame_name}' of '{frame.frame_name}' "
                                f"is not the mount frame or an earlier frame")
            known.append(frame.frame_name)
        names = set(known)
        for label, used in (("tool_frames", self.tool_frames), ("collision.spheres", list(self.collision.spheres)),
                            ("collision.buffer_distance", list(self.collision.buffer_distance)),
                            ("self_collision.ignore", list(self.self_collision.ignore)
                             + [f for v in self.self_collision.ignore.values() for f in v])):
            for name in used:
                if name not in names:
                    problems.append(f"fragment '{self.name}': {label} names '{name}', which is not one of its frames {known}")
        return problems


class StackEntry(BaseModel):
    """One end effector in a combination: which fragment, its name prefix, the frame it mounts on (a robot frame or
    an earlier entry's prefixed frame), and the mount offset (from the composition, as Drake's ``add_frame``)."""
    model_config = ConfigDict(extra="forbid")
    fragment: str
    prefix: str
    parent: str
    offset: FixedTransform = Field(default_factory=FixedTransform)

    @field_validator("prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        if not _PREFIX_PATTERN.match(value):
            raise ValueError(f"prefix {value!r} must match {_PREFIX_PATTERN.pattern}")
        return value


class Combination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stack: List[StackEntry] = Field(default_factory=list)
    active_tcp: Optional[str] = Field(None, description="A prefixed tool frame (default: the robot's first tool frame).")
    ignore: Dict[str, List[str]] = Field(default_factory=dict, description="Extra self-collision ignore pairs.")


class CombinationSet(BaseModel):
    """A robot's end-effector combinations (e.g. a ``combinations.yaml`` beside the robot)."""
    model_config = ConfigDict(extra="forbid")
    robot: str
    separator: str = DEFAULT_SEPARATOR
    combinations: Dict[str, Combination]


# ---- reading ------------------------------------------------------------------------------------------------

def load_yaml(text: str) -> Any:
    """Parse YAML (or JSON) text. JSON works without PyYAML; YAML needs it (Isaac Sim bundles it)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml  # noqa: PLC0415 (optional: Isaac Sim bundles PyYAML; the library doesn't declare it)
    except ImportError:
        raise FragmentError("this text is not JSON, and reading YAML needs the PyYAML package") from None
    return yaml.safe_load(text)


def _validated(model: type, data: Any, what: str) -> Any:
    if isinstance(data, model):
        return data
    try:
        return model.model_validate(data)
    except ValidationError as ex:
        raise FragmentError([f"{what}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in ex.errors()]) from None


def load_fragment(source: Union[str, Path, Mapping[str, Any], XrdfFragment]) -> XrdfFragment:
    """A fragment from a model, a dict, a file path, or YAML/JSON text; checked (:class:`FragmentError`)."""
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and source.strip().endswith(
            (".yaml", ".yml", ".json")) and Path(source).is_file()):
        source = Path(source).read_text(encoding="utf-8")
    data = load_yaml(source) if isinstance(source, str) else source
    fragment = _validated(XrdfFragment, data, "fragment")
    problems = fragment.problems()
    if problems:
        raise FragmentError(problems)
    return fragment


def load_combinations(source: Union[str, Path, Mapping[str, Any]]) -> CombinationSet:
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).is_file()):
        source = Path(source).read_text(encoding="utf-8")
    data = load_yaml(source) if isinstance(source, str) else source
    return _validated(CombinationSet, data, "combinations")


# ---- merging ---------------------------------------------------------------------------------------------------

def _added_frames(xrdf: Mapping[str, Any]) -> Dict[str, str]:
    """``{frame: parent}`` of the XRDF's ``add_frame`` modifiers."""
    result = {}
    for modifier in xrdf.get("modifiers") or []:
        add = (modifier or {}).get("add_frame")
        if add:
            result[str(add.get("frame_name"))] = str(add.get("parent_frame_name"))
    return result


def _link_of(frame: str, parents: Mapping[str, str]) -> str:
    """The URDF link a frame is fixed to (following ``add_frame`` parents)."""
    seen = set()
    while frame in parents and frame not in seen:
        seen.add(frame)
        frame = parents[frame]
    return frame


def _group_spheres(geometry: Dict[str, Any], group: str, seen: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """The ``spheres`` mapping a geometry group holds, or None (e.g. a group that only clones others)."""
    entry = geometry.get(group)
    if not isinstance(entry, dict):
        return None
    return entry.get("spheres") if isinstance(entry.get("spheres"), dict) else None


def _clones(geometry: Mapping[str, Any], group: str, target: str, seen: Optional[set] = None) -> bool:
    """Whether ``group`` is ``target`` or (transitively) clones it."""
    seen = seen or set()
    if group == target:
        return True
    if group in seen:
        return False
    seen.add(group)
    entry = geometry.get(group) or {}
    return any(_clones(geometry, g, target, seen) for g in (entry.get("clone") or []))


def urdf_link_parents(urdf_text: str) -> Dict[str, Tuple[str, str]]:
    """``{child link: (parent link, joint type)}`` of a URDF."""
    from xml.etree import ElementTree  # noqa: PLC0415
    result = {}
    for joint in ElementTree.fromstring(urdf_text).iter("joint"):
        parent, child = joint.find("parent"), joint.find("child")
        if parent is not None and child is not None:
            result[child.get("link")] = (parent.get("link"), joint.get("type", "fixed"))
    return result


def _adjacent_links(link: str, links: Mapping[str, Tuple[str, str]]) -> List[str]:
    """The rigid body ``link`` belongs to (up through fixed joints to the first link moved by a joint) and the link
    across that joint: the links an end effector fixed to ``link`` touches by construction (MoveIt's Setup Assistant
    disables collisions between such adjacent links)."""
    result, seen = [link], set()
    while link in links and link not in seen:
        seen.add(link)
        parent, kind = links[link]
        result.append(parent)
        if kind != "fixed":
            break
        link = parent
    return result


def merge_xrdf(robot_xrdf: Mapping[str, Any], fragments: Mapping[str, XrdfFragment], combination: Combination,
               separator: str = DEFAULT_SEPARATOR, robot_frames: Optional[Iterable[str]] = None,
               urdf_text: Optional[str] = None) -> Dict[str, Any]:
    """The XRDF of one combination (rules 1–7 of the spec): a new dict; ``robot_xrdf`` is not changed.
    ``robot_frames`` (the robot's URDF links and XRDF frames, if known) lets the first mount parent be checked; with
    the ``urdf_text``, the robot's links are known and the generated ignores also cover the links adjacent to the
    mount's link (through fixed joints, and across the joint that moves it). Raises :class:`FragmentError` listing
    every problem."""
    import copy  # noqa: PLC0415
    xrdf: Dict[str, Any] = copy.deepcopy(dict(robot_xrdf))
    problems: List[str] = []
    parents = _added_frames(xrdf)
    links = urdf_link_parents(urdf_text) if urdf_text else {}
    if links:
        robot_frames = set(robot_frames or []) | set(links) | {p for p, _ in links.values()}
    known = set(robot_frames or []) | set(parents) | set(parents.values())
    modifiers = list(xrdf.get("modifiers") or [])
    spheres: Dict[str, List[Dict[str, Any]]] = {}
    buffers: Dict[str, float] = {}
    ignore: Dict[str, List[str]] = {}
    tool_frames_extra: List[str] = []
    prefixes = set()
    body_of: Dict[str, List[str]] = {}  # a fragment frame -> every frame of its fragment (one rigid body)

    def add_ignore(a: str, b: str) -> None:
        if a != b and b not in ignore.setdefault(a, []):
            ignore[a].append(b)

    for position, entry in enumerate(combination.stack):
        fragment = fragments.get(entry.fragment)
        if fragment is None:
            problems.append(f"stack entry {position}: unknown fragment '{entry.fragment}' (fragments: {sorted(fragments)})")
            continue
        if entry.prefix in prefixes:
            problems.append(f"stack entry {position}: prefix '{entry.prefix}' is used twice")
        prefixes.add(entry.prefix)
        problems += fragment.problems()

        def name(frame: str) -> str:
            return f"{entry.prefix}{separator}{frame}"
        if entry.parent not in known and (robot_frames is not None or separator in entry.parent):
            problems.append(f"stack entry {position} ('{entry.fragment}'): parent '{entry.parent}' is not a robot frame "
                                f"or a frame of an earlier entry")
        mount = name(fragment.mount_frame)
        new = [mount] + [name(f.frame_name) for f in fragment.frames]
        clashes = sorted(set(new) & known)
        if clashes:
            problems.append(f"stack entry {position} ('{entry.fragment}'): frame name(s) {clashes} already exist")
        # rule 3: the mount, at the composition's offset
        modifiers.append({"add_frame": {"frame_name": mount, "parent_frame_name": entry.parent,
                                        "joint_name": f"{mount}_joint", "joint_type": "FIXED",
                                        "fixed_transform": entry.offset.to_xrdf()}})
        parents[mount] = entry.parent
        # rule 4: the fragment's frames
        for frame in fragment.frames:
            modifiers.append({"add_frame": {"frame_name": name(frame.frame_name),
                                            "parent_frame_name": name(frame.parent_frame_name),
                                            "joint_name": f"{name(frame.frame_name)}_joint", "joint_type": "FIXED",
                                            "fixed_transform": frame.fixed_transform.to_xrdf()}})
            parents[name(frame.frame_name)] = name(frame.parent_frame_name)
        known |= set(new)
        for frame in new:
            body_of[frame] = new
        # rule 5: spheres and buffer distances, by prefixed frame
        for frame, items in fragment.collision.spheres.items():
            spheres.setdefault(name(frame), []).extend(
                {"center": [float(v) for v in s.center], "radius": float(s.radius)} for s in items)
        for frame, value in fragment.collision.buffer_distance.items():
            buffers[name(frame)] = float(value)
        # rule 6: the fragment's own ignores, and the generated cross-boundary pairs
        for frame, others in fragment.self_collision.ignore.items():
            for other in others:
                add_ignore(name(frame), name(other))
        one_hop = [mount] + [name(f.frame_name) for f in fragment.frames if f.parent_frame_name == fragment.mount_frame]
        add_ignore(mount, entry.parent)
        parent_link = _link_of(entry.parent, parents)
        neighbours = _adjacent_links(parent_link, links) if links else [parent_link]
        for frame in one_hop:
            for link in neighbours:
                add_ignore(link, frame)
            if entry.parent != parent_link:
                add_ignore(entry.parent, frame)
            for other in body_of.get(entry.parent, []):  # stacked on an earlier fragment: its whole body
                if other not in new:
                    add_ignore(other, frame)
        tool_frames_extra += [name(t) for t in fragment.tool_frames]
    for frame, others in combination.ignore.items():
        for other in others:
            if frame not in known or other not in known:
                problems.append(f"combination ignore: '{frame}' or '{other}' is not a frame")
            add_ignore(frame, other)

    # rule 7: tool frames: the active TCP first, then the robot's, then the other fragments' tool frames
    robot_tools = [str(t) for t in (xrdf.get("tool_frames") or [])]
    active = combination.active_tcp
    if active is not None and active not in tool_frames_extra + robot_tools:
        problems.append(f"active_tcp '{active}' is not a tool frame of the combination ({tool_frames_extra + robot_tools})")
    ordered: List[str] = []
    for frame in ([active] if active else []) + robot_tools + tool_frames_extra:
        if frame and frame not in ordered:
            ordered.append(frame)
    if problems:
        raise FragmentError(problems)

    xrdf["modifiers"] = modifiers
    xrdf["tool_frames"] = ordered
    if spheres:
        geometry = xrdf.setdefault("geometry", {})
        world = (xrdf.get("world_collision") or {}).get("geometry")
        own = (xrdf.get("self_collision") or {}).get("geometry")
        groups = [g for g in dict.fromkeys([world, own]) if g]
        if not groups:
            groups = ["collision_spheres"]
            geometry.setdefault("collision_spheres", {"spheres": {}})
        handled: List[str] = []
        for group in groups:
            if any(_clones(geometry, group, h) for h in handled):
                continue
            target = _group_spheres(geometry, group)
            if target is None:
                entry = geometry.setdefault(group, {})
                if "clone" in entry:  # a group of clones: add a group with the end effectors' spheres and clone it too
                    extra = f"{group}_end_effectors"
                    geometry[extra] = {"spheres": {}}
                    entry["clone"] = list(entry["clone"]) + [extra]
                    target = geometry[extra]["spheres"]
                else:
                    entry["spheres"] = {}
                    target = entry["spheres"]
            for frame, items in spheres.items():
                target.setdefault(frame, []).extend(items)
            handled.append(group)
        xrdf.setdefault("world_collision", {}).setdefault("geometry", groups[0])
        xrdf.setdefault("self_collision", {}).setdefault("geometry", groups[-1])
    if buffers:
        for section in ("world_collision", "self_collision"):
            if section in xrdf:
                xrdf[section].setdefault("buffer_distance", {}).update(buffers)
    if ignore:
        self_collision = xrdf.setdefault("self_collision", {})
        merged = {str(k): list(v) for k, v in (self_collision.get("ignore") or {}).items()}
        for frame, others in ignore.items():
            for other in others:
                if other not in merged.setdefault(frame, []):
                    merged[frame].append(other)
        self_collision["ignore"] = merged
    return xrdf


# ---- writing ---------------------------------------------------------------------------------------------------

def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else (".inf" if value > 0 else "-.inf" if value < 0 else ".nan")
    return json.dumps(str(value))  # a JSON string is a valid YAML double-quoted scalar


def _emit(value: Any, indent: int, lines: List[str]) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item and not _flow(item):
                lines.append(f"{pad}{_scalar(key)}:")
                _emit(item, indent + 1, lines)
            else:
                lines.append(f"{pad}{_scalar(key)}: {_inline(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)) and item and not _flow(item):
                sub: List[str] = []
                _emit(item, indent + 1, sub)
                lines.append(f"{pad}- {sub[0].lstrip()}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {_inline(item)}")


def _flow(value: Any) -> bool:
    """Short lists of scalars (positions, orientations) are written inline."""
    return isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value)


def _inline(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_scalar(k)}: {_inline(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_inline(v) for v in value) + "]"
    return _scalar(value)


def render_xrdf(xrdf: Mapping[str, Any], header: Sequence[str] = ()) -> str:
    """Deterministic YAML text of an XRDF dict (keys in their order), with ``header`` lines as comments."""
    lines = [f"# {line}" if line else "#" for line in header]
    _emit(dict(xrdf), 0, lines)
    return "\n".join(lines) + "\n"


def source_line(name: str, data: Union[str, bytes]) -> str:
    """A header line naming a source and its SHA-256."""
    data = data.encode("utf-8") if isinstance(data, str) else data
    return f"  {name}  sha256 {hashlib.sha256(data).hexdigest()}"


def merged_file_name(robot: str, combination: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9 _.-]", "_", f"{robot}__{combination}").strip(" .")
    return f"{safe}.xrdf"


def write_merged_xrdf(directory: Union[str, Path], robot: str, combination: str, xrdf: Mapping[str, Any],
                      sources: Sequence[Tuple[str, Union[str, bytes]]] = ()) -> Path:
    """Write a merged XRDF as ``<robot>__<combination>.xrdf`` in ``directory`` (created if needed), with a header
    listing the sources and their SHA-256; unchanged content is not rewritten. Returns the path."""
    header = [f"Merged XRDF: robot '{robot}', end-effector combination '{combination}'.",
              "Generated by amt.dtp.ise.lib.control.motion.endeffector (do not edit; edit the sources). Sources:"]
    header += [source_line(n, d) for n, d in sources]
    text = render_xrdf(xrdf, header)
    path = Path(directory) / merged_file_name(robot, combination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def default_joint_positions(xrdf: Mapping[str, Any], joints: Sequence[str]) -> Optional[List[float]]:
    """The XRDF's ``default_joint_positions`` in c-space order (radians/meters), if it lists every joint."""
    defaults = xrdf.get("default_joint_positions") or {}
    if not all(j in defaults for j in joints):
        return None
    return [float(defaults[j]) for j in joints]

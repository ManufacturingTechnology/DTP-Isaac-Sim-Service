"""Compile-then-merge (ADR-0011; a deferred preview feature): align command frames compiled by several high-level APIs (the
RobotAPI's ``compile``, a future CNC API, hand-written frames) step by step and merge them into aggregate rows, which
a cell controller writes to the segment (``SegmentClient.write(rows, expected_revision=...)``). Synchronization stays
in one place, the segment queue: row *k* is applied on one physics step, whatever it holds.

Deferred: kept, but the API routes it relies on (compile from a start,
``expected_revision`` writes) are served only with ``preview_features=True``.

Pure Python (pydantic models or dicts in, dicts out). Design: ``docs/src/Agents/Specs/robot-control.md`` ("Compile, then
merge").
"""
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel

FrameLike = Union[BaseModel, Mapping[str, Any]]


class MergeConflict(ValueError):
    """Two sources set the same value on the same step: ``step`` (0-based row) and ``path`` (component, transducer,
    field...)."""
    error_code = "MERGE_CONFLICT"

    def __init__(self, step: int, path: Sequence[str], sources: Sequence[int]):
        self.step = step
        self.path = list(path)
        self.sources = list(sources)
        super().__init__(f"step {step}: sources {self.sources[0]} and {self.sources[1]} both set "
                         f"{'.'.join(self.path)}; merge them in separate steps or drop one")


def _as_dict(frame: Optional[FrameLike]) -> Dict[str, Any]:
    if frame is None:
        return {}
    if isinstance(frame, BaseModel):
        return frame.model_dump(mode="json", exclude_unset=True)
    return dict(frame)


def _merge_into(target: Dict[str, Any], owners: Dict[tuple, int], value: Mapping[str, Any], source: int, step: int,
                path: tuple) -> None:
    """Deep-merge a component tree; a leaf (a joint target, a transducer's atomic setpoint) belongs to one source."""
    for key, item in value.items():
        if item is None:
            continue
        here = path + (key,)
        # Joint/setpoint models are atomic (ADR-0003): a dict holding only scalars is a leaf value.
        atomic = not isinstance(item, Mapping) or all(not isinstance(v, Mapping) for v in item.values())
        if atomic:
            if here in owners and owners[here] != source:
                raise MergeConflict(step, here, [owners[here], source])
            if any(here == p[:len(here)] and owners[p] != source for p in owners if len(p) > len(here)):
                raise MergeConflict(step, here, [next(owners[p] for p in owners if p[:len(here)] == here), source])
            target[key] = item
            owners[here] = source
        else:
            child = target.setdefault(key, {})
            if not isinstance(child, dict):
                raise MergeConflict(step, here, [owners.get(here, -1), source])
            _merge_into(child, owners, item, source, step, here)


def merge_frames(*sources: Sequence[FrameLike], offsets: Optional[Sequence[int]] = None,
                 frame_id: Union[Literal["first"], int, None] = "first") -> List[Dict[str, Any]]:
    """Aggregate rows from several frame sequences (one per source, each one frame per physics step): row *k* holds
    every source's frame ``k − offset`` (sources that have ended, or not started, contribute nothing: their machines
    hold). Components merge down to the joint or setpoint: two sources may address the same component's different
    transducers or joints, but not the same one on the same step (:class:`MergeConflict`).

    :param offsets: The step at which each source starts (default: all at step 0).
    :param frame_id: ``"first"``: each row takes the frame_id of the first source (in argument order) that has one at
        that step; an int: every row gets it; None: no frame ids.
    :returns: Command frames as dicts (``{"frame_id"?, "components"?}``), ready for ``/api/segment/process/write``.
    """
    offsets = list(offsets) if offsets is not None else [0] * len(sources)
    if len(offsets) != len(sources) or any(o < 0 for o in offsets):
        raise ValueError("give one non-negative offset per source")
    frames = [[_as_dict(f) for f in source] for source in sources]
    total = max((o + len(f) for o, f in zip(offsets, frames)), default=0)
    rows: List[Dict[str, Any]] = []
    for step in range(total):
        components: Dict[str, Any] = {}
        owners: Dict[tuple, int] = {}
        chosen_id = frame_id if isinstance(frame_id, int) and not isinstance(frame_id, bool) else None
        for index, (offset, source) in enumerate(zip(offsets, frames)):
            k = step - offset
            if not 0 <= k < len(source):
                continue
            frame = source[k]
            if frame_id == "first" and chosen_id is None and frame.get("frame_id") is not None:
                chosen_id = frame["frame_id"]
            _merge_into(components, owners, frame.get("components") or {}, index, step, ())
        row: Dict[str, Any] = {}
        if chosen_id is not None:
            row["frame_id"] = chosen_id
        if components:
            row["components"] = components
        rows.append(row)
    return rows

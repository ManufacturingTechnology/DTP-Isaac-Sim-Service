"""Pure helpers for editing pydantic models in a UI, kept free of omni/Isaac imports so they can be
unit-tested offline. Used by the extension's model editor (`amt.dtp.ise.ui.model_editor`) for list
fields: which annotations are lists, their item types and length limits, default values for new items,
item titles, and the index arithmetic of moving items."""
import copy
import types
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo


def unwrap_optional(annotation) -> Tuple[Any, bool]:
    """Return (inner_annotation, is_optional). For Optional[X] / X | None returns (X, True); anything else
    is returned unchanged with False. Unions of more than one non-None type are left intact."""
    origin = get_origin(annotation)
    if origin is Union or origin is getattr(types, "UnionType", None):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


def list_item_annotation(annotation) -> Optional[Any]:
    """The item annotation of a list-like annotation, or None if it isn't one. Accepts List[T], list[T],
    and variadic Tuple[T, ...] (fixed-length tuples are not lists). Optional is NOT unwrapped here."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        return args[0] if args else Any
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        return args[0]
    return None


def is_list_annotation(annotation) -> bool:
    """True for List[T], list[T], Tuple[T, ...], and Optional of those."""
    inner, _ = unwrap_optional(annotation)
    return list_item_annotation(inner) is not None


def length_limits(field_info: Optional[FieldInfo]) -> Tuple[Optional[int], Optional[int]]:
    """(min_length, max_length) of a list field, from Field(min_length=..., max_length=...)."""
    lo = hi = None
    if field_info is not None:
        for m in field_info.metadata:
            if getattr(m, "min_length", None) is not None:
                lo = m.min_length
            if getattr(m, "max_length", None) is not None:
                hi = m.max_length
    return lo, hi


def _numeric_bound(field_info: Optional[FieldInfo], attr: str):
    if field_info is None:
        return None
    for m in field_info.metadata:
        if getattr(m, attr, None) is not None:
            return getattr(m, attr)
    return None


def default_value(annotation, field_info: Optional[FieldInfo] = None) -> Any:
    """A sensible starting value for a new item (or field) of this type, in the form an editor and
    pydantic both accept: primitives as values, models as dicts of their own defaults (required fields
    without a default are left out, so validation flags them), lists as []."""
    if field_info is not None and not field_info.is_required():
        if field_info.default_factory is not None:
            return copy.deepcopy(field_info.default_factory())
        return copy.deepcopy(field_info.default)
    inner, optional = unwrap_optional(annotation)
    if optional:
        return None
    if list_item_annotation(inner) is not None:
        return []
    if get_origin(inner) is Literal:
        args = get_args(inner)
        return args[0] if args else None
    if isinstance(inner, type):
        if issubclass(inner, BaseModel):
            return model_defaults(inner)
        if issubclass(inner, Enum):
            return next(iter(inner))
        if issubclass(inner, bool):
            return False
        if issubclass(inner, int):
            lo = _numeric_bound(field_info, "ge")
            return lo if lo is not None and lo > 0 else 0
        if issubclass(inner, float):
            lo = _numeric_bound(field_info, "ge")
            return float(lo) if lo is not None and lo > 0 else 0.0
        if issubclass(inner, str):
            return ""
        if issubclass(inner, Path):
            return Path("")
    return None


def model_defaults(model_type) -> dict:
    """A dict of a model's field defaults, recursing into required nested models; required Optional
    fields are None. Required non-Optional primitive fields without a default are omitted (so validation
    flags them until they are filled in)."""
    result = {}
    for name, info in model_type.model_fields.items():
        if not info.is_required():
            result[name] = default_value(info.annotation, info)
            continue
        inner, optional = unwrap_optional(info.annotation)
        if optional:
            result[name] = None  # a required Optional field: None is a valid explicit value
            continue
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            result[name] = model_defaults(inner)
        elif list_item_annotation(inner) is not None:
            result[name] = []
    return result


def to_plain(value) -> Any:
    """Copy a value for storage in an editor's item list: models become dicts of their raw attributes
    (not model_dump(), which would turn enums and paths into JSON forms), lists are copied."""
    if isinstance(value, BaseModel):
        return {name: to_plain(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    return copy.deepcopy(value)


def item_title(value, index: int, model_type=None, title_field: Optional[str] = None) -> str:
    """The title of a model item: '<Model> "<value of title_field>"' when the field is set and non-empty,
    otherwise '<Model> <index>' (or 'Item <index>' when the type is unknown)."""
    base = model_type.__name__.lstrip("_") if isinstance(model_type, type) else "Item"
    if title_field:
        v = value.get(title_field) if isinstance(value, dict) else getattr(value, title_field, None)
        if isinstance(v, Enum):
            v = v.value
        if v not in (None, ""):
            return f'{base} "{v}"'
    return f"{base} {index}"


def move(items: List[Any], source: int, target: int) -> List[Any]:
    """Return a new list with items[source] moved to position target (an index in the result).
    Out-of-range indices raise IndexError."""
    n = len(items)
    if not (0 <= source < n) or not (0 <= target < n):
        raise IndexError(f"move({source}, {target}) out of range for {n} items")
    result = list(items)
    result.insert(target, result.pop(source))
    return result


def drop_target(source: int, gap: int) -> int:
    """Convert a drop onto 'gap' (0 = before the first item, n = after the last) into the target index
    for move(): dropping an item below its own position shifts the gap by one."""
    return gap - 1 if gap > source else gap

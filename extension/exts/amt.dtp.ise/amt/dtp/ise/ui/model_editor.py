from abc import ABC, abstractmethod
import ast
import asyncio
import inspect
import os
from pathlib import Path
import textwrap
import types
from typing import Annotated, List, Optional, Type, Literal, Callable, ClassVar, Generic, TypeVar, Union, get_args, get_origin
from amt.dtp.ise.service.subscription import SubscriptionService
from pydantic import BaseModel, ConfigDict, ValidationError, Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from enum import Enum, StrEnum, auto

from isaacsim.gui.components.element_wrappers import (
    Button,
    CheckBox,
    CollapsableFrame,
    ColorPicker,
    DropDown,
    FloatField,
    IntField,
    StateButton,
    StringField,
    TextBlock,
    XYPlot,
)
import isaacsim.gui.components.ui_utils
import omni
import omni.ui
import omni.usd
import pxr.Usd


from .core import Renderable, ExtensionWindow
from ..lib.logging import _logger
from ..lib.model_editing import (
    default_value,
    is_list_annotation,
    item_title,
    length_limits,
    list_item_annotation,
    model_defaults,
    move as list_move,
    to_plain,
)


class FuncEnumMarker:
    """Metadata attached via Annotated[...] that carries the zero-arg callable producing
    the allowed string options, resolved at runtime when the editor is built."""

    def __init__(self, func: Callable[[], List[str]]):
        if not callable(func):
            raise TypeError("FuncEnum[...] expects a zero-arg callable returning List[str]")
        self.func = func


class FuncEnum:
    """Field-annotation factory for a runtime-resolved enum of strings:

        choice: FuncEnum[_function_enum] = Field(title="Choice")

    FuncEnum[fn] -> Annotated[str, FuncEnumMarker(fn)]. pydantic validates the field as a
    plain str and stows the marker in FieldInfo.metadata, where _FuncEnumFieldEditor finds
    it. Unlike Enum / Literal, the choices come from fn() at editor-build time, not from a
    fixed set declared on the class."""

    def __class_getitem__(cls, func: Callable[[], List[str]]):
        return Annotated[str, FuncEnumMarker(func)]


class _ModelCommandMarker:
    """Metadata attached by @model_command, read at editor-build time to render the button.
    `description` is None when the decorator omitted it, signalling a fallback to the
    method's docstring."""

    def __init__(self, title: str, description: Optional[str]):
        self.title = title
        self.description = description


def model_command(title: str, description: Optional[str] = None):
    """Mark a model method as a command button in the editor. `title` is the button text;
    `description` is its tooltip (falls back to the method's docstring when omitted). The
    decorated method keeps the (self, model) -> Optional[model] contract that the editor
    invokes on click."""
    if not isinstance(title, str):
        raise TypeError("title must be a str")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a str or None")

    def _decorator(func):
        if not callable(func):
            raise TypeError("model_command can only decorate a callable")
        func.__model_command__ = _ModelCommandMarker(title, description)
        return func

    return _decorator


class _ExampleEnum(StrEnum):
    X = "Option X"
    Y = "Option Y"
    Z = "Option Z"

def _function_enum() -> List[str]:
    return ["Option E", "Option F", "Option G"]

class _NestedNestedModel(BaseModel):
    name: str = Field(
        title="Nested Nested Name",
    )
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v

class _NestedModel(BaseModel):
    name: str = Field(
        title="Nested Name",
    )
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v
    nested: _NestedNestedModel = Field(
        title="Nested Nested Model",
    )
    optional_nested: Optional[_NestedNestedModel] = Field(
        title="Optional Nested Nested Model",
    )

class _ExampleAllModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True) # Required for validation to work
    model_name: ClassVar[str] = "Example"
    custom_data_key: ClassVar[str] = "amt:dtp:ise:example" # Where the custom data should be located

    name: str = Field(
        title="Name",
        description="Name Tooltip",
    )
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v
    
    selection: Literal["A", "B", "C"] = Field(
        title="Selection",
        description="Selection Tooltip",
    )

    enum: _ExampleEnum = Field(
        title="Enum",
        description="Enum Tooltip",
    )

    func_enum: FuncEnum[_function_enum] = Field(
        title="Func Enum",
        description="Func Enum Tooltip",
    )

    items: List[str] = Field(
        title="Items",
        description="Items Tooltip",
    )

    enabled: bool = Field(
        title="Enabled",
    )

    count: int = Field(
        1,
        title="Count",
        description="Count Tooltip",
        ge=-2,
        le=2,
    )

    value1: float = Field(
        0.2,
        title="Value 1",
        description="Value 1 Tooltip",
        ge=-1.0,
        le=1.0,
        json_schema_extra={
            "step": 0.1,
        },
    )

    value2: float = Field(
        0.03,
        title="Value 2",
        description="Value 2 Tooltip",
    )

    nested: _NestedModel = Field(
        title="Nested Model",
    )
    optional_nested: Optional[_NestedModel] = Field(
        None,
        title="Optional Nested Model",
        description="Toggle the checkbox to include this sub-model.",
    )

    file1: Path = Field(
        "",
        title="File 1",
        description="File 1 Tooltip",
        json_schema_extra={
            "extensions": [".txt", ".csv"],
            # "must_exist": True,
        },
    )

    # file2: Path = Field(
    #     "",
    #     title="File 2",
    #     description="File 2 Tooltip",
    #     json_schema_extra={
    #         "must_exist": True, 
    #         "extensions": [".txt", ".csv"],
    #     },
    # )

    dir1: Path = Field(
        "",
        title="Directory 1",
        description="Directory  1 Tooltip",
        json_schema_extra={
            "is_directory": True,
            # "must_exist": True,
        },
    )

    # dir2: Path = Field(
    #     "",
    #     title="Directory  2",
    #     description="Directory  2 Tooltip",
    #     json_schema_extra={
    #         "is_directory": True,
    #         "must_exist": True,
    #     },
    # )

    # ---- lists (any JSON-serializable item type) ----------------------------------------------
    numbers: List[Annotated[int, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: [1, 2],
        title="Numbers",
        description="1 to 5 numbers, each from 0 to 10 (item and list limits are validated live).",
        min_length=1,
        max_length=5,
    )

    weights: List[float] = Field(
        default_factory=list,
        title="Weights",
        description="A list of floats.",
    )

    choices: List[_ExampleEnum] = Field(
        default_factory=list,
        title="Choices",
        description="A list of enum values.",
    )

    files: List[Path] = Field(
        default_factory=list,
        title="Files",
        description="A list of file paths (the field's 'extensions' apply to every item).",
        json_schema_extra={
            "extensions": [".txt", ".csv"],
        },
    )

    people: List[_NestedModel] = Field(
        default_factory=list,
        title="People",
        description="A list of models; each item's title shows its 'name'.",
        json_schema_extra={
            "item_title_field": "name",
        },
    )

    matrix: List[List[float]] = Field(
        default_factory=list,
        title="Matrix",
        description="A list of lists (rows of floats). No duplicate buttons here.",
        json_schema_extra={
            "duplicate": False,
        },
    )

    optional_list: Optional[List[str]] = Field(
        None,
        title="Optional List",
        description="Toggle the checkbox to include this list (None vs. a list).",
    )

    # ---- Optional[...] of every kind (a checkbox before the field; unchecked = None) -----------------------
    optional_name: Optional[str] = Field(
        None,
        title="Optional Name",
        description="Optional[str].",
    )

    optional_count: Optional[int] = Field(
        None,
        title="Optional Count",
        description="Optional[int], 0 to 10 when set.",
        ge=0,
        le=10,
    )

    optional_value: Optional[float] = Field(
        None,
        title="Optional Value",
        description="Optional[float].",
    )

    optional_flag: Optional[bool] = Field(
        None,
        title="Optional Flag",
        description="Optional[bool].",
    )

    optional_selection: Optional[Literal["A", "B", "C"]] = Field(
        None,
        title="Optional Selection",
        description="Optional[Literal].",
    )

    optional_enum: Optional[_ExampleEnum] = Field(
        None,
        title="Optional Enum",
        description="Optional[Enum].",
    )

    optional_func_enum: Optional[FuncEnum[_function_enum]] = Field(
        None,
        title="Optional Func Enum",
        description="Optional[FuncEnum].",
    )

    optional_file: Optional[Path] = Field(
        None,
        title="Optional File",
        description="Optional[Path].",
        json_schema_extra={
            "extensions": [".txt", ".csv"],
        },
    )

    optional_numbers: List[Optional[int]] = Field(
        default_factory=list,
        title="Optional Numbers",
        description="List[Optional[int]]: each item has its own checkbox.",
    )

    @model_command(title="Do Thing", description="Toggle 'enabled' and save the model.")
    def do_thing(self, model: "_ExampleAllModel") -> Optional["_ExampleAllModel"]:
        """
        Toggle 'enabled' and return the modified model, which the editor then saves and
        reloads into the widgets. Returning None instead would make no change.
        """
        model.enabled = not model.enabled
        return model

    @model_validator(mode="after")
    def validate_model(self) -> "_ExampleAllModel":
        return self


T = TypeVar("T", bound=BaseModel)


# omni.ui color format is 0xAABBGGRR (alpha, blue, green, red).
_INVALID_LABEL_STYLE = {"color": 0xFF0000FF}
_VALID_LABEL_STYLE = {"color": 0xFFCCCCCC}
# Dark-theme tooltip to replace Isaac's default light-yellow/dark-gray one, which reads
# poorly against the rest of the (dark) editor. Light text on a dark background, with a
# little padding so the text isn't cramped against the edge.
_TOOLTIP_STYLE = {
    "background_color": 0xFF2A2A2A,
    "color": 0xFFCCCCCC,
    "padding": 6,
    "border_width": 1,
    "border_radius": 3,
    "border_color": 0xFF555555,
}
# Nested-model frame styling. Border turns red when the frame (or anything inside it) is
# invalid; background dims and the title + built-in disclosure triangle grey out when an
# optional sub-model is disabled. The "color" key drives both the title text and the
# triangle drawn by CollapsableFrame's default header.
_INVALID_FRAME_BORDER = {"border_color": 0xFF0000FF, "border_width": 1, "border_radius": 2}
_VALID_FRAME_BORDER = {"border_width": 0}
_DISABLED_FRAME_BG_COLOR = 0xFF1E1E1E
_DISABLED_HEADER_COLOR = 0xFF5A5A5A
_ENABLED_HEADER_COLOR = 0xFFCCCCCC


def _unwrap_optional(annotation) -> tuple:
    """Return (inner_annotation, is_optional). For Optional[X] / X | None (a Union of a
    single type with None) returns (X, True); for anything else returns (annotation, False).
    Unions of more than one non-None type are treated as non-optional and left intact."""
    origin = get_origin(annotation)
    if origin is Union or origin is getattr(types, "UnionType", None):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    return annotation, False


def _get_numeric_constraint(field_info: FieldInfo, attr: str, default=None):
    """Extract a numeric constraint (ge, le, multiple_of) from pydantic FieldInfo metadata."""
    for m in field_info.metadata:
        if hasattr(m, attr):
            return getattr(m, attr) # Returns a tuple for ge and le field attributes
    return default


class FieldEditor(ABC):
    """Thin wrapper around a single omni.ui widget. Has no model reference — values are
    read via get_value() and written via set_value(). The on_edited_fn callback fires
    when the user edits the widget; ModelEditor uses it to aggregate values across
    fields and validate the whole model at once."""

    @staticmethod
    @abstractmethod
    def get_field_type() -> type:
        raise NotImplementedError()

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        return annotation == cls.get_field_type()

    def __init__(
        self,
        field_name: str,
        field_info: FieldInfo,
        on_edited_fn: Optional[Callable[["FieldEditor", object, object], None]] = None,
        *,
        label: Optional[str] = None,
        label_width: int = 150,
    ):
        if not isinstance(field_name, str):
            raise TypeError("field_name must be a str")
        self._field_name = field_name

        if not isinstance(field_info, FieldInfo):
            raise TypeError("field_info must be a FieldInfo")
        self._field_info = field_info

        if on_edited_fn is not None and not isinstance(on_edited_fn, Callable):
            raise TypeError("on_edited_fn must be a Callable")
        self._on_edited_fn = on_edited_fn

        if label is not None and not isinstance(label, str):
            raise TypeError("label must be a str or None")
        self._label_override = label

        if not isinstance(label_width, int):
            raise TypeError("label_width must be an int")
        self._label_width = label_width

        self._suppress_edited = False
        self._last_value: object = None

    @property
    def field_name(self) -> str:
        return self._field_name

    @property
    def field_info(self) -> FieldInfo:
        return self._field_info

    @property
    def label_text(self) -> str:
        """The label shown next to the widget: the override (list items pass their own), else the
        field's title, else its name."""
        if self._label_override is not None:
            return self._label_override
        return self._field_info.title or self._field_name

    @property
    def label_width(self) -> int:
        return self._label_width

    @abstractmethod
    def construct(self, initial_value) -> None:
        """Build the widget with the initial value already populated, then attach the
        change callback. Subclasses must set self._last_value before attaching."""
        raise NotImplementedError()

    @abstractmethod
    def get_value(self):
        """Read and return the current value from the widget. May raise if the widget
        value cannot be coerced to the field's declared type."""
        raise NotImplementedError()

    @abstractmethod
    def set_value(self, value) -> None:
        """Push a value into the widget without firing on_edited_fn. Must be called
        after construct()."""
        raise NotImplementedError()

    def set_invalid(self, invalid: bool) -> None:
        """Color the field label red when invalid, restore default otherwise. Subclasses
        with externally-managed labels (e.g., Isaac Sim StringField wrapper) override
        this. Safe to call before construct() — no-op if the label doesn't exist yet."""
        label = getattr(self, "_label", None)
        if label is None:
            return
        label.set_style(_INVALID_LABEL_STYLE if invalid else _VALID_LABEL_STYLE)

    def validate_value(self, value) -> None:
        """Per-field validation hook beyond what pydantic catches at model construction.
        Default: no extra check. Subclasses raise ValueError to signal a bad value
        (e.g., must_exist failure for path fields)."""
        return

    def collect_errors(self) -> List[tuple]:
        """Return a list of (loc_tuple, message) describing problems with this editor's
        current widget state, where loc_tuple is relative to the parent model (a leaf
        field yields a one-element loc). Empty list means valid. Captures both get_value()
        coercion failures and validate_value() hook failures. Nested editors override this
        to prepend their field name onto each sub-editor's loc."""
        try:
            value = self.get_value()
            self.validate_value(value)
            return []
        except Exception as ex:
            return [((self._field_name,), str(ex))]

    def apply_validation_errors(self, error_locs: set) -> None:
        """Color this field's label based on a set of error loc tuples (str components)
        relative to the parent model. Marks invalid when any loc's first element is this
        field's name. Nested editors override to also recurse into their sub-editors."""
        self.set_invalid(any(loc and loc[0] == self._field_name for loc in error_locs))

    def _notify_edited(self) -> None:
        """Called by the widget's change callback. Fires on_edited_fn with old/new value,
        skipping if a programmatic set_value() is in progress."""
        try:
            if self._suppress_edited:
                return
            try:
                new_value = self.get_value()
            except Exception as ex:
                _logger.error(f"Failed to read widget value: {ex}", exception=ex)
                return
            old_value = self._last_value
            self._last_value = new_value
            if self._on_edited_fn is not None:
                self._on_edited_fn(self, old_value, new_value)
        except Exception as ex:
            _logger.error(f"Error notifying FieldEditor edited: {ex}", exception=ex)
            raise ex


class _StringFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return str

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        # Decline FuncEnum[...] fields (Annotated[str, FuncEnumMarker]) so _FuncEnumFieldEditor
        # claims them; their annotation is bare str and would otherwise match here first.
        if field_info is not None and any(
            isinstance(m, FuncEnumMarker) for m in field_info.metadata
        ):
            return False
        return annotation == str

    def construct(self, initial_value) -> None:
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            self._field = omni.ui.StringField(
                tooltip=self._field_info.description or "",
            )
            self._field.model.set_value(str(initial_value) if initial_value is not None else "")
            self._last_value = self.get_value()
            self._field.model.add_value_changed_fn(lambda *a: self._notify_edited())

    def get_value(self) -> str:
        return str(self._field.model.get_value_as_string())

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.model.set_value(str(value) if value is not None else "")
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _BoolFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return bool

    def construct(self, initial_value) -> None:
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            self._field = omni.ui.CheckBox(
                tooltip=self._field_info.description or "",
            )
            self._field.model.set_value(bool(initial_value))
            self._last_value = self.get_value()
            self._field.model.add_value_changed_fn(lambda *a: self._notify_edited())

    def get_value(self) -> bool:
        return bool(self._field.model.get_value_as_bool())

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.model.set_value(bool(value))
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _IntFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return int

    def construct(self, initial_value) -> None:
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            self._field = omni.ui.IntDrag(
                tooltip=self._field_info.description or "",
                min=_get_numeric_constraint(self._field_info, "ge", 0),
                max=_get_numeric_constraint(self._field_info, "le", 100),
            )
            self._field.model.set_value(int(initial_value) if initial_value is not None else 0)
            self._last_value = self.get_value()
            self._field.model.add_value_changed_fn(lambda *a: self._notify_edited())

    def get_value(self) -> int:
        return int(self._field.model.get_value_as_int())

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.model.set_value(int(value) if value is not None else 0)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _FloatFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return float

    def construct(self, initial_value) -> None:
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            self._field = omni.ui.FloatDrag(
                tooltip=self._field_info.description or "",
                min=_get_numeric_constraint(self._field_info, "ge", 0.0),
                max=_get_numeric_constraint(self._field_info, "le", 100.0),
                step=(self._field_info.json_schema_extra or {}).get("step", 0.01),
            )
            self._field.model.set_value(float(initial_value) if initial_value is not None else 0.0)
            self._last_value = self.get_value()
            self._field.model.add_value_changed_fn(lambda *a: self._notify_edited())

    def get_value(self) -> float:
        # Clamp to [ge, le] to absorb floating-point drift accumulated by drag steps
        # (e.g. 0.1 + 0.1 + ... = 1.0000000001) which would otherwise fail pydantic.
        value = float(self._field.model.get_value_as_float())
        lo = _get_numeric_constraint(self._field_info, "ge", None)
        hi = _get_numeric_constraint(self._field_info, "le", None)
        if lo is not None and value < lo:
            value = lo
        if hi is not None and value > hi:
            value = hi
        return value

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.model.set_value(float(value) if value is not None else 0.0)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _LiteralFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return Literal

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        return get_origin(annotation) is Literal

    def construct(self, initial_value) -> None:
        self._options = list(get_args(self._field_info.annotation))
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            initial_index = self._options.index(initial_value) if initial_value in self._options else 0
            self._field = omni.ui.ComboBox(
                initial_index,
                *[str(o) for o in self._options],
                tooltip=self._field_info.description or "",
            )
            self._last_value = self.get_value()
            self._field.model.add_item_changed_fn(lambda *a: self._notify_edited())

    def get_value(self):
        index = self._field.model.get_item_value_model().get_value_as_int()
        return self._options[index]

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            index = self._options.index(value) if value in self._options else 0
            self._field.model.get_item_value_model().set_value(index)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _EnumFieldEditor(FieldEditor):

    @staticmethod
    def get_field_type() -> type:
        return Enum

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        return isinstance(annotation, type) and issubclass(annotation, Enum)

    def construct(self, initial_value) -> None:
        self._options = list(self._field_info.annotation)
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            initial_index = self._options.index(initial_value) if initial_value in self._options else 0
            self._field = omni.ui.ComboBox(
                initial_index,
                *[o.value if isinstance(o.value, str) else o.name for o in self._options],
                tooltip=self._field_info.description or "",
            )
            self._last_value = self.get_value()
            self._field.model.add_item_changed_fn(lambda *a: self._notify_edited())

    def get_value(self):
        index = self._field.model.get_item_value_model().get_value_as_int()
        return self._options[index]

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            index = self._options.index(value) if value in self._options else 0
            self._field.model.get_item_value_model().set_value(index)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


class _FuncEnumFieldEditor(FieldEditor):
    """Combo box whose options are produced by a function at construct() time, for fields
    annotated with FuncEnum[fn] (i.e. Annotated[str, FuncEnumMarker(fn)]). Parallels
    _EnumFieldEditor but resolves its choices at runtime rather than from a fixed Enum."""

    @staticmethod
    def get_field_type() -> type:
        return str  # underlying value type; real matching is via the marker below

    @staticmethod
    def _marker(field_info: Optional[FieldInfo]) -> Optional[FuncEnumMarker]:
        if field_info is None:
            return None
        return next((m for m in field_info.metadata if isinstance(m, FuncEnumMarker)), None)

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        return cls._marker(field_info) is not None

    def construct(self, initial_value) -> None:
        self._options = list(self._marker(self._field_info).func())  # resolved at runtime
        with omni.ui.HStack():
            self._label = omni.ui.Label(
                self.label_text,
                tooltip=self._field_info.description or "",
                width=self.label_width,
            )
            omni.ui.Spacer(width=10)
            initial_index = self._options.index(initial_value) if initial_value in self._options else 0
            self._field = omni.ui.ComboBox(
                initial_index,
                *[str(o) for o in self._options],
                tooltip=self._field_info.description or "",
            )
            self._last_value = self.get_value()
            self._field.model.add_item_changed_fn(lambda *a: self._notify_edited())

    def get_value(self):
        index = self._field.model.get_item_value_model().get_value_as_int()
        return self._options[index]

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            index = self._options.index(value) if value in self._options else 0
            self._field.model.get_item_value_model().set_value(index)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False


def _to_forward_slashes(value) -> str:
    """Render a path value as a string using forward slashes as the directory separator
    on every platform (the folder picker writes backslashes on Windows). Empty/falsy
    values become ''."""
    if not value:
        return ""
    return str(value).replace("\\", "/")


class _FileFieldEditor(FieldEditor):
    """Path field rendered as a string field with a file picker button. Reads
    json_schema_extra={'must_exist': bool} for optional existence requirement.

    Displayed and stored paths always use forward slashes as the directory separator,
    regardless of the host operating system."""

    @staticmethod
    def get_field_type() -> type:
        return Path

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        if annotation is not Path:
            return False
        extra = (field_info.json_schema_extra if field_info is not None else None) or {}
        return not bool(extra.get("is_directory", False))

    @property
    def must_exist(self) -> Optional[bool]:
        extra = self._field_info.json_schema_extra or {}
        return extra.get("must_exist")

    @property
    def extensions(self) -> Optional[List[str]]:
        extra = self._field_info.json_schema_extra or {}
        raw = extra.get("extensions")
        if raw is None:
            return None
        # Normalize to lowercase with a leading dot.
        return [("." + e.lstrip(".")).lower() for e in raw]

    def construct(self, initial_value) -> None:
        allowed = self.extensions

        def _item_filter_fn(file_path: str) -> bool:
            if allowed is None:
                return True # No filter configured — show everything.
            return any(file_path.lower().endswith(ext) for ext in allowed)

        self._field = StringField(
            label=self.label_text,
            tooltip=self._field_info.description or "",
            default_value=_to_forward_slashes(initial_value),
            use_folder_picker=True,
            item_filter_fn=_item_filter_fn,
            on_value_changed_fn=lambda *a: self._on_field_changed(),
            folder_dialog_title="Select File",
            folder_button_title="Select File",
        )
        self._last_value = self.get_value()

    def _on_field_changed(self) -> None:
        # Rewrite any backslashes (e.g. a native path from the picker on Windows) to
        # forward slashes so the displayed/stored path is OS-independent, then notify.
        raw = self._field.get_value()
        normalized = _to_forward_slashes(raw)
        if normalized != raw:
            self._suppress_edited = True
            try:
                self._field.set_value(normalized)
            finally:
                self._suppress_edited = False
        self._notify_edited()

    def get_value(self) -> Path:
        return Path(self._field.get_value())

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.set_value(_to_forward_slashes(value))
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False

    def set_invalid(self, invalid: bool) -> None:
        field = getattr(self, "_field", None)
        if field is None:
            return
        field.label.set_style(_INVALID_LABEL_STYLE if invalid else _VALID_LABEL_STYLE)

    def validate_value(self, value) -> None:
        if not self.must_exist:
            return
        # Check the raw widget string — Path("") normalizes to Path(".") which would
        # bypass the existence check on some platforms.
        raw = self._field.get_value().strip()
        if not raw:
            raise ValueError("file path is empty")
        if not Path(raw).is_file():
            raise ValueError(f"file does not exist: {raw}")


class _DirectoryFieldEditor(FieldEditor):
    """Path field rendered as a string field with a folder picker button. Activated by
    json_schema_extra={'is_directory': True}. Reads json_schema_extra={'must_exist': bool}
    for optional existence requirement.

    Displayed and stored paths always use forward slashes as the directory separator,
    regardless of the host operating system."""

    @staticmethod
    def get_field_type() -> type:
        return Path

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        if annotation is not Path:
            return False
        extra = (field_info.json_schema_extra if field_info is not None else None) or {}
        return bool(extra.get("is_directory", False))

    @property
    def must_exist(self) -> Optional[bool]:
        extra = self._field_info.json_schema_extra or {}
        return extra.get("must_exist")

    def construct(self, initial_value) -> None:
        def _item_filter_fn(file_path: str) -> bool:
            return False # Hide files so only directories appear; users navigate folders.

        self._field = StringField(
            label=self.label_text,
            tooltip=self._field_info.description or "",
            default_value=_to_forward_slashes(initial_value),
            use_folder_picker=True,
            item_filter_fn=_item_filter_fn,
            on_value_changed_fn=lambda *a: self._on_field_changed(),
            folder_dialog_title="Select Directory",
            folder_button_title="Select Directory",
        )
        self._last_value = self.get_value()

    def _on_field_changed(self) -> None:
        # Rewrite any backslashes (e.g. a native path from the picker on Windows) to
        # forward slashes so the displayed/stored path is OS-independent, then notify.
        raw = self._field.get_value()
        normalized = _to_forward_slashes(raw)
        if normalized != raw:
            self._suppress_edited = True
            try:
                self._field.set_value(normalized)
            finally:
                self._suppress_edited = False
        self._notify_edited()

    def get_value(self) -> Path:
        return Path(self._field.get_value())

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._field.set_value(_to_forward_slashes(value))
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False

    def set_invalid(self, invalid: bool) -> None:
        field = getattr(self, "_field", None)
        if field is None:
            return
        field.label.set_style(_INVALID_LABEL_STYLE if invalid else _VALID_LABEL_STYLE)

    def validate_value(self, value) -> None:
        if not self.must_exist:
            return
        # Check the raw widget string — Path("") normalizes to Path(".") whose is_dir()
        # is True (current working directory), so an empty field would silently pass.
        raw = self._field.get_value().strip()
        if not raw:
            raise ValueError("directory path is empty")
        if not Path(raw).is_dir():
            raise ValueError(f"directory does not exist: {raw}")


class _BaseModelFieldEditor(FieldEditor):
    """Edits a nested pydantic BaseModel by recursively building a FieldEditor per
    sub-field inside an omni.ui.CollapsableFrame / VStack. Handles arbitrarily deep
    nesting (a sub-field that is itself a BaseModel gets another _BaseModelFieldEditor).

    Supports Optional[BaseModel]: an enable checkbox toggles the sub-model on/off,
    writing None when disabled. The frame is expanded when populated and collapsed when
    None.

    get_value() returns a dict of sub-values (or None when optional+disabled) rather than
    a constructed model instance, so the parent model's pydantic validation runs over the
    whole tree at once and produces nested loc paths like ("nested", "name"). Those paths
    are routed to the exact offending sub-field via apply_validation_errors() and
    collect_errors(), which prepend/strip this field's name as they recurse."""

    @staticmethod
    def get_field_type() -> type:
        return BaseModel

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        inner, _ = _unwrap_optional(annotation)
        return isinstance(inner, type) and issubclass(inner, BaseModel)

    def construct(self, initial_value) -> None:
        self._inner_type, self._is_optional = _unwrap_optional(self._field_info.annotation)
        self._enabled = initial_value is not None
        # Sub-editor edits bubble up as an edit of this whole field.
        self._sub_editors = _build_field_editors(self._inner_type, lambda *a: self._notify_edited())

        self._base_title = self.label_text
        self._invalid = False

        with omni.ui.HStack(height=0):
            if self._is_optional:
                self._enable_checkbox = omni.ui.CheckBox(width=20, tooltip=self._field_info.description or "")
                self._enable_checkbox.model.set_value(self._enabled)
                self._enable_checkbox.model.add_value_changed_fn(lambda *a: self._on_toggle())
                omni.ui.Spacer(width=6)
            with omni.ui.VStack(height=0):
                # Keep the default header so its built-in disclosure triangle renders; the
                # disabled look is applied via frame style (color) + title, not a custom header.
                collapsed = initial_value is None
                if getattr(self, "initial_collapsed", None) is not None:
                    collapsed = self.initial_collapsed  # set by a list editor to keep an item's state
                self._frame = omni.ui.CollapsableFrame(self._base_title, collapsed=collapsed)
                with self._frame:
                    with omni.ui.VStack(spacing=5, height=0):
                        for fe in self._sub_editors:
                            # No sub-model instance yet → seed each sub-field with its
                            # default (recurses for nested-nested models), matching how the
                            # root editor seeds defaults when the model doesn't exist.
                            fe.construct(self._sub_value(initial_value, fe.field_name))

        self._update_enabled_state()
        self._refresh_frame_style()
        self._last_value = self.get_value()

    def get_value(self):
        if self._is_optional and not self._enabled:
            return None
        result: dict = {}
        for fe in self._sub_editors:
            try:
                result[fe.field_name] = fe.get_value()
            except Exception:
                pass # Reported via collect_errors(); omit so pydantic flags it too.
        return result

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            if value is None:
                if self._is_optional:
                    self._set_enabled(False)
                else:
                    self._reset_subs_to_defaults()
            else:
                if self._is_optional:
                    self._set_enabled(True)
                # value is a BaseModel instance (read raw typed attributes, not model_dump(),
                # which would coerce enums/paths and confuse leaf editors) or, for list items,
                # a dict of such values.
                for fe in self._sub_editors:
                    fe.set_value(self._sub_value(value, fe.field_name))
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False

    def _sub_value(self, value, name: str):
        """The initial value of sub-field `name`: from a model instance or a dict (list items are
        stored as dicts), or the sub-field's default when the value is None or lacks it."""
        if value is None:
            return _default_for_field(self._inner_type.model_fields[name])
        if isinstance(value, dict):
            if name in value:
                return value[name]
            return _default_for_field(self._inner_type.model_fields[name])
        return getattr(value, name)

    @property
    def collapsed(self) -> Optional[bool]:
        frame = getattr(self, "_frame", None)
        return None if frame is None else frame.collapsed

    def set_title(self, title: str) -> None:
        """Change the frame title (list items retitle themselves when their title field changes)."""
        self._base_title = title
        frame = getattr(self, "_frame", None)
        if frame is not None:
            frame.title = title + ("   (disabled)" if self._is_disabled() else "")

    def collect_errors(self) -> List[tuple]:
        if self._is_optional and not self._enabled:
            return [] # None is a valid value for an optional sub-model.
        errors: List[tuple] = []
        for fe in self._sub_editors:
            for loc, msg in fe.collect_errors():
                errors.append(((self._field_name, *loc), msg))
        return errors

    def apply_validation_errors(self, error_locs: set) -> None:
        # Mark our own frame if an error targets this field exactly (e.g. a required
        # sub-model left None), then recurse with our name stripped from the path so the
        # offending sub-field marks itself.
        self.set_invalid(any(loc and loc[0] == self._field_name for loc in error_locs))
        child_locs = {loc[1:] for loc in error_locs if len(loc) > 1 and loc[0] == self._field_name}
        for fe in self._sub_editors:
            fe.apply_validation_errors(child_locs)

    def set_invalid(self, invalid: bool) -> None:
        self._invalid = invalid
        self._refresh_frame_style()

    def _is_disabled(self) -> bool:
        return self._is_optional and not self._enabled

    def _refresh_frame_style(self) -> None:
        """Recompute the frame style from current invalid/disabled state. "color" drives
        both the title text and the built-in disclosure triangle, so greying it greys the
        whole default header; a dim background reinforces the disabled look, and a red
        border flags an invalid frame."""
        frame = getattr(self, "_frame", None)
        if frame is None:
            return
        disabled = self._is_disabled()
        style = dict(_INVALID_FRAME_BORDER if getattr(self, "_invalid", False) else _VALID_FRAME_BORDER)
        style["color"] = _DISABLED_HEADER_COLOR if disabled else _ENABLED_HEADER_COLOR
        if disabled:
            style["background_color"] = _DISABLED_FRAME_BG_COLOR
            style["secondary_color"] = _DISABLED_FRAME_BG_COLOR
        frame.set_style({"CollapsableFrame": style})

    def _on_toggle(self) -> None:
        enabled = self._enable_checkbox.model.get_value_as_bool()
        # A genuine user click that enables the sub-model seeds its fields with the
        # sub-model's defaults so it starts from a valid/default state instead of leftover
        # empty widgets. Skip when set_value() is driving the checkbox programmatically
        # (it fills the real loaded values itself) — detected via _suppress_edited.
        fill_defaults = enabled and not self._suppress_edited
        self._set_enabled(enabled)
        if fill_defaults:
            self._reset_subs_to_defaults()
        self._notify_edited()

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        checkbox = getattr(self, "_enable_checkbox", None)
        if checkbox is not None and checkbox.model.get_value_as_bool() != enabled:
            checkbox.model.set_value(enabled)
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        frame = getattr(self, "_frame", None)
        if frame is None or not self._is_optional:
            return
        frame.enabled = self._enabled
        frame.collapsed = not self._enabled
        frame.title = self._base_title + ("   (disabled)" if not self._enabled else "")
        self._refresh_frame_style()

    def _reset_subs_to_defaults(self) -> None:
        for fe in self._sub_editors:
            fe.set_value(_default_for_field(self._inner_type.model_fields[fe.field_name]))


# ---------------------------------------------------------------------------------------------------
# List fields
# ---------------------------------------------------------------------------------------------------

# omni.ui colors are 0xAABBGGRR.
# Add and delete are the default gray with only a slight green / red tint, so they don't distract.
_LIST_ADD_STYLE = {"Button": {"background_color": 0xFF3A463A, "border_radius": 3}, "Button.Label": {"color": 0xFFB8D4B8}}
_LIST_DELETE_STYLE = {"Button": {"background_color": 0xFF3A3A48, "border_radius": 3}, "Button.Label": {"color": 0xFFB8B8D4}}
_LIST_BUTTON_STYLE = {"Button": {"background_color": 0xFF3A3A3A, "border_radius": 3}}
_LIST_INDEX_STYLE = {"Button": {"background_color": 0xFF2A2A2A, "border_radius": 2}, "Button.Label": {"color": 0xFFCCCCCC}}
_LIST_INDEX_SELECTED_STYLE = {"Button": {"background_color": 0xFF7A5A2A, "border_radius": 2}, "Button.Label": {"color": 0xFFFFFFFF}}
_LIST_INDEX_INVALID_STYLE = {"Button": {"background_color": 0xFF2A2A2A, "border_radius": 2}, "Button.Label": {"color": 0xFF0000FF}}
_LIST_GRIP_COLOR = 0xFF777777
_LIST_DROP_INDICATOR = 0xFFFF9E4A
_LIST_DROP_TRANSPARENT = 0x00000000
_LIST_BUTTON_SIZE = 22
_LIST_ROW_SPACING = 4

# Field metadata (json_schema_extra) understood by the list editor. Every key is optional.
_LIST_DUPLICATE_KEY = "duplicate"            # bool, default True: show a duplicate button per item
_LIST_TITLE_FIELD_KEY = "item_title_field"   # str: field of a model item used in its frame title
_LIST_REORDER_KEY = "reorder"                # "row" | "list" | "both" (default "both"): where the up/down buttons are
_LIST_DRAG_KEY = "drag"                      # bool, default True: drag handles to reorder
_LIST_KEYS = {_LIST_DUPLICATE_KEY, _LIST_TITLE_FIELD_KEY, _LIST_REORDER_KEY, _LIST_DRAG_KEY}


def _list_button(text: str, width: int, style: dict, tooltip: str, clicked_fn, enabled: bool = True):
    button = omni.ui.Button(text, width=width, height=_LIST_BUTTON_SIZE, style=style, tooltip=tooltip,
                            clicked_fn=clicked_fn)
    button.enabled = enabled
    return button


class _ListItem:
    """One entry of a _ListFieldEditor: its stable id (survives reordering), its stored value (the
    source of truth between rebuilds), and the editor currently showing it (None until built)."""

    def __init__(self, uid: int, value):
        self.uid = uid
        self.value = value
        self.editor: Optional[FieldEditor] = None
        self.collapsed: Optional[bool] = None
        self.index_button = None


class _ListFieldEditor(FieldEditor):
    """Edits a list field of any JSON-serializable item type: primitives, enums and literals, paths,
    pydantic models (nested frames), and nested lists. Items can be added (green +), deleted (red X),
    duplicated, and reordered with up/down buttons on each row, with up/down buttons for the list acting
    on the selected row, or by dragging a row's grip handle.

    Each item is shown by the editor its type would get as a field (built with _editor_for), labelled by
    its index. Structural changes (add, delete, move, duplicate) store the current item values and
    rebuild the rows, because omni.ui widgets can't be re-parented; edits inside an item don't rebuild.
    Every change counts as an edit of the whole list, so the parent model is validated (and, when valid,
    saved) as for any other field. Error locations ("field", "2", "name") are routed to the item at that
    index, which marks its index red and passes the rest of the location to its own editor.

    Options, in the field's json_schema_extra: "duplicate" (default True), "item_title_field" (a field of
    model items shown in their titles), "reorder" ("row", "list", or "both"; default "both"), "drag"
    (default True). Other json_schema_extra keys (e.g. "extensions" for paths) are passed to the items."""

    _next_list_id = 0

    @staticmethod
    def get_field_type() -> type:
        return list

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        return is_list_annotation(annotation)

    # ---- set-up -----------------------------------------------------------------------------------
    def _setup(self) -> None:
        inner, self._is_optional = _unwrap_optional(self._field_info.annotation)
        self._item_annotation = list_item_annotation(inner)
        extra = dict(self._field_info.json_schema_extra or {}) if isinstance(self._field_info.json_schema_extra, dict) else {}
        self._allow_duplicate = bool(extra.get(_LIST_DUPLICATE_KEY, True))
        self._title_field = extra.get(_LIST_TITLE_FIELD_KEY)
        self._reorder = extra.get(_LIST_REORDER_KEY, "both")
        self._allow_drag = bool(extra.get(_LIST_DRAG_KEY, True))
        self._item_extra = {k: v for k, v in extra.items() if k not in _LIST_KEYS}
        self._min_length, self._max_length = length_limits(self._field_info)
        self._item_info = FieldInfo.from_annotation(self._item_annotation)
        if self._item_extra:
            self._item_info.json_schema_extra = dict(self._item_extra)
        inner_item, _ = _unwrap_optional(self._item_info.annotation)
        self._item_model = inner_item if isinstance(inner_item, type) and issubclass(inner_item, BaseModel) else None
        # Fail early, with the field's name, when the item type has no editor.
        if _editor_type_for(self._item_info) is None:
            raise TypeError(
                f"List field '{self._field_name}': items of type {self._item_annotation} can't be edited "
                f"(supported: str, int, float, bool, Enum, Literal, FuncEnum, Path, BaseModel, and lists of those)."
            )
        _ListFieldEditor._next_list_id += 1
        self._list_id = f"amt-list-{_ListFieldEditor._next_list_id}"
        self._items: List[_ListItem] = []
        self._next_uid = 0
        self._selected_uid: Optional[int] = None
        self._invalid = False
        self._invalid_indices: set = set()
        self._child_locs: set = set()
        self._drop_indicators: list = []

    def _new_item(self, value) -> _ListItem:
        self._next_uid += 1
        return _ListItem(self._next_uid, value)

    def _default_item_value(self):
        if self._item_model is not None:
            return model_defaults(self._item_model)
        value = default_value(self._item_info.annotation, self._item_info)
        marker = _FuncEnumFieldEditor._marker(self._item_info)
        if marker is not None:  # FuncEnum items start at their first runtime option
            options = list(marker.func())
            value = options[0] if options else ""
        return value

    # ---- FieldEditor interface ----------------------------------------------------------------------
    def construct(self, initial_value) -> None:
        self._setup()
        self._enabled = not (self._is_optional and initial_value is None)
        self._items = [self._new_item(to_plain(v)) for v in (initial_value or [])]

        with omni.ui.HStack(height=0):
            if self._is_optional:
                self._enable_checkbox = omni.ui.CheckBox(width=20, tooltip=self._field_info.description or "")
                self._enable_checkbox.model.set_value(self._enabled)
                self._enable_checkbox.model.add_value_changed_fn(lambda *a: self._on_toggle())
                omni.ui.Spacer(width=6)
            with omni.ui.VStack(height=0):
                self._frame = omni.ui.CollapsableFrame(self._title(), collapsed=not self._enabled)
                with self._frame:
                    with omni.ui.VStack(spacing=_LIST_ROW_SPACING, height=0):
                        self._rows = omni.ui.Frame(height=0)
                        self._rows.set_build_fn(self._build_rows)
                        with omni.ui.HStack(height=0, spacing=4):
                            if self._reorder in ("list", "both"):
                                self._list_up_button = _list_button(
                                    "Move Up", 72, _LIST_BUTTON_STYLE,
                                    "Move the selected item up (click an item's index to select it).",
                                    lambda: self._move_selected(-1),
                                )
                                self._list_down_button = _list_button(
                                    "Move Down", 86, _LIST_BUTTON_STYLE,
                                    "Move the selected item down (click an item's index to select it).",
                                    lambda: self._move_selected(1),
                                )
                            omni.ui.Spacer()
                            self._add_button = _list_button(
                                "+ Add item", 92, _LIST_ADD_STYLE, "Append a new item with default values.",
                                self._on_add,
                            )
        self._refresh_frame_style()
        self._refresh_buttons()
        self._last_value = self.get_value()

    def get_value(self):
        if self._is_optional and not self._enabled:
            return None
        return [self._item_value(item) for item in self._items]

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            if value is None and self._is_optional:
                self._set_enabled(False)
                self._items = []
            else:
                if self._is_optional:
                    self._set_enabled(True)
                self._items = [self._new_item(to_plain(v)) for v in (value or [])]
            self._selected_uid = None
            self._rebuild()
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False

    def collect_errors(self) -> List[tuple]:
        if self._is_optional and not self._enabled:
            return []
        errors: List[tuple] = []
        for item in self._items:
            if item.editor is None:
                continue
            for loc, msg in item.editor.collect_errors():
                errors.append(((self._field_name, *loc), msg))
        return errors

    def apply_validation_errors(self, error_locs: set) -> None:
        self.set_invalid(any(loc and loc[0] == self._field_name and len(loc) == 1 for loc in error_locs))
        child_locs = {loc[1:] for loc in error_locs if len(loc) > 1 and loc[0] == self._field_name}
        self._child_locs = child_locs
        self._invalid_indices = {loc[0] for loc in child_locs if loc}
        for index, item in enumerate(self._items):
            self._style_index_button(item, index)
            if item.editor is not None:
                item.editor.apply_validation_errors(child_locs)

    def set_invalid(self, invalid: bool) -> None:
        self._invalid = invalid
        self._refresh_frame_style()

    # ---- rows -------------------------------------------------------------------------------------
    def _title(self) -> str:
        title = f"{self.label_text} ({len(self._items)})"
        if self._is_optional and not self._enabled:
            title += "   (disabled)"
        return title

    def _item_value(self, item: _ListItem):
        if item.editor is not None:
            try:
                item.value = item.editor.get_value()
            except Exception:
                pass  # keep the last readable value; collect_errors() reports the problem
        return item.value

    def _store_values(self) -> None:
        """Copy every item's current widget value (and frame state) into the item before a rebuild."""
        for item in self._items:
            self._item_value(item)
            if isinstance(item.editor, _BaseModelFieldEditor):
                item.collapsed = item.editor.collapsed

    def _build_rows(self) -> None:
        self._drop_indicators = []
        with omni.ui.VStack(spacing=2, height=0):
            for index, item in enumerate(self._items):
                self._build_row(index, item)
        self._refresh_buttons()
        # Rebuilt editors start unmarked: re-apply the last validation result to them.
        for item in self._items:
            if item.editor is not None:
                item.editor.apply_validation_errors(self._child_locs)

    def _build_row(self, index: int, item: _ListItem) -> None:
        # The whole row (not a thin gap between rows) is the drop target: releasing a dragged item over a
        # row moves it to that row's position. A line above or below the row shows where it will land.
        with omni.ui.VStack(height=0, spacing=0) as row_box:
            top = omni.ui.Rectangle(height=2, style={"background_color": _LIST_DROP_TRANSPARENT})
            self._build_row_content(index, item)
            bottom = omni.ui.Rectangle(height=2, style={"background_color": _LIST_DROP_TRANSPARENT})
        self._drop_indicators.append((top, bottom))
        if self._allow_drag:
            row_box.set_accept_drop_fn(lambda data, i=index: self._accept_drop(i, data))
            row_box.set_drop_fn(lambda event, i=index: self._on_drop(i, event))

    def _build_row_content(self, index: int, item: _ListItem) -> None:
        with omni.ui.HStack(height=0, spacing=4):
            if self._allow_drag:
                self._build_grip(item)
            item.index_button = _list_button(
                str(index), 26, _LIST_INDEX_STYLE, "Select this item (for the list's Move Up / Move Down buttons).",
                lambda uid=item.uid: self._select(uid),
            )
            self._style_index_button(item, index)
            with omni.ui.VStack(height=0):
                item.editor = self._make_item_editor(index, item)
                item.editor.construct(item.value)
            if self._reorder in ("row", "both"):
                _list_button("Up", 34, _LIST_BUTTON_STYLE, "Move this item up.",
                             lambda uid=item.uid: self._move(uid, -1), enabled=index > 0)
                _list_button("Down", 46, _LIST_BUTTON_STYLE, "Move this item down.",
                             lambda uid=item.uid: self._move(uid, 1), enabled=index < len(self._items) - 1)
            if self._allow_duplicate:
                _list_button("Copy", 44, _LIST_BUTTON_STYLE, "Duplicate this item (the copy is inserted below it).",
                             lambda uid=item.uid: self._duplicate(uid), enabled=self._can_add())
            _list_button("X", _LIST_BUTTON_SIZE, _LIST_DELETE_STYLE, "Delete this item.",
                         lambda uid=item.uid: self._delete(uid), enabled=self._can_delete())

    def _make_item_editor(self, index: int, item: _ListItem) -> FieldEditor:
        if self._item_model is not None:
            title = item_title(item.value, index, self._item_model, self._title_field)
            editor = _editor_for(str(index), self._item_info, lambda *a, uid=item.uid: self._on_item_edited(uid),
                                 label=title)
            editor.initial_collapsed = True if item.collapsed is None else item.collapsed
        elif is_list_annotation(self._item_info.annotation):
            editor = _editor_for(str(index), self._item_info, lambda *a, uid=item.uid: self._on_item_edited(uid),
                                 label="Items")
        else:
            editor = _editor_for(str(index), self._item_info, lambda *a, uid=item.uid: self._on_item_edited(uid),
                                 label="", label_width=0)
        return editor

    def _build_grip(self, item: _ListItem) -> None:
        with omni.ui.ZStack(width=12, height=_LIST_BUTTON_SIZE, tooltip="Drag to reorder.") as handle:
            omni.ui.Rectangle(style={"background_color": _LIST_DROP_TRANSPARENT})
            with omni.ui.HStack():
                omni.ui.Spacer()
                for i in range(3):
                    omni.ui.Rectangle(width=2, height=_LIST_BUTTON_SIZE - 6,
                                      style={"background_color": _LIST_GRIP_COLOR, "border_radius": 1})
                    if i < 2:
                        omni.ui.Spacer(width=1)
                omni.ui.Spacer()
        handle.set_drag_fn(lambda uid=item.uid: f"{self._list_id}:{uid}")

    def _style_index_button(self, item: _ListItem, index: int) -> None:
        button = item.index_button
        if button is None:
            return
        if item.uid == self._selected_uid:
            button.set_style(_LIST_INDEX_SELECTED_STYLE)
        elif str(index) in self._invalid_indices:
            button.set_style(_LIST_INDEX_INVALID_STYLE)
        else:
            button.set_style(_LIST_INDEX_STYLE)

    # ---- actions ------------------------------------------------------------------------------------
    def _can_add(self) -> bool:
        return self._max_length is None or len(self._items) < self._max_length

    def _can_delete(self) -> bool:
        return self._min_length is None or len(self._items) > self._min_length

    def _index_of(self, uid: int) -> Optional[int]:
        return next((i for i, item in enumerate(self._items) if item.uid == uid), None)

    def _changed(self) -> None:
        """After a structural change: rebuild the rows, retitle, and report an edit of the list."""
        self._rebuild()
        self._notify_edited()

    def _rebuild(self) -> None:
        for item in self._items:
            item.editor = None
            item.index_button = None
        frame = getattr(self, "_frame", None)
        if frame is not None:
            frame.title = self._title()
        rows = getattr(self, "_rows", None)
        if rows is not None:
            rows.rebuild()
        self._refresh_buttons()

    def _on_add(self) -> None:
        try:
            if not self._can_add():
                return
            self._store_values()
            item = self._new_item(self._default_item_value())
            item.collapsed = False  # show the new item's fields
            self._items.append(item)
            self._changed()
        except Exception as ex:
            _logger.error(f"Failed to add a list item: {ex}", exception=ex)

    def _delete(self, uid: int) -> None:
        try:
            index = self._index_of(uid)
            if index is None or not self._can_delete():
                return
            self._store_values()
            del self._items[index]
            if self._selected_uid == uid:
                self._selected_uid = None
            self._changed()
        except Exception as ex:
            _logger.error(f"Failed to delete a list item: {ex}", exception=ex)

    def _duplicate(self, uid: int) -> None:
        try:
            index = self._index_of(uid)
            if index is None or not self._can_add():
                return
            self._store_values()
            source = self._items[index]
            copy_item = self._new_item(to_plain(source.value))
            copy_item.collapsed = source.collapsed
            self._items.insert(index + 1, copy_item)
            self._changed()
        except Exception as ex:
            _logger.error(f"Failed to duplicate a list item: {ex}", exception=ex)

    def _move(self, uid: int, delta: int) -> None:
        index = self._index_of(uid)
        if index is None:
            return
        self._move_to(index, index + delta)

    def _move_to(self, source: int, target: int) -> None:
        try:
            if source == target or not (0 <= target < len(self._items)):
                return
            self._store_values()
            self._items = list_move(self._items, source, target)
            self._changed()
        except Exception as ex:
            _logger.error(f"Failed to move a list item: {ex}", exception=ex)

    def _move_selected(self, delta: int) -> None:
        if self._selected_uid is not None:
            self._move(self._selected_uid, delta)

    def _select(self, uid: int) -> None:
        self._selected_uid = None if self._selected_uid == uid else uid
        for index, item in enumerate(self._items):
            self._style_index_button(item, index)
        self._refresh_buttons()

    def _drag_source(self, data) -> Optional[int]:
        """The index of the dragged item if the payload came from this list (\"<list id>:<uid>\"), else None."""
        if not isinstance(data, str) or not data.startswith(self._list_id + ":"):
            return None
        try:
            uid = int(data.split(":", 1)[1])
        except ValueError:
            return None
        return self._index_of(uid)

    def _clear_drop_indicators(self) -> None:
        for top, bottom in self._drop_indicators:
            top.set_style({"background_color": _LIST_DROP_TRANSPARENT})
            bottom.set_style({"background_color": _LIST_DROP_TRANSPARENT})

    def _accept_drop(self, index: int, data) -> bool:
        """Hovering a dragged item over row `index`: accept items from this list only, and show the line
        where it will land (below the row when moving down, above it when moving up)."""
        source = self._drag_source(data)
        self._clear_drop_indicators()
        if source is None:
            return False
        if source != index and 0 <= index < len(self._drop_indicators):
            top, bottom = self._drop_indicators[index]
            (bottom if source < index else top).set_style({"background_color": _LIST_DROP_INDICATOR})
        return True

    def _on_drop(self, index: int, event) -> None:
        """A dragged item released over row `index` takes that row's position."""
        self._clear_drop_indicators()
        data = getattr(event, "mime_data", event)
        source = self._drag_source(data)
        _logger.debug(f"List '{self._field_name}': drop on row {index} (from row {source})")
        if source is None:
            return  # from another list, or not a list item
        self._move_to(source, index)

    def _on_item_edited(self, uid: int) -> None:
        index = self._index_of(uid)
        if index is not None and self._item_model is not None and self._title_field:
            item = self._items[index]
            if isinstance(item.editor, _BaseModelFieldEditor):
                item.editor.set_title(item_title(self._item_value(item), index, self._item_model, self._title_field))
        self._notify_edited()

    def _on_toggle(self) -> None:
        enabled = self._enable_checkbox.model.get_value_as_bool()
        self._set_enabled(enabled)
        if not enabled:
            self._store_values()
        self._rebuild()
        self._notify_edited()

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        checkbox = getattr(self, "_enable_checkbox", None)
        if checkbox is not None and checkbox.model.get_value_as_bool() != enabled:
            checkbox.model.set_value(enabled)
        frame = getattr(self, "_frame", None)
        if frame is not None:
            frame.enabled = enabled
            frame.collapsed = not enabled
            frame.title = self._title()
        self._refresh_frame_style()

    def _refresh_buttons(self) -> None:
        add = getattr(self, "_add_button", None)
        if add is not None:
            add.enabled = self._enabled and self._can_add()
        index = self._index_of(self._selected_uid) if self._selected_uid is not None else None
        up = getattr(self, "_list_up_button", None)
        if up is not None:
            up.enabled = index is not None and index > 0
        down = getattr(self, "_list_down_button", None)
        if down is not None:
            down.enabled = index is not None and index < len(self._items) - 1

    def _refresh_frame_style(self) -> None:
        frame = getattr(self, "_frame", None)
        if frame is None:
            return
        disabled = self._is_optional and not self._enabled
        style = dict(_INVALID_FRAME_BORDER if self._invalid else _VALID_FRAME_BORDER)
        style["color"] = _DISABLED_HEADER_COLOR if disabled else _ENABLED_HEADER_COLOR
        if disabled:
            style["background_color"] = _DISABLED_FRAME_BG_COLOR
            style["secondary_color"] = _DISABLED_FRAME_BG_COLOR
        frame.set_style({"CollapsableFrame": style})


def _inner_field_info(inner, field_info: Optional[FieldInfo]) -> FieldInfo:
    """A FieldInfo for the T of an Optional[T] field: the inner annotation's own constraints (from
    Annotated) plus the field's title, description, json_schema_extra, and field-level constraints
    (Field(ge=...) on an Optional field applies to T)."""
    info = FieldInfo.from_annotation(inner)
    if field_info is not None:
        info.title = field_info.title
        info.description = field_info.description
        if isinstance(field_info.json_schema_extra, dict):
            info.json_schema_extra = dict(field_info.json_schema_extra)
        info.metadata = list(info.metadata) + [m for m in field_info.metadata if m not in info.metadata]
    return info


class _OptionalFieldEditor(FieldEditor):
    """Optional[T] for any T that has an editor: a checkbox before T's editor. Unchecked means None (T's
    editor is greyed out and keeps its value, so checking it again restores it); checked means T's value.
    Models and lists have their own enable checkbox (_BaseModelFieldEditor, _ListFieldEditor), so they
    don't use this. Works for list items too (List[Optional[int]])."""

    @staticmethod
    def get_field_type() -> type:
        return Optional

    @classmethod
    def matches(cls, annotation, field_info: Optional[FieldInfo] = None) -> bool:
        inner, optional = _unwrap_optional(annotation)
        if not optional:
            return False
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return False  # _BaseModelFieldEditor handles Optional[Model]
        if is_list_annotation(inner):
            return False  # _ListFieldEditor handles Optional[List[...]]
        return _editor_type_for(_inner_field_info(inner, field_info)) is not None

    def construct(self, initial_value) -> None:
        inner, _ = _unwrap_optional(self._field_info.annotation)
        self._inner_info = _inner_field_info(inner, self._field_info)
        self._enabled = initial_value is not None
        self._inner = _editor_for(
            self._field_name, self._inner_info, lambda *a: self._on_inner_edited(),
            label=self._label_override, label_width=self._label_width,
        )
        with omni.ui.HStack(height=0):
            self._checkbox = omni.ui.CheckBox(
                width=20, tooltip="Unchecked: no value (None). " + (self._field_info.description or ""),
            )
            self._checkbox.model.set_value(self._enabled)
            self._checkbox.model.add_value_changed_fn(lambda *a: self._on_toggle())
            omni.ui.Spacer(width=6)
            self._container = omni.ui.VStack(height=0)
            with self._container:
                self._inner.construct(initial_value if initial_value is not None else self._default_inner())
        self._container.enabled = self._enabled
        self._last_value = self.get_value()

    def _default_inner(self):
        marker = _FuncEnumFieldEditor._marker(self._inner_info)
        if marker is not None:
            options = list(marker.func())
            return options[0] if options else ""
        return default_value(self._inner_info.annotation, self._inner_info)

    def get_value(self):
        if not self._enabled:
            return None
        return self._inner.get_value()

    def set_value(self, value) -> None:
        self._suppress_edited = True
        try:
            self._set_enabled(value is not None)
            if value is not None:
                self._inner.set_value(value)
            self._last_value = self.get_value()
        finally:
            self._suppress_edited = False

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        checkbox = getattr(self, "_checkbox", None)
        if checkbox is not None and checkbox.model.get_value_as_bool() != enabled:
            checkbox.model.set_value(enabled)
        container = getattr(self, "_container", None)
        if container is not None:
            container.enabled = enabled

    def _on_toggle(self) -> None:
        self._set_enabled(self._checkbox.model.get_value_as_bool())
        if not self._enabled:
            self._inner.apply_validation_errors(set())  # None is valid: clear the inner marks
        self._notify_edited()

    def _on_inner_edited(self) -> None:
        if self._enabled:
            self._notify_edited()

    def validate_value(self, value) -> None:
        if self._enabled:
            self._inner.validate_value(value)

    def collect_errors(self) -> List[tuple]:
        return self._inner.collect_errors() if self._enabled else []

    def apply_validation_errors(self, error_locs: set) -> None:
        self._inner.apply_validation_errors(error_locs if self._enabled else set())

    def set_invalid(self, invalid: bool) -> None:
        inner = getattr(self, "_inner", None)
        if inner is not None:
            inner.set_invalid(invalid)


class _CommandFieldEditor:
    """Renders a button that invokes a method on the model when clicked. Separate from
    FieldEditor since it has no value to get/set. ModelEditor wires up on_click_fn to
    invoke the named method on the current model."""

    def __init__(
        self,
        method_name: str,
        title: str,
        tooltip: str,
        on_click_fn: Callable[[], None],
    ):
        if not isinstance(method_name, str):
            raise TypeError("method_name must be a str")
        self._method_name = method_name

        if not isinstance(title, str):
            raise TypeError("title must be a str")
        self._title = title

        if not isinstance(tooltip, str):
            raise TypeError("tooltip must be a str")
        self._tooltip = tooltip

        if not isinstance(on_click_fn, Callable):
            raise TypeError("on_click_fn must be a Callable")
        self._on_click_fn = on_click_fn

    @property
    def method_name(self) -> str:
        return self._method_name

    def construct(self) -> None:
        with omni.ui.HStack():
            self._field = omni.ui.Button(
                text=self._title,
                tooltip=self._tooltip,
                clicked_fn=self._on_click_fn,
            )


def _collect_field_editor_types() -> List[Type[FieldEditor]]:
    """Collect all concrete subclasses of FieldEditor."""
    def _get_subclasses(cls):
        result = []
        for subclass in cls.__subclasses__():
            if not getattr(subclass, '__abstractmethods__', None):
                result.append(subclass)
            result.extend(_get_subclasses(subclass))
        return result
    return _get_subclasses(FieldEditor)


def _default_for_field(field_info: FieldInfo):
    """The value to seed a field's widget with when there is no model instance yet.
    Returns the pydantic default (or default_factory() result) when one is declared, or
    None for required fields with no default (the widget then shows its empty value)."""
    if field_info.is_required():
        return None
    if field_info.default_factory is not None:
        return field_info.default_factory()
    return field_info.default


def _editor_type_for(field_info: FieldInfo) -> Optional[Type[FieldEditor]]:
    """The first FieldEditor subclass that accepts this field (or list item), or None."""
    for candidate in _collect_field_editor_types():
        if candidate.matches(field_info.annotation, field_info):
            return candidate
    return None


def _editor_for(
    name: str,
    field_info: FieldInfo,
    on_edited_fn: Callable[["FieldEditor", object, object], None],
    **kwargs,
) -> FieldEditor:
    """Build the editor for one model field or one list item (list items pass a FieldInfo made from the
    item annotation, and a label). Raises when no editor accepts the type."""
    editor_type = _editor_type_for(field_info)
    if editor_type is None:
        raise Exception(f"FieldEditor not found for '{name}' of type {field_info.annotation}")
    return editor_type(name, field_info, on_edited_fn, **kwargs)


def _build_field_editors(
    model_type: Type[BaseModel],
    on_edited_fn: Callable[["FieldEditor", object, object], None],
) -> List[FieldEditor]:
    """Build one FieldEditor per field of model_type, matching each field's annotation to
    the first FieldEditor subclass that accepts it. Used by both ModelEditor and the
    nested _BaseModelFieldEditor (so nesting reuses the exact same matching rules)."""
    return [_editor_for(field_name, field_info, on_edited_fn)
            for field_name, field_info in model_type.model_fields.items()]


def _declaration_order(model_type: Type[BaseModel]) -> dict:
    """Map each field/method name to its source-declaration position, so fields and command
    buttons can be rendered in the order they appear in the model body (pydantic strips
    field assignments from the class __dict__, so this is recovered from the source via AST).
    Walks the MRO base-first (skipping pydantic/object) so inherited members precede subclass
    ones and overrides take their subclass position. Returns {} if the source can't be parsed,
    letting callers fall back to their existing order."""
    order: dict = {}
    idx = 0
    for cls in reversed(model_type.__mro__):
        if cls is object or cls.__module__.split(".")[0] == "pydantic":
            continue
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
        except (OSError, TypeError, SyntaxError):
            continue
        classdef = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
        if classdef is None:
            continue
        for node in classdef.body:
            name = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id  # field: annotated assignment
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name  # method (commands are @model_command-decorated)
            if name is not None:
                order[name] = idx
                idx += 1
    return order


class ModelEditor(Generic[T], Renderable):
    """Owns the model and a list of FieldEditors. On any field edit, aggregates current
    values from all field editors and constructs a new instance of the model class so
    pydantic validates the whole model at once (including cross-field validators).

    On success, the new model replaces self._model and on_edited_fn fires. On failure,
    self._model stays at the last valid state and on_validation_error_fn fires.

    Parameterize with the concrete model class (e.g. ModelEditor[ExampleModel]) so the
    model property and callbacks are typed against that class, not bare BaseModel."""

    def __init__(
        self,
        model: Type[T] | T,
        *,
        on_edited_fn: Optional[Callable[["ModelEditor[T]", Optional["FieldEditor"], T, object], None]] = None,
        on_validation_error_fn: Optional[Callable[["ModelEditor[T]", "FieldEditor", ValidationError], None]] = None,
        on_validate_fn: Optional[Callable[["ModelEditor[T]", "FieldEditor", T], Optional[str]]] = None,
    ):
        if isinstance(model, type) and issubclass(model, BaseModel):
            self._model: Optional[T] = None
            self._model_type: Type[T] = model
        else:
            if not isinstance(model, BaseModel):
                raise TypeError("model must be a Type[BaseModel] | BaseModel")
            self._model = model.model_copy(deep=True) # Don't edit the original model
            self._model_type = type(model)

        if on_edited_fn is not None and not isinstance(on_edited_fn, Callable):
            raise TypeError("on_edited_fn must be a Callable")
        self._on_edited_fn = on_edited_fn

        if on_validation_error_fn is not None and not isinstance(on_validation_error_fn, Callable):
            raise TypeError("on_validation_error_fn must be a Callable")
        self._on_validation_error_fn = on_validation_error_fn

        if on_validate_fn is not None and not isinstance(on_validate_fn, Callable):
            raise TypeError("on_validate_fn must be a Callable")
        self._on_validation_fn = on_validate_fn

        self._field_editors: List[FieldEditor] = _build_field_editors(
            self._model_type, self._on_field_edited
        )

        # Commands are methods marked with @model_command (own class only; not inherited).
        self._command_editors: List[_CommandFieldEditor] = []
        for method_name, method in vars(self._model_type).items():
            if not inspect.isfunction(method):
                continue
            marker: Optional[_ModelCommandMarker] = getattr(method, "__model_command__", None)
            if marker is None:
                continue
            # description falls back to the method docstring when the decorator omitted it.
            tooltip = marker.description if marker.description is not None else (method.__doc__ or "").strip()
            self._command_editors.append(
                _CommandFieldEditor(method_name, marker.title, tooltip, self._make_command_click(method_name))
            )

        # Interleave fields and command buttons in the order they appear in the model body.
        # Unrecognized names (or an unparseable source) sort to the end, preserving the
        # prior fields-then-commands order as a stable fallback.
        order = _declaration_order(self._model_type)
        fallback = len(order) + 1
        self._render_items: List[object] = sorted(
            [*self._field_editors, *self._command_editors],
            key=lambda item: order.get(
                item.field_name if isinstance(item, FieldEditor) else item.method_name,
                fallback,
            ),
        )

    @property
    def model(self) -> Optional[T]:
        return self._model

    @property
    def model_type(self) -> Type[T]:
        return self._model_type

    @model.setter
    def model(self, model: T) -> None:
        if not isinstance(model, BaseModel):
            raise TypeError("model must be a BaseModel")
        if type(model) is not self._model_type:
            raise TypeError(f"model must be a {self._model_type.__name__}")
        self._model = model.model_copy(deep=True)
        for field_editor in self._field_editors:
            field_editor.set_value(getattr(self._model, field_editor.field_name))

    @property
    def title(self) -> str:
        t = self._model_type.__name__
        if self._custom_data_key is not None:
            t += f" ({self._custom_data_key})"
        t += f" @ {self._prim.GetPath()}"
        return getattr(self._model_type, "model_name", t)

    def render(self):
        try:
            for item in self._render_items:
                if not isinstance(item, FieldEditor):
                    item.construct()  # command button
                    continue
                # With no model instance yet (model doesn't exist on the prim), seed each
                # widget with the field's declared default instead of an empty value.
                if self._model is not None:
                    initial_value = getattr(self._model, item.field_name)
                else:
                    initial_value = _default_for_field(self._model_type.model_fields[item.field_name])
                item.construct(initial_value)
        except Exception as ex:
            _logger.debug(f"Error rendering ModelEditor: {ex}", exception=ex)
            raise ex

    def reset_to_defaults(self) -> None:
        """Push each field's pydantic default into its widget (suppressing edit callbacks).
        Required fields with no declared default get reset to the widget's type-appropriate
        empty value via set_value(None). Then try to construct a default model instance;
        if validation fails (e.g., a required field has no default), self._model = None."""
        for field_editor in self._field_editors:
            field_info = self._model_type.model_fields[field_editor.field_name]
            field_editor.set_value(_default_for_field(field_info))

        try:
            self._model = self._model_type()
        except Exception:
            self._model = None
        self._apply_validation_errors(None) # Clear any red labels left over from prior validation.

    def validate(self) -> bool:
        """Read-only validation. Aggregates current widget values and tries to construct
        a model. On failure, fires on_validation_error_fn. Does NOT update self._model
        or fire on_edited_fn — use this when you want to surface schema errors without
        triggering a save. Returns True if the current widget state is valid."""
        values, field_error = self._collect_values()
        if field_error is not None:
            self._apply_validation_errors(field_error)
            if self._on_validation_error_fn is not None:
                field_editor = self._field_editors[0] if self._field_editors else None
                self._on_validation_error_fn(self, field_editor, field_error)
            return False

        try:
            model = self._model_type(**values)
        except ValidationError as ex:
            self._apply_validation_errors(ex)
            if self._on_validation_error_fn is not None:
                # No specific field caused this — pass the first as context.
                field_editor = self._field_editors[0] if self._field_editors else None
                self._on_validation_error_fn(self, field_editor, ex)
            return False
        
        self._apply_validation_errors(None)

        if self._on_validation_fn is not None:
            field_editor = self._field_editors[0] if self._field_editors else None
            try:
                error = self._on_validation_fn(self, field_editor, model)
            except Exception as ex:
                _logger.debug(f"Error executing on_validate_fn: {ex}", exception=ex)
                return False
            if error is not None:
                validation_error = self._make_validation_error(error, field_editor)
                self._apply_validation_errors(validation_error)
                if self._on_validation_error_fn is not None:
                    self._on_validation_error_fn(self, field_editor, validation_error)
                return False

        return True

    def commit(self) -> bool:
        """Validate the current widget state and, if valid, commit it — swapping in the new
        model and firing on_edited_fn (which persists). Unlike validate(), this saves. Use
        it to explicitly save the current fields (e.g. a Save button) without first editing.
        Returns True if committed, False if validation failed (errors are surfaced)."""
        return self._validate_and_commit_from_widgets(None, None)

    def _collect_values(self) -> tuple[dict, Optional[ValidationError]]:
        """Read every field editor's value and gather its per-field errors (via
        collect_errors(), which recurses into nested models), then run pydantic's
        whole-model validation. Returns (values, None) on success, or (partial_values,
        synthetic_error) if anything failed. Field-level errors and pydantic errors are
        merged into a single ValidationError, deduped by full loc path (field-level takes
        precedence) so each invalid field flows through the same on_validation_error_fn /
        _apply_validation_errors plumbing exactly once. Locs are tuples — ("name",) for a
        top-level field, ("nested", "name") for a field inside a nested model."""
        from pydantic_core import PydanticCustomError

        values: dict = {}
        field_errors: list = [] # (loc_tuple, message)
        for fe in self._field_editors:
            try:
                values[fe.field_name] = fe.get_value()
            except Exception:
                pass # Coercion failure; collect_errors() reports it with a loc below.
            field_errors.extend(fe.collect_errors())

        # Run pydantic too so we catch model_validator failures, required-but-missing
        # fields, range violations, etc. — even when field-level errors already exist.
        pydantic_errors: list = []
        try:
            self._model_type(**values)
        except ValidationError as ex:
            pydantic_errors = list(ex.errors())

        if not field_errors and not pydantic_errors:
            return values, None

        field_error_locs = {loc for loc, _ in field_errors}
        line_errors = [
            {
                "type": PydanticCustomError("value_error", msg),
                "loc": loc,
                "input": None,
            }
            for loc, msg in field_errors
        ]
        for err in pydantic_errors:
            loc = tuple(str(p) for p in (err.get("loc") or ()))
            if loc in field_error_locs:
                continue # Already covered by a field-level error for the same field.
            line_errors.append({
                "type": PydanticCustomError("value_error", err.get("msg") or ""),
                "loc": loc,
                "input": err.get("input"),
            })

        synthetic = ValidationError.from_exception_data(self._model_type.__name__, line_errors)
        return values, synthetic

    def _make_validation_error(self, message: str, field_editor: Optional["FieldEditor"] = None) -> ValidationError:
        """Wrap a plain error string from on_validate_fn into a ValidationError so it flows
        through the same on_validation_error_fn / _apply_validation_errors plumbing as
        pydantic errors. Attributes the error to field_editor's field when provided."""
        from pydantic_core import PydanticCustomError

        loc = (field_editor.field_name,) if field_editor is not None else ()
        line_errors = [{
            "type": PydanticCustomError("value_error", message),
            "loc": loc,
            "input": None,
        }]
        return ValidationError.from_exception_data(self._model_type.__name__, line_errors)

    def _apply_validation_errors(self, validation_error: Optional[ValidationError]) -> None:
        """Color invalid field labels red, clear color for valid ones. Pass None to
        clear all invalid markers. Passes the full loc tuple of each error to every field
        editor so nested editors can route the marker to the exact offending sub-field."""
        error_locs: set = set()
        if validation_error is not None:
            for err in validation_error.errors():
                loc = tuple(str(p) for p in (err.get("loc") or ()))
                if loc:
                    error_locs.add(loc)
        for fe in self._field_editors:
            fe.apply_validation_errors(error_locs)

    def _make_command_click(self, method_name: str) -> Callable[[], None]:
        def _on_click():
            try:
                source = self._source_model_for_command()
                # Run the command on a deep copy so in-place mutations can't corrupt the
                # live model; only an explicitly returned model is applied. The bound
                # method's `self` is the copy, which is also passed as the `model` argument.
                working = source.model_copy(deep=True)
                result = getattr(working, method_name)(working)
                if result is None:
                    return  # Command chose to make no change.
                if not isinstance(result, self._model_type):
                    _logger.error(
                        f"Command '{method_name}' must return Optional[{self._model_type.__name__}], "
                        f"got {type(result).__name__}"
                    )
                    return
                self._commit_external_model(result)
            except Exception as ex:
                _logger.error(f"Failed to execute command: {ex}", exception=ex)
        return _on_click

    def _source_model_for_command(self) -> T:
        """The model to hand a command: the saved model if one exists, otherwise one built
        from the current field values WITHOUT validation (via model_construct), filling
        defaults for fields whose widget value couldn't be read. Skipping validation here is
        deliberate — commands like "load demo values" exist precisely to populate empty or
        invalid fields, so they must be able to run from an invalid state. The command's
        returned model is validated in _commit_external_model before anything is saved."""
        if self._model is not None:
            return self._model
        values, _ = self._collect_values()
        return self._model_type.model_construct(**values)

    def _on_field_edited(self, field_editor: FieldEditor, old_value: object, new_value: object) -> None:
        self._validate_and_commit_from_widgets(field_editor, old_value)

    def _commit_external_model(self, new_model: T) -> None:
        """Apply a model produced outside the widgets (a command return value): push its
        values into the fields, then validate and save exactly like a manual edit — only
        persisting if the resulting widget state is valid. If validation fails, the values
        stay in the fields (with red labels) and nothing is saved. field_editor is None
        since the change isn't tied to a single field."""
        for fe in self._field_editors:
            fe.set_value(getattr(new_model, fe.field_name))
        self._validate_and_commit_from_widgets(None, self._model)

    def _validate_and_commit_from_widgets(
        self, field_editor: Optional[FieldEditor], old_value: object
    ) -> bool:
        """Aggregate current widget values + run per-field validate_value() hooks. If any
        field fails its own check (e.g., must_exist on a Path), bail before model
        construction. Then ask pydantic to validate the whole model. On success, commit.
        Returns True if committed, False otherwise."""
        values, field_error = self._collect_values()
        if field_error is not None:
            self._apply_validation_errors(field_error)
            if self._on_validation_error_fn is not None:
                self._on_validation_error_fn(self, field_editor, field_error)
            return False

        try:
            new_model = self._model_type(**values)
        except ValidationError as ex:
            self._apply_validation_errors(ex)
            if self._on_validation_error_fn is not None:
                self._on_validation_error_fn(self, field_editor, ex)
            return False

        return self._commit_model(new_model, field_editor, old_value)

    def _commit_model(
        self,
        new_model: T,
        field_editor: Optional[FieldEditor],
        old_value: object,
    ) -> bool:
        """Run the on_validate_fn business-rule hook, then swap in the validated model and
        fire on_edited_fn. Returns True if committed, False if on_validate_fn rejected it."""
        self._apply_validation_errors(None)

        if self._on_validation_fn is not None:
            try:
                error = self._on_validation_fn(self, field_editor, new_model)
            except Exception as ex:
                _logger.debug(f"Error executing on_validate_fn: {ex}", exception=ex)
                return False
            if error is not None:
                validation_error = self._make_validation_error(error, field_editor)
                self._apply_validation_errors(validation_error)
                if self._on_validation_error_fn is not None:
                    self._on_validation_error_fn(self, field_editor, validation_error)
                return False

        self._model = new_model
        if self._on_edited_fn is not None:
            self._on_edited_fn(self, field_editor, new_model, old_value)
        return True


class ModelEditorPane(Generic[T], Renderable):

    def __init__(
        self,
        prim: pxr.Usd.Prim,
        model_type: Type[T],
        *,
        custom_data_key: Optional[str] = None,
        on_edited_fn: Optional[Callable[["ModelEditor[T]", Optional[FieldEditor], T, object], None]] = None,
        on_validation_error_fn: Optional[Callable[["ModelEditor[T]", FieldEditor, ValidationError], None]] = None,
    ):
        if not isinstance(prim, pxr.Usd.Prim):
            raise TypeError("prim must be a pxr.Usd.Prim")
        self._prim = prim

        if not (isinstance(model_type, type) and issubclass(model_type, BaseModel)):
            raise TypeError("model_type must be a Type[BaseModel]")
        self._model_type: Type[T] = model_type

        if custom_data_key is None:
            custom_data_key = getattr(model_type, "custom_data_key", None)
        if custom_data_key is None or not isinstance(custom_data_key, str):
            raise TypeError("custom_data_key must be defined in the model_type or be a str")
        self._custom_data_key = custom_data_key

        if self._prim.HasCustomDataKey(custom_data_key):
            try:
                model: T = model_type.model_validate_json(self._prim.GetCustomDataByKey(custom_data_key))
                self._editor: ModelEditor[T] = ModelEditor[T](model, on_edited_fn=self._on_edited, on_validation_error_fn=self._on_validation_error)
            except Exception as ex:
                # Failed validation, create new
                self._editor: ModelEditor[T] = ModelEditor[T](model_type, on_edited_fn=self._on_edited, on_validation_error_fn=self._on_validation_error)
        else:
            self._editor: ModelEditor[T] = ModelEditor[T](model_type, on_edited_fn=self._on_edited, on_validation_error_fn=self._on_validation_error)

        if on_edited_fn is not None and not isinstance(on_edited_fn, Callable):
            raise TypeError("on_edited_fn must be an Optional[Callable]")
        self._on_edited_fn = on_edited_fn

        if on_validation_error_fn is not None and not isinstance(on_validation_error_fn, Callable):
            raise TypeError("on_validation_error_fn must be an Optional[Callable]")
        self._on_validation_error_fn = on_validation_error_fn

        # Get the path to the isaac sim installation directory
        ext_manager = omni.kit.app.get_app().get_extension_manager()
        isaacsim_core_path = ext_manager.get_extension_path_by_module("isaacsim.core.simulation_manager")
        isaacsim_install_path = str(Path(isaacsim_core_path).parent.parent.absolute())
        if not (os.path.isfile(os.path.join(isaacsim_install_path, "isaac-sim.sh")) or os.path.isfile(os.path.join(isaacsim_install_path, "isaac-sim.bat"))):
            isaacsim_install_path = ""

        # Get the path to status icons provided by kit if available
        self._good_status_icon_path = f"{isaacsim_install_path}/kit/resources/glyphs/check_solid.svg"
        if not os.path.isfile(self._good_status_icon_path):
            self._good_status_icon_path = ""
        self._bad_status_icon_path = f"{isaacsim_install_path}/kit/resources/glyphs/menu_deactivate.svg"
        if not os.path.isfile(self._bad_status_icon_path):
            self._bad_status_icon_path = ""

    @property
    def editor(self) -> ModelEditor[T]:
        return self._editor

    @property
    def prim(self) -> Optional[pxr.Usd.Prim]:
        return self._prim

    @property
    def custom_data_key(self) -> Optional[str]:
        return self._custom_data_key

    @property
    def title(self) -> str:
        t = getattr(self._model_type, "model_name", self._model_type.__name__)
        return t
        #t = self._model_type.__name__
        if self._custom_data_key is not None:
            t += f" ({self._custom_data_key})"
        t += f" @ {self._prim.GetPath()}"
        return t
        return getattr(self._model_type, "model_name", t)

    def render(self) -> None:
        try:
            style = {**isaacsim.gui.components.ui_utils.get_style(), "Tooltip": _TOOLTIP_STYLE}
            with omni.ui.VStack(style=style, spacing=5, height=0):
                with omni.ui.HStack():

                    self._status_label = omni.ui.Label(
                        "Edit\nStatus",
                        tooltip="The status of edits.",
                        width=50,
                        alignment=omni.ui.Alignment.CENTER,
                    )
                    omni.ui.Spacer(width=10)
                    self._status_field = omni.ui.StringField(
                        tooltip="The status of edits.",
                        multiline=True,
                        height=96,
                        enabled=False,
                    )
                    if not self._prim.HasCustomDataKey(self._custom_data_key):
                        self._status_field.model.set_value("Model does not exist, edit to create it.")

                    if self._good_status_icon_path is not None or self._bad_status_icon_path is not None:
                        omni.ui.Spacer(width=10)
                        self._status_icon = omni.ui.Image(
                            "",
                            tooltip="The status of edits.",
                            width=24,
                            height=96,
                            alignment=omni.ui.Alignment.CENTER,
                            fill_policy=omni.ui.FillPolicy.PRESERVE_ASPECT_FIT,
                        )

                with omni.ui.HStack():

                    def _save():
                        try:
                            # Only meaningful before the model exists; once it does, edits
                            # auto-save, so the button is disabled (guard defensively too).
                            if self._prim.HasCustomDataKey(self._custom_data_key):
                                return
                            # commit() validates the current fields and, if valid, fires
                            # _on_edited (which persists + updates reload/clear/icon). On
                            # failure it surfaces validation errors and saves nothing.
                            if self._editor.commit():
                                self._status_field.model.set_value("Model Saved")
                                self._save_button.enabled = False
                        except Exception as ex:
                            self._status_field.model.set_value(f"Error Saving Model:\n{ex}")
                            if self._bad_status_icon_path is not None:
                                self._status_icon.source_url = self._bad_status_icon_path

                    self._save_button = omni.ui.Button(
                        text="Save",
                        clicked_fn=_save,
                        tooltip="Save the current field values as a new model. Disabled once "
                                "the model exists, since edits then save automatically.",
                    )
                    # Enabled only when the model does not yet exist.
                    self._save_button.enabled = not self._prim.HasCustomDataKey(self._custom_data_key)

                    omni.ui.Spacer(width=10)

                    def _reload():
                        try:
                            json_str = self._prim.GetCustomDataByKey(self._custom_data_key)
                            model = type(self._editor.model).model_validate_json(json_str)
                            self._editor.model = model
                            #self._editor = ModelEditor(model, on_edited_fn=self._on_edited, on_validation_error_fn=self._on_validation_error)

                            self._status_field.model.set_value("Reloaded Model from Custom Properties")
                            self._status_icon.source_url = ""
                            self._reload_button.enabled = True
                            self._save_button.enabled = False  # Model exists; edits auto-save.
                            # Validate after loading — if stored data fails the current schema
                            # (e.g., must_exist paths no longer exist), surface red labels and
                            # the validation error. On success, validate() is silent.
                            self._editor.validate()
                        except Exception as ex:
                            self._status_field.model.set_value(f"Error Reloading Model:\n{ex}")
                            if self._bad_status_icon_path is not None:
                                self._status_icon.source_url = self._bad_status_icon_path

                    self._reload_button = omni.ui.Button(
                        text="Reload",
                        clicked_fn=_reload,
                        # style={
                        #     "Button:disabled": {"background_color": 0xFF333333}, # Color when disabled
                        #     "Button.Label:disabled": {"color": 0xFF666666}, # Color when disabled
                        # },
                    )
                    self._reload_button.enabled = False
                    self._reload_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)

                    omni.ui.Spacer(width=10)

                    def _clear():
                        try:
                            self._editor.reset_to_defaults()
                            self._prim.ClearCustomDataByKey(self._custom_data_key)
                            self._reload_button.enabled = False
                            self._clear_button.enabled = False
                            self._save_button.enabled = True  # Model no longer exists; allow saving again.
                            self._status_field.model.set_value("Cleared Model Custom Properties\nEdit to recreate it.")
                            self._status_icon.source_url = ""
                            # Validate after clearing so the cleared/default state is
                            # re-checked (e.g. required fields now empty) and validation
                            # callbacks fire. On success validate() is silent.
                            self._editor.validate()
                        except Exception as ex:
                            self._status_field.model.set_value(f"Error Reloading Model:\n{ex}")
                            if self._bad_status_icon_path is not None:
                                self._status_icon.source_url = self._bad_status_icon_path

                    self._clear_button = omni.ui.Button(
                        text="Clear",
                        clicked_fn=_clear,
                    )
                    self._clear_button.enabled = False
                    self._clear_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)


                self._editor.render()
                self._editor.validate()
        except Exception as ex:
            _logger.debug(f"Error rendering ModelEditorPane: {ex}", exception=ex)
            raise ex
    

    def _on_edited(self, model_editor: ModelEditor[T], field_editor: Optional[FieldEditor], model: T, old_value: object) -> None:
        if field_editor is None:
            # Command-originated change — not tied to a single field.
            self._status_field.model.set_value("Model updated by command")
        else:
            self._status_field.model.set_value(f"Field '{field_editor.field_info.title}' Value Edited Successfully")

        self._prim.SetCustomDataByKey(self._custom_data_key, model.model_dump_json())
        self._reload_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)
        self._clear_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)
        self._save_button.enabled = not self._prim.HasCustomDataKey(self._custom_data_key)

        if self._good_status_icon_path is not None:
            self._status_icon.source_url = self._good_status_icon_path

        if self._on_edited_fn is not None:
            self._on_edited_fn(model_editor, field_editor, model, old_value)

    def _on_validation_error(self, model_editor: ModelEditor[T], field_editor: FieldEditor, validation_error: ValidationError) -> None:
        self._status_field.model.set_value(str(validation_error))

        self._reload_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)
        self._clear_button.enabled = self._prim.HasCustomDataKey(self._custom_data_key)
        # Invalid field values can never be saved, so keep Save disabled regardless of
        # whether the model exists.
        self._save_button.enabled = False

        if self._bad_status_icon_path is not None:
            self._status_icon.source_url = self._bad_status_icon_path

        if self._on_validation_error_fn is not None:
            self._on_validation_error_fn(model_editor, field_editor, validation_error)


class ModelEditorWindow(ExtensionWindow, Generic[T]):

    def __init__(self, pane: ModelEditorPane[T]):
        title = "Model Editor"
        if pane.prim is not None:
            title += f" | {pane.title}"
            if pane.custom_data_key is not None:
                title += f" ({pane.custom_data_key}) @ "
            title += str(pane.prim.GetPath()) + ")"

        super().__init__(title, height=900, width=1_000) #, flags=omni.ui.WINDOW_FLAGS_MODAL)

        if not isinstance(pane, ModelEditorPane):
            raise TypeError("pane must be a DataEditorPane")
        self._pane: ModelEditorPane[T] = pane

    @property
    def pane(self) -> ModelEditorPane[T]:
        return self._pane

    def construct(self) -> None:
        self._pane.render()


def _example_all_model_values() -> _ExampleAllModel:
    """A valid _ExampleAllModel with every list populated, for the example window."""
    return _ExampleAllModel(
        name="Example",
        selection="A",
        enum=_ExampleEnum.X,
        func_enum=_function_enum()[0],
        items=["first", "second"],
        enabled=True,
        nested=_NestedModel(name="Nested", nested=_NestedNestedModel(name="Inner"), optional_nested=None),
        numbers=[1, 5, 10],
        weights=[0.5, 1.5],
        choices=[_ExampleEnum.X, _ExampleEnum.Z],
        people=[
            _NestedModel(name="Ada", nested=_NestedNestedModel(name="a"), optional_nested=None),
            _NestedModel(name="Grace", nested=_NestedNestedModel(name="g"),
                         optional_nested=_NestedNestedModel(name="h")),
        ],
        matrix=[[1.0, 0.0], [0.0, 1.0]],
        optional_list=["x", "y"],
        optional_name="set",
        optional_count=3,
        optional_enum=_ExampleEnum.Y,
        optional_numbers=[1, None, 3],
    )


class ModelEditorExampleWindow(ExtensionWindow):
    """A test bench for the model editor: edits an in-memory _ExampleAllModel (nothing is written to the
    stage) and shows the validation status and the current model as JSON. In the AMT menu."""

    def __init__(self):
        super().__init__("Model Editor Example", width=900, height=900)
        self._model: Optional[_ExampleAllModel] = None

    def construct(self) -> None:
        style = {**isaacsim.gui.components.ui_utils.get_style(), "Tooltip": _TOOLTIP_STYLE}
        with omni.ui.VStack(style=style, spacing=5, height=0):
            omni.ui.Label(
                "Edits an in-memory example model; nothing is saved to the stage.",
                height=0, style={"color": 0xFF999999},
            )
            with omni.ui.HStack(height=0, spacing=6):
                omni.ui.Button("Reset to Defaults", clicked_fn=self._reset)
                omni.ui.Button("Load Example Values", clicked_fn=self._load_example)
            self._status_field = omni.ui.StringField(multiline=True, height=80, enabled=False)
            self._editor = ModelEditor[_ExampleAllModel](
                self._model if self._model is not None else _ExampleAllModel,
                on_edited_fn=self._on_edited,
                on_validation_error_fn=self._on_validation_error,
            )
            self._editor.render()
            omni.ui.Label("Current model (JSON)", height=0)
            self._json_field = omni.ui.StringField(multiline=True, height=320, enabled=False)
            self._editor.validate()
            self._show_model()

    def _show_model(self) -> None:
        if self._model is None:
            self._json_field.model.set_value("(no valid model yet)")
        else:
            self._json_field.model.set_value(self._model.model_dump_json(indent=2))

    def _reset(self) -> None:
        try:
            self._editor.reset_to_defaults()
            self._model = self._editor.model
            if self._editor.validate():
                self._status_field.model.set_value("Reset to defaults.")
            self._show_model()
        except Exception as ex:
            _logger.error("Failed to reset the example model", exception=ex)

    def _load_example(self) -> None:
        try:
            self._model = _example_all_model_values()
            self._editor.model = self._model
            if self._editor.validate():
                self._status_field.model.set_value("Loaded example values.")
            self._show_model()
        except Exception as ex:
            _logger.error("Failed to load the example model", exception=ex)

    def _on_edited(self, model_editor: ModelEditor[_ExampleAllModel], field_editor: Optional[FieldEditor], model: _ExampleAllModel, old_value: object) -> None:
        self._model = model
        where = f"'{field_editor.field_info.title or field_editor.field_name}'" if field_editor is not None else "the model"
        self._status_field.model.set_value(f"Valid. Last edit: {where}.")
        self._show_model()

    def _on_validation_error(self, model_editor: ModelEditor[_ExampleAllModel], field_editor: FieldEditor, validation_error: ValidationError) -> None:
        self._status_field.model.set_value(str(validation_error))

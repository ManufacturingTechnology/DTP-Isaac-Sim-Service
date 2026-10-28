"""Automation components (no Isaac Sim dependency): the base class the segment engine controls, and
the helpers for building and checking component models (ids, class names, containers, constrained
subclasses, schema fingerprints).

An automation component owns a set of transducers exclusively; its models are ``{Name}Actuals`` (one
field per sensor id), ``{Name}Commands`` (one field per actuator id), and an optional ``{Name}Config``.
The Isaac Sim implementation is ``amt.dtp.ise.transducer.core.AutomationComponent``.
"""
import hashlib
import json
import keyword
import re
import types
import typing
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import models_json_schema


# Names the generated client module (models.py) defines or imports itself. A model class with
# one of these names would shadow them, so it is rejected up front.
GENERATED_MODULE_RESERVED_NAMES = frozenset({
    "Any", "Dict", "List", "Literal", "Optional", "Tuple", "Union",
    "BaseModel", "ConfigDict", "Field",
    "SegmentClient", "CommandFrameBase", "ResponseFrameBase", "CommandResponseBase", "ComponentStateBase",
    "ConfigurationBase", "SegmentError",
    "require_builtins",
    "API_TITLE", "API_DESCRIPTION", "API_VERSION", "SCHEMA_FINGERPRINT", "COMPONENTS",
    # Fixed aliases
    "Client", "CommandFrame", "ResponseFrame", "CommandResponse", "ComponentCommands", "ComponentStates",
    "ComponentConfigs", "Configuration",
})

# JSON Schema keywords that only annotate (documentation), so they don't change what validates.
_ANNOTATION_KEYWORDS = frozenset({"title", "description", "examples"})
# Keywords whose value is a map of name -> schema (the names must be kept).
_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
# Keywords whose value is a schema or a list of schemas.
_SCHEMA_KEYWORDS = frozenset({
    "items", "prefixItems", "anyOf", "oneOf", "allOf", "not", "additionalProperties",
    "if", "then", "else", "contains", "propertyNames",
})


def _strip_annotations(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_strip_annotations(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    result: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _ANNOTATION_KEYWORDS or key.startswith("x-"):
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            result[key] = {name: _strip_annotations(sub) for name, sub in value.items()}
        elif key in _SCHEMA_KEYWORDS:
            result[key] = _strip_annotations(value)
        else:
            result[key] = value
    return result


def schema_fingerprint(*models: Type[BaseModel]) -> str:
    """A fingerprint of the structure of ``models``: sha256 of their canonical JSON Schema with
    annotation-only keywords (title, description, examples, x-*) removed. Documentation edits
    don't change it; changing a field, type, constraint, default, or class name does."""
    _, schema = models_json_schema([(m, "validation") for m in models], ref_template="#/$defs/{model}")
    canonical = json.dumps(_strip_annotations(schema), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def same_structure(a: Type[BaseModel], b: Type[BaseModel]) -> bool:
    """Whether two model classes have the same name and structure (e.g. the models of two
    identical machines). Such classes can share one definition in generated client code."""
    return a.__name__ == b.__name__ and schema_fingerprint(a) == schema_fingerprint(b)

# Attributes of BaseModel; a field with one of these names shadows it (pydantic warns or fails).
_BASE_MODEL_ATTRIBUTES = frozenset(dir(BaseModel))


class ComponentDefinitionError(ValueError):
    """An automation component (or one of its transducers, or a segment API) is defined in a way that
    can't be turned into valid models, API routes, or generated client code. The message lists every
    problem found, each with how to fix it."""

    def __init__(self, subject: str, problems: Sequence[str]):
        self.subject = subject
        self.problems = list(problems)
        if len(self.problems) == 1:
            message = f"{subject}: {self.problems[0]}"
        else:
            lines = [f"{subject} has {len(self.problems)} problems:"]
            for i, problem in enumerate(self.problems, start=1):
                lines.append(f"  {i}. " + problem.replace("\n", "\n     "))
            message = "\n".join(lines)
        super().__init__(message)


class ComponentWriteError(Exception):
    """Writing an automation component's commands (or configuration) failed for one or more
    transducers. Every transducer in the write was attempted: the ones not in ``errors`` were written."""

    def __init__(self, errors: Mapping[str, BaseException]):
        self.errors: Dict[str, BaseException] = dict(errors)
        super().__init__("; ".join(f"{id}: {type(ex).__name__}: {ex}" for id, ex in self.errors.items()))

    @property
    def messages(self) -> Dict[str, str]:
        """``{transducer id: "ExceptionType: message"}``"""
        return {id: f"{type(ex).__name__}: {ex}" for id, ex in self.errors.items()}


def path_to_id(path: Any) -> str:
    """Convert a prim path (or any other string) to a field-name id.

    Every non-word character becomes '_', and leading/trailing underscores are dropped, so
    ``/World/Mill Cell/X-Axis`` becomes ``World_Mill_Cell_X_Axis`` and ``Spherical:0`` becomes
    ``Spherical_0``.
    """
    return re.sub(r"\W", "_", str(path)).strip("_")


def id_problem(id: Any) -> Optional[str]:
    """Why ``id`` can't be used as a model field name (and so as a JSON key and a Python
    attribute in generated client code), or None if it can."""
    if not isinstance(id, str) or len(id) == 0:
        return f"{id!r} is not a non-empty string"
    if not id.isidentifier():
        return f"'{id}' is not a valid Python identifier (letters, digits, and '_' only, not starting with a digit)"
    if keyword.iskeyword(id):
        return f"'{id}' is a Python keyword, so generated client code could not use it as an attribute"
    if id.startswith("_"):
        return f"'{id}' starts with '_', which pydantic treats as a private attribute and silently drops as a field"
    if id.startswith("model_"):
        return f"'{id}' starts with 'model_', which pydantic reserves for its own attributes"
    if id in _BASE_MODEL_ATTRIBUTES:
        return f"'{id}' is the name of a pydantic BaseModel attribute, so the field would shadow it"
    return None


def to_class_name(name: str) -> str:
    """Convert a human-readable name to a class-name prefix: ``Mill Cell`` -> ``MillCell``,
    ``AMT VM-A1`` -> ``AmtVmA1`` (all-caps words are capitalized), ``3-Axis Mill`` -> ``Component3AxisMill``."""
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    class_name = "".join(p.capitalize() if p.isupper() else p[0].upper() + p[1:] for p in parts)
    if not class_name or class_name[0].isdigit():
        class_name = "Component" + class_name
    return class_name


def dynamic_model_name(prefix: str, *parts: str) -> str:
    """Name for a dynamically created model class: ``dynamic_model_name("Mill", "X_Axis", "Commands")``
    -> ``Mill_X_Axis_Commands``."""
    return "_".join([prefix, *parts])


def _numeric_example(constraints: Mapping[str, Any]) -> float:
    """A sensible example value for a constrained number, used by API docs (e.g. Swagger's request
    examples, which otherwise show a bound such as -180): 0 if allowed, otherwise the middle of the
    range, otherwise the nearest bound."""
    lower = constraints.get("ge", constraints.get("gt"))
    upper = constraints.get("le", constraints.get("lt"))
    if (lower is None or lower < 0 or (lower == 0 and "gt" not in constraints)) and \
            (upper is None or upper > 0 or (upper == 0 and "lt" not in constraints)):
        return 0.0
    if lower is not None and upper is not None:
        return (float(lower) + float(upper)) / 2.0
    return float(lower if lower is not None else upper)


def constrained_subclass(
    class_name: str,
    base: Type[BaseModel],
    field_name: str,
    description: Optional[str] = None,
    **constraints: Any,
) -> Type[BaseModel]:
    """A subclass of ``base`` whose ``field_name`` has extra Field constraints (``ge``, ``le``,
    ...), e.g. a joint's limits. Constraints that are None are skipped; with none left, ``base``
    itself is returned.

    The subclass validates ``from_attributes``, so a field typed with it also accepts plain
    ``base`` instances (converted, with the constraints applied). Without that, pydantic rejects an
    instance of a parent class.
    """
    constraints = {k: v for k, v in constraints.items() if v is not None}
    if not constraints:
        return base
    base_field = base.model_fields[field_name]
    examples = None
    if base_field.annotation in (int, float):
        examples = [_numeric_example(constraints)]
    model = create_model(
        class_name,
        __base__=base,
        __doc__=base.__doc__,
        **{field_name: (base_field.annotation, Field(
            title=base_field.title,
            description=description if description is not None else base_field.description,
            examples=examples,
            **constraints,
        ))},
    )
    model.model_config["from_attributes"] = True
    model.model_rebuild(force=True)
    return model


@dataclass
class ModelFieldSpec:
    """One field of an automation component's actuals, commands, or configuration model."""
    model_type: Type[BaseModel]
    description: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def build_container_model(
    class_name: str,
    doc: str,
    fields: Mapping[str, ModelFieldSpec],
) -> Type[BaseModel]:
    """Build a model with one optional field per id. Missing (or null) fields mean "no value":
    for actual values, a sensor without a sample this step; for commands, leave that actuator unchanged.
    Unknown ids are rejected, so a misspelt id is an error rather than an actuator left unchanged."""
    definitions: Dict[str, Any] = {}
    for id, spec in fields.items():
        definitions[id] = (
            Optional[spec.model_type],
            Field(
                None,
                title=id,
                description=spec.description,
                json_schema_extra=dict(spec.extra) if spec.extra else None,
            ),
        )
    return create_model(class_name, __doc__=doc, __config__=ConfigDict(extra="forbid"), **definitions)


def _annotation_models(annotation: Any) -> Iterator[Type[BaseModel]]:
    """Every BaseModel class referenced by a type annotation (Optional, List, Tuple, Union,
    Annotated, ... are unwrapped)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        if arg is Ellipsis or isinstance(arg, (str, int, float, bool, bytes)) or arg is None:
            continue
        yield from _annotation_models(arg)


def model_dependencies(model: Type[BaseModel]) -> List[Type[BaseModel]]:
    """Model classes ``model`` directly depends on: its BaseModel bases (a parametrized generic
    base contributes its origin and type arguments) and the models in its field annotations."""
    deps: List[Type[BaseModel]] = []
    for base in model.__bases__:
        if not (isinstance(base, type) and issubclass(base, BaseModel)) or base is BaseModel:
            continue
        metadata = getattr(base, "__pydantic_generic_metadata__", None) or {}
        if metadata.get("origin") is not None:
            deps.append(metadata["origin"])
            for arg in metadata.get("args", ()):
                deps.extend(_annotation_models(arg))
        else:
            deps.append(base)
    for f in model.model_fields.values():
        deps.extend(_annotation_models(f.annotation))
    return deps


def iter_model_classes(model: Type[BaseModel]) -> Iterator[Type[BaseModel]]:
    """``model`` and every model class reachable from it, each once."""
    seen: Set[int] = set()
    stack = [model]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend(model_dependencies(current))


def models_accepting_unknown_fields(model: Type[BaseModel]) -> List[Type[BaseModel]]:
    """The model classes reachable from ``model`` that don't forbid unknown fields (``extra`` isn't
    ``"forbid"``). In a command, a misspelt field of such a model is silently ignored, so the
    actuator is left unchanged instead of the frame being rejected."""
    return [m for m in iter_model_classes(model) if m.model_config.get("extra") != "forbid"]


def find_class_name_conflicts(
    owners: Iterable[Tuple[str, Type[BaseModel]]],
    reserved: Iterable[str] = (),
    library_classes: Iterable[Type[BaseModel]] = (),
) -> List[str]:
    """Problems for model class names that are used by two different classes, or that are
    reserved. ``owners`` pairs a human-readable owner (e.g. a transducer) with a root model.

    Two classes with the same name and the same structure (e.g. the models of two identical
    machines) are not a conflict: generated client code defines the class once and uses it for both.
    ``library_classes`` are the library's own classes that the generated module imports under their
    reserved names.
    """
    reserved = set(reserved)
    by_name: Dict[str, Dict[int, Tuple[Type[BaseModel], List[str]]]] = {}
    for owner, root in owners:
        for model in iter_model_classes(root):
            classes = by_name.setdefault(model.__name__, {})
            _, model_owners = classes.setdefault(id(model), (model, []))
            if owner not in model_owners:
                model_owners.append(owner)

    problems: List[str] = []
    for name, classes in by_name.items():
        distinct = list(classes.values())
        if len(distinct) > 1 and all(same_structure(distinct[0][0], m) for m, _ in distinct[1:]):
            distinct = distinct[:1]
        if len(distinct) > 1:
            lines = [f"two different model classes are named '{name}':"]
            for model, model_owners in classes.values():
                lines.append(f"  - {model.__module__}.{model.__qualname__}, used by {', '.join(model_owners)}")
            lines.append(
                "The generated client module has one namespace, so model class names must be unique.\n"
                "Fix: rename one of the classes, or give one of the transducers a different name=..."
            )
            problems.append("\n".join(lines))
        elif name in reserved and not any(distinct[0][0] is c for c in library_classes):
            model, model_owners = distinct[0]
            problems.append(
                f"model class '{name}' ({model.__module__}.{model.__qualname__}, used by {', '.join(model_owners)}) "
                f"has a name the generated client module reserves for itself.\nFix: rename the class."
            )
    return problems


class AutomationComponentBase(ABC):
    """What a segment (and anything else that controls a component) needs from an automation
    component: its identity, its actuals and commands models, and reading and writing them.

    The Isaac Sim implementation (``amt.dtp.ise.transducer.core.AutomationComponent``) builds the
    models from its transducers; this base has no Isaac Sim dependency, so the segment can be tested
    against a plain-Python implementation.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def description(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def actual_model(self) -> Type[BaseModel]:
        """Model with one optional field per sensor id (``{Name}Actuals``)."""
        raise NotImplementedError()

    @property
    @abstractmethod
    def command_model(self) -> Type[BaseModel]:
        """Model with one optional field per actuator id (``{Name}Commands``). A missing (or null)
        field means "leave this actuator unchanged" when writing."""
        raise NotImplementedError()

    @abstractmethod
    def sensor_read(self) -> BaseModel:
        """Read every sensor (the actual values), as an instance of :attr:`actual_model`."""
        raise NotImplementedError()

    @abstractmethod
    def actuator_read(self) -> BaseModel:
        """Read back every actuator's commanded targets, as an instance of :attr:`command_model`."""
        raise NotImplementedError()

    @abstractmethod
    def actuator_write(self, value: BaseModel) -> None:
        """Write the commands set in ``value`` (an instance of :attr:`command_model`).

        Implementations should attempt every actuator in ``value`` even if one fails, then raise
        :class:`ComponentWriteError` naming the ones that failed, so a failure is reported per actuator
        rather than silently skipping the actuators after it.
        """
        raise NotImplementedError()

    # Configuration is acyclic: read and written on request (not once per step like process data),
    # and optional, so these have defaults instead of being abstract.

    @property
    def config_model(self) -> Optional[Type[BaseModel]]:
        """Model with one optional field per configurable transducer id, or None (the default) if the
        component has no configuration. Like the other models, it is fixed for the component's
        lifetime: configuration values can change while the simulation plays, the schema can't."""
        return None

    def config_read(self) -> Optional[BaseModel]:
        """Read the configuration, as an instance of :attr:`config_model` (None if there is none)."""
        return None

    def config_write(self, value: BaseModel) -> None:
        """Write the configuration of the transducers set in ``value`` (an instance of
        :attr:`config_model`); the others are left unchanged. Like :meth:`actuator_write`,
        implementations should attempt every transducer and raise :class:`ComponentWriteError`
        naming the ones that failed."""
        raise NotImplementedError(f"automation component '{self.name}' has no configuration")

    @property
    def model_prefix(self) -> str:
        """Prefix of the component-level class names, e.g. ``MillCell`` for ``Mill Cell``."""
        return to_class_name(self.name)

    @property
    def sensor_ids(self) -> List[str]:
        return list(self.actual_model.model_fields)

    @property
    def actuator_ids(self) -> List[str]:
        return list(self.command_model.model_fields)

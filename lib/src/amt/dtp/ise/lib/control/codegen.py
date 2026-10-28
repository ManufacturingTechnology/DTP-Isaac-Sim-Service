"""Render live pydantic model classes as Python source, and fingerprint model schemas.

The renderer works from the live classes rather than from JSON Schema, so it can see bases (a
dynamic subclass of a built-in model is emitted as a subclass of the imported built-in) and the
exact field constraints. It supports the subset of pydantic used by transducer models:
primitives, Optional/Union, List, Tuple, Dict, Literal, nested models, Field metadata
(title, description, examples, json_schema_extra, ge/gt/le/lt, multiple_of, min/max_length,
pattern), and simple defaults. Anything else is reported as a problem instead of being guessed.
"""
import inspect
import math
import types
import typing
import warnings
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple, Type, Union

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .component import model_dependencies, same_structure, schema_fingerprint  # noqa: F401 (re-exported)


# model_config keys that are rendered into generated classes when they differ from the base.
_RENDERED_CONFIG_KEYS = ("from_attributes", "extra", "frozen", "populate_by_name")

# The application named in a generated client module's header. Unset, it is this library; an application that
# embeds the library (the Isaac Sim extension, whose copy of the library carries no version) names itself.
_generator: Optional[Tuple[str, str]] = None


def set_generator(name: str, version: str) -> None:
    """Name the application that generates client modules, as written into their header."""
    global _generator
    _generator = (name, version)


def generator() -> str:
    """``"<name> <version>"`` of the application generating client modules: the one given to
    :func:`set_generator`, else this library."""
    if _generator is not None:
        return f"{_generator[0]} {_generator[1]}"
    try:
        from .. import __version__ as version
    except ImportError:  # an embedded copy whose __init__ was emptied
        version = "(unknown version)"
    return f"AMT DTP Isaac Sim Python Library (amt-dtp-ise-lib) {version}"


def require_builtins(expected: Mapping[str, Tuple[Type[BaseModel], str]]) -> None:
    """Called by generated client modules: warn if a built-in model installed here (from
    amt-dtp-ise-lib) differs from the server's, which means the two lib versions differ."""
    for name, (model, fingerprint) in expected.items():
        actual = schema_fingerprint(model)
        if actual != fingerprint:
            warnings.warn(
                f"Built-in model '{name}' in the installed amt-dtp-ise-lib differs from the server's "
                f"({actual} != {fingerprint}). Install the amt-dtp-ise-lib version that matches the server.",
                stacklevel=2,
            )


@dataclass
class RenderedModels:
    """Result of :func:`render_models`."""
    source: str = ""
    # module path -> names to import from it
    imports: Dict[str, List[str]] = field(default_factory=dict)
    imported_classes: List[Type[BaseModel]] = field(default_factory=list)
    generated_classes: List[Type[BaseModel]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class _Unsupported(Exception):
    pass


class _Renderer:

    def __init__(self, importable: Mapping[str, ModuleType], reserved: Set[str]):
        self._importable = importable
        self._reserved = reserved
        self.result = RenderedModels()
        self._order: List[Type[BaseModel]] = []
        self._visited: Set[int] = set()
        self._names: Dict[str, Type[BaseModel]] = {}

    # ---- discovery ------------------------------------------------------------------------

    def _import_path(self, model: Type[BaseModel]) -> Optional[str]:
        for module_path, module in self._importable.items():
            if getattr(module, model.__name__, None) is model:
                return module_path
        return None

    def visit(self, model: Type[BaseModel]) -> None:
        if id(model) in self._visited:
            return
        self._visited.add(id(model))

        existing = self._names.get(model.__name__)
        if existing is not None and existing is not model:
            if same_structure(existing, model):
                return  # e.g. the models of two identical machines: define the class once
            self.result.problems.append(
                f"two different model classes are named '{model.__name__}' "
                f"({existing.__module__}.{existing.__qualname__} and {model.__module__}.{model.__qualname__}); "
                f"the generated client module has one namespace, so model class names must be unique. "
                f"Fix: rename one of them."
            )
            return
        self._names[model.__name__] = model

        import_path = self._import_path(model)
        if import_path is not None:
            self.result.imports.setdefault(import_path, []).append(model.__name__)
            self.result.imported_classes.append(model)
            return

        if model.__name__ in self._reserved:
            self.result.problems.append(
                f"model class '{model.__name__}' ({model.__module__}.{model.__qualname__}) has a name the "
                f"generated client module reserves for itself. Fix: rename the class."
            )
            return

        for dep in model_dependencies(model):
            self.visit(dep)
        self._order.append(model)

    # ---- rendering ------------------------------------------------------------------------

    def _class_ref(self, model: Type[BaseModel]) -> str:
        metadata = getattr(model, "__pydantic_generic_metadata__", None) or {}
        origin = metadata.get("origin")
        if origin is not None:
            args = ", ".join(self.render_type(a) for a in metadata.get("args", ()))
            return f"{origin.__name__}[{args}]"
        return model.__name__

    def render_type(self, tp: Any) -> str:
        if tp is None or tp is type(None):
            return "None"
        if tp is Any:
            return "Any"
        if tp in (int, float, str, bool, bytes):
            return tp.__name__
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            return self._class_ref(tp)
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)
        if origin is Literal:
            return f"Literal[{', '.join(repr(a) for a in args)}]"
        if origin in (Union, types.UnionType):
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1 and len(args) == 2:
                return f"Optional[{self.render_type(non_none[0])}]"
            return f"Union[{', '.join(self.render_type(a) for a in args)}]"
        if origin is list:
            return f"List[{self.render_type(args[0])}]" if args else "List[Any]"
        if origin is tuple:
            if len(args) == 2 and args[1] is Ellipsis:
                return f"Tuple[{self.render_type(args[0])}, ...]"
            return f"Tuple[{', '.join(self.render_type(a) for a in args)}]" if args else "Tuple[Any, ...]"
        if origin is dict:
            return f"Dict[{self.render_type(args[0])}, {self.render_type(args[1])}]" if args else "Dict[Any, Any]"
        raise _Unsupported(f"type {tp!r} is not supported in generated client code")

    @staticmethod
    def _render_value(value: Any) -> str:
        def check(v: Any) -> None:
            if v is None or isinstance(v, (bool, int, str)):
                return
            if isinstance(v, float):
                if not math.isfinite(v):
                    raise _Unsupported(f"the non-finite value {v!r} can't be represented in JSON")
                return
            if isinstance(v, (list, tuple)):
                for e in v:
                    check(e)
                return
            if isinstance(v, dict):
                for k, e in v.items():
                    if not isinstance(k, str):
                        raise _Unsupported(f"dict key {k!r} is not a str")
                    check(e)
                return
            raise _Unsupported(f"the value {v!r} ({type(v).__name__}) is not supported in generated client code")
        check(value)
        return repr(value)

    def _render_field(self, name: str, info: FieldInfo) -> str:
        annotation = self.render_type(info.annotation)
        args: List[str] = []
        kwargs: List[str] = []
        if info.default_factory is not None:
            if info.default_factory in (list, dict, tuple, set):
                kwargs.append(f"default_factory={info.default_factory.__name__}")
            else:
                raise _Unsupported(f"default_factory {info.default_factory!r} is not supported (use list, dict, or tuple)")
        elif info.default is PydanticUndefined:
            args.append("...")
        else:
            args.append(self._render_value(info.default))
        if info.alias is not None and info.alias != name:
            kwargs.append(f"alias={info.alias!r}")
        if info.title is not None:
            kwargs.append(f"title={info.title!r}")
        if info.description is not None:
            kwargs.append(f"description={info.description!r}")
        if info.examples is not None:
            kwargs.append(f"examples={self._render_value(info.examples)}")
        if info.json_schema_extra is not None:
            if callable(info.json_schema_extra):
                raise _Unsupported("a callable json_schema_extra is not supported")
            kwargs.append(f"json_schema_extra={self._render_value(info.json_schema_extra)}")
        if info.discriminator is not None:
            if not isinstance(info.discriminator, str):
                raise _Unsupported("only string discriminators are supported")
            kwargs.append(f"discriminator={info.discriminator!r}")
        for meta in info.metadata:
            if isinstance(meta, annotated_types.Ge):
                kwargs.append(f"ge={self._render_value(meta.ge)}")
            elif isinstance(meta, annotated_types.Gt):
                kwargs.append(f"gt={self._render_value(meta.gt)}")
            elif isinstance(meta, annotated_types.Le):
                kwargs.append(f"le={self._render_value(meta.le)}")
            elif isinstance(meta, annotated_types.Lt):
                kwargs.append(f"lt={self._render_value(meta.lt)}")
            elif isinstance(meta, annotated_types.MultipleOf):
                kwargs.append(f"multiple_of={self._render_value(meta.multiple_of)}")
            elif isinstance(meta, annotated_types.MinLen):
                kwargs.append(f"min_length={meta.min_length!r}")
            elif isinstance(meta, annotated_types.MaxLen):
                kwargs.append(f"max_length={meta.max_length!r}")
            elif type(meta).__name__ == "_PydanticGeneralMetadata":
                for key, value in vars(meta).items():
                    kwargs.append(f"{key}={self._render_value(value)}")
            else:
                raise _Unsupported(f"field constraint {meta!r} is not supported")
        return f"    {name}: {annotation} = Field({', '.join(args + kwargs)})"

    @staticmethod
    def _model_base(model: Type[BaseModel]) -> Type[BaseModel]:
        bases = [b for b in model.__bases__ if isinstance(b, type) and issubclass(b, BaseModel)]
        if len(bases) != 1:
            raise _Unsupported("multiple inheritance from pydantic models is not supported")
        return bases[0]

    def _lost_behavior(self, model: Type[BaseModel]) -> List[str]:
        """Names of validators, serializers and methods defined on ``model`` itself, which a
        generated copy can't carry."""
        decorators = model.__pydantic_decorators__
        decorator_names = set()
        for group in (decorators.validators, decorators.field_validators, decorators.root_validators,
                      decorators.field_serializers, decorators.model_serializers,
                      decorators.model_validators, decorators.computed_fields):
            decorator_names.update(group.keys())
        lost = [n for n in decorator_names if n in model.__dict__]
        for name, value in model.__dict__.items():
            if name.startswith("_") or name in decorator_names or name in model.model_fields:
                continue
            if callable(value) or isinstance(value, (property, staticmethod, classmethod)):
                lost.append(name)
        return sorted(lost)

    def render_class(self, model: Type[BaseModel]) -> str:
        base = self._model_base(model)
        lines = [f"class {model.__name__}({'BaseModel' if base is BaseModel else self._class_ref(base)}):"]

        doc = model.__dict__.get("__doc__")
        if doc:
            escaped = inspect.cleandoc(doc).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            lines.append('    """' + escaped.replace("\n", "\n    ") + '"""')

        config = {k: model.model_config.get(k) for k in _RENDERED_CONFIG_KEYS
                  if model.model_config.get(k) is not None
                  and model.model_config.get(k) != base.model_config.get(k)}
        if config:
            lines.append(f"    model_config = ConfigDict({', '.join(f'{k}={v!r}' for k, v in config.items())})")

        base_fields = base.model_fields
        for name, info in model.model_fields.items():
            inherited = base_fields.get(name)
            if inherited is not None and repr(inherited) == repr(info):
                continue
            lines.append(self._render_field(name, info))

        if len(lines) == 1:
            lines.append("    pass")

        lost = self._lost_behavior(model)
        if lost:
            self.result.warnings.append(
                f"model '{model.__name__}' ({model.__module__}.{model.__qualname__}) defines {', '.join(repr(n) for n in lost)}, "
                f"which can't be included in the generated client module; clients won't have them (the server "
                f"still runs them). To enforce rules on clients too, express them with Field(ge=..., le=..., "
                f"pattern=...) or Literal[...]."
            )
        return "\n".join(lines)

    def render(self) -> None:
        blocks: List[str] = []
        for model in self._order:
            try:
                blocks.append(self.render_class(model))
                self.result.generated_classes.append(model)
            except _Unsupported as ex:
                self.result.problems.append(
                    f"model '{model.__name__}' ({model.__module__}.{model.__qualname__}) can't be included in the "
                    f"generated client module: {ex}."
                )
        self.result.source = "\n\n\n".join(blocks)


def render_models(
    roots: Sequence[Type[BaseModel]],
    importable: Mapping[str, ModuleType],
    reserved: Set[str] = frozenset(),
) -> RenderedModels:
    """Render ``roots`` and every model they depend on as Python source, in dependency order.

    A class that is exported (as the same object) by one of the ``importable`` modules is
    imported from that module path instead of being rendered. Problems (unsupported types,
    name conflicts) are collected in the result instead of raised, so all of them can be
    reported at once.
    """
    renderer = _Renderer(importable, set(reserved))
    for root in roots:
        renderer.visit(root)
    if not renderer.result.problems:
        renderer.render()
    return renderer.result

"""Transducers (sensors and actuators on prims) and automation components (sets of transducers that one
segment controls; ``AutomationComponent``)."""
import builtins
import inspect
import os
import typing
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, Dict, Generic, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar

from pydantic import BaseModel

import omni
import omni.usd
import pxr
import pxr.Sdf
import pxr.Usd

from ..lib.control.component import (
    GENERATED_MODULE_RESERVED_NAMES,
    AutomationComponentBase,
    ComponentDefinitionError,
    ComponentWriteError,
    ModelFieldSpec,
    build_container_model,
    find_class_name_conflicts,
    id_problem,
    path_to_id,
    to_class_name,
)


T = TypeVar("T", bound=BaseModel)


def _resolve_prim(prim: pxr.Usd.Prim | pxr.Sdf.Path | str) -> pxr.Usd.Prim:
    if isinstance(prim, pxr.Sdf.Path):
        prim = str(prim)
    if isinstance(prim, str):
        stage: pxr.Usd.Stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim)
    if not isinstance(prim, pxr.Usd.Prim):
        raise TypeError("prim must be a pxr.Usd.Prim | pxr.Sdf.Path | str")
    if not prim.IsValid():
        raise ValueError("prim must be valid")
    return prim


class Transducer(ABC):
    """Base of sensors and actuators: a prim, and an optional name.

    The name is the prefix of the transducer's dynamically created model classes (e.g. an
    ArticulationTransducer named ``Mill`` has a command model ``Mill_Commands``). Without a
    name, the prefix comes from the prim path; an AutomationComponent names an unnamed transducer
    after its id.
    """

    def __init__(self, prim: pxr.Usd.Prim | pxr.Sdf.Path | str, name: Optional[str] = None):
        self._prim: pxr.Usd.Prim = _resolve_prim(prim)
        if name is not None:
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError(
                    f"name {name!r} must be a valid Python identifier (letters, digits, and '_'), because it "
                    f"prefixes model class names"
                )
        self._name: Optional[str] = name
        self._dynamic_models: Dict[str, Type[BaseModel]] = {}

    @property
    def prim(self) -> pxr.Usd.Prim:
        return self._prim

    @property
    def name(self) -> Optional[str]:
        return self._name

    @property
    def model_prefix(self) -> str:
        """Prefix of this transducer's dynamically created model class names: its name, or its
        prim path converted to an id (``/World/Mill`` -> ``World_Mill``)."""
        return self._name if self._name is not None else path_to_id(self._prim.GetPath())

    # ---- configuration (acyclic, optional) ---------------------------------------------------

    @property
    def config_model_type(self) -> Optional[Type[BaseModel]]:
        """The model of this transducer's configuration, or None (the default) if it has none.

        Configuration is acyclic: parameters read and changed on request (e.g. drive gains or a
        tool offset) rather than once per simulation step like actual values and commands. Override to return a model class. Return the same class every time: the schema is
        fixed for the transducer's lifetime, only the values change (build a dynamic model with
        ``_dynamic_model("config", build)``). To allow partial writes, give its fields a default of
        None and apply only ``value.model_fields_set`` in :meth:`config_write`.
        """
        return None

    def config_read(self) -> Optional[BaseModel]:
        """The current configuration, an instance of :attr:`config_model_type` (None if it has none)."""
        return None

    def config_write(self, value: BaseModel) -> None:
        """Apply a configuration, an instance of :attr:`config_model_type`. Raise (e.g. ValueError)
        if it can't be applied."""
        raise NotImplementedError(f"{self!r} has no configuration")

    def _assign_default_name(self, name: str) -> None:
        """Name the transducer, unless it already has a name or has already built (and so
        named) its dynamic models."""
        if self._name is None and not self._dynamic_models:
            self._name = name

    def _dynamic_model(self, key: str, build: Callable[[], Type[BaseModel]]) -> Type[BaseModel]:
        """A dynamically created model type, built on first use and cached. Building lazily lets
        an AutomationComponent name the transducer before the model class names are fixed."""
        model = self._dynamic_models.get(key)
        if model is None:
            model = build()
            self._dynamic_models[key] = model
        return model

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self._prim.GetPath()}')"


# --8<-- [start:Sensor]
class Sensor(Transducer, Generic[T]):

    @property
    def actual_model_schema(self) -> Dict[str, Any]:
        return self.actual_model_type.model_json_schema()

    @property
    @abstractmethod
    def actual_model_type(self) -> Type[BaseModel]:
        """The model of the values this sensor measures."""
        raise NotImplementedError()

    @abstractmethod
    def sensor_read(self) -> Optional[T]:
        """Read the actual values (None when no sample is available this step)."""
        raise NotImplementedError()
# --8<-- [end:Sensor]


# --8<-- [start:Actuator]
class Actuator(Transducer, Generic[T]):

    @property
    def command_model_schema(self) -> Dict[str, Any]:
        return self.command_model_type.model_json_schema()

    @property
    @abstractmethod
    def command_model_type(self) -> Type[BaseModel]:
        """The model of the commands this actuator accepts."""
        raise NotImplementedError()

    @abstractmethod
    def actuator_write(self, value: T) -> None:
        """Apply a command (its targets)."""
        raise NotImplementedError()

    @abstractmethod
    def actuator_read(self) -> T:
        """Read back the targets in effect."""
        raise NotImplementedError()
# --8<-- [end:Actuator]


class _TransducerDeclaration:
    """A transducer declared in the body of an AutomationComponent subclass (see :func:`transducer`)."""

    def __init__(self, path: Optional[str], name: Optional[str], description: Optional[str],
                 transducer_type: Optional[type], kwargs: Dict[str, Any]):
        self.path = path
        self.name = name
        self.description = description
        self.transducer_type = transducer_type
        self.kwargs = kwargs
        self.attribute: str = ""


class _TransducerRef:
    def __init__(self, attribute: str):
        self.attribute = attribute


class _AssetPath:
    def __init__(self, relative_path: str):
        self.relative_path = relative_path


def transducer(
    path: Optional[str] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    type: Optional[type] = None,
    **kwargs: Any,
) -> Any:
    """Declare a transducer in the body of an :class:`AutomationComponent` subclass. The attribute name
    is the transducer's id, and the attribute's annotation is its class::

        class MillCell(AutomationComponent):
            mill: ArticulationTransducer = transducer("Mill", name="Mill")
            probe: DistanceSensor = transducer("/World/Shared/Probe", maximum_distance=0.5)

    :param path: The prim path. A path starting with '/' is absolute; any other path is relative
        to the component's ``prim`` (``..`` is allowed, and ``.`` is the prim itself). None for transducers that aren't
        constructed from a prim (e.g. a ToolChanger, which is constructed from its robot).
    :param name: The transducer's name (the prefix of its model class names); defaults to the id.
    :param description: Shown in the API docs for this transducer's field.
    :param type: The transducer class, if the attribute has no annotation.
    :param kwargs: Other constructor arguments. Use :func:`ref` to pass another transducer of
        the component, and :func:`asset` for a file next to the component's module.
    """
    return _TransducerDeclaration(path, name, description, type, kwargs)


def ref(attribute: str) -> Any:
    """Pass another transducer of the same component (declared earlier) as a constructor argument."""
    return _TransducerRef(attribute)


def asset(relative_path: str) -> Any:
    """Pass a file path relative to the module that defines the AutomationComponent subclass."""
    return _AssetPath(relative_path)


class AutomationComponent(AutomationComponentBase):
    """An automation component: a set of sensors and actuators (transducers), each with an id, that it
    owns exclusively.

    Building one builds its actuals model and commands model (``{Name}Actuals``, ``{Name}Commands``): one
    optional field per sensor/actuator id, typed with that transducer's model. These are what the segment
    API serves and what generated client code contains.

    Two ways to define one. With a dict (or a sequence, using converted prim paths as ids)::

        component = AutomationComponent("Mill Cell", items={
            "mill": ArticulationTransducer(f"{self.prim_path}/Mill"),
            "conveyor": ConveyorActuator(f"{self.prim_path}/Conveyor"),
        })

    Or declaratively, which also gives typed attribute access (``cell.mill``)::

        class MillCell(AutomationComponent):
            \"\"\"3-axis mill with an infeed conveyor.\"\"\"   # the description
            name = "Mill Cell"                               # defaults to the class name
            version = "0.2.0"                                # defaults to 0.1.0

            mill: ArticulationTransducer = transducer("Mill")
            conveyor: ConveyorActuator = transducer("../Conveyors/Infeed")

        cell = MillCell(prim=self.prim_path)

    Every problem with the definition (invalid ids, colliding ids or model class names, bad
    paths) is reported at once in a :class:`ComponentDefinitionError`.
    """

    _declared_name: ClassVar[Optional[str]] = None
    _declared_description: ClassVar[Optional[str]] = None
    _declared_version: ClassVar[Optional[str]] = None
    _declarations: ClassVar[Dict[str, _TransducerDeclaration]] = {}
    _declaring_file: ClassVar[Optional[str]] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # name/description/version class attributes would shadow the properties of the same
        # names; move them aside.
        for key in ("name", "description", "version"):
            value = cls.__dict__.get(key)
            if isinstance(value, str):
                setattr(cls, f"_declared_{key}", value)
                delattr(cls, key)
        doc = cls.__dict__.get("__doc__")
        if doc and cls.__dict__.get("_declared_description") is None:
            cls._declared_description = inspect.cleandoc(doc)

        declarations = dict(cls._declarations)
        for attribute, value in list(cls.__dict__.items()):
            if isinstance(value, _TransducerDeclaration):
                value.attribute = attribute
                declarations[attribute] = value
        cls._declarations = declarations
        cls._declaring_file = cls._find_declaring_file()

    @classmethod
    def _find_declaring_file(cls) -> Optional[str]:
        """The file of the module that defines this class (for :func:`asset` paths)."""
        try:
            return inspect.getfile(cls)
        except (TypeError, OSError):
            pass
        # Behavior scripts may be loaded without registering their module in sys.modules; find the
        # module's globals among the frames that are creating this class.
        frame = inspect.currentframe()
        while frame is not None:
            if frame.f_globals.get("__name__") == cls.__module__ and frame.f_globals.get("__file__"):
                return frame.f_globals["__file__"]
            frame = frame.f_back
        return None

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        version: Optional[str] = None,
        items: Optional[Mapping[str, Transducer] | Sequence[Transducer]] = None,
        prim: Optional[pxr.Usd.Prim | pxr.Sdf.Path | str] = None,
    ):
        cls = type(self)
        if isinstance(name, (Mapping, list, tuple)):
            raise TypeError(
                "AutomationComponent's first argument is the component name; pass the transducers as items=..., "
                "e.g. AutomationComponent('Mill', items={'mill': ArticulationTransducer(prim)})"
            )
        if name is None:
            name = cls._declared_name or (cls.__name__ if cls is not AutomationComponent else None)
        if not isinstance(name, str) or not name.strip():
            raise TypeError("name must be a non-empty str")
        if description is None:
            description = cls._declared_description or ""
        if not isinstance(description, str):
            raise TypeError("description must be a str")
        if version is None:
            version = cls._declared_version or "0.1.0"
        if not isinstance(version, str) or not version.strip():
            raise TypeError("version must be a non-empty str")
        self._name = name
        self._description = description
        self._version = version
        subject = f"AutomationComponent '{name}'"

        descriptions: Dict[str, Optional[str]] = {}
        if cls._declarations:
            if items is not None:
                raise TypeError(f"{cls.__name__} declares its transducers in its class body; don't also pass items")
            items = self._build_declared(subject, prim, descriptions)
        elif items is None:
            raise TypeError("items is required: a Dict[str, Sensor | Actuator] or a Sequence[Sensor | Actuator]")

        transducers = self._normalize_items(subject, items)
        for id, t in transducers.items():
            t._assign_default_name(id)

        self._transducers: Dict[str, Transducer] = transducers
        self._sensors: Dict[str, Sensor] = {id: t for id, t in transducers.items() if isinstance(t, Sensor)}
        self._actuators: Dict[str, Actuator] = {id: t for id, t in transducers.items() if isinstance(t, Actuator)}

        def spec(id: str, t: Transducer, model_type: Type[BaseModel]) -> ModelFieldSpec:
            return ModelFieldSpec(model_type, descriptions.get(id), {
                "x-prim-path": str(t.prim.GetPath()),
                "x-transducer-type": f"{type(t).__module__}.{type(t).__qualname__}",
            })

        actual_types = {id: s.actual_model_type for id, s in self._sensors.items()}
        command_types = {id: a.command_model_type for id, a in self._actuators.items()}
        prefix = self.model_prefix
        self._actual_model = build_container_model(
            f"{prefix}Actuals",
            f"Actual (measured) values of {name}, by sensor id. A sensor without a value this step is null.",
            {id: spec(id, self._sensors[id], m) for id, m in actual_types.items()},
        )
        self._command_model = build_container_model(
            f"{prefix}Commands",
            f"Commands of {name}, by actuator id. When writing, actuators that are omitted (or null) are left unchanged.",
            {id: spec(id, self._actuators[id], m) for id, m in command_types.items()},
        )
        # Configuration is optional per transducer; the container only has the configurable ones,
        # and there is none if no transducer is configurable.
        config_types = {id: m for id, t in transducers.items() if (m := t.config_model_type) is not None}
        self._config_model: Optional[Type[BaseModel]] = build_container_model(
            f"{prefix}Config",
            f"Configuration of {name}, by transducer id. When writing, transducers that are omitted (or null) are "
            f"left unchanged.",
            {id: spec(id, transducers[id], m) for id, m in config_types.items()},
        ) if config_types else None

        owners: List[Tuple[str, Type[BaseModel]]] = []
        for id, m in actual_types.items():
            owners.append((f"sensor '{id}' ({self._sensors[id]!r})", m))
        for id, m in command_types.items():
            owners.append((f"actuator '{id}' ({self._actuators[id]!r})", m))
        for id, m in config_types.items():
            owners.append((f"configuration of '{id}' ({transducers[id]!r})", m))
        owners.append((subject, self._actual_model))
        owners.append((subject, self._command_model))
        if self._config_model is not None:
            owners.append((subject, self._config_model))
        # Names a segment API with this component's prefix would give its own models.
        reserved = set(GENERATED_MODULE_RESERVED_NAMES) | {
            f"{prefix}{suffix}" for suffix in ("Client", "CommandFrame", "ResponseFrame", "CommandResponse",
                                               "ComponentCommands", "ComponentStates", "ComponentConfigs",
                                               "Configuration")
        }
        problems = find_class_name_conflicts(owners, reserved)
        if problems:
            raise ComponentDefinitionError(subject, problems)

        if cls._declarations:
            for attribute, t in transducers.items():
                setattr(self, attribute, t)

    def _normalize_items(self, subject: str, items: Any) -> Dict[str, Transducer]:
        problems: List[str] = []
        result: Dict[str, Transducer] = {}

        def check_type(label: str, item: Any) -> bool:
            if not isinstance(item, (Sensor, Actuator)):
                problems.append(f"{label} is a {type(item).__name__}, not a Sensor or Actuator")
                return False
            return True

        if isinstance(items, Mapping):
            for id, item in items.items():
                if not check_type(f"item {id!r}", item):
                    continue
                problem = id_problem(id)
                if problem is not None:
                    problems.append(f"id of {type(item).__name__} at {item.prim.GetPath()}: {problem}.\n"
                                    f"Fix: choose another id.")
                    continue
                result[id] = item
        elif isinstance(items, Sequence) and not isinstance(items, str):
            paths: Dict[str, str] = {}
            for i, item in enumerate(items):
                if not check_type(f"item {i}", item):
                    continue
                path = str(item.prim.GetPath())
                id = path_to_id(path)
                problem = id_problem(id)
                if problem is not None:
                    problems.append(f"id of {type(item).__name__} at {path}: {problem}.\n"
                                    f"Fix: pass the transducers as a dict with explicit ids.")
                    continue
                if id in paths:
                    problems.append(
                        f"'{paths[id]}' and '{path}' both become the id '{id}' (prim paths become ids by replacing "
                        f"'/' with '_').\nFix: pass the transducers as a dict with explicit ids."
                    )
                    continue
                paths[id] = path
                result[id] = item
        else:
            raise TypeError("items must be a Dict[str, Sensor | Actuator] | Sequence[Sensor | Actuator]")

        seen: Dict[int, str] = {}
        for item_id, item in result.items():
            other = seen.get(builtins.id(item))
            if other is not None:
                problems.append(
                    f"{item!r} is listed twice, as '{other}' and '{item_id}'.\nFix: list each transducer once; a "
                    f"transducer that is both a sensor and an actuator appears in both models under one id."
                )
            else:
                seen[builtins.id(item)] = item_id

        if problems:
            raise ComponentDefinitionError(subject, problems)
        return result

    def _build_declared(self, subject: str, prim: Any, descriptions: Dict[str, Optional[str]]) -> Dict[str, Transducer]:
        cls = type(self)
        problems: List[str] = []
        prim_path: Optional[pxr.Sdf.Path] = None
        if prim is not None:
            if isinstance(prim, pxr.Usd.Prim):
                prim_path = prim.GetPath()
            else:
                prim_path = pxr.Sdf.Path(str(prim))
            if prim_path.isEmpty or not prim_path.IsAbsolutePath():
                raise ValueError(f"{cls.__name__}: prim {str(prim)!r} must be an absolute prim path")

        try:
            hints = typing.get_type_hints(cls)
        except Exception:
            hints = {}

        paths: Dict[str, Optional[str]] = {}
        types_: Dict[str, type] = {}
        for attribute, declaration in cls._declarations.items():
            label = f"{cls.__name__}.{attribute}"
            problem = id_problem(attribute)
            if problem is not None:
                problems.append(f"{label}: {problem}.\nFix: rename the attribute.")
            transducer_type = declaration.transducer_type or hints.get(attribute)
            if not (isinstance(transducer_type, type) and issubclass(transducer_type, (Sensor, Actuator))):
                problems.append(
                    f"{label}: can't tell which transducer class to construct (the annotation is "
                    f"{transducer_type!r}).\nFix: annotate the attribute with the transducer class, e.g. "
                    f"'{attribute}: DistanceSensor = transducer(...)', or pass type=..."
                )
            else:
                types_[attribute] = transducer_type
            if declaration.path is None:
                paths[attribute] = None
                continue
            if declaration.path == ".":
                if prim_path is None:
                    problems.append(f"{label}: the path '.' (the component's prim itself) needs a prim.\nFix: "
                                    f"construct {cls.__name__}(prim=...).")
                else:
                    paths[attribute] = str(prim_path)
                continue
            valid = pxr.Sdf.Path.IsValidPathString(declaration.path)
            valid = valid[0] if isinstance(valid, tuple) else bool(valid)
            sdf_path = pxr.Sdf.Path(declaration.path) if valid else pxr.Sdf.Path.emptyPath
            if not valid or sdf_path.isEmpty:
                problems.append(
                    f"{label}: {declaration.path!r} is not a valid USD prim path.\nFix: use a path relative to "
                    f"the component's prim (e.g. 'Probe', or '../Probe' for a sibling of it), or an absolute path (e.g. '/World/Probe')."
                )
                continue
            if sdf_path.IsAbsolutePath():
                paths[attribute] = str(sdf_path)
            elif prim_path is None:
                problems.append(
                    f"{label}: the relative path {declaration.path!r} needs a prim to be relative to.\nFix: construct "
                    f"{cls.__name__}(prim=...) with the prim the relative paths start from, or use an absolute path."
                )
            else:
                paths[attribute] = str(sdf_path.MakeAbsolutePath(prim_path))
        if problems:
            raise ComponentDefinitionError(subject, problems)

        built: Dict[str, Transducer] = {}
        base_dir = os.path.dirname(cls._declaring_file) if cls._declaring_file else os.getcwd()
        for attribute, declaration in cls._declarations.items():
            label = f"{cls.__name__}.{attribute}"
            kwargs: Dict[str, Any] = {}
            for key, value in declaration.kwargs.items():
                if isinstance(value, _TransducerRef):
                    if value.attribute not in built:
                        raise ComponentDefinitionError(subject, [
                            f"{label}: ref({value.attribute!r}) must name a transducer declared before '{attribute}'."
                        ])
                    value = built[value.attribute]
                elif isinstance(value, _AssetPath):
                    value = os.path.join(base_dir, value.relative_path)
                kwargs[key] = value
            transducer_type = types_[attribute]
            args = [] if paths[attribute] is None else [paths[attribute]]
            try:
                instance = transducer_type(*args, **kwargs)
            except Exception as ex:
                where = f" at {paths[attribute]}" if paths[attribute] is not None else ""
                raise ComponentDefinitionError(subject, [
                    f"{label}: constructing {transducer_type.__name__}{where} failed: {type(ex).__name__}: {ex}"
                ]) from ex
            instance._assign_default_name(declaration.name or attribute)
            descriptions[attribute] = declaration.description
            built[attribute] = instance
        return built

    # ---- AutomationComponentBase ----------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def version(self) -> str:
        return self._version

    @property
    def model_prefix(self) -> str:
        """Prefix of the component-level class names: the class name of a declarative component
        (``AmtVmA1`` -> ``AmtVmA1Commands``), otherwise the name converted to a class name. Every
        instance of a declarative component has the same class names, so identical machines share
        them in generated client code."""
        cls = type(self)
        if cls is not AutomationComponent and cls._declarations:
            return cls.__name__
        return to_class_name(self._name)

    @property
    def actual_model(self) -> Type[BaseModel]:
        return self._actual_model

    @property
    def command_model(self) -> Type[BaseModel]:
        return self._command_model

    def sensor_read(self) -> BaseModel:
        """Read every sensor: the actual values, as an instance of :attr:`actual_model`."""
        return self._actual_model(**{id: sensor.sensor_read() for id, sensor in self._sensors.items()})

    def actuator_read(self) -> BaseModel:
        """Read back every actuator's commanded targets, as an instance of :attr:`command_model`."""
        return self._command_model(**{id: actuator.actuator_read() for id, actuator in self._actuators.items()})

    def actuator_write(self, value: BaseModel | Mapping[str, Any]) -> None:
        """Write the commands set in ``value``; actuators that are omitted (or None) are left
        unchanged."""
        if isinstance(value, Mapping):
            value = self._command_model.model_validate(value)
        elif not isinstance(value, self._command_model):
            raise TypeError(f"value must be a {self._command_model.__name__} (or an equivalent dict)")
        # Attempt every actuator even if one fails, so one failure doesn't silently skip the rest, then
        # report the failures by actuator id. The writes aren't transactional: the actuators that
        # succeeded keep their new commands.
        errors: Dict[str, BaseException] = {}
        for id in value.model_fields_set:
            command = getattr(value, id)
            if command is None:
                continue
            try:
                self._actuators[id].actuator_write(command)
            except Exception as ex:
                errors[id] = ex
        if errors:
            raise ComponentWriteError(errors)

    @property
    def config_model(self) -> Optional[Type[BaseModel]]:
        return self._config_model

    def config_read(self) -> Optional[BaseModel]:
        if self._config_model is None:
            return None
        return self._config_model(**{id: self._transducers[id].config_read() for id in self._config_model.model_fields})

    def config_write(self, value: BaseModel | Mapping[str, Any]) -> None:
        """Write the configuration of the transducers set in ``value``; transducers that are omitted
        (or None) are left unchanged. Every transducer is attempted; failures are raised together as
        a ComponentWriteError (not transactional, like :meth:`actuator_write`)."""
        if self._config_model is None:
            raise NotImplementedError(f"automation component '{self._name}' has no configuration")
        if isinstance(value, Mapping):
            value = self._config_model.model_validate(value)
        elif not isinstance(value, self._config_model):
            raise TypeError(f"value must be a {self._config_model.__name__} (or an equivalent dict)")
        errors: Dict[str, BaseException] = {}
        for id in value.model_fields_set:
            config = getattr(value, id)
            if config is None:
                continue
            try:
                self._transducers[id].config_write(config)
            except Exception as ex:
                errors[id] = ex
        if errors:
            raise ComponentWriteError(errors)

    # ---- access -------------------------------------------------------------------------------

    @property
    def transducers(self) -> Dict[str, Transducer]:
        return dict(self._transducers)

    @property
    def sensors(self) -> List[Sensor]:
        return list(self._sensors.values())

    @property
    def actuators(self) -> List[Actuator]:
        return list(self._actuators.values())

    @property
    def actual_model_types(self) -> Dict[str, Type[BaseModel]]:
        return {id: sensor.actual_model_type for id, sensor in self._sensors.items()}

    @property
    def command_model_types(self) -> Dict[str, Type[BaseModel]]:
        return {id: actuator.command_model_type for id, actuator in self._actuators.items()}

    def get_sensor(self, id: str) -> Sensor:
        return self._sensors[id]

    def get_actuator(self, id: str) -> Actuator:
        return self._actuators[id]


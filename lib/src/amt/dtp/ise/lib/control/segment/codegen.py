"""The client module a segment API generates (``GET /api/segment/models.py``): every model a client
needs, a typed client class, and fixed aliases."""
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Type

from pydantic import BaseModel

from ...logging import _logger
from ..codegen import generator, render_models
from ..component import (
    GENERATED_MODULE_RESERVED_NAMES,
    AutomationComponentBase,
    ComponentDefinitionError,
    models_accepting_unknown_fields,
    schema_fingerprint,
)
from ..robot import models as _robot_models
from ..transducer import models as _transducer_models
from . import models as _segment_models
from .models import SegmentModels


# Public modules that generated client code imports built-in classes from (instead of copying
# them, which would lose their methods and validators).
_TRANSDUCER_MODELS_PATH = "amt.dtp.ise.lib.control.transducer.models"
_SEGMENT_MODELS_PATH = "amt.dtp.ise.lib.control.segment.models"
_ROBOT_MODELS_PATH = "amt.dtp.ise.lib.control.robot.models"
IMPORTABLE_MODULES = {
    _TRANSDUCER_MODELS_PATH: _transducer_models,
    _SEGMENT_MODELS_PATH: _segment_models,
    _ROBOT_MODELS_PATH: _robot_models,
}

# The fixed aliases a RobotApi's generated module adds: alias -> RobotModels field.
ROBOT_FIXED_ALIASES = {
    "RobotCommand": "command",
    "RobotResponseFrame": "response_frame",
    "RobotCompileResponse": "compile_response",
}
# Imported into a RobotApi's generated module for client code: (robot models, transducer models).
ROBOT_CONVENIENCE_IMPORTS = (
    ["SetTcp", "PtpMove", "LinMove", "CircMove", "PlanMove", "RobotState", "RobotConfig"],
    ["Vector3", "Quaternion", "Pose", "FramedPose"],
)
ROBOT_RESERVED_NAMES = frozenset({"RobotClient", "RobotCommandBase", "RobotResponseFrameBase", "RobotCompileResponseBase",
                                  *ROBOT_FIXED_ALIASES, *ROBOT_CONVENIENCE_IMPORTS[0], *ROBOT_CONVENIENCE_IMPORTS[1]})


@dataclass
class RobotModels:
    """The RobotAPI models of one RobotApi, e.g. ``MachiningCellRobotCommand``."""
    command: Type[BaseModel]
    response_frame: Type[BaseModel]
    compile_response: Type[BaseModel]

    @property
    def roots(self) -> List[Type[BaseModel]]:
        return [self.command, self.response_frame, self.compile_response]

# The fixed aliases every generated module ends with, whatever the API's title: alias -> SegmentModels field.
FIXED_ALIASES = {
    "CommandFrame": "command_frame",
    "ResponseFrame": "response_frame",
    "CommandResponse": "command_response",
    "ComponentCommands": "component_commands",
    "ComponentStates": "component_states",
    "ComponentConfigs": "component_configs",
    "Configuration": "configuration",
}


def client_class_name(prefix: str) -> str:
    return f"{prefix}Client"


def client_file_name(prefix: str) -> str:
    """``MachiningCell`` -> ``machining_cell.py``, ``AmtVmA1`` -> ``amt_vm_a1.py``"""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", prefix).lower() + ".py"


def generate_client_module(
    prefix: str,
    title: str,
    description: str,
    version: str,
    components: Mapping[str, AutomationComponentBase],
    models: SegmentModels,
    fingerprint: str,
    robot_models: Optional[RobotModels] = None,
) -> str:
    """Python source of a client module for a segment API: every model a client needs, and a typed
    client class. With ``robot_models`` (a RobotApi), also the RobotAPI models, a client class based on
    ``RobotClient``, and the fixed aliases ``RobotCommand``, ``RobotResponseFrame``, ``RobotCompileResponse``.

    Raises :class:`ComponentDefinitionError` if a model can't be represented (every problem is
    listed); logs a warning for model behavior (validators, methods) that can't be included.
    """
    client_name = client_class_name(prefix)
    reserved = set(GENERATED_MODULE_RESERVED_NAMES) | {client_name}
    if robot_models is not None:
        reserved |= ROBOT_RESERVED_NAMES
    rendered = render_models(
        models.roots + (robot_models.roots if robot_models is not None else []),
        IMPORTABLE_MODULES,
        reserved=reserved,
    )
    if rendered.problems:
        raise ComponentDefinitionError(f"Segment API '{title}'", rendered.problems)
    for warning in rendered.warnings:
        _logger.warning(f"Segment API '{title}': {warning}")
    lenient = sorted({m.__name__ for m in models_accepting_unknown_fields(models.command_frame)})
    if lenient:
        _logger.warning(
            f"Segment API '{title}': model(s) {', '.join(repr(n) for n in lenient)} accept unknown fields, so a misspelt "
            f"field in a command frame is silently ignored (that actuator is left unchanged) instead of rejecting "
            f"the batch. Fix: add model_config = ConfigDict(extra=\"forbid\") to each.")

    builtin_checks = sorted(
        (model for model in rendered.imported_classes
         if getattr(_transducer_models, model.__name__, None) is model
         or (getattr(_robot_models, model.__name__, None) is model and not model.__name__.endswith("Base"))),
        key=lambda model: model.__name__,
    )

    lines: List[str] = [
        f"# {client_file_name(prefix)}",
        f"# Client models for the segment API '{title}' v{version}, generated by the API.",
        f"# Generated by {generator()}, part of the AMT DTP Isaac Sim Service (ISS).",
        "# Do not edit: download a fresh copy from GET /api/segment/models.py when the components change.",
        "from typing import Any, Dict, List, Literal, Optional, Tuple, Union",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
        "",
        "from amt.dtp.ise.lib.control.segment.client import SegmentClient",
        "from amt.dtp.ise.lib.control.codegen import require_builtins",
    ]
    if robot_models is not None:
        lines.append("from amt.dtp.ise.lib.control.robot.client import RobotClient")
    imports: Dict[str, List[str]] = {path: list(names) for path, names in rendered.imports.items()}
    if robot_models is not None:  # what robot client code needs to build commands
        for path, names in ((_ROBOT_MODELS_PATH, ROBOT_CONVENIENCE_IMPORTS[0]), (_TRANSDUCER_MODELS_PATH, ROBOT_CONVENIENCE_IMPORTS[1])):
            imports.setdefault(path, []).extend(n for n in names if n not in imports.get(path, []))
    for module_path in sorted(imports):
        lines.append(f"from {module_path} import (")
        lines.extend(f"    {name}," for name in sorted(imports[module_path]))
        lines.append(")")
    lines += [
        "",
        "",
        f"API_TITLE = {title!r}",
        f"API_DESCRIPTION = {description!r}",
        f"API_VERSION = {version!r}",
        f"SCHEMA_FINGERPRINT = {fingerprint!r}",
        "# Component id -> (name, version)",
        "COMPONENTS = {",
    ]
    lines.extend(f"    {id!r}: ({c.name!r}, {c.version!r})," for id, c in components.items())
    lines += ["}", ""]
    if builtin_checks:
        lines.append("# Warns if the installed amt-dtp-ise-lib has different built-in models than the server.")
        lines.append("require_builtins({")
        for model in builtin_checks:
            lines.append(f"    {model.__name__!r}: ({model.__name__}, {schema_fingerprint(model)!r}),")
        lines.append("})")
    lines += ["", ""]
    if rendered.source:
        lines += [rendered.source, "", ""]
    base_client = "RobotClient" if robot_models is not None else "SegmentClient"
    lines += [
        f"class {client_name}({base_client}[{models.component_commands.__name__}, {models.component_states.__name__}]):",
        f'    """Typed client for the {"robot" if robot_models is not None else "segment"} API {title!r}."""',
        "    API_TITLE = API_TITLE",
        "    API_VERSION = API_VERSION",
        "    SCHEMA_FINGERPRINT = SCHEMA_FINGERPRINT",
        f"    commands_model = {models.component_commands.__name__}",
        f"    states_model = {models.component_states.__name__}",
        f"    configs_model = {models.component_configs.__name__}",
        f"    configuration_model = {models.configuration.__name__}",
        f"    command_frame_model = {models.command_frame.__name__}",
        f"    response_frame_model = {models.response_frame.__name__}",
        f"    command_response_model = {models.command_response.__name__}",
    ]
    if robot_models is not None:
        lines += [
            f"    robot_command_model = {robot_models.command.__name__}",
            f"    robot_response_frame_model = {robot_models.response_frame.__name__}",
            f"    robot_compile_response_model = {robot_models.compile_response.__name__}",
        ]
    lines += [
        "",
        "",
        "# Fixed aliases: the same names in every generated module, whatever the API's title, so client",
        "# code doesn't change when the title does. To use several APIs in one program, import each module",
        "# under a name (import machining_cell as cell; cell.CommandFrame) rather than with *.",
        f"Client = {client_name}",
    ]
    lines.extend(f"{alias} = {getattr(models, field).__name__}" for alias, field in FIXED_ALIASES.items())
    if robot_models is not None:
        lines.extend(f"{alias} = {getattr(robot_models, field).__name__}" for alias, field in ROBOT_FIXED_ALIASES.items())
    lines.append("")
    return "\n".join(lines)

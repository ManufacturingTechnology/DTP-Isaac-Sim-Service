import importlib.util
import sys
import warnings

import pytest
from pydantic import BaseModel, Field, create_model, field_validator

from amt.dtp.ise.lib.control.codegen import render_models, require_builtins, schema_fingerprint
from amt.dtp.ise.lib.control.component import ComponentDefinitionError, ModelFieldSpec, build_container_model
from amt.dtp.ise.lib.control.segment.codegen import FIXED_ALIASES, IMPORTABLE_MODULES, client_file_name
from amt.dtp.ise.lib.control.segment.server import SegmentApi
from amt.dtp.ise.lib.control.transducer.models import DistanceModel, RevoluteJointPositionModel, VelocityModel

from .conftest import BeltConfig, FakeCell, FakeLine


def load_module(source: str, tmp_path, name: str):
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cell_api(cell=None, line=None, **kwargs):
    return SegmentApi({"mill": cell or FakeCell(), "line": line or FakeLine()}, title="Machining Cell", **kwargs)


def test_generated_module_imports_and_matches_server_models(tmp_path):
    api = cell_api()
    module = load_module(api.client_source, tmp_path, "machining_cell_generated")

    # Built-ins are imported (same objects), dynamic classes are generated.
    assert module.VelocityModel is VelocityModel
    assert issubclass(module.mill_A1_Commands, RevoluteJointPositionModel)
    assert module.mill_A1_Commands.model_config.get("from_attributes") is True
    assert module.BeltConfig is not BeltConfig and module.ConveyorLineConfig.model_fields["belt"]  # custom: rendered

    # The generated models have exactly the server models' structure.
    assert schema_fingerprint(module.MachiningCellCommandFrame, module.MachiningCellResponseFrame,
                              module.MachiningCellConfiguration) == api.fingerprint
    assert module.SCHEMA_FINGERPRINT == api.fingerprint
    assert module.API_TITLE == "Machining Cell"
    assert module.COMPONENTS == {"mill": ("Mill Cell", "0.1.0"), "line": ("Conveyor Line", "1.2.0")}

    # Limits survive generation and are enforced on the client.
    with pytest.raises(ValueError):
        module.mill_A1_Commands(position=500)

    client_class = module.MachiningCellClient
    assert client_class.commands_model is module.MachiningCellComponentCommands
    assert client_class.command_frame_model is module.MachiningCellCommandFrame
    assert client_class.configs_model is module.MachiningCellComponentConfigs
    assert client_class.configuration_model is module.MachiningCellConfiguration
    frame = module.MachiningCellCommandFrame(frame_id=3, components=module.MachiningCellComponentCommands(
        mill=module.MillCellCommands(mill=module.mill_Commands(A1=RevoluteJointPositionModel(position=10))),
        line=module.ConveyorLineCommands(belt=VelocityModel(velocity=0.2)),
    ))
    dumped = frame.model_dump(exclude_unset=True)
    assert dumped["components"]["mill"]["mill"]["A1"]["position"] == 10
    assert dumped["components"]["line"]["belt"]["velocity"] == 0.2
    # Component states carry the per-component status and typed errors.
    assert {"status", "actuals", "commands", "errors"} <= set(module.MillCellState.model_fields)
    # Only configurable components are in the configuration.
    assert list(module.MachiningCellComponentConfigs.model_fields) == ["line"]


def test_generation_is_deterministic():
    assert cell_api().client_source == cell_api().client_source


def test_single_component_defaults(cell):
    api = SegmentApi(cell)
    assert api.title == "Mill Cell" and api.model_prefix == "MillCell"
    assert list(api.components) == ["Mill_Cell"]
    assert api.client_file_name == "mill_cell.py"
    assert api.command_frame_model.__name__ == "MillCellCommandFrame"


def test_api_models_are_named_after_the_api_and_component_models_after_the_component(cell, tmp_path):
    api = SegmentApi(cell, title="Controller")
    assert api.model_prefix == "Controller"
    assert api.command_frame_model.__name__ == "ControllerCommandFrame"
    assert api.response_frame_model.__name__ == "ControllerResponseFrame"
    assert api.component_commands_model.__name__ == "ControllerComponentCommands"
    assert api.component_states_model.__name__ == "ControllerComponentStates"
    assert api.command_response_model.__name__ == "ControllerCommandResponse"
    assert api.component_configs_model.__name__ == "ControllerComponentConfigs"
    assert api.configuration_model.__name__ == "ControllerConfiguration"
    assert api.client_file_name == "controller.py"
    # Each component's own models (including its state) keep the component's prefix.
    module = load_module(api.client_source, tmp_path, "controller_generated")
    assert {"MillCellActuals", "MillCellCommands", "MillCellState", "ControllerClient"} <= set(vars(module))
    assert module.ControllerClient.command_response_model is module.ControllerCommandResponse
    # Fixed aliases, whatever the title
    assert module.Client is module.ControllerClient
    assert module.CommandFrame is module.ControllerCommandFrame
    assert module.ResponseFrame is module.ControllerResponseFrame
    assert module.CommandResponse is module.ControllerCommandResponse
    assert module.ComponentCommands is module.ControllerComponentCommands
    assert module.ComponentStates is module.ControllerComponentStates
    assert module.ComponentConfigs is module.ControllerComponentConfigs
    assert module.Configuration is module.ControllerConfiguration
    assert set(FIXED_ALIASES) == {"CommandFrame", "ResponseFrame", "CommandResponse", "ComponentCommands",
                                  "ComponentStates", "ComponentConfigs", "Configuration"}


def test_file_name():
    assert client_file_name("MachiningCell") == "machining_cell.py"
    assert client_file_name("AmtVmA1") == "amt_vm_a1.py"


def test_identical_equipment_share_generated_classes(tmp_path):
    # Two identical machines build separate, same-named, same-structure classes; the generated
    # module defines each class once and uses it for both.
    api = SegmentApi({"mill1": FakeCell(), "mill2": FakeCell()}, title="Two Mills")
    assert api.client_source.count("class mill_A1_Commands(") == 1
    assert api.client_source.count("class MillCellState(") == 1
    module = load_module(api.client_source, tmp_path, "two_mills")
    # Each machine's generated state model has that machine's exact structure. (The fingerprint of the
    # whole API differs: on the server, pydantic gives the two same-named classes distinct schema names.
    # Clients compare the server's own fingerprint, embedded in the module, so that doesn't matter.)
    for id in ("mill1", "mill2"):
        assert schema_fingerprint(module.MillCellState) == schema_fingerprint(api.segment.models.states[id])
    assert module.SCHEMA_FINGERPRINT == api.fingerprint


def test_different_components_with_the_same_class_names_conflict():
    other = FakeLine(name="Mill Cell")  # different structure, but its state model is also 'MillCellState'
    with pytest.raises(ComponentDefinitionError, match="two different model classes are named 'MillCellState'"):
        SegmentApi({"a": FakeCell(), "b": other}, title="Clash")


class NamedLikeASegment(FakeCell):
    """Its own commands class has the name the default segment prefix would give the segment's."""

    def __init__(self):
        super().__init__(name="Joint Palooza")
        self._command_model = build_container_model("JointPaloozaComponentCommands", "Commands", {
            "conveyor": ModelFieldSpec(VelocityModel, "Infeed conveyor")})


def test_default_model_prefix_falls_back_when_it_collides_with_a_component():
    # A declarative component class JointPaloozaComponent makes JointPaloozaComponentCommands; so does the
    # default prefix of a segment named "Joint Palooza". The default falls back to {prefix}Segment.
    api = SegmentApi(NamedLikeASegment())
    assert api.model_prefix == "JointPaloozaSegment"
    assert "class JointPaloozaSegmentComponentCommands(" in api.client_source
    # A prefix that is given is never changed.
    with pytest.raises(ComponentDefinitionError, match="pass model_prefix"):
        SegmentApi(NamedLikeASegment(), model_prefix="JointPalooza")


def test_fingerprint_ignores_documentation_but_not_structure():
    a = create_model("Thing", x=(float, Field(title="X", description="old")))
    b = create_model("Thing", x=(float, Field(title="Renamed", description="new", examples=[1.0])))
    c = create_model("Thing", x=(float, Field(ge=0)))
    d = create_model("Thing", x=(int, ...))
    e = create_model("Other", x=(float, ...))
    assert schema_fingerprint(a) == schema_fingerprint(b)
    assert len({schema_fingerprint(a), schema_fingerprint(c), schema_fingerprint(d), schema_fingerprint(e)}) == 4


def test_fingerprint_keeps_property_named_title():
    a = create_model("Doc", title=(str, ...))
    b = create_model("Doc", heading=(str, ...))
    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_unsupported_types_are_reported():
    import numpy as np

    class Weird(BaseModel):
        model_config = {"arbitrary_types_allowed": True}
        data: np.ndarray

    rendered = render_models([Weird], IMPORTABLE_MODULES)
    assert any("not supported" in p for p in rendered.problems)


def test_unsupported_model_fails_api_construction(cell):
    class Bad(BaseModel):
        value: float = float("nan")

    cell._command_model = build_container_model("MillCellCommands", "x", {"bad": ModelFieldSpec(Bad)})
    with pytest.raises(ComponentDefinitionError) as info:
        SegmentApi(cell)
    assert "non-finite" in str(info.value) and "'Bad'" in str(info.value)


def test_lost_validators_are_warned():

    class Gripper(BaseModel):
        width: float

        @field_validator("width")
        @classmethod
        def _check_width(cls, v):
            return v

        def close(self):
            return Gripper(width=0)

    rendered = render_models([Gripper], IMPORTABLE_MODULES)
    assert not rendered.problems
    assert any("_check_width" in w and "close" in w for w in rendered.warnings)


def test_models_accepting_unknown_fields_are_reported(cell, caplog):
    from amt.dtp.ise.lib.control.component import models_accepting_unknown_fields

    class Gripper(BaseModel):  # a custom model that doesn't forbid unknown fields
        width: float

    assert models_accepting_unknown_fields(cell_api().command_frame_model) == []
    cell._command_model = build_container_model("MillCellCommands", "x", {"gripper": ModelFieldSpec(Gripper)})
    api = SegmentApi(cell)
    assert models_accepting_unknown_fields(api.command_frame_model) == [Gripper]
    # Generated client models keep extra="forbid", so the client rejects a misspelt field too.
    assert "extra='forbid'" in api.client_source


def test_require_builtins_warns_on_mismatch():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        require_builtins({"DistanceModel": (DistanceModel, schema_fingerprint(DistanceModel))})
    with pytest.warns(UserWarning, match="DistanceModel"):
        require_builtins({"DistanceModel": (DistanceModel, "sha256:different")})


def test_name_conflict_between_custom_and_builtin(cell):
    DistanceModel2 = create_model("DistanceModel", d=(float, ...))
    cell._actual_model = build_container_model("MillCellActuals", "x", {
        "a": ModelFieldSpec(DistanceModel), "b": ModelFieldSpec(DistanceModel2)})
    with pytest.raises(ComponentDefinitionError, match="two different model classes are named 'DistanceModel'"):
        SegmentApi(cell)


def test_reserved_alias_names_are_rejected(cell):
    # A component model named like a fixed alias would shadow it in the generated module.
    Clash = create_model("CommandFrame", x=(float, ...))
    cell._actual_model = build_container_model("MillCellActuals", "x", {"a": ModelFieldSpec(Clash)})
    with pytest.raises(ComponentDefinitionError, match="reserves for itself"):
        SegmentApi(cell)

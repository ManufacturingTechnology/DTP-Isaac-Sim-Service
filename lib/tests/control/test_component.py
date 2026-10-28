"""Automation component helpers: ids, class names, containers, constrained subclasses."""
import warnings
from typing import Optional

import pytest
from pydantic import BaseModel, create_model

from amt.dtp.ise.lib.control.component import (
    ComponentDefinitionError,
    ModelFieldSpec,
    build_container_model,
    find_class_name_conflicts,
    id_problem,
    iter_model_classes,
    path_to_id,
    to_class_name,
)
from amt.dtp.ise.lib.control.transducer.models import ColorModel, DistanceModel, VelocityModel


@pytest.mark.parametrize("path, expected", [
    ("/World/Mill", "World_Mill"),
    ("/World/Mill Cell/X-Axis", "World_Mill_Cell_X_Axis"),
    ("Spherical:0", "Spherical_0"),
    ("/_Hidden/Probe", "Hidden_Probe"),
])
def test_path_to_id(path, expected):
    assert path_to_id(path) == expected


@pytest.mark.parametrize("id", ["mill", "X_Axis", "conveyor1", "match", "type"])
def test_valid_ids(id):
    assert id_problem(id) is None


@pytest.mark.parametrize("id, fragment", [
    ("", "non-empty"),
    ("3Axis", "not a valid Python identifier"),
    ("Mill Cell", "not a valid Python identifier"),
    ("class", "Python keyword"),
    ("_World_Mill", "private attribute"),
    ("model_x", "reserves"),
    ("json", "BaseModel attribute"),
    ("copy", "BaseModel attribute"),
])
def test_invalid_ids(id, fragment):
    assert fragment in id_problem(id)


def test_every_rejected_id_really_breaks_pydantic_or_python():
    # Leading underscore: pydantic silently drops the field (the reason it is rejected).
    model = create_model("M", **{"_World_Mill": (Optional[VelocityModel], None)})
    assert "_World_Mill" not in model.model_fields
    # BaseModel attribute names: pydantic warns about shadowing.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        create_model("M2", **{"json": (Optional[VelocityModel], None)})
    assert any("shadows an attribute" in str(w.message) for w in caught)


@pytest.mark.parametrize("name, expected", [
    ("Mill Cell", "MillCell"),
    ("mill", "Mill"),
    ("3-Axis Mill", "Component3AxisMill"),
    ("", "Component"),
    ("robotCell", "RobotCell"),
    ("AMT VM-A1", "AmtVmA1"),
    ("AMT MR-A1 Controller", "AmtMrA1Controller"),
])
def test_to_class_name(name, expected):
    assert to_class_name(name) == expected


def test_container_fields_are_optional_and_carry_metadata():
    model = build_container_model("CellCommands", "Commands of Cell", {
        "conveyor": ModelFieldSpec(VelocityModel, "Infeed belt", {"x-prim-path": "/World/Conveyor"}),
        "beacon": ModelFieldSpec(ColorModel),
    })
    value = model(conveyor=VelocityModel(velocity=0.2))
    assert value.beacon is None
    assert value.model_fields_set == {"conveyor"}
    schema = model.model_json_schema()
    assert schema["description"] == "Commands of Cell"
    conveyor = schema["properties"]["conveyor"]
    assert conveyor["description"] == "Infeed belt"
    assert conveyor["x-prim-path"] == "/World/Conveyor"


def test_error_lists_every_problem():
    error = ComponentDefinitionError("automation component 'Cell'", ["first\nwith detail", "second"])
    text = str(error)
    assert "has 2 problems" in text
    assert "1. first\n     with detail" in text
    assert "2. second" in text
    assert str(ComponentDefinitionError("X", ["only"])) == "X: only"


def test_class_name_conflicts(cell):
    a = create_model("Mill_Commands", x=(float, ...))
    b = create_model("Mill_Commands", y=(float, ...))
    problems = find_class_name_conflicts([("mill (A)", a), ("mill2 (B)", b), ("again (A)", a)])
    assert len(problems) == 1
    assert "two different model classes are named 'Mill_Commands'" in problems[0]
    assert "mill (A), again (A)" in problems[0]
    # The same class reused by many owners is fine.
    assert find_class_name_conflicts([("a", DistanceModel), ("b", DistanceModel)]) == []
    # Reserved names are rejected.
    reserved = create_model("Field", x=(float, ...))
    assert "reserves" in find_class_name_conflicts([("x", reserved)], reserved={"Field"})[0]


def test_identical_classes_are_not_a_conflict():
    from amt.dtp.ise.lib.control.component import same_structure
    a = create_model("Mill_Commands", x=(float, ...))
    b = create_model("Mill_Commands", x=(float, ...), __doc__="another machine")
    c = create_model("Mill_Commands", x=(int, ...))
    assert same_structure(a, b) and not same_structure(a, c)
    assert find_class_name_conflicts([("mill1", a), ("mill2", b)]) == []
    assert len(find_class_name_conflicts([("mill1", a), ("mill2", c)])) == 1


def test_iter_model_classes_walks_nested_models(cell):
    names = {m.__name__ for m in iter_model_classes(cell.command_model)}
    assert {"MillCellCommands", "mill_Commands", "mill_A1_Commands", "RevoluteJointPositionModel",
            "RevoluteJointVelocityModel", "VelocityModel", "ColorModel"} <= names


def test_dynamic_limit_subclass_accepts_parent_instances(cell):
    from amt.dtp.ise.lib.control.transducer.models import RevoluteJointPositionModel
    value = cell.mill_commands(A1=RevoluteJointPositionModel(position=10))
    assert type(value.A1) is cell.a1_model
    with pytest.raises(Exception) as info:
        cell.mill_commands(A1=RevoluteJointPositionModel(position=500))
    assert "less than or equal to 90" in str(info.value)


def test_constrained_subclass():
    from amt.dtp.ise.lib.control.component import constrained_subclass
    from amt.dtp.ise.lib.control.transducer.models import PrismaticJointPositionModel
    assert constrained_subclass("X", PrismaticJointPositionModel, "position", ge=None, le=None) is PrismaticJointPositionModel
    limited = constrained_subclass("Mill_X_Commands", PrismaticJointPositionModel, "position", "X in m", ge=-0.5, le=0.5)
    assert issubclass(limited, PrismaticJointPositionModel)
    assert limited.model_fields["position"].description == "X in m"
    assert limited.model_validate(PrismaticJointPositionModel(position=0.1)).position == 0.1
    container = create_model("C", x=(Optional[limited], None))
    assert type(container(x=PrismaticJointPositionModel(position=0.1)).x) is limited
    with pytest.raises(ValueError):
        container(x=PrismaticJointPositionModel(position=1.0))
    # The base class is untouched.
    assert not PrismaticJointPositionModel.model_config.get("from_attributes")
    with pytest.raises(ValueError):
        create_model("C2", x=(limited, ...))(x=object())


def test_color_model_none_semantics():
    assert ColorModel().is_colorless
    assert ColorModel.colorless() == ColorModel()
    assert ColorModel(r=1, g=0, b=0).to_sequence() == [1, 0, 0]
    restored = ColorModel.model_validate_json(ColorModel().model_dump_json())
    assert restored.is_colorless
    with pytest.raises(ValueError):
        ColorModel(r=1)
    with pytest.raises(ValueError):
        ColorModel(r=2, g=0, b=0)


def test_axis_command_needs_exactly_one_of_position_or_velocity():
    from amt.dtp.ise.lib.control.transducer.models import JointAxisActuatorModel
    JointAxisActuatorModel(axis="rotX", position=1)
    JointAxisActuatorModel(axis="rotX", velocity=1)
    for kwargs in ({}, {"position": 1, "velocity": 1}):
        with pytest.raises(ValueError, match="exactly one of position or velocity"):
            JointAxisActuatorModel(axis="rotX", **kwargs)


@pytest.mark.parametrize("constraints, expected", [
    ({"ge": -180, "le": 180}, 0.0),
    ({"ge": 0, "le": 2}, 0.0),
    ({"ge": -0.1, "le": 1}, 0.0),
    ({"ge": 0.5, "le": 2}, 1.25),
    ({"ge": -2, "le": -1}, -1.5),
    ({"gt": 0, "le": 4}, 2.0),
    ({"ge": 3}, 3.0),
    ({"le": -1}, -1.0),
])
def test_numeric_example(constraints, expected):
    from amt.dtp.ise.lib.control.component import _numeric_example
    assert _numeric_example(constraints) == expected


def test_constrained_subclass_has_a_sensible_example():
    from amt.dtp.ise.lib.control.component import constrained_subclass
    from amt.dtp.ise.lib.control.transducer.models import RevoluteJointPositionModel
    limited = constrained_subclass("J1_Commands", RevoluteJointPositionModel, "position", ge=-180, le=180)
    position = limited.model_json_schema()["properties"]["position"]
    assert position["examples"] == [0.0] and position["minimum"] == -180


def test_configuration_is_optional(cell):
    # Configuration (acyclic) is optional, so AutomationComponentBase has defaults rather than abstract members.
    assert cell.config_model is None and cell.config_read() is None
    with pytest.raises(NotImplementedError, match="has no configuration"):
        cell.config_write(None)


def test_joint_commands_and_actuals_share_position_and_velocity():
    from amt.dtp.ise.lib.control.transducer.models import (
        PrismaticJointPositionModel, PrismaticJointVelocityModel, RevoluteJointPositionModel,
        RevoluteJointSensorModel, RevoluteJointVelocityModel)
    assert list(RevoluteJointPositionModel.model_fields) == ["position"]
    assert list(RevoluteJointVelocityModel.model_fields) == ["velocity"]
    assert list(PrismaticJointPositionModel.model_fields) == ["position"]
    assert list(PrismaticJointVelocityModel.model_fields) == ["velocity"]
    assert {"position", "velocity"} <= set(RevoluteJointSensorModel.model_fields)
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        RevoluteJointPositionModel(target_position=1)  # an unknown field is rejected, not ignored

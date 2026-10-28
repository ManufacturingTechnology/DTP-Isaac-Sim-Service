"""Plain-Python automation components (no Isaac Sim) shaped like real ones: a mill cell (a mill
articulation with a position-driven revolute joint with limits, a velocity-driven spindle and a fixed
joint, a conveyor, a status beacon, and a distance probe), and a conveyor line with a configurable belt."""
import socket
import threading
import time
from typing import Dict, Optional, Type

import pytest
from pydantic import BaseModel, ConfigDict, Field, create_model

from amt.dtp.ise.lib.control.component import (
    AutomationComponentBase,
    ComponentWriteError,
    ModelFieldSpec,
    build_container_model,
    dynamic_model_name,
)
from amt.dtp.ise.lib.control.transducer.models import (
    ColorModel,
    DistanceModel,
    FixedJointSensorModel,
    RevoluteJointPositionModel,
    RevoluteJointSensorModel,
    RevoluteJointVelocityModel,
    VelocityModel,
)
from amt.dtp.ise.lib.control.transducer.models import ArticulationActuatorModel, ArticulationSensorModel


def make_mill_models(prefix: str = "mill"):
    a1 = create_model(
        dynamic_model_name(prefix, "A1", "Commands"),
        __base__=RevoluteJointPositionModel,
        position=(float, Field(ge=-90.0, le=90.0, title="Position",
                                      description="A1 target position in degrees")),
    )
    a1.model_config["from_attributes"] = True
    a1.model_rebuild(force=True)
    commands = create_model(
        dynamic_model_name(prefix, "Commands"),
        __base__=ArticulationActuatorModel,  # like ArticulationTransducer's models (which forbid unknown fields)
        __doc__="Commands of the mill articulation. Omitted joints are left unchanged.",
        A1=(Optional[a1], None),
        Spindle=(Optional[RevoluteJointVelocityModel], None),
    )
    actuals = create_model(
        dynamic_model_name(prefix, "Actuals"),
        __base__=ArticulationSensorModel,
        __doc__="Measured state of the mill articulation.",
        A1=(RevoluteJointSensorModel, ...),
        Spindle=(RevoluteJointSensorModel, ...),
        Vise=(FixedJointSensorModel, ...),
    )
    return a1, commands, actuals


class FakeCell(AutomationComponentBase):

    def __init__(self, name: str = "Mill Cell", version: str = "0.1.0", fail_on_write: bool = False):
        self._name = name
        self._version = version
        self.fail_on_write = fail_on_write
        self.a1_model, self.mill_commands, self.mill_actuals = make_mill_models()
        self._actual_model = build_container_model(
            "MillCellActuals", "Actual values of Mill Cell", {
                "mill": ModelFieldSpec(self.mill_actuals, "The mill", {"x-prim-path": "/World/MillCell/Mill"}),
                "probe": ModelFieldSpec(DistanceModel, "Part probe"),
            })
        self._command_model = build_container_model(
            "MillCellCommands", "Commands of Mill Cell", {
                "mill": ModelFieldSpec(self.mill_commands, "The mill"),
                "conveyor": ModelFieldSpec(VelocityModel, "Infeed conveyor"),
                "beacon": ModelFieldSpec(ColorModel, "Stack light"),
            })
        self.a1 = 0.0
        self.spindle = 0.0
        self.conveyor = 0.0
        self.beacon = ColorModel()
        self.writes = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "A fake mill cell for tests"

    @property
    def version(self) -> str:
        return self._version

    @property
    def actual_model(self) -> Type[BaseModel]:
        return self._actual_model

    @property
    def command_model(self) -> Type[BaseModel]:
        return self._command_model

    def sensor_read(self) -> BaseModel:
        joint = dict(drive_mode="position", velocity=0.0, effort=0.0, forces=(0, 0, 0), torques=(0, 0, 0))
        return self._actual_model(
            mill=self.mill_actuals(
                A1=RevoluteJointSensorModel(position=self.a1, **joint),
                Spindle=RevoluteJointSensorModel(**{**joint, "drive_mode": "velocity", "position": 0.0, "velocity": self.spindle}),
                Vise=FixedJointSensorModel(forces=(0, 0, 1), torques=(0, 0, 0)),
            ),
            probe=DistanceModel(distance=0.25),
        )

    def actuator_read(self) -> BaseModel:
        return self._command_model(
            mill=self.mill_commands(
                A1=self.a1_model.model_construct(position=self.a1),
                Spindle=RevoluteJointVelocityModel(velocity=self.spindle),
            ),
            conveyor=VelocityModel(velocity=self.conveyor),
            beacon=self.beacon,
        )

    def actuator_write(self, value: BaseModel) -> None:
        if self.fail_on_write:
            raise RuntimeError("actuator fault")
        self.writes.append(value)
        for id in value.model_fields_set:
            v = getattr(value, id)
            if v is None:
                continue
            if id == "mill":
                if v.A1 is not None:
                    self.a1 = v.A1.position
                if v.Spindle is not None:
                    self.spindle = v.Spindle.velocity
            elif id == "conveyor":
                self.conveyor = v.velocity
            elif id == "beacon":
                self.beacon = v


class BeltConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_velocity: Optional[float] = Field(None, gt=0, description="Velocity limit in m/s.")
    reversed: Optional[bool] = None


class FakeLine(AutomationComponentBase):
    """A second kind of component: a conveyor line with a probe and a configurable belt."""

    def __init__(self, name: str = "Conveyor Line"):
        self._name = name
        self._actual_model = build_container_model("ConveyorLineActuals", "Actual values of the line", {
            "probe": ModelFieldSpec(DistanceModel, "Part probe")})
        self._command_model = build_container_model("ConveyorLineCommands", "Commands of the line", {
            "belt": ModelFieldSpec(VelocityModel, "Belt")})
        self._config_model = build_container_model("ConveyorLineConfig", "Configuration of the line", {
            "belt": ModelFieldSpec(BeltConfig, "Belt")})
        self.belt = 0.0
        self.belt_config = {"max_velocity": 1.0, "reversed": False}
        self.fail_on_write = False
        self.fail_on_read = False

    name = property(lambda self: self._name)
    description = property(lambda self: "A fake conveyor line")
    version = property(lambda self: "1.2.0")
    actual_model = property(lambda self: self._actual_model)
    command_model = property(lambda self: self._command_model)
    config_model = property(lambda self: self._config_model)

    def sensor_read(self) -> BaseModel:
        if self.fail_on_read:
            raise RuntimeError("probe offline")
        return self._actual_model(probe=DistanceModel(distance=0.5))

    def actuator_read(self) -> BaseModel:
        return self._command_model(belt=VelocityModel(velocity=self.belt))

    def actuator_write(self, value: BaseModel) -> None:
        if self.fail_on_write == "transducer":
            raise ComponentWriteError({"belt": RuntimeError("belt jammed")})
        if self.fail_on_write:
            raise RuntimeError("belt jammed")
        if value.belt is not None:
            self.belt = value.belt.velocity

    def config_read(self) -> BaseModel:
        return self._config_model(belt=BeltConfig(**self.belt_config))

    def config_write(self, value: BaseModel) -> None:
        belt = value.belt
        if belt is None:
            return
        if belt.reversed:
            raise ComponentWriteError({"belt": ValueError("reversing is not supported")})
        for field in belt.model_fields_set:
            self.belt_config[field] = getattr(belt, field)


@pytest.fixture
def cell() -> FakeCell:
    return FakeCell()


@pytest.fixture
def line() -> FakeLine:
    return FakeLine()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stepper:
    """Calls server.step() on a background thread, like a simulation would."""

    def __init__(self, server, dt: float = 0.01):
        self.server = server
        self.dt = dt
        self.time = 0.0
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            if not self._paused.is_set():
                self.time += self.dt
                self.server.step(self.dt, timeline_time=self.time)
            time.sleep(self.dt)

    def start(self):
        self._thread.start()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2.0)


@pytest.fixture
def running_server(cell, line):
    """A segment API serving two components, 'mill' and 'line'."""
    from amt.dtp.ise.lib.control.segment.server import SegmentApi
    api = SegmentApi({"mill": cell, "line": line}, title="Machining Cell", port=free_port(),
                     host="127.0.0.1", max_queue_length=50, subscriber_queue_size=8, buffer_size=20)
    api.start()
    stepper = Stepper(api)
    yield api, stepper
    stepper.stop()
    api.stop()

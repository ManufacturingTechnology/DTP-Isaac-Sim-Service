"""The RobotAPI and its kinematics routes against a fake solver and a fake robot:
batch IK with continuity seeding, compile → segment frames, all-or-nothing rejection, the jump check, the
start-state prediction, the engine's optimistic queueing, the routes end to end, the robot stream, the
generated client module, and the kinematics routes it serves for its robots (the standalone kinematics
service is tested in test_kinematics_service.py)."""
import importlib
import math
import sys
import time

import numpy as np
import pytest

from amt.dtp.ise.lib.control.core.client import CoreApiError
from amt.dtp.ise.lib.control.motion.client import KinematicsClient
from amt.dtp.ise.lib.control.motion.kinematics import KinematicsError
from amt.dtp.ise.lib.control.robot.client import RobotClient
from amt.dtp.ise.lib.control.robot.compiler import RobotBinding, RobotCompileError
from amt.dtp.ise.lib.control.robot.models import LinMove, PtpMove, SetTcp
from amt.dtp.ise.lib.control.robot.server import RobotApi
from amt.dtp.ise.lib.control.segment.client import download_client_module
from amt.dtp.ise.lib.control.segment.engine import QueueChangedError, SegmentHaltedError
from amt.dtp.ise.lib.control.segment.models import HaltInfo
from amt.dtp.ise.lib.control.transducer.models import Pose, Quaternion, Vector3

from .conftest import FakeLine, Stepper, free_port
from .robot_fakes import FakeRobot, FakeSolver

DT = 0.01


def pose(x, y, z, q=None) -> Pose:
    return Pose(position=Vector3(x=x, y=y, z=z), orientation=q or Quaternion.identity())


def make_api(**binding):
    robot, line = FakeRobot(), FakeLine()
    solver = FakeSolver()
    api = RobotApi({"robot": robot, "line": line}, robots={"arm": RobotBinding(solver, component="robot", **binding)},
                   title="Gantry Cell", port=free_port(), host="127.0.0.1", buffer_size=50)
    return api, robot, line, solver


def run(api, steps):
    for _ in range(steps):
        api.step(DT)


def joint_rows(frames, component="robot"):
    """The joints of each compiled frame dict (degrees / meters), None where the frame has none."""
    rows = []
    for f in frames:
        joints = ((f.get("components") or {}).get(component) or {}).get("joints")
        rows.append(None if joints is None else {k: v["position"] for k, v in joints.items()})
    return rows


@pytest.fixture
def api():
    api, robot, line, solver = make_api()
    api.step(DT)  # a first step: the start state and the physics step are known
    return api


def compile_frames(api, commands):
    return [f.model_dump(mode="json", exclude_unset=True) for f in api.compile(commands).frames]


# ---- the solver base class --------------------------------------------------------------------------

def test_batch_ik_is_continuous_and_measures_its_errors():
    solver = FakeSolver()
    q = np.array([[0.1 * i, 0.0, 0.5, 0.2 * i, 0.1, 3.0 - 0.1 * i] for i in range(10)])
    positions, orientations = solver.fk(q)
    solution = solver.ik(positions, orientations, seed=q[0])
    assert solution.all_success and np.allclose(solution.q, q)  # Roll stays near 3 rad: no wrap to -π
    assert solution.position_error.max() < 1e-9 and solution.orientation_error.max() < 1e-6
    positions[4] = [5.0, 0, 0]  # unreachable
    solution = solver.ik(positions, orientations, seed=q[0])
    assert solution.first_failure() == 4 and not solution.success[4] and solution.success[5]


def test_unwrap_prefers_the_equivalent_nearest_the_seed():
    solver = FakeSolver()
    q = np.array([0, 0, 0, -1.0, 0, 0.0])
    assert np.isclose(solver.unwrap(q, np.array([0, 0, 0, 5.0, 0, 0]))[3], -1.0 + 2 * math.pi)
    q = np.array([0, 0, 0, 0.1, 0, 0.0])  # 0.1 + 2π would exceed the 2π limit: unchanged
    assert np.isclose(solver.unwrap(q, np.array([0, 0, 0, 6.2, 0, 0]))[3], 0.1)
    assert np.isclose(solver.unwrap(np.array([0, 0, 0, 0, 0, 6.0]), np.zeros(6))[5], 6.0 - 2 * math.pi)


def test_frames_and_unknown_names():
    solver = FakeSolver()
    solver.set_frame("tcp", "tool0", [0, 0, 0.1])
    assert solver.tool_frames == ["tool0", "tcp"] and solver.revision == 1
    p, _ = solver.fk(np.zeros(6), "tcp")
    assert np.allclose(p[0], [0, 0, 0.1])
    with pytest.raises(KinematicsError) as caught:
        solver.fk(np.zeros(6), "nope")
    assert caught.value.code == "UNKNOWN_FRAME"
    with pytest.raises(KinematicsError):
        solver.joints_from_wire({"X": 0.1, "Bogus": 1})


# ---- compiling ------------------------------------------------------------------------------------------

def test_set_is_one_frame_in_wire_units(api):
    target = pose(0.0, 0.0, 0.5, Quaternion.from_axis_angle([0, 0, 1], math.radians(1.0)))
    frames = compile_frames(api, [{"frame_id": 4, "robots": {"arm": SetTcp(target=target)}}])
    assert len(frames) == 1 and frames[0]["frame_id"] == 4
    joints = joint_rows(frames)[0]
    assert math.isclose(joints["Yaw"], 1.0, abs_tol=1e-9) and math.isclose(joints["Z"], 0.5)


def test_ptp_frames_carry_the_frame_id_and_respect_the_limits(api):
    response = api.compile([{"frame_id": 9, "robots": {"arm": PtpMove(joints={"X": 0.4, "Yaw": 90.0})}}])
    frames = [f.model_dump(mode="json", exclude_unset=True) for f in response.frames]
    rows = joint_rows(frames)
    assert {f["frame_id"] for f in frames} == {9}
    assert response.commands[0].steps == len(frames) and response.commands[0].first_step == 0
    assert math.isclose(rows[-1]["X"], 0.4) and math.isclose(rows[-1]["Yaw"], 90.0)
    x = np.array([0.0] + [r["X"] for r in rows])
    yaw = np.array([0.0] + [r["Yaw"] for r in rows])
    assert np.abs(np.diff(x)).max() <= 0.5 * DT + 1e-12  # 0.5 m/s
    assert np.abs(np.diff(yaw)).max() <= math.degrees(2.0) * DT + 1e-9  # 2 rad/s
    # X (0.4 m at 0.5 m/s, 2 m/s²) needs longer than Yaw (90° at 2 rad/s, 10 rad/s²): 1/1.25 + 1.25/5 = 1.05 s
    assert math.isclose(response.commands[0].duration, 1.05)


def test_lin_follows_a_straight_line(api):
    response = api.compile([{"robots": {"arm": LinMove(target=pose(0.3, 0.2, 0.5), velocity=0.5, acceleration=None)}}])
    rows = joint_rows([f.model_dump(mode="json", exclude_unset=True) for f in response.frames])
    xy = np.array([[r["X"], r["Y"]] for r in rows])
    assert np.allclose(xy[:, 1] / xy[:, 0], 2 / 3)
    assert math.isclose(response.commands[0].duration, math.ceil(math.hypot(0.3, 0.2) / 0.5 / DT) * DT)


def test_ptp_to_a_tcp_target_and_world_reference():
    api, *_ = make_api(base_pose=lambda: pose(1.0, 0.0, 0.0))
    api.step(DT)
    frames = compile_frames(api, [{"robots": {"arm": PtpMove(target=pose(1.2, 0.0, 0.5), reference="world")}}])
    assert math.isclose(joint_rows(frames)[-1]["X"], 0.2)
    api2, *_ = make_api()
    api2.step(DT)
    with pytest.raises(RobotCompileError) as caught:
        api2.compile([{"robots": {"arm": PtpMove(target=pose(0.2, 0, 0.5), reference="world")}}])
    assert caught.value.code == "REFERENCE_UNAVAILABLE"


def test_robots_hold_passthrough_goes_first_and_dwell_adds_steps(api):
    frames = compile_frames(api, [
        {"frame_id": 1, "robots": {"arm": PtpMove(joints={"X": 0.05})}, "components": {"line": {"belt": {"velocity": 0.3}}},
         "dwell": 0.05},
        {"frame_id": 2},  # an empty command: one delay step
    ])
    moving = [f for f in frames if f["frame_id"] == 1 and "components" in f and "robot" in f["components"]]
    assert frames[0]["components"]["line"] == {"belt": {"velocity": 0.3}}
    assert all("line" not in f.get("components", {}) for f in frames[1:])
    held = [f for f in frames if f["frame_id"] == 1 and "components" not in f]
    assert len(held) == 5  # 0.05 s of dwell at 0.01 s
    assert frames[-1] == {"frame_id": 2}
    assert len(moving) + len(held) + 1 == len(frames)


def test_passthrough_to_a_moving_robots_joints_is_a_conflict(api):
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.05})},
                      "components": {"robot": {"joints": {"Y": {"position": 0.1}}}}}])
    assert caught.value.code == "CONFLICT"


def test_a_batch_is_rejected_whole(api):
    before = api.segment.queue_length
    with pytest.raises(RobotCompileError) as caught:
        api.write([{"robots": {"arm": LinMove(target=pose(0.1, 0, 0.5))}},
                   {"robots": {"arm": LinMove(target=pose(3.0, 0, 0.5))}}])  # X can't reach 3 m
    error = caught.value
    assert error.code == "IK_UNREACHABLE" and error.command == 1 and error.robot == "arm" and error.step is not None
    assert api.segment.queue_length == before  # nothing queued


@pytest.mark.parametrize("move,code", [
    (PtpMove(joints={"Pitch": 120.0}), "JOINT_LIMIT"),
    (PtpMove(joints={"X": 0.5}, duration=0.1), "DURATION_TOO_SHORT"),
    (SetTcp(target=pose(0.5, 0, 0.5)), "JOINT_JUMP"),  # 0.5 m in one 0.01 s step
    (SetTcp(target=pose(0, 0, 0.5), tool_frame="nope"), "UNKNOWN_FRAME"),
    (PtpMove(joints={"Bogus": 1.0}), "UNKNOWN_JOINT"),
])
def test_rejections(api, move, code):
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": move}}])
    assert caught.value.code == code
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"nobody": move}}])
    assert caught.value.code == "UNKNOWN_ROBOT"


def test_the_jump_check_catches_a_wrist_flip_through_the_singularity(api):
    # Rotating about Y from 80° to 100° passes Pitch = 90° (the limit): IK can only follow by flipping Yaw and
    # Roll by 180°, which the jump check rejects, naming the step and joint.
    y = [0, 1, 0]
    api.write([{"robots": {"arm": PtpMove(joints={"Pitch": 80.0})}}])
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": LinMove(target=pose(0, 0, 0.5, Quaternion.from_axis_angle(y, math.radians(100))))}}])
    assert caught.value.code == "JOINT_JUMP" and caught.value.joint in ("Yaw", "Roll")
    assert "singularity" in caught.value.message


def test_velocity_limit_sources_and_the_fallback():
    solver = FakeSolver(velocity=1e6)  # a placeholder, like the MR-A1's URDF
    binding = RobotBinding(solver, component="robot", velocity_limits={"X": 0.1, "Roll": 90.0}, max_step_revolute=2.0)
    assert binding.velocity_limit(0) == (0.1, "override")  # wire units: m/s
    assert binding.velocity_limit(5) == (math.radians(90.0), "override")  # wire units: deg/s
    assert binding.velocity_limit(1) == (0.5, "urdf")
    assert binding.velocity_limit(3) == (None, "fallback")
    steps = binding.max_steps(DT)
    assert math.isclose(steps[0], 0.1 * DT) and math.isclose(steps[3], math.radians(2.0))


# ---- the start state and the engine ---------------------------------------------------------------------

def test_the_start_is_the_end_of_the_queue_including_segment_writes(api):
    api.enqueue([{"components": {"robot": {"joints": {"X": {"position": 0.3}}}}}])  # queued, not applied
    frames = compile_frames(api, [{"robots": {"arm": PtpMove(joints={"Y": 0.1})}}])
    assert all(math.isclose(r["X"], 0.3) for r in joint_rows(frames))


def test_optimistic_queueing(api):
    engine = api.segment
    revision = engine.queue_revision
    engine.enqueue([{}])
    with pytest.raises(QueueChangedError):
        engine.enqueue([{}], expected_revision=revision)
    future = engine.submit_many([{}, {}, {"frame_id": 3}])
    assert engine.queue_length == 4
    assert engine.withdraw(future) and engine.queue_length == 1  # all three of the batch
    latest, pending, _ = engine.prediction_snapshot()
    assert latest is not None and len(pending) == 1


def test_execute_while_halted_and_writes_that_raced(api):
    api.segment._halt = HaltInfo(sequence=1)  # simulate a halt
    with pytest.raises(SegmentHaltedError):
        api._submit({"robots": {"arm": PtpMove(joints={"X": 0.01})}})
    api.segment._halt = None
    original = api.segment.enqueue
    calls = []

    def racing_enqueue(frames, expected_revision=None):
        calls.append(expected_revision)
        if len(calls) == 1:
            original([{}])  # another client wrote meanwhile
        return original(frames, expected_revision=expected_revision)

    api.segment.enqueue = racing_enqueue
    try:
        answer = api.write([{"robots": {"arm": PtpMove(joints={"X": 0.01})}}])
    finally:
        del api.segment.enqueue
    assert len(calls) == 2 and answer.accepted > 0  # recompiled once


# ---- routes, the robot stream, the generated client ----------------------------------------------------

@pytest.fixture
def served():
    api, robot, line, solver = make_api()
    api.start()
    stepper = Stepper(api, dt=DT)
    stepper.start()
    yield api, robot
    stepper.stop()
    api.stop()


def test_routes_end_to_end(served, tmp_path):
    api, robot = served
    base = f"http://127.0.0.1:{api.port}"
    deadline = time.monotonic() + 5
    while api.segment.latest is None and time.monotonic() < deadline:
        time.sleep(0.01)

    path = download_client_module(base, tmp_path, "gantry_cell_robot.py")
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module("gantry_cell_robot")
    finally:
        sys.path.remove(str(tmp_path))
    client = module.Client(base)
    assert isinstance(client, RobotClient)
    assert client.info().layers == ["CoreApi", "SegmentApi", "RobotApi", "KinematicsApi"]

    info = client.robot_info()
    assert info.robots[0].id == "arm" and info.robots[0].joints[0].field == "X"
    assert info.robots[0].joints[3].velocity_limit_source == "urdf"

    # compile doesn't queue; write does
    compiled = client.robot_compile([module.RobotCommand(robots={"arm": module.PtpMove(joints={"X": 0.1})})])
    assert compiled.frames and api.segment.queue_length == 0
    answer = client.robot_write([module.RobotCommand(frame_id=1, robots={"arm": module.LinMove(
        target=module.Pose(position=module.Vector3(x=0.1, y=0.0, z=0.5), orientation=module.Quaternion(w=1, x=0, y=0, z=0)),
        velocity=1.0)})])
    assert answer.accepted == answer.commands[0].steps

    with client.robot_stream(executed_only=True) as stream:
        for frame in stream:
            assert frame.robots["arm"].target_tcp_pose.frame == "base"
            if frame.frame.frame_id == 1 and math.isclose(frame.frame.components.robot.commands.joints.X.position, 0.1):
                assert math.isclose(frame.robots["arm"].target_tcp_pose.position.x, 0.1)
                break

    # execute waits for the last frame of its command
    done = client.robot_execute(module.RobotCommand(frame_id=2, robots={"arm": module.PtpMove(joints={"X": 0.0})}))
    assert done.frame.frame_id == 2 and math.isclose(done.robots["arm"].actual_tcp_pose.position.x, 0.0, abs_tol=1e-9)
    assert api.segment.queue_length == 0

    # a coded 422, nothing queued
    with pytest.raises(CoreApiError) as caught:
        client.robot_write([{"robots": {"arm": {"type": "set", "target": {"position": {"x": 0.9, "y": 0, "z": 0.5},
                                                                          "orientation": {"w": 1, "x": 0, "y": 0, "z": 0}}}}}])
    assert caught.value.status_code == 422 and caught.value.code == "JOINT_JUMP"

    # the TCP definition: tcp 10 cm along the tool's Z, which becomes the active tool frame
    config = client.robot_config_write({"arm": {"tcp_offset": {"position": {"x": 0, "y": 0, "z": 0.1},
                                                               "orientation": {"w": 1, "x": 0, "y": 0, "z": 0}}}})
    assert config.robots["arm"].tool_frame == "tcp"
    state = client.robot_read()
    assert math.isclose(state.robots["arm"].target_tcp_pose.position.z, 0.6) and state.robots["arm"].tool_frame == "tcp"

    # the kinematics routes on the same server: the robot is a read-only solver whose active tool frame is the robot's
    kinematics = KinematicsClient(base)
    assert [s.id for s in client.solvers()] == ["arm"]
    described = kinematics.solver("arm")
    assert described.read_only and described.tool_frame == "tcp" and "tcp" in described.frames
    fk = kinematics.fk("arm", [{"X": 0.1, "Y": 0, "Z": 0.5, "Yaw": 0, "Pitch": 0, "Roll": 0}], frame="tool0")
    assert math.isclose(fk.poses[0].position.x, 0.1)
    assert kinematics.fk("arm", [{"X": 0.1, "Y": 0, "Z": 0.5, "Yaw": 0, "Pitch": 0, "Roll": 0}]).frame == "tcp"
    ik = kinematics.ik("arm", fk.poses + [pose(4.0, 0, 0)], frame="tool0")
    assert not ik.success and ik.results[0].success and ik.results[1].error.code == "IK_UNREACHABLE"
    assert math.isclose(ik.results[0].joints["X"], 0.1)
    with pytest.raises(CoreApiError) as caught:
        kinematics.configure_solver("arm", tool_frame="tool0")
    assert caught.value.status_code == 409 and caught.value.code == "READ_ONLY"
    with pytest.raises(CoreApiError) as caught:
        kinematics.delete_solver("arm")
    assert caught.value.status_code == 405
    with pytest.raises(CoreApiError) as caught:
        kinematics.create_solver(urdf_text="<robot/>", xrdf_text="x", upload="json")
    assert caught.value.status_code == 405
    with pytest.raises(CoreApiError) as caught:
        kinematics.fk("nobody", [{}])
    assert caught.value.status_code == 404 and caught.value.code == "UNKNOWN_SOLVER"


def test_no_state_before_the_first_step():
    api, *_ = make_api()
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.1})}}])
    assert caught.value.code == "NO_STATE" and caught.value.status == 503

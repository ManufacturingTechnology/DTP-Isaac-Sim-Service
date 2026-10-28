"""The RobotAPI's planning and execution features against fakes: jerk-limited timing, CIRC, blending (cnt), plan and move (a fake planner),
compile from a start and compile-then-merge, pause/resume, path/goal tolerances and the start-state check, the
decelerating clear, and end-effector combinations. The cuMotion parts are exercised offline by
docs/src/Agents/Scratch/harnesses/upper_layer_harness.py."""
import math
import threading
import time

import numpy as np
import pytest

from amt.dtp.ise.lib.control.motion.planning import CuboidObstacle, PlanningWorld, SphereObstacle
from amt.dtp.ise.lib.control.motion.trajectory import (
    CartesianPath,
    InvalidCircle,
    circ_samples,
    joint_time_scaling,
    time_scaling,
)
from amt.dtp.ise.lib.control.robot.compiler import RobotBinding, RobotCompileError
from amt.dtp.ise.lib.control.robot.models import (
    CircMove,
    LinMove,
    PlanMove,
    PtpMove,
    RobotConfiguration,
    RobotPlanRequest,
)
from amt.dtp.ise.lib.control.robot.server import RobotApi
from amt.dtp.ise.lib.control.segment.engine import QueueChangedError
from amt.dtp.ise.lib.control.segment.merge import MergeConflict, merge_frames
from amt.dtp.ise.lib.control.transducer.models import Pose, Quaternion, Vector3

from .conftest import FakeLine, free_port
from .robot_fakes import FakePlanner, FakeRobot, FakeSolver

DT = 0.01
HOME = {"X": 0.0, "Y": 0.0, "Z": 0.5, "Yaw": 0.0, "Pitch": 0.0, "Roll": 0.0}


def pose(x, y, z, q=None) -> Pose:
    return Pose(position=Vector3(x=x, y=y, z=z), orientation=q or Quaternion.identity())


def make_api(**binding):
    robot, line = FakeRobot(), FakeLine()
    solver = binding.pop("solver", None) or FakeSolver()
    api = RobotApi({"robot": robot, "line": line}, robots={"arm": RobotBinding(solver, component="robot", **binding)},
                   title="Gantry Cell", port=free_port(), host="127.0.0.1", buffer_size=50, preview_features=True)
    api.step(DT)
    return api, robot, line, solver


def frames_of(response):
    return [f.model_dump(mode="json", exclude_unset=True) for f in response.frames]


def x_of(frames):
    return np.array([f["components"]["robot"]["joints"]["X"]["position"] for f in frames
                     if "robot" in (f.get("components") or {})])


def run(api, steps):
    for _ in range(steps):
        api.step(DT)


# ---- timing ------------------------------------------------------------------------------------------------

def test_jerk_limited_profile_respects_its_limits():
    v, a, j = 2.0, 4.0, 40.0
    scaling = time_scaling(v, a, DT, jerk=j)
    s = np.concatenate([[0.0], scaling.s])
    velocity = np.diff(s) / DT
    acceleration = np.diff(np.concatenate([[0.0], velocity])) / DT
    assert s[-1] == 1.0 and velocity.max() <= v + 1e-9 and np.abs(acceleration).max() <= a * 1.05
    assert scaling.steps > time_scaling(v, a, DT).steps  # jerk limiting takes longer than the trapezoid
    assert 0 < scaling.ramp < scaling.duration / 2 + 1e-9
    # ptp: jerk-limited when every moving joint has a jerk limit
    plain = joint_time_scaling([1.0, 0.5], [1, 1], [2, 2], DT)
    smooth = joint_time_scaling([1.0, 0.5], [1, 1], [2, 2], DT, jerk_limits=[5, 5])
    assert smooth.steps > plain.steps
    assert joint_time_scaling([1.0, 0.5], [1, 1], [2, 2], DT, jerk_limits=[5, None]).steps == plain.steps


def test_circ_follows_the_arc_through_the_via_point():
    positions, orientations, scaling = circ_samples([0.1, 0, 0.5], [1, 0, 0, 0], [0, 0.1, 0.5], [-0.1, 0, 0.5],
                                                    [1, 0, 0, 0], 0.2, 1.0, 1.0, 1.0, DT)
    radii = np.linalg.norm(positions[:, :2], axis=1)
    assert np.allclose(radii, 0.1, atol=1e-9) and np.allclose(positions[:, 2], 0.5)
    assert positions[:, 1].max() > 0.099 and positions[:, 1].min() > -1e-9  # through +y (the via), not -y
    assert np.allclose(positions[-1], [-0.1, 0, 0.5])
    assert math.isclose(CartesianPath.arc([0.1, 0, 0], [1, 0, 0, 0], [0, 0.1, 0], [-0.1, 0, 0], [1, 0, 0, 0]).length,
                        0.1 * math.pi)
    with pytest.raises(InvalidCircle):
        CartesianPath.arc([0, 0, 0], [1, 0, 0, 0], [0.1, 0, 0], [0.2, 0, 0], [1, 0, 0, 0])


def test_circ_move_through_the_robot_api():
    api, *_ = make_api()
    start = api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.1})}}])
    api.enqueue([f for f in frames_of(start)])
    answer = api.compile([{"robots": {"arm": CircMove(via=Vector3(x=0.0, y=0.1, z=0.5), target=pose(-0.1, 0, 0.5),
                                                      velocity=0.2)}}])
    rows = [f["components"]["robot"]["joints"] for f in frames_of(answer)]
    xy = np.array([[r["X"]["position"], r["Y"]["position"]] for r in rows])
    assert np.allclose(np.linalg.norm(xy, axis=1), 0.1, atol=1e-6)
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": CircMove(via=Vector3(x=0.0, y=0.0, z=0.5), target=pose(-0.1, 0, 0.5))}}])
    assert caught.value.code == "INVALID_CIRCLE"


# ---- blending -----------------------------------------------------------------------------------------------

def test_cnt_blends_consecutive_ptp_moves_without_stopping():
    api, *_ = make_api()
    fine = api.compile([{"frame_id": 1, "robots": {"arm": PtpMove(joints={"X": 0.3})}},
                        {"frame_id": 2, "robots": {"arm": PtpMove(joints={"Y": 0.3})}}])
    blended = api.compile([{"frame_id": 1, "robots": {"arm": PtpMove(joints={"X": 0.3}, termination="cnt")}},
                           {"frame_id": 2, "robots": {"arm": PtpMove(joints={"Y": 0.3})}}])
    assert blended.commands[0].blended and not fine.commands[0].blended
    assert len(blended.frames) < len(fine.frames)
    rows = frames_of(blended)
    xy = np.array([[r["components"]["robot"]["joints"][j]["position"] for j in ("X", "Y")] for r in rows])
    speed = np.linalg.norm(np.diff(xy, axis=0), axis=1) / DT
    assert speed[5:-5].min() > 1e-3  # never at rest in between
    assert np.abs(np.diff(xy, axis=0)).max() <= 0.5 * DT + 1e-12  # within the joints' 0.5 m/s
    assert np.allclose(xy[-1], [0.3, 0.3])
    ids = [r["frame_id"] for r in rows]
    assert ids == sorted(ids) and blended.commands[1].first_step == ids.index(2)
    assert sum(c.steps for c in blended.commands) == len(rows)


def test_cnt_blends_lin_moves_and_ends_at_rest_without_a_successor():
    api, *_ = make_api()
    answer = api.compile([
        {"robots": {"arm": LinMove(target=pose(0.2, 0, 0.5), velocity=0.4, termination="cnt", cnt=50)}},
        {"robots": {"arm": LinMove(target=pose(0.2, 0.2, 0.5), velocity=0.4, termination="cnt")}},  # no successor
    ])
    rows = frames_of(answer)
    xy = np.array([[r["components"]["robot"]["joints"][j]["position"] for j in ("X", "Y")] for r in rows])
    assert answer.commands[0].blended and not answer.commands[1].blended
    corner = np.linalg.norm(xy - [0.2, 0.0], axis=1).min()
    assert corner > 1e-4  # the corner is rounded: the robot never reaches (0.2, 0)
    assert np.allclose(xy[-1], [0.2, 0.2])  # the last move ends at its target, at rest
    step = np.linalg.norm(xy[-1] - xy[-2])
    assert step < 0.4 * DT  # decelerated into the end


def test_cnt_does_not_blend_across_different_robots_or_with_a_dwell():
    api, *_ = make_api()
    answer = api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.1}, termination="cnt")}, "dwell": 0.05},
                          {"robots": {"arm": PtpMove(joints={"X": 0.2})}}])
    assert not answer.commands[0].blended


# ---- plan and move -----------------------------------------------------------------------------------------------

def planned_api(**kwargs):
    solver = FakeSolver()
    planner = FakePlanner(solver, **kwargs)
    planner.set_world(PlanningWorld(reference="base", obstacles=[
        SphereObstacle(name="/World/Ball", pose=pose(0.2, 0.0, 0.5), radius=0.05)]))
    api, *_ = make_api(solver=solver, planner=planner)
    return api, planner


def test_plan_moves_around_an_obstacle_and_checks_every_step():
    api, planner = planned_api()
    answer = api.compile([{"robots": {"arm": PlanMove(joints={"X": 0.4})}}])
    rows = frames_of(answer)
    q = np.array([[r["components"]["robot"]["joints"][j]["position"] for j in ("X", "Y", "Z")] for r in rows])
    assert q[:, 2].max() > 0.9  # went over the top
    assert np.allclose(q[-1], [0.4, 0.0, 0.5])
    assert np.linalg.norm(q - [0.2, 0.0, 0.5], axis=1).min() > 0.05
    plan = api.plan(RobotPlanRequest(robot="arm", move=PlanMove(target=pose(0.4, 0, 0.5)), include_samples=True))
    assert len(plan.waypoints) == 4 and plan.steps == len(plan.samples) and plan.smoothed and plan.attempts == 1
    info = api.robot_info().robots[0]
    assert info.planner.backend == "fake" and info.planner.obstacles == ["/World/Ball"]


def test_plan_errors():
    api, planner = planned_api(careless=True)
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": PlanMove(joints={"X": 0.4}, attempts=2)}}])
    assert caught.value.code == "PATH_IN_COLLISION" and planner.plans == 2 and "2 plan(s)" in caught.value.message
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": PlanMove(joints={"X": 0.2})}}])
    assert caught.value.code == "GOAL_IN_COLLISION"
    plain, *_ = make_api()
    with pytest.raises(RobotCompileError) as caught:
        plain.compile([{"robots": {"arm": PlanMove(joints={"X": 0.2})}}])
    assert caught.value.code == "NO_PLANNER"


def test_obstacles_in_the_world_frame():
    solver = FakeSolver()
    planner = FakePlanner(solver)
    planner.set_obstacles([CuboidObstacle(name="box", pose=pose(1.2, 0, 0.5), size=Vector3(x=0.1, y=0.1, z=0.1))],
                          base_pose=pose(1.0, 0, 0))
    assert planner.collisions([[0.2, 0, 0.5, 0, 0, 0], [0.0, 0, 0.5, 0, 0, 0]]) == ["obstacle box", ""]
    with pytest.raises(ValueError):
        PlanningWorld(reference="world", obstacles=[SphereObstacle(name="a", pose=pose(0, 0, 0), radius=1)])


# ---- compile from a start, then merge ------------------------------------------------------------------------------

def test_compile_from_a_start_and_merge_with_another_machine():
    api, robot, line, _ = make_api()
    first = api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.2})}}])
    assert math.isclose(first.end["arm"]["X"], 0.2) and first.queue_revision == api.segment.queue_revision
    second = api.compile([{"robots": {"arm": PtpMove(joints={"Y": 0.1})}}], start={"arm": first.end["arm"]})
    assert all(math.isclose(r["components"]["robot"]["joints"]["X"]["position"], 0.2) for r in frames_of(second))
    belt = [{"frame_id": 100, "components": {"line": {"belt": {"velocity": 0.1 * k}}}} for k in range(5)]
    rows = merge_frames(frames_of(first) + frames_of(second), belt, offsets=[0, 3])
    assert len(rows) == len(first.frames) + len(second.frames)
    assert rows[3]["components"]["line"]["belt"]["velocity"] == 0.0 and "robot" in rows[3]["components"]
    assert "line" not in rows[8]["components"]
    answer = api.segment.enqueue(rows, expected_revision=first.queue_revision)
    assert answer.accepted == len(rows)
    with pytest.raises(QueueChangedError):  # the queue changed since the compile
        api.segment.enqueue(rows, expected_revision=first.queue_revision)
    with pytest.raises(MergeConflict) as caught:
        merge_frames(belt, belt)
    assert caught.value.step == 0 and caught.value.path == ["line", "belt"]
    assert merge_frames([{"frame_id": 1}], [{"frame_id": 2}, {}], frame_id="first") == [{"frame_id": 1}, {}]


# ---- pause, monitoring, and stopping --------------------------------------------------------------------------------

def test_pause_holds_the_queue_and_resume_continues():
    api, robot, *_ = make_api()
    events = []
    api.segment.add_listener(lambda e: events.append(e.model.reason) if e.event == "status" else None)
    api.write([{"frame_id": 1, "robots": {"arm": PtpMove(joints={"X": 0.1})}}])
    run(api, 3)
    status = api.segment.pause()
    assert status.state == "paused" and status.paused
    x = robot.targets["X"]
    length = api.segment.queue_length
    run(api, 5)
    assert robot.targets["X"] == x and api.segment.queue_length == length  # held, not cleared
    assert api._info().paused and api.segment.status().state == "paused"
    api.segment.clear()
    assert api.segment.paused  # clear doesn't release a pause
    api.write([{"robots": {"arm": PtpMove(joints={"X": 0.0})}}])
    run(api, 2)
    assert api.segment.queue_length > 0
    api.segment.resume()
    run(api, 200)
    assert api.segment.queue_length == 0 and math.isclose(robot.targets["X"], 0.0)
    assert events == ["paused", "cleared", "resumed"]


def test_path_and_goal_tolerances_halt_the_segment():
    api, robot, *_ = make_api(path_tolerance={"X": 0.005}, goal_tolerance=0.002, goal_time=0.05)
    robot.measured_offset = 0.01  # the robot lags: 1 cm on every joint
    api.write([{"frame_id": 7, "robots": {"arm": PtpMove(joints={"X": 0.1})}}])
    frames = [api.step(DT) for _ in range(3)]
    halt = api.segment.halt
    assert halt is not None and halt.errors[0].code == "PATH_TOLERANCE" and halt.frame_id == 7
    failed = next(f for f in frames if f.sequence == halt.sequence)  # the step's frame carries the error
    assert failed.halted and [e.code for e in failed.components.robot.errors] == ["PATH_TOLERANCE"]
    api.segment.clear()
    # goal: no motion commanded now, but the robot hasn't settled within goal_time after the last motion frame
    api.write([{"robots": {"arm": PtpMove(joints={"Y": 0.01})}}])
    robot.measured_offset = 0.003  # within the path tolerance of X (5 mm), outside the goal tolerance (2 mm)
    run(api, 40)
    assert api.segment.halt is not None and api.segment.halt.errors[0].code == "GOAL_TOLERANCE"


def test_start_state_check():
    api, robot, *_ = make_api(start_tolerance=0.001)
    robot.measured_offset = 0.01
    api.step(DT)
    with pytest.raises(RobotCompileError) as caught:
        api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.1})}}])
    assert caught.value.code == "START_TOLERANCE" and caught.value.status == 409
    robot.measured_offset = 0.0
    api.step(DT)
    assert api.compile([{"robots": {"arm": PtpMove(joints={"X": 0.1})}}]).frames


def test_clear_with_deceleration():
    api, robot, *_ = make_api()
    api.write([{"robots": {"arm": PtpMove(joints={"X": 0.8})}}])
    run(api, 60)  # mid-move, at speed
    moving = robot.targets["X"]
    answer = api.clear_robots("decelerate")
    assert answer.robots == {"arm": "decelerate"} and answer.steps > 1 and answer.removed > 0
    trace = []
    for _ in range(answer.steps + 2):
        api.step(DT)
        trace.append(robot.targets["X"])
    steps = np.diff([moving] + trace)
    assert steps[0] > 0 and np.all(np.diff(steps[:answer.steps]) <= 1e-12) and abs(steps[-1]) < 1e-12
    assert trace[-1] < 0.8  # stopped before the old target
    assert api.clear_robots("decelerate").robots == {"arm": "hold"}  # at rest: nothing to ramp


# ---- end-effector combinations ---------------------------------------------------------------------------------------

class PointerSolver(FakeSolver):
    """The gantry with a pointer: its TCP p1/tcp 10 cm along the tool's Z is the first tool frame."""

    def __init__(self):
        super().__init__()
        self.set_frame("p1/tcp", "tool0", [0, 0, 0.1])

    @property
    def tool_frames(self):
        return ["p1/tcp", "tool0"]


def test_combinations_switch_the_solver():
    plain, pointer = FakeSolver(), PointerSolver()
    api, *_ = make_api(solver=plain, combinations={"none": plain, "pointer": pointer})
    info = api.robot_info().robots[0]
    assert info.combination == "none" and info.combinations == ["none", "pointer"]
    config = api.config_write_robots(RobotConfiguration(robots={"arm": {"combination": "pointer"}}))
    assert config.robots["arm"].combination == "pointer" and config.robots["arm"].tool_frame == "p1/tcp"
    state = api.robot_states(api.segment.latest)["arm"]
    assert state.combination == "pointer" and math.isclose(state.target_tcp_pose.position.z, 0.6)
    assert api._registry.get("arm").solver is pointer  # the kinematics routes follow the active combination
    with pytest.raises(RobotCompileError) as caught:
        api.config_write_robots(RobotConfiguration(robots={"arm": {"combination": "gripper"}}))
    assert caught.value.code == "UNKNOWN_COMBINATION"
    with pytest.raises(ValueError):
        other = FakeSolver()
        other._joints = other._joints[:5]
        RobotBinding(plain, component="robot", combinations={"a": plain, "b": other})


# ---- over HTTP --------------------------------------------------------------------------------------------------------

def test_routes_end_to_end(tmp_path):
    import importlib
    import sys

    from amt.dtp.ise.lib.control.core.client import CoreApiError
    from amt.dtp.ise.lib.control.segment.client import download_client_module

    from .conftest import Stepper
    solver = FakeSolver()
    planner = FakePlanner(solver)
    planner.set_world(PlanningWorld(reference="base", obstacles=[
        SphereObstacle(name="/World/Ball", pose=pose(0.2, 0.0, 0.5), radius=0.05)]))
    api = RobotApi({"robot": FakeRobot(), "line": FakeLine()}, robots={"arm": RobotBinding(solver, component="robot",
                                                                                         planner=planner)},
                   title="Upper Cell", port=free_port(), host="127.0.0.1", preview_features=True)
    api.start()
    stepper = Stepper(api, dt=DT)
    stepper.start()
    try:
        base = f"http://127.0.0.1:{api.port}"
        deadline = time.monotonic() + 5
        while api.segment.latest is None and time.monotonic() < deadline:
            time.sleep(0.01)
        download_client_module(base, tmp_path, "upper_cell.py")
        sys.path.insert(0, str(tmp_path))
        try:
            module = importlib.import_module("upper_cell")
        finally:
            sys.path.remove(str(tmp_path))
        client = module.Client(base)

        # compile from a start, then write the rows only if the queue didn't change
        compiled = client.robot_compile([module.RobotCommand(robots={"arm": module.PtpMove(joints={"X": 0.1})})],
                                        start={"arm": {**HOME, "Y": 0.05}}, dt=0.02)
        assert all(math.isclose(f.components.robot.joints.Y.position, 0.05) for f in compiled.frames)
        assert math.isclose(compiled.end["arm"]["X"], 0.1)
        info = client.segment_info()
        assert info.queue_revision == compiled.queue_revision and info.stop_policy == "hold" and not info.paused
        client.write([{}], expected_revision=compiled.queue_revision)
        with pytest.raises(CoreApiError) as caught:
            client.write([{}], expected_revision=compiled.queue_revision)
        assert caught.value.status_code == 409 and caught.value.code == "QUEUE_CHANGED"

        # plan (not queued), then plan and move
        plan = client.robot_plan("arm", module.PlanMove(joints={"X": 0.4}))
        assert len(plan.waypoints) == 4 and plan.samples is None
        world = client.world("arm")  # the RobotAPI robot's world: its scene, read-only
        assert world.obstacles[0]["name"] == "/World/Ball"
        with pytest.raises(CoreApiError) as caught:
            client.clear_world("arm")
        assert caught.value.code == "READ_ONLY"
        assert client.robot_info().robots[0].planner.obstacles == ["/World/Ball"]

        # pause holds the queue; resume continues; a decelerating clear
        client.robot_write([module.RobotCommand(frame_id=5, robots={"arm": module.PtpMove(joints={"X": 0.8})})])
        time.sleep(0.2)
        assert client.pause().state == "paused" and client.segment_info().paused
        length = client.segment_info().queue_length
        time.sleep(0.1)
        assert client.segment_info().queue_length == length > 0
        assert client.resume().state == "running"
        time.sleep(0.1)
        cleared = client.robot_clear("decelerate")
        assert cleared.stop == "decelerate" and cleared.removed > 0 and cleared.robots["arm"] in ("decelerate", "hold")
        assert client.robot_config_read().robots["arm"].combination is None
    finally:
        stepper.stop()
        api.stop()


def test_preview_features_are_off_by_default():
    """The preview features are deferred: without preview_features they are neither served
    nor in the schema, and the in-process calls refuse them; a halt still resumes."""
    import requests

    api, robot, line, solver = make_api_default()
    api.start()
    try:
        base = f"http://127.0.0.1:{api.port}"
        schema = requests.get(f"{base}/api/openapi.json").json()
        assert "/api/segment/process/pause" not in schema["paths"]
        assert "/api/segment/process/resume" in schema["paths"]
        write_params = [p["name"] for p in schema["paths"]["/api/segment/process/write"]["post"].get("parameters", [])]
        assert "expected_revision" not in write_params
        info = requests.get(f"{base}/api/segment/info").json()
        assert "queue_revision" not in info and "paused" not in info
        assert requests.post(f"{base}/api/segment/process/pause").status_code in (404, 405)
        answer = requests.post(f"{base}/api/segment/process/write?expected_revision=0", json=[{}])
        assert answer.status_code == 400 and len(api.segment.pending()) == 0
        command = {"robots": {"arm": {"type": "ptp", "joints": {"X": 0.1}}}}
        answer = requests.post(f"{base}/api/robot/process/compile", json={"commands": [command], "start": {"arm": HOME}})
        assert answer.status_code == 422  # the {commands, start, dt} body isn't accepted
        compiled = requests.post(f"{base}/api/robot/process/compile", json=[command])
        assert compiled.status_code == 200 and "queue_revision" not in compiled.json() and "end" not in compiled.json()
        with pytest.raises(RuntimeError):
            api.pause()
        with pytest.raises(RobotCompileError) as caught:
            api.compile([command], start={"arm": HOME})
        assert caught.value.status == 400
    finally:
        api.stop()


def make_api_default():
    robot, line, solver = FakeRobot(), FakeLine(), FakeSolver()
    api = RobotApi({"robot": robot, "line": line}, robots={"arm": RobotBinding(solver, component="robot")},
                   title="Plain Cell", port=free_port(), host="127.0.0.1")
    api.step(DT)
    return api, robot, line, solver

"""The kinematics service (ADR-0010) against a fake backend: creating, listing, and deleting solvers (JSON and
multipart uploads), every route, the errors and limits, the concurrency policy (a lock per solver), and the
client. The real cuMotion backend is exercised offline by docs/src/Agents/Scratch/harnesses/kinematics_service_probe.py."""
import math
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import requests

from amt.dtp.ise.lib.control.core.client import CoreApiError
from amt.dtp.ise.lib.control.motion.client import KinematicsClient
from amt.dtp.ise.lib.control.motion.kinematics import KinematicsSolver
from amt.dtp.ise.lib.control.motion.registry import RegistryError, SolverRegistry, SolverSource
from amt.dtp.ise.lib.control.motion.server import KinematicsApi, _multipart_available
from amt.dtp.ise.lib.control.robot.models import LinMove, PtpMove, SetTcp
from amt.dtp.ise.lib.control.transducer.models import Pose, Quaternion, Vector3

from .conftest import free_port
from .robot_fakes import FakeSolver

URDF = '<robot name="gantry"><link name="base"/></robot>'
XRDF = "format: xrdf\nformat_version: 2.0\ntool_frames: [tool0]\n"
HOME = {"X": 0.0, "Y": 0.0, "Z": 0.5, "Yaw": 0.0, "Pitch": 0.0, "Roll": 0.0}
MULTIPART = _multipart_available()


class NoCollisionSolver(FakeSolver):
    """A backend without self-collision checks."""
    _self_collision = KinematicsSolver._self_collision
    _collision_spheres = KinematicsSolver._collision_spheres


class SlowSolver(FakeSolver):
    """Records how many FK calls overlap (across every instance) and on which threads they run."""
    active = 0
    peak = 0
    threads = set()
    guard = threading.Lock()

    def _fk(self, q, frame):
        cls = SlowSolver
        with cls.guard:
            cls.active += 1
            cls.peak = max(cls.peak, cls.active)
            cls.threads.add(threading.current_thread().name)
        try:
            time.sleep(0.05)
            return super()._fk(q, frame)
        finally:
            with cls.guard:
                cls.active -= 1


def fake_factory(source: SolverSource) -> KinematicsSolver:
    assert source.urdf_path.read_text(encoding="utf-8") == source.urdf_text  # the files are stored
    if "<robot" not in source.urdf_text:
        raise RuntimeError("Failed to parse URDF")
    solver = SlowSolver() if "slow" in source.urdf_text else (
        NoCollisionSolver() if "nocollision" in source.urdf_text else FakeSolver())
    if source.tool_frame is not None and source.tool_frame not in solver.tool_frames:
        raise ValueError(f"tool_frame '{source.tool_frame}' is not one of the XRDF tool_frames {solver.tool_frames}")
    return solver


def pose(x, y, z, q=None) -> Pose:
    return Pose(position=Vector3(x=x, y=y, z=z), orientation=q or Quaternion.identity())


@pytest.fixture
def service(tmp_path):
    api = KinematicsApi({"fake": fake_factory}, port=free_port(), host="127.0.0.1", max_solvers=3,
                        max_upload_bytes=4096, max_batch=50, max_samples=500, temp_root=str(tmp_path))
    api.start()
    client = KinematicsClient(f"http://127.0.0.1:{api.port}")
    yield api, client, tmp_path
    api.stop()


def create(client, name=None, urdf=URDF, upload="json", **kwargs):
    return client.create_solver(urdf_text=urdf, xrdf_text=XRDF, name=name, upload=upload, **kwargs)


def code_of(call):
    with pytest.raises(CoreApiError) as caught:
        call()
    return caught.value.status_code, caught.value.code


# ---- solvers --------------------------------------------------------------------------------------------

def test_create_list_get_delete(service):
    api, client, tmp_path = service
    info = client.kinematics_info()
    assert info.backends == ["fake"] and info.creatable and info.solvers == 0
    assert (info.limits.max_solvers, info.limits.max_batch, info.limits.max_samples) == (3, 50, 500)
    base = f"http://127.0.0.1:{api.port}"
    assert requests.get(f"{base}/api/core/info").status_code == 404  # an ApiHost, not a CoreApi (ADR-0024)
    assert requests.get(f"{base}/api/docs").status_code == 200 and requests.get(f"{base}/api/redoc").status_code == 200

    arm = create(client, "arm")
    assert arm.id == "arm" and arm.backend == "fake" and arm.tool_frame == "tool0" and not arm.read_only
    assert arm.capabilities == ["fk", "ik", "jacobian", "frames", "self_collision"]
    assert [(f.name, f.kind) for f in arm.files] == [("robot.urdf", "urdf"), ("robot.xrdf", "xrdf")]
    joint = arm.joints[3]
    assert (joint.name, joint.unit, joint.lower, joint.velocity_limit) == ("Yaw", "deg", -360.0, math.degrees(2.0))
    assert client.download_solver_file("arm", "robot.urdf").decode() == URDF
    directories = list(tmp_path.iterdir())
    assert len(directories) == 1 and (directories[0] / "robot.xrdf").read_text() == XRDF

    generated = create(client)
    assert generated.id.startswith("solver-") and len(generated.id) == len("solver-") + 8
    assert sorted(s.id for s in client.solvers()) == sorted(["arm", generated.id])
    assert client.solver("arm").id == "arm"

    client.delete_solver("arm")
    assert code_of(lambda: client.solver("arm")) == (404, "UNKNOWN_SOLVER")
    assert code_of(lambda: client.delete_solver("arm")) == (404, "UNKNOWN_SOLVER")
    assert len(list(tmp_path.iterdir())) == 1  # the deleted solver's files are gone

    api.stop()  # stopping deletes every solver and its files
    assert len(api.registry) == 0 and list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not MULTIPART, reason="python-multipart is not installed")
def test_multipart_upload(service, tmp_path_factory):
    api, client, _ = service
    folder = tmp_path_factory.mktemp("upload")
    (folder / "Gantry 1.urdf").write_text(URDF)
    (folder / "Gantry 1.xrdf").write_text(XRDF)
    arm = client.create_solver(folder / "Gantry 1.urdf", folder / "Gantry 1.xrdf", name="arm", tool_frame="tool0")
    assert arm.id == "arm" and [f.name for f in arm.files] == ["Gantry 1.urdf", "Gantry 1.xrdf"]
    base = f"http://127.0.0.1:{api.port}/api/kinematics/solvers"
    answer = requests.post(base, files={"urdf": ("a.urdf", URDF)})  # the XRDF is missing
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "INVALID_DESCRIPTION"
    answer = requests.post(base, files={"urdf": ("a.urdf", URDF), "xrdf": ("a.xrdf", XRDF), "extra": ("x", "y")})
    assert answer.status_code == 422


@pytest.mark.skipif(MULTIPART, reason="python-multipart is installed")
def test_multipart_without_python_multipart_is_415(service):
    api, client, _ = service
    assert code_of(lambda: create(client, upload="multipart")) == (415, "UNSUPPORTED_MEDIA_TYPE")


def test_create_errors_and_limits(service):
    api, client, _ = service
    base = f"http://127.0.0.1:{api.port}/api/kinematics/solvers"
    assert code_of(lambda: create(client, urdf="not a urdf")) == (422, "INVALID_DESCRIPTION")
    assert code_of(lambda: create(client, tool_frame="nowhere")) == (422, "INVALID_DESCRIPTION")
    assert code_of(lambda: create(client, backend="cuRobo")) == (422, "UNKNOWN_BACKEND")
    assert code_of(lambda: create(client, name="bad name!")) == (422, "INVALID_NAME")
    assert code_of(lambda: create(client, urdf="<robot>" + "x" * 5000 + "</robot>")) == (413, "UPLOAD_TOO_LARGE")
    answer = requests.post(base, data="x" * 100_000, headers={"Content-Type": "application/json"})
    assert answer.status_code == 413 and answer.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
    answer = requests.post(base, data="hello", headers={"Content-Type": "text/plain"})
    assert answer.status_code == 415 and answer.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    answer = requests.post(base, json={"urdf": URDF})  # the XRDF is missing: a validation error
    assert answer.status_code == 422 and isinstance(answer.json()["detail"], list)
    create(client, "a")
    assert code_of(lambda: create(client, "a")) == (409, "NAME_TAKEN")
    create(client, "b")
    create(client, "c")
    assert code_of(lambda: create(client, "d")) == (409, "TOO_MANY_SOLVERS")
    assert sorted(s.id for s in client.solvers()) == ["a", "b", "c"]  # failures left nothing behind


def test_no_limits(tmp_path):
    """Every limit can be None (no limit); the defaults are high."""
    default = KinematicsApi({"fake": fake_factory}, port=free_port(), host="127.0.0.1")
    assert (default.registry.max_solvers, default.registry.max_upload_bytes, default.max_batch, default.max_samples) \
        == (1024, 256 * 1024 * 1024, 1_000_000, 10_000_000)
    api = KinematicsApi({"fake": fake_factory}, port=free_port(), host="127.0.0.1", max_solvers=None,
                        max_upload_bytes=None, max_batch=None, max_samples=None, temp_root=str(tmp_path))
    api.start()
    try:
        client = KinematicsClient(f"http://127.0.0.1:{api.port}")
        limits = client.kinematics_info().limits
        assert (limits.max_solvers, limits.max_upload_bytes, limits.max_batch, limits.max_samples) == (None,) * 4
        assert create(client, "big", urdf="<robot>" + "x" * 100_000 + "</robot>").id == "big"
        for i in range(5):
            create(client, f"s{i}")
        assert len(client.solvers()) == 6
        assert len(client.fk("big", [HOME] * 200).poses) == 200
    finally:
        api.stop()
    with pytest.raises(ValueError):
        KinematicsApi({"fake": fake_factory}, max_batch=0)


# ---- kinematics -----------------------------------------------------------------------------------------

def test_fk_ik_reachable_jacobian(service):
    api, client, _ = service
    create(client, "arm")
    fk = client.fk("arm", [HOME, {**HOME, "X": 0.1, "Yaw": 90.0}])
    assert fk.frame == "tool0" and fk.reference_frame == "base" and len(fk.poses) == 2
    assert math.isclose(fk.poses[1].position.x, 0.1)
    ik = client.ik("arm", fk.poses + [pose(4.0, 0, 0)], seed=HOME)
    assert not ik.success and [r.success for r in ik.results] == [True, True, False]
    assert math.isclose(ik.results[1].joints["Yaw"], 90.0, abs_tol=1e-6) and ik.results[2].error.code == "IK_UNREACHABLE"
    reach = client.reachable("arm", fk.poses + [pose(4.0, 0, 0)])
    assert reach.reachable == [True, True, False] and not reach.all_reachable

    # Jacobian in wire units: prismatic columns m/m, revolute columns m/deg (linear) and deg/deg (angular)
    client.configure_solver("arm", tcp_offset=pose(0, 0, 0.1))  # tcp 10 cm along the tool's Z
    jac = client.jacobian("arm", [HOME]).jacobians[0]
    linear, angular = np.array(jac.linear), np.array(jac.angular)
    assert np.allclose(linear[:, 0], [1, 0, 0], atol=1e-6) and np.allclose(angular[:, 0], 0, atol=1e-6)
    assert np.allclose(angular[:, 3], [0, 0, 1], atol=1e-6)  # Yaw: deg/s per deg/s about Z
    assert np.allclose(linear[:, 4], [0.1 * math.pi / 180, 0, 0], atol=1e-8)  # Pitch moves the tcp along X
    assert jac.manipulability > 0
    assert client.jacobian("arm", [HOME], frame="tool0").frame == "tool0"

    assert code_of(lambda: client.fk("arm", [{"X": 0.0}])) == (422, "UNKNOWN_JOINT")
    assert code_of(lambda: client.fk("arm", [HOME], frame="elbow")) == (422, "UNKNOWN_FRAME")
    assert code_of(lambda: client.fk("nobody", [HOME])) == (404, "UNKNOWN_SOLVER")
    assert code_of(lambda: client.fk("arm", [HOME] * 51)) == (413, "BATCH_TOO_LARGE")
    assert client.fk("arm", []).poses == [] and client.ik("arm", []).results == []


# ---- trajectories ---------------------------------------------------------------------------------------

def test_sample_trajectories(service):
    api, client, _ = service
    create(client, "arm")
    ptp = client.sample_trajectory("arm", HOME, PtpMove(joints={"X": 0.2, "Yaw": 30.0}), dt=0.01)
    assert ptp.steps == len(ptp.joints) and math.isclose(ptp.duration, ptp.steps * 0.01)
    assert math.isclose(ptp.joints[-1]["X"], 0.2) and math.isclose(ptp.joints[-1]["Yaw"], 30.0)
    assert ptp.poses is None and ptp.tool_frame == "tool0"
    slower = client.sample_trajectory("arm", HOME, PtpMove(joints={"X": 0.2, "Yaw": 30.0}), dt=0.01,
                                      velocity_limits={"Yaw": 10.0})
    assert slower.steps > ptp.steps

    lin = client.sample_trajectory("arm", HOME, LinMove(target=pose(0.2, 0.1, 0.5), velocity=0.5), dt=0.01,
                                   include_poses=True)
    assert len(lin.poses) == lin.steps and all(math.isclose(p.position.z, 0.5, abs_tol=1e-9) for p in lin.poses)
    assert math.isclose(lin.poses[-1].position.x, 0.2) and math.isclose(lin.joints[-1]["Y"], 0.1)
    one = client.sample_trajectory("arm", HOME, SetTcp(target=pose(0.001, 0, 0.5)), dt=0.01)
    assert one.steps == 1

    flip = Quaternion.from_axis_angle([0, 1, 0], math.radians(100))  # past the ±90° pitch: a wrist flip
    tilt = Quaternion.from_axis_angle([0, 1, 0], math.radians(80))
    start = {**HOME, "Pitch": 80.0}
    for move, code in [
        (LinMove(target=pose(0, 0, 0.5, flip), angular_velocity=30), "JOINT_JUMP"),
        (LinMove(target=pose(1.2, 0, 0.5, tilt), velocity=2.0), "IK_UNREACHABLE"),
        (PtpMove(joints={"X": 2.0}), "JOINT_LIMIT"),
        (PtpMove(joints={"X": 0.5}, duration=0.01), "DURATION_TOO_SHORT"),
        (PtpMove(joints={"X": 0.5}, duration=100.0), "TOO_MANY_STEPS"),
        (LinMove(target=pose(0, 0, 0.5), reference="world"), "REFERENCE_UNAVAILABLE"),
    ]:
        status, got = code_of(lambda: client.sample_trajectory("arm", start, move, dt=0.01))
        assert (status, got) == (422, code), move


def test_check_path(service):
    api, client, _ = service
    create(client, "arm")
    path = client.sample_trajectory("arm", HOME, PtpMove(joints={"X": 0.05}), dt=0.01).joints
    ok = client.check_path("arm", path, dt=0.01, start=HOME)
    assert ok.ok and ok.steps == len(path) and ok.errors == []
    bad = path[:3] + [{**path[3], "Yaw": 45.0}, {**path[3], "X": 3.0}]
    answer = client.check_path("arm", bad, dt=0.01, start=HOME)
    assert not answer.ok and [(e.code, e.step, e.joint) for e in answer.errors] == [("JOINT_LIMIT", 4, "X"), ("JOINT_JUMP", 3, "Yaw")]
    assert client.check_path("arm", bad[:4], dt=0.01, start=HOME, max_step_revolute=50.0,
                             velocity_limits={"Yaw": 10000.0}).ok


# ---- tool frames -----------------------------------------------------------------------------------------

def test_tool_frames_and_tcp(service):
    api, client, _ = service
    create(client, "arm")
    frames = client.frames("arm")
    assert (frames.tool_frame, frames.flange_frame, frames.tcp_offset) == ("tool0", "tool0", None)
    config = client.configure_solver("arm", tcp_offset=pose(0, 0, 0.1))
    assert config.tool_frame == "tcp" and config.tcp_offset.position.z == 0.1
    assert math.isclose(client.fk("arm", [HOME]).poses[0].position.z, 0.6)  # the default frame is now tcp
    assert client.solver("arm").revision == 1 and "tcp" in client.frames("arm").tool_frames
    assert client.configure_solver("arm", tool_frame="tool0").tool_frame == "tool0"
    assert client.solver_config("arm").tcp_offset is not None
    cleared = client.configure_solver("arm", clear_tcp=True)
    assert cleared.tcp_offset is None and "tcp" not in client.frames("arm").frames
    assert code_of(lambda: client.configure_solver("arm", tool_frame="elbow")) == (422, "UNKNOWN_FRAME")
    assert code_of(lambda: client.configure_solver("arm", tcp_offset=pose(0, 0, 0.1), clear_tcp=True)) == (422, "CONFLICT")


# ---- collision ---------------------------------------------------------------------------------------------

def test_self_collision_and_spheres(service):
    api, client, _ = service
    create(client, "arm")
    path = [HOME, {**HOME, "Z": 0.15}, {**HOME, "Z": 0.05}]
    answer = client.self_collision("arm", path)
    assert not answer.collision_free and answer.first_collision == 1
    assert [r.in_collision for r in answer.results] == [False, True, True] and answer.results[1].pairs == [["base", "wrist"]]
    assert client.self_collision("arm", [HOME]).collision_free
    spheres = client.collision_spheres("arm", HOME)
    assert [(s.frame, s.radius) for s in spheres.spheres] == [("base", 0.1), ("wrist", 0.1)]
    assert math.isclose(spheres.spheres[1].center.z, 0.5)

    plain = create(client, "plain", urdf='<robot name="nocollision"/>')
    assert "self_collision" not in plain.capabilities
    assert code_of(lambda: client.self_collision("plain", [HOME])) == (422, "UNSUPPORTED")
    assert code_of(lambda: client.collision_spheres("plain", HOME)) == (422, "UNSUPPORTED")


# ---- concurrency ------------------------------------------------------------------------------------------

def test_requests_are_handled_one_at_a_time_on_the_server_thread(service):
    # ADR-0010: the service's own server thread, one request at a time (no thread pool).
    api, client, _ = service
    create(client, "slow1", urdf='<robot name="slow"/>')
    create(client, "slow2", urdf='<robot name="slow"/>')
    base = client.base_url
    expected = {id: client.fk(id, [{**HOME, "X": 0.1 * i}]).poses[0].position.x
                for i, id in enumerate(["slow1", "slow2"], start=1)}
    answers, errors = [], []

    def call(id, i):
        try:
            answers.append((id, KinematicsClient(base).fk(id, [{**HOME, "X": 0.1 * i}]).poses[0].position.x))
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)

    SlowSolver.peak, SlowSolver.threads = 0, set()
    threads = [threading.Thread(target=call, args=(id, i)) for _ in range(3) for i, id in enumerate(["slow1", "slow2"], 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(answers) == 6
    assert all(math.isclose(x, expected[id]) for id, x in answers)  # correct answers
    assert SlowSolver.peak == 1  # never two at once, even on different solvers
    assert SlowSolver.threads == {api.server_thread.name}  # all on the service's own server thread


def test_delete_waits_for_a_running_request_and_the_registry_directly(tmp_path):
    registry = SolverRegistry({"fake": fake_factory}, temp_root=tmp_path)
    entry = registry.create(URDF, XRDF, name="arm")
    assert entry.directory.is_dir()
    order = []
    with registry.use("arm"):
        deleter = threading.Thread(target=lambda: (registry.delete("arm"), order.append("deleted")))
        deleter.start()
        time.sleep(0.05)
        order.append("request done")
    deleter.join()
    assert order == ["request done", "deleted"] and not entry.directory.exists()
    with pytest.raises(RegistryError) as caught:
        with registry.use("arm"):
            pass
    assert caught.value.code == "UNKNOWN_SOLVER" and caught.value.status == 404
    with pytest.raises(RegistryError) as caught:
        registry.create(b"\xff\xfe", XRDF)
    assert caught.value.code == "INVALID_DESCRIPTION"
    assert list(tmp_path.iterdir()) == []


# ---- world (library-neutral obstacles), planning, and end-effector fragments (the upper layer) ----------------

ROBOT_XRDF_JSON = '{"format": "xrdf", "format_version": 2.0, "tool_frames": ["tool0"]}'  # JSON: valid YAML, no PyYAML
URDF_TOOL0 = ('<robot name="gantry"><link name="base"/><link name="wrist"/><link name="tool0"/>'
              '<joint name="Roll" type="revolute"><parent link="base"/><child link="wrist"/></joint>'
              '<joint name="flange" type="fixed"><parent link="wrist"/><child link="tool0"/></joint></robot>')
FRAGMENT = {"format": "xrdf-fragment", "format_version": "0.1", "name": "P1", "mount_frame": "mount",
            "frames": [{"frame_name": "tcp", "parent_frame_name": "mount", "fixed_transform": {"position": [0, 0, 0.1]}}],
            "tool_frames": ["tcp"], "collision": {"spheres": {"mount": [{"center": [0, 0, 0.02], "radius": 0.03}]}}}


@pytest.fixture
def planning_service(tmp_path):
    from .robot_fakes import FakePlanner
    api = KinematicsApi({"fake": fake_factory}, port=free_port(), host="127.0.0.1", temp_root=str(tmp_path),
                        planner_factories={"fake": FakePlanner}, max_batch=50)
    api.start()
    yield api, KinematicsClient(f"http://127.0.0.1:{api.port}")
    api.stop()


def test_world_obstacles_collision_and_planning(planning_service):
    from amt.dtp.ise.lib.control.motion.planning import SphereObstacle
    from amt.dtp.ise.lib.control.robot.models import PlanMove
    api, client = planning_service
    assert client.kinematics_info().planning_backends == ["fake"]
    create(client, "arm")
    assert client.world("arm").obstacles == []
    ball = SphereObstacle(name="ball", pose=pose(1.2, 0, 0.5), radius=0.05)
    world = client.set_world("arm", [ball], reference="world", base_pose=pose(1.0, 0, 0))
    assert world.reference == "world" and world.revision == 1 and world.obstacles[0]["name"] == "ball"
    check = client.world_collision("arm", [HOME, {**HOME, "X": 0.2}])
    assert not check.collision_free and check.first_collision == 1 and "ball" in check.results[1].detail
    planned = client.sample_trajectory("arm", HOME, PlanMove(joints={"X": 0.4}), dt=0.01)
    xs = [(j["X"], j["Z"]) for j in planned.joints]
    assert max(z for _, z in xs) > 0.9 and math.isclose(planned.joints[-1]["X"], 0.4)  # over the ball
    assert client.clear_world("arm").obstacles == []
    assert code_of(lambda: client.set_world("arm", [ball, ball])) == (422, None)  # duplicate names: validation
    status, code = code_of(lambda: client.set_world("arm", [ball], reference="world"))  # no base pose
    assert status == 422


def test_world_unsupported_without_a_planner(service):
    from amt.dtp.ise.lib.control.robot.models import PlanMove
    api, client, _ = service
    create(client, "arm")
    assert code_of(lambda: client.world("arm")) == (422, "UNSUPPORTED")
    assert code_of(lambda: client.sample_trajectory("arm", HOME, PlanMove(joints={"X": 0.4}), dt=0.01)) == (422, "NO_PLANNER")


def test_create_with_fragments(service):
    api, client, _ = service
    arm = client.create_solver(urdf_text=URDF_TOOL0, xrdf_text=ROBOT_XRDF_JSON, name="arm", upload="json",
                               fragments=[FRAGMENT], combination={"stack": [{"fragment": "P1", "prefix": "p1",
                                                                             "parent": "tool0"}], "active_tcp": "p1/tcp"})
    kinds = [f.kind for f in arm.files]
    assert kinds == ["urdf", "xrdf", "robot_xrdf", "fragment", "combination"]
    merged = client.download_solver_file("arm", arm.files[1].name).decode()
    assert "p1/tcp" in merged and "sha256" in merged
    assert '"wrist":' in merged and '"base":' in merged  # ignores for tool0's link and the link across its joint
    bad = {"stack": [{"fragment": "P1", "prefix": "p1", "parent": "tool0"}], "active_tcp": "p1/nope"}
    assert code_of(lambda: client.create_solver(urdf_text=URDF, xrdf_text=ROBOT_XRDF_JSON, upload="json",
                                                fragments=[FRAGMENT], combination=bad)) == (422, "INVALID_FRAGMENT")
    assert code_of(lambda: client.create_solver(urdf_text=URDF, xrdf_text=ROBOT_XRDF_JSON, upload="json",
                                                fragments=[FRAGMENT])) == (422, "INVALID_FRAGMENT")

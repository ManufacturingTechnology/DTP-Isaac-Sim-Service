"""Named pose models (ADR-0014): validation, normalization, converters and their round trips, and the
pose model of the robot actuators (numpy arrays go through from_numpy)."""
import math

import numpy as np
import pytest
from pydantic import ValidationError

from amt.dtp.ise.lib.control.transducer.models import (
    FramedPose,
    ManipulatorRobotActuatorPose,
    Pose,
    Quaternion,
    Vector3,
)


def test_quaternion_is_normalized_and_zero_is_rejected():
    q = Quaternion(w=2.0, x=0.0, y=0.0, z=0.0)
    assert (q.w, q.x, q.y, q.z) == (1.0, 0.0, 0.0, 0.0)
    q = Quaternion(w=1.0, x=1.0, y=0.0, z=0.0)
    assert math.isclose(q.w, math.sqrt(0.5)) and math.isclose(q.x, math.sqrt(0.5))
    with pytest.raises(ValidationError, match="must not be zero"):
        Quaternion(w=0, x=0, y=0, z=0)
    with pytest.raises(ValidationError, match="finite"):
        Quaternion(w=float("nan"), x=0, y=0, z=0)
    with pytest.raises(ValidationError, match="finite"):
        Vector3(x=float("inf"), y=0, z=0)


def test_named_fields_only_on_the_wire():
    pose = Pose.model_validate_json('{"position": {"x": 1, "y": 2, "z": 3}, "orientation": {"w": 1, "x": 0, "y": 0, "z": 0}}')
    assert pose.position.to_numpy().tolist() == [1, 2, 3]
    with pytest.raises(ValidationError):  # no bare arrays
        Pose.model_validate_json('{"position": [1, 2, 3], "orientation": [1, 0, 0, 0]}')
    with pytest.raises(ValidationError):  # unknown fields are rejected
        Quaternion.model_validate({"w": 1, "x": 0, "y": 0, "z": 0, "q": 1})
    framed = FramedPose(position=pose.position, orientation=pose.orientation, frame="base")
    assert framed.model_dump()["frame"] == "base"


def test_wxyz_xyzw_round_trips():
    q = Quaternion.from_axis_angle([1, 0, 0], math.pi / 2)
    assert np.allclose(q.to_wxyz(), [math.sqrt(0.5), math.sqrt(0.5), 0, 0])
    assert np.allclose(q.to_xyzw(), [math.sqrt(0.5), 0, 0, math.sqrt(0.5)])  # Warp / SciPy / ROS order
    assert Quaternion.from_wxyz(q.to_wxyz()) == q
    assert Quaternion.from_xyzw(q.to_xyzw()) == q
    with pytest.raises(ValueError, match="needs 4 values"):
        Quaternion.from_wxyz([1, 0, 0])


@pytest.mark.parametrize("rotvec", [[0.0, 0.0, 0.0], [math.pi / 2, 0, 0], [0.1, -0.2, 0.3], [0, 0, 3.0]])
def test_rotvec_and_matrix_round_trips(rotvec):
    q = Quaternion.from_rotvec(rotvec)
    assert np.allclose(q.to_rotvec(), rotvec, atol=1e-12)
    assert np.allclose(Quaternion.from_matrix(q.to_matrix()).to_wxyz() * np.sign(q.w or 1), q.to_wxyz(), atol=1e-12)
    # the matrix rotates like the quaternion
    v = np.array([0.3, -0.4, 0.5])
    assert np.allclose(q.to_matrix() @ v, q.rotate(v))


def test_rotvec_matches_scipy_convention():
    # UR's rotation vector [pi/2, 0, 0] is 90 degrees about X: x+y-> y maps to z
    q = Quaternion.from_rotvec([math.pi / 2, 0, 0])
    assert np.allclose(q.rotate([0, 1, 0]), [0, 0, 1])


def test_pose_algebra():
    a = Pose.from_numpy([1, 0, 0], Quaternion.from_axis_angle([0, 0, 1], math.pi / 2).to_wxyz())
    b = Pose.from_numpy([1, 0, 0], [1, 0, 0, 0])
    c = a * b
    assert np.allclose(c.position.to_numpy(), [1, 1, 0])
    identity = a.inverse() * a
    assert np.allclose(identity.position.to_numpy(), 0, atol=1e-12)
    assert identity.orientation.angle_to(Quaternion.identity()) < 1e-6
    assert math.isclose(a.orientation.angle_to(Quaternion.identity()), math.pi / 2)


def test_to_gf_needs_pxr():
    pytest.importorskip("pxr")
    q = Quaternion.from_axis_angle([1, 0, 0], math.pi / 2)
    gf = q.to_gf()
    assert math.isclose(gf.GetReal(), q.w)
    assert Quaternion.from_gf(gf) == q


def test_actuator_pose_uses_named_models():
    pose = ManipulatorRobotActuatorPose(position=Vector3(x=0.3, y=0, z=0.5), orientation=Quaternion(w=0, x=1, y=0, z=0))
    assert pose.model_dump(mode="json") == {
        "position": {"x": 0.3, "y": 0.0, "z": 0.5}, "orientation": {"w": 0.0, "x": 1.0, "y": 0.0, "z": 0.0},
        "frame_of_reference": "local"}
    position, orientation = pose.to_numpy()
    assert position.tolist() == [0.3, 0, 0.5] and orientation.tolist() == [0, 1, 0, 0]
    again = ManipulatorRobotActuatorPose.from_numpy(position, orientation, "global")
    assert again.position == pose.position and again.frame_of_reference == "global"
    with pytest.raises(ValidationError):  # tuples are no longer accepted
        ManipulatorRobotActuatorPose(position=(0.3, 0, 0.5), orientation=(0, 1, 0, 0))


def test_actuator_pose_rejects_numpy_arrays_use_from_numpy():
    with pytest.raises(ValidationError):  # no implicit numpy conversion (ADR-0014)
        ManipulatorRobotActuatorPose(position=np.array([0.1, 0.2, 0.3]), orientation=np.array([2.0, 0, 0, 0]),
                                     frame_of_reference="global")
    pose = ManipulatorRobotActuatorPose.from_numpy(np.array([0.1, 0.2, 0.3]), np.array([2.0, 0, 0, 0]), "global")
    assert pose.orientation.w == 1.0 and pose.position.z == 0.3

"""Interpolation and time parameterization: trapezoidal time scaling sampled at the
physics step, synchronized PTP, LIN with slerp, and the jump check."""
import math

import numpy as np
import pytest

from amt.dtp.ise.lib.control.motion.trajectory import (
    DurationTooShort,
    check_steps,
    lin_samples,
    ptp_samples,
    slerp,
    time_scaling,
)

DT = 1.0 / 60.0


def derivatives(samples: np.ndarray, start, dt: float):
    q = np.vstack([np.atleast_2d(start), samples])
    v = np.diff(q, axis=0) / dt
    a = np.diff(np.vstack([np.zeros((1, q.shape[1])), v, np.zeros((1, q.shape[1]))]), axis=0) / dt
    return v, a


@pytest.mark.parametrize("v,a", [(1.0, 4.0), (0.5, 100.0), (10.0, 2.0), (2.0, None)])
def test_time_scaling_respects_limits_and_ends_on_a_step(v, a):
    scaling = time_scaling(v, a, DT)
    s = scaling.s
    assert s[-1] == 1.0 and np.all(np.diff(s) >= -1e-12) and np.all(s > 0)
    assert scaling.steps == math.ceil(scaling.min_duration / DT - 1e-9)
    velocity = np.diff(np.concatenate([[0.0], s])) / DT
    assert velocity.max() <= v * (1 + 1e-9)
    if a is not None:
        # The trapezoid's velocity changes by at most a×dt per step (sampled at step ends).
        assert np.abs(np.diff(velocity)).max() <= a * DT * (1 + 1e-6) + 1e-9


def test_time_scaling_minimum_durations():
    assert math.isclose(time_scaling(1.0, 4.0, DT).min_duration, 1.0 + 0.25)  # trapezoid: 1/v + v/a
    assert math.isclose(time_scaling(10.0, 4.0, DT).min_duration, 1.0)  # triangle: 2/sqrt(a)
    assert math.isclose(time_scaling(2.0, None, DT).min_duration, 0.5)  # constant velocity
    assert time_scaling(math.inf, None, DT).steps == 1  # nothing to do: one step at the target


def test_duration_stretches_and_too_short_is_rejected():
    scaling = time_scaling(1.0, 4.0, DT, duration=3.0)
    assert scaling.steps == 180 and math.isclose(scaling.duration, 3.0)
    with pytest.raises(DurationTooShort) as caught:
        time_scaling(1.0, 4.0, DT, duration=1.0)
    assert math.isclose(caught.value.min_duration, 1.25)


def test_ptp_is_synchronized_and_within_each_joints_limits():
    start, goal = [0.0, 0.0, 0.0], [1.0, -0.5, 0.0]
    v_limits, a_limits = [2.0, 0.1, 1.0], [10.0, 10.0, 10.0]
    samples, scaling = ptp_samples(start, goal, v_limits, a_limits, DT)
    assert np.allclose(samples[-1], goal)
    # joint 1 (0.5 at 0.1/s) is the slowest: about 5 s
    assert 5.0 <= scaling.duration <= 5.0 + 0.1 + DT
    velocity, _ = derivatives(samples, start, DT)
    for j in range(3):
        assert np.abs(velocity[:, j]).max() <= v_limits[j] * (1 + 1e-9)
    # synchronized: every moving joint is at the same fraction of its distance
    fraction = (samples[:, :2] - np.array(start[:2])) / (np.array(goal[:2]) - np.array(start[:2]))
    assert np.allclose(fraction[:, 0], fraction[:, 1])


def test_lin_is_straight_with_slerp_and_respects_linear_and_angular_limits():
    q90 = [math.cos(math.pi / 4), 0, 0, math.sin(math.pi / 4)]
    positions, orientations, scaling = lin_samples([0, 0, 0], [1, 0, 0, 0], [0.3, 0.4, 0], q90,
                                                   velocity=0.25, acceleration=1.2, angular_velocity=math.radians(30),
                                                   angular_acceleration=None, dt=DT)
    assert np.allclose(positions[-1], [0.3, 0.4, 0]) and np.allclose(orientations[-1], q90)
    direction = positions / np.linalg.norm(positions, axis=1, keepdims=True)
    assert np.allclose(direction, [0.6, 0.8, 0])  # a straight line
    speed = np.linalg.norm(np.diff(np.vstack([[0, 0, 0], positions]), axis=0), axis=1) / DT
    assert speed.max() <= 0.25 * (1 + 1e-9)
    # 90 degrees at 30 deg/s sets the velocity (s' = 1/3 per s); the 0.5 m line's 1.2 m/s² still sets the
    # acceleration (s'' = 2.4): 1/v + v/a = 3 + 0.139 s
    assert math.isclose(scaling.min_duration, 3.0 + (1 / 3) / 2.4)
    assert scaling.steps == math.ceil(scaling.min_duration / DT)


def test_slerp_takes_the_short_way():
    a = [1, 0, 0, 0]
    b = [-math.cos(0.1), 0, 0, -math.sin(0.1)]  # the same rotation as +0.2 rad about z, sign flipped
    mid = slerp(a, b, np.array([0.5]))[0]
    assert np.allclose(np.abs(mid), [math.cos(0.05), 0, 0, math.sin(0.05)])


def test_check_steps_accepts_and_rejects():
    start = [0.0, 0.0]
    samples = np.array([[0.1, 0.0], [0.2, 0.05], [0.3, 0.5]])
    assert check_steps(start, samples, [0.1, 0.5]) is None
    violation = check_steps(start, samples, [0.1, 0.2])
    assert (violation.step, violation.joint) == (2, 1)
    assert math.isclose(violation.change, 0.45) and violation.allowed == 0.2
    assert check_steps(start, np.array([[0.11, 0]]), [0.1, 1]).step == 0  # measured from the start

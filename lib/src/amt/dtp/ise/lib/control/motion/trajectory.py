"""Interpolation and time parameterization, sampled at the physics step.

- :func:`time_scaling`: a rest-to-rest trapezoidal velocity profile over the path parameter
  ``s`` in [0, 1] (or, with a jerk limit, a jerk-limited double-S), with its duration rounded **up** to a whole
  number of steps and the profile stretched to exactly that (stretching only lowers velocities and accelerations).
  Samples are at ``k × dt``, ``k = 1…N``; the last is exactly ``s = 1``.
- :class:`CartesianPath`, :func:`circ_samples`: straight lines and circular arcs (through a via point) for the tool.
- Blending (the upper layer): :func:`blend_overlaps`, :func:`joint_chain_samples`, :func:`cartesian_chain_samples`
  superpose consecutive moves so the robot doesn't stop in between.
- :func:`ptp_samples`: synchronized joint interpolation (every joint starts and stops together; the
  slowest joint sets the timing).
- :func:`lin_samples`: Cartesian interpolation: linear position, slerp orientation, timed by the
  tighter of the linear and angular limits.
- :func:`check_steps`: the jump check (ADR-0013): per-step joint change against a per-joint maximum.

Units are SI (rad, m, s); orientations are wxyz arrays. Design: ``docs/src/Agents/Specs/robot-control.md``
("Interpolation and time parameterization", "Execution semantics").
"""
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np


class DurationTooShort(ValueError):
    """The requested duration is shorter than the limits allow (``min_duration``, s)."""
    error_code = "DURATION_TOO_SHORT"

    def __init__(self, duration: float, min_duration: float):
        super().__init__(f"duration {duration:.6g} s is shorter than the limits allow ({min_duration:.6g} s)")
        self.duration = duration
        self.min_duration = min_duration


class TooManySteps(ValueError):
    """The move would take more than ``max_steps`` steps."""
    error_code = "TOO_MANY_STEPS"

    def __init__(self, steps: int, max_steps: int):
        super().__init__(f"the move would take {steps} steps, more than the maximum of {max_steps}")
        self.steps = steps
        self.max_steps = max_steps


@dataclass
class TimeScaling:
    """Samples of the path parameter: ``s[k]`` at time ``(k + 1) × dt`` for ``k < steps``. ``ramp`` (s) is
    how long the profile accelerates (and, symmetrically, decelerates): blending overlaps moves only within
    these ramps."""
    s: np.ndarray
    dt: float
    min_duration: float
    ramp: float = 0.0

    @property
    def steps(self) -> int:
        return int(self.s.shape[0])

    @property
    def duration(self) -> float:
        return self.steps * self.dt

    @property
    def ramp_steps(self) -> int:
        """Whole steps within the acceleration (or deceleration) ramp."""
        return int(math.floor(self.ramp / self.dt + 1e-9))


def _limit(value: Optional[float]) -> float:
    return math.inf if value is None or not math.isfinite(value) or value <= 0 else float(value)


def _min_duration(v: float, a: float) -> float:
    if math.isinf(v):
        return 0.0
    if math.isinf(a):
        return 1.0 / v
    if v * v / a >= 1.0:  # triangular: the peak velocity is never reached
        return 2.0 / math.sqrt(a)
    return 1.0 / v + v / a


def _profile(t: np.ndarray, duration: float, a: float) -> np.ndarray:
    """s(t) of the rest-to-rest trapezoid of total ``duration`` with acceleration ``a`` (or a constant
    velocity if ``a`` is infinite), for 0 ≤ t ≤ duration."""
    if duration <= 0:
        return np.ones_like(t)
    if math.isinf(a):
        return np.clip(t / duration, 0.0, 1.0)
    # The cruise velocity v for which 1/v + v/a == duration (the smaller root).
    discriminant = max(0.0, a * a * duration * duration - 4.0 * a)
    v = (a * duration - math.sqrt(discriminant)) / 2.0
    t_a = v / a
    s = np.where(
        t < t_a, 0.5 * a * t * t,
        np.where(t <= duration - t_a, 0.5 * a * t_a * t_a + v * (t - t_a), 1.0 - 0.5 * a * (duration - t) ** 2))
    return np.clip(s, 0.0, 1.0)


def _scurve(v: float, a: float, j: float) -> Tuple[float, float, float, float, float]:
    """The rest-to-rest double-S (jerk-limited) profile over a unit distance with limits ``v``, ``a``, ``j``
    (finite, positive): ``(duration, ramp Ta, jerk time Tj, peak acceleration, peak velocity)``. After
    Biagiotti & Melchiorri, *Trajectory Planning for Automatic Machines and Robots* (2008), §3.4, with
    zero boundary velocities (symmetric: Td = Ta)."""
    h = 1.0
    if v * j >= a * a:
        tj = a / j
        ta = tj + v / a
    else:
        tj = math.sqrt(v / j)
        ta = 2.0 * tj
    tv = h / v - ta
    if tv < 0:  # the cruise velocity is not reached
        tv = 0.0
        tj = a / j
        delta = a ** 4 / j ** 2 + 4.0 * h * a
        ta = (a * a / j + math.sqrt(delta)) / (2.0 * a)
        if ta < 2.0 * tj:  # nor the peak acceleration
            tj = (h / (2.0 * j)) ** (1.0 / 3.0)
            ta = 2.0 * tj
    a_peak = j * tj
    v_peak = (ta - tj) * a_peak
    return 2.0 * ta + tv, ta, tj, a_peak, v_peak


def _scurve_positions(t: np.ndarray, duration: float, ta: float, tj: float, a_peak: float, v_peak: float,
                      j: float) -> np.ndarray:
    """s(t) of the profile from :func:`_scurve` (0 ≤ t ≤ duration)."""
    def accel(u: np.ndarray) -> np.ndarray:  # 0 ≤ u ≤ ta: the acceleration ramp, from rest
        return np.where(
            u < tj, j * u ** 3 / 6.0,
            np.where(u < ta - tj, a_peak / 6.0 * (3 * u * u - 3 * tj * u + tj * tj),
                     v_peak * ta / 2.0 - v_peak * (ta - u) + j * (ta - u) ** 3 / 6.0))
    t = np.clip(t, 0.0, duration)
    half = v_peak * ta / 2.0
    s = np.where(t <= ta, accel(np.minimum(t, ta)),
                 np.where(t < duration - ta, half + v_peak * (t - ta), 1.0 - accel(np.clip(duration - t, 0.0, ta))))
    return np.clip(s, 0.0, 1.0)


def time_scaling(velocity: float, acceleration: Optional[float], dt: float, duration: Optional[float] = None,
                 max_steps: Optional[int] = None, jerk: Optional[float] = None) -> TimeScaling:
    """Samples of ``s`` in [0, 1] for a rest-to-rest move with path-parameter limits ``velocity`` (1/s),
    ``acceleration`` (1/s², None or inf: unlimited), and ``jerk`` (1/s³, None or inf: unlimited; with a finite
    jerk and acceleration the profile is a jerk-limited double-S, else a trapezoid). ``duration`` (s)
    stretches the move; shorter than the limits allow raises :class:`DurationTooShort`; more than
    ``max_steps`` steps raises :class:`TooManySteps` (before anything is allocated). At least one step."""
    if not (dt > 0 and math.isfinite(dt)):
        raise ValueError(f"the step dt must be a positive number, not {dt}")
    v = math.inf if velocity is None else float(velocity)
    a = _limit(acceleration)
    jl = _limit(jerk)
    if not v > 0:
        raise ValueError("velocity must be positive")
    scurve = None
    if not math.isinf(jl) and not math.isinf(a):
        scurve = _scurve(v if not math.isinf(v) else 1e9, a, jl)  # unlimited velocity: the ramps decide
        minimum = scurve[0]
    else:
        minimum = _min_duration(v, a)
    target = minimum
    if duration is not None:
        if duration < minimum * (1 - 1e-9) - 1e-12:
            raise DurationTooShort(duration, minimum)
        target = float(duration)
    steps = max(1, math.ceil(target / dt - 1e-9))
    if max_steps is not None and steps > max_steps:
        raise TooManySteps(steps, max_steps)
    total = steps * dt
    t = np.arange(1, steps + 1, dtype=np.float64) * dt
    ramp = 0.0
    if minimum == 0.0 and target == 0.0:
        s = np.ones(steps)
    elif scurve is not None:
        # Uniform time stretching to exactly `total`: velocity, acceleration, and jerk only get lower.
        length, ta, tj, a_peak, v_peak = scurve
        c = total / length
        s = _scurve_positions(t / c, length, ta, tj, a_peak, v_peak, jl)
        ramp = ta * c
    else:
        # Stretch the profile to exactly `total`: acceleration scales with 1/c², c = total / target.
        c = total / target if target > 0 else 1.0
        a_used = a / (c * c) if not math.isinf(a) else a
        s = _profile(t, total, a_used) if target > 0 else np.ones(steps)
        if not math.isinf(a_used) and target > 0:
            discriminant = max(0.0, a_used * a_used * total * total - 4.0 * a_used)
            ramp = (a_used * total - math.sqrt(discriminant)) / 2.0 / a_used
    s[-1] = 1.0
    return TimeScaling(s=s, dt=dt, min_duration=minimum, ramp=ramp)


def ptp_samples(
    start: Sequence[float],
    goal: Sequence[float],
    velocity_limits: Sequence[Optional[float]],
    acceleration_limits: Sequence[Optional[float]],
    dt: float,
    duration: Optional[float] = None,
    max_steps: Optional[int] = None,
    jerk_limits: Optional[Sequence[Optional[float]]] = None,
) -> Tuple[np.ndarray, TimeScaling]:
    """Synchronized point-to-point joint motion: joint positions ``(N, n)`` at each step. Every joint
    follows the same normalized profile, scaled to its distance, so all start and stop together; the
    joint that needs longest sets the timing. Limits are per joint (None: unlimited); with jerk limits for
    every moving joint (and acceleration limits) the profile is jerk-limited (double-S)."""
    q0 = np.asarray(start, dtype=np.float64)
    q1 = np.asarray(goal, dtype=np.float64)
    scaling = joint_time_scaling(q1 - q0, velocity_limits, acceleration_limits, dt, duration, max_steps, jerk_limits)
    return q0 + np.outer(scaling.s, q1 - q0), scaling


def joint_time_scaling(delta: Sequence[float], velocity_limits: Sequence[Optional[float]],
                       acceleration_limits: Sequence[Optional[float]], dt: float, duration: Optional[float] = None,
                       max_steps: Optional[int] = None, jerk_limits: Optional[Sequence[Optional[float]]] = None
                       ) -> TimeScaling:
    """The synchronized time scaling of a straight joint-space move by ``delta`` (see :func:`ptp_samples`)."""
    distance = np.abs(np.asarray(delta, dtype=np.float64))
    jerks = list(jerk_limits) if jerk_limits is not None else [None] * len(distance)
    v_s = a_s = j_s = math.inf
    jerk_known = True
    for d, v, a, j in zip(distance, velocity_limits, acceleration_limits, jerks):
        if d <= 1e-12:
            continue
        v_s = min(v_s, _limit(v) / d)
        a_s = min(a_s, _limit(a) / d)
        if math.isinf(_limit(j)):
            jerk_known = False
        else:
            j_s = min(j_s, _limit(j) / d)
    if math.isinf(v_s) and not math.isinf(a_s):
        # Only accelerations limit: a triangular profile.
        v_s = math.sqrt(a_s)
    return time_scaling(v_s, a_s, dt, duration, max_steps, jerk=j_s if jerk_known else None)


def slerp(q0: Sequence[float], q1: Sequence[float], s: np.ndarray) -> np.ndarray:
    """Spherical linear interpolation of wxyz quaternions along the shorter arc: ``(len(s), 4)``."""
    a = np.asarray(q0, dtype=np.float64)
    b = np.asarray(q1, dtype=np.float64)
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    dot = float(np.dot(a, b))
    if dot < 0:
        b, dot = -b, -dot
    s = np.asarray(s, dtype=np.float64).reshape(-1, 1)
    if dot > 0.9995:  # nearly parallel: linear interpolation, renormalized
        result = a + s * (b - a)
        return result / np.linalg.norm(result, axis=1, keepdims=True)
    theta = math.acos(dot)
    return (np.sin((1 - s) * theta) * a + np.sin(s * theta) * b) / math.sin(theta)


def lin_samples(
    start_position: Sequence[float],
    start_orientation: Sequence[float],
    goal_position: Sequence[float],
    goal_orientation: Sequence[float],
    velocity: float,
    acceleration: Optional[float],
    angular_velocity: float,
    angular_acceleration: Optional[float],
    dt: float,
    duration: Optional[float] = None,
    max_steps: Optional[int] = None,
    jerk: Optional[float] = None,
    angular_jerk: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, TimeScaling]:
    """Linear Cartesian motion: positions ``(N, 3)`` and wxyz orientations ``(N, 4)`` at each step.
    Limits: ``velocity`` (m/s), ``acceleration`` (m/s²), ``angular_velocity`` (rad/s),
    ``angular_acceleration`` (rad/s²), optionally ``jerk`` (m/s³) and ``angular_jerk`` (rad/s³); the tighter
    of linear and angular sets the timing."""
    path = CartesianPath.line(start_position, start_orientation, goal_position, goal_orientation)
    scaling = path.time_scaling(velocity, acceleration, angular_velocity, angular_acceleration, dt, duration, max_steps,
                                jerk, angular_jerk)
    positions, orientations = path.at(scaling.s)
    return positions, orientations, scaling


class InvalidCircle(ValueError):
    """The three points of a circular move don't define a circle (coincident or collinear)."""
    error_code = "INVALID_CIRCLE"


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.atleast_2d(a)
    b = np.atleast_2d(b)
    w1, x1, y1, z1 = a.T
    w2, x2, y2, z2 = b.T
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
                     w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                     w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], axis=1)


def _quat_log(q: np.ndarray) -> np.ndarray:
    """The rotation vector (rad) of one wxyz quaternion, along the shorter arc."""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.linalg.norm(q)
    if q[0] < 0:
        q = -q
    s = float(np.linalg.norm(q[1:]))
    if s < 1e-12:
        return np.zeros(3)
    return q[1:] / s * 2.0 * math.atan2(s, q[0])


def _quat_exp(v: np.ndarray) -> np.ndarray:
    """wxyz quaternions ``(N, 4)`` of rotation vectors ``(N, 3)``."""
    v = np.atleast_2d(v)
    angle = np.linalg.norm(v, axis=1)
    half = angle / 2.0
    scale = np.where(angle > 1e-12, np.sin(half) / np.where(angle > 1e-12, angle, 1.0), 0.5)
    return np.column_stack([np.cos(half), v * scale[:, None]])


def _rotate(axis: np.ndarray, angle: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rodrigues: ``v`` (3,) rotated about the unit ``axis`` by each ``angle`` (N,)."""
    angle = np.asarray(angle, dtype=np.float64).reshape(-1, 1)
    cross = np.cross(axis, v)
    return v * np.cos(angle) + cross * np.sin(angle) + axis * float(np.dot(axis, v)) * (1 - np.cos(angle))


@dataclass
class CartesianPath:
    """A tool-frame path over ``s`` in [0, 1]: a straight line or a circular arc for the position, slerp
    for the orientation (wxyz, from ``start_orientation`` to ``end_orientation``). ``length`` (m) and
    ``rotation`` (rad) set the timing."""
    start_position: np.ndarray
    start_orientation: np.ndarray
    end_position: np.ndarray
    end_orientation: np.ndarray
    length: float
    rotation: float
    center: Optional[np.ndarray] = None  # arcs: the center, the unit normal, and the angle (rad)
    normal: Optional[np.ndarray] = None
    angle: float = 0.0

    @staticmethod
    def _orientations(o0: Sequence[float], o1: Sequence[float]) -> Tuple[np.ndarray, np.ndarray, float]:
        a = np.asarray(o0, dtype=np.float64)
        b = np.asarray(o1, dtype=np.float64)
        a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
        return a, b, 2.0 * math.acos(min(1.0, abs(float(np.dot(a, b)))))

    @classmethod
    def line(cls, p0: Sequence[float], o0: Sequence[float], p1: Sequence[float], o1: Sequence[float]) -> "CartesianPath":
        p0 = np.asarray(p0, dtype=np.float64).reshape(3)
        p1 = np.asarray(p1, dtype=np.float64).reshape(3)
        a, b, rotation = cls._orientations(o0, o1)
        return cls(p0, a, p1, b, float(np.linalg.norm(p1 - p0)), rotation)

    @classmethod
    def arc(cls, p0: Sequence[float], o0: Sequence[float], via: Sequence[float], p1: Sequence[float],
            o1: Sequence[float]) -> "CartesianPath":
        """The circular arc from ``p0`` through ``via`` to ``p1`` (as MoveIt Pilz CIRC's ``interim`` point and
        a circular move's via point in industrial robot languages). Raises :class:`InvalidCircle`."""
        p0 = np.asarray(p0, dtype=np.float64).reshape(3)
        pv = np.asarray(via, dtype=np.float64).reshape(3)
        p1 = np.asarray(p1, dtype=np.float64).reshape(3)
        u, w = pv - p0, p1 - p0
        normal = np.cross(u, w)
        scale = max(float(np.linalg.norm(u)), float(np.linalg.norm(w)), 1e-12)
        if np.linalg.norm(normal) < 1e-9 * scale * scale or min(np.linalg.norm(u), np.linalg.norm(w),
                                                                   np.linalg.norm(p1 - pv)) < 1e-9:
            raise InvalidCircle("the start, via, and target positions of a circular move must be distinct and not on "
                                "one line")
        # Circumcenter of the triangle (p0, via, p1).
        uu, ww, uw = float(np.dot(u, u)), float(np.dot(w, w)), float(np.dot(u, w))
        denominator = 2.0 * (uu * ww - uw * uw)
        alpha = ww * (uu - uw) / denominator
        beta = uu * (ww - uw) / denominator
        center = p0 + alpha * u + beta * w
        n = normal / np.linalg.norm(normal)
        r0 = p0 - center

        def angle_of(p: np.ndarray) -> float:
            r = p - center
            return math.atan2(float(np.dot(np.cross(r0, r), n)), float(np.dot(r0, r))) % (2 * math.pi)
        # n = (via − p0) × (p1 − p0): turning about +n from p0, the arc passes via before p1.
        angle = angle_of(p1)
        a, b, rotation = cls._orientations(o0, o1)
        radius = float(np.linalg.norm(r0))
        return cls(p0, a, p1, b, radius * angle, rotation, center, n, angle)

    def at(self, s: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Positions ``(N, 3)`` and wxyz orientations ``(N, 4)`` at ``s``."""
        s = np.asarray(s, dtype=np.float64).reshape(-1)
        return self.displacement(s) + self.start_position, slerp(self.start_orientation, self.end_orientation, s)

    def displacement(self, s: np.ndarray) -> np.ndarray:
        """Position minus the start position at each ``s`` ``(N, 3)``."""
        s = np.asarray(s, dtype=np.float64).reshape(-1)
        if self.center is None:
            return np.outer(s, self.end_position - self.start_position)
        positions = self.center + _rotate(self.normal, s * self.angle, self.start_position - self.center)
        positions[s >= 1.0] = self.end_position
        return positions - self.start_position

    @property
    def body_rotation(self) -> np.ndarray:
        """The orientation change as a rotation vector in the start's frame (slerp = start ⊗ exp(s × this))."""
        return _quat_log(_quat_mul(self.start_orientation * np.array([1, -1, -1, -1]), self.end_orientation)[0])

    def time_scaling(self, velocity: float, acceleration: Optional[float], angular_velocity: float,
                     angular_acceleration: Optional[float], dt: float, duration: Optional[float] = None,
                     max_steps: Optional[int] = None, jerk: Optional[float] = None,
                     angular_jerk: Optional[float] = None) -> TimeScaling:
        v_s = a_s = j_s = math.inf
        jerk_known = True
        for distance, v, a, j in ((self.length, velocity, acceleration, jerk),
                                  (self.rotation, angular_velocity, angular_acceleration, angular_jerk)):
            if distance <= 1e-12:
                continue
            v_s = min(v_s, _limit(v) / distance)
            a_s = min(a_s, _limit(a) / distance)
            if math.isinf(_limit(j)):
                jerk_known = False
            else:
                j_s = min(j_s, _limit(j) / distance)
        if math.isinf(v_s) and not math.isinf(a_s):
            v_s = math.sqrt(a_s)
        return time_scaling(v_s, a_s, dt, duration, max_steps, jerk=j_s if jerk_known else None)


def circ_samples(start_position, start_orientation, via_position, goal_position, goal_orientation, velocity: float,
                 acceleration: Optional[float], angular_velocity: float, angular_acceleration: Optional[float],
                 dt: float, duration: Optional[float] = None, max_steps: Optional[int] = None,
                 jerk: Optional[float] = None, angular_jerk: Optional[float] = None
                 ) -> Tuple[np.ndarray, np.ndarray, TimeScaling]:
    """Circular Cartesian motion through ``via_position``: positions ``(N, 3)`` and wxyz orientations
    ``(N, 4)`` at each step, timed by the arc length and the rotation like :func:`lin_samples`."""
    path = CartesianPath.arc(start_position, start_orientation, via_position, goal_position, goal_orientation)
    scaling = path.time_scaling(velocity, acceleration, angular_velocity, angular_acceleration, dt, duration, max_steps,
                                jerk, angular_jerk)
    positions, orientations = path.at(scaling.s)
    return positions, orientations, scaling


# ---- blending (superposition of consecutive moves) -------------------------------------------------------

def blend_overlaps(scalings: Sequence[TimeScaling], cnt: Sequence[float]) -> List[int]:
    """The overlap (steps) between consecutive moves: ``cnt[m]`` (0…1, the fraction a CNT value 0…100 gives)
    of the shorter of move *m*'s deceleration ramp and move *m+1*'s acceleration ramp."""
    result = []
    for m in range(len(scalings) - 1):
        limit = min(scalings[m].ramp_steps, scalings[m + 1].ramp_steps, scalings[m].steps - 1, scalings[m + 1].steps - 1)
        result.append(max(0, int(math.floor(max(0.0, min(1.0, cnt[m])) * limit + 1e-9))))
    return result


def _segment_s(scaling: TimeScaling, k: np.ndarray) -> np.ndarray:
    """s of a move at chain steps ``k`` (1 = its first step): 0 before it starts, 1 after it ends."""
    index = np.clip(k - 1, 0, scaling.steps - 1)
    return np.where(k <= 0, 0.0, np.where(k > scaling.steps, 1.0, scaling.s[index]))


def chain_starts(scalings: Sequence[TimeScaling], overlaps: Sequence[int]) -> Tuple[List[int], int]:
    """The step (0-based) at which each move of a chain starts, and the chain's total steps."""
    starts = [0]
    for m in range(1, len(scalings)):
        starts.append(starts[-1] + scalings[m - 1].steps - overlaps[m - 1])
    return starts, starts[-1] + scalings[-1].steps


def joint_chain_samples(start: Sequence[float], goals: Sequence[Sequence[float]], scalings: Sequence[TimeScaling],
                        overlaps: Sequence[int]) -> Tuple[np.ndarray, List[int]]:
    """Joint positions ``(N, n)`` of consecutive joint-space moves (``start`` → ``goals[0]`` → …) whose
    profiles overlap by ``overlaps`` steps (superposition: in an overlap the first move decelerates while the
    next accelerates, so the velocity never stops and stays within the larger of the two moves' velocities).
    Without overlaps every move ends at rest. Also each move's first step (0-based)."""
    starts, total = chain_starts(scalings, overlaps)
    k = np.arange(1, total + 1, dtype=np.int64)
    q = np.tile(np.asarray(start, dtype=np.float64), (total, 1))
    previous = np.asarray(start, dtype=np.float64)
    for goal, scaling, first in zip(goals, scalings, starts):
        goal = np.asarray(goal, dtype=np.float64)
        q += np.outer(_segment_s(scaling, k - first), goal - previous)
        previous = goal
    q[-1] = previous
    return q, starts


def cartesian_chain_samples(paths: Sequence[CartesianPath], scalings: Sequence[TimeScaling],
                            overlaps: Sequence[int]) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """Positions ``(N, 3)`` and wxyz orientations ``(N, 4)`` of consecutive Cartesian moves (each starting
    where the previous ends) superposed like :func:`joint_chain_samples`: displacements add, and the
    orientation is the first start rotated by each move's body rotation scaled by its ``s``."""
    starts, total = chain_starts(scalings, overlaps)
    k = np.arange(1, total + 1, dtype=np.int64)
    positions = np.tile(paths[0].start_position, (total, 1))
    orientations = np.tile(paths[0].start_orientation, (total, 1))
    for path, scaling, first in zip(paths, scalings, starts):
        s = _segment_s(scaling, k - first)
        positions = positions + path.displacement(s)
        orientations = _quat_mul(orientations, _quat_exp(np.outer(s, path.body_rotation)))
    positions[-1] = paths[-1].end_position
    orientations[-1] = paths[-1].end_orientation
    return positions, orientations / np.linalg.norm(orientations, axis=1, keepdims=True), starts


@dataclass
class StepViolation:
    """A step that exceeds the jump check: sample ``step`` (0-based; its change is measured from the
    previous sample, or from the start for step 0), ``joint`` index, the change, and the maximum."""
    step: int
    joint: int
    change: float
    allowed: float


def check_steps(start: Sequence[float], samples: np.ndarray, max_step: Sequence[float]) -> Optional[StepViolation]:
    """The jump check (ADR-0013): the first step where a joint changes by more than ``max_step`` (per
    joint, e.g. velocity limit × dt × margin), or None."""
    q = np.vstack([np.asarray(start, dtype=np.float64).reshape(1, -1), np.asarray(samples, dtype=np.float64)])
    change = np.abs(np.diff(q, axis=0))
    allowed = np.asarray(max_step, dtype=np.float64)
    bad = change > allowed * (1 + 1e-9) + 1e-12
    if not bad.any():
        return None
    step, joint = np.argwhere(bad)[0]
    return StepViolation(int(step), int(joint), float(change[step, joint]), float(allowed[joint]))

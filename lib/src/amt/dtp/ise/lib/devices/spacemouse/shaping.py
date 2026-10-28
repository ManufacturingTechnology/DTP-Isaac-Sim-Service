"""Per-axis response shaping: deadzone and expo."""


def shape_axis(value: float, deadzone: float = 0.04, expo: float = 0.35) -> float:
    """Apply a deadzone and expo to one axis, in isolation.

    Deliberately takes one scalar rather than a vector: every axis is shaped without reference to any other, so
    moving one axis never changes the scaling of the rest. Just outside the deadzone the response starts at zero, and
    it still reaches 1.0 at full deflection. `expo` blends linear (0.0) and cubic (1.0) response.
    """
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    x = min((magnitude - deadzone) / (1.0 - deadzone), 1.0)
    shaped = expo * x * x * x + (1.0 - expo) * x
    return shaped if value > 0.0 else -shaped

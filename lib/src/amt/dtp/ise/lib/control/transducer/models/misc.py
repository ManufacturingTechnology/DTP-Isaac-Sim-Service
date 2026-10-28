from typing import List, Optional, Sequence
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Every model forbids unknown fields (extra="forbid"), so a misspelt field in a command is a
# validation error rather than silently ignored (which would leave that actuator unchanged).


class DistanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    distance: float = Field(
        title="Distance",
        description="The distance in meters."
    )


class VelocityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    velocity: float = Field(
        title="Velocity",
        description="The surface velocity of the conveyor in m/s.",
    )


# --8<-- [start:ColorModel]
class ColorModel(BaseModel):
    """An RGB color. All three channels set is a color; all three None is colorless
    (no color override)."""
    model_config = ConfigDict(extra="forbid")

    r: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        title="Red",
        description="Red color intensity from 0.0 to 1.0, or null for colorless.",
    )
    g: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        title="Green",
        description="Green color intensity from 0.0 to 1.0, or null for colorless.",
    )
    b: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        title="Blue",
        description="Blue color intensity from 0.0 to 1.0, or null for colorless.",
    )

    @model_validator(mode="after")
    def _validate(self) -> "ColorModel":
        channels = (self.r, self.g, self.b)
        if any(c is None for c in channels) and not all(c is None for c in channels):
            raise ValueError("r, g, and b must either all be set (a color) or all be null (colorless)")
        return self
# --8<-- [end:ColorModel]

    @staticmethod
    def colorless() -> "ColorModel":
        return ColorModel()

    @property
    def is_colorless(self) -> bool:
        return self.r is None

    def to_sequence(self) -> List[float]:
        if self.is_colorless:
            raise ValueError("a colorless ColorModel has no color sequence")
        return [self.r, self.g, self.b]

    @staticmethod
    def from_sequence(sequence: Sequence[int | float]) -> "ColorModel":
        if not isinstance(sequence, Sequence) or not all(isinstance(e, (int, float)) for e in sequence):
            raise TypeError("sequence must be a Sequence[float]")
        if not len(sequence) == 3:
            raise TypeError("sequence must be 3 long")
        return ColorModel(r=float(sequence[0]), g=float(sequence[1]), b=float(sequence[2]))

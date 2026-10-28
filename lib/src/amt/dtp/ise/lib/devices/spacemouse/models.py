"""The data a SpaceMouse produces: which device it is, and its state. Pydantic models, so a state can be sent over
the network as JSON (e.g. a device on one machine driving a remote Isaac Sim instance)."""
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

Vector3 = Tuple[float, float, float]


class DeviceInfo(BaseModel):
    """A connected multi-axis controller."""
    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Product string reported by the device, e.g. 'SpaceMouse Compact'")
    manufacturer: str = Field("", description="Manufacturer string reported by the device")
    vendor_id: int = Field(description="USB vendor ID")
    product_id: int = Field(description="USB product ID")
    version: int = Field(0, description="Device release number (bcdDevice)")
    path: str = Field(description="OS device path (Windows interface path or Linux /dev/hidrawN); opaque")
    platform: str = Field(description="'windows' or 'linux'")
    driver: str = Field("", description="Driver bound to the HID interface: e.g. '3dxhid' (3Dconnexion's), "
                                        "'' for Windows' generic HID driver, 'hid-generic' on Linux")

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04X}:{self.product_id:04X}"

    def describe(self) -> str:
        driver = self.driver or ("Windows generic HID" if self.platform == "windows" else "unknown")
        return f"{self.name or 'unnamed device'} ({self.usb_id}, rev {self.version:04X}, driver: {driver})"


class SpaceMouseState(BaseModel):
    """The latest state of a SpaceMouse.

    Axes are in the device's own HID frame, normalized per axis to [-1, 1] from the device's declared logical range,
    each independent of the others: `translation` = (X, Y, Z), `rotation` = (Rx, Ry, Rz). Which physical motion is
    positive is the device's convention, not ours; applications map axes to their own meaning (see
    `docs` for the extension's camera mapping).
    """
    translation: Vector3 = (0.0, 0.0, 0.0)
    rotation: Vector3 = (0.0, 0.0, 0.0)
    buttons: List[int] = Field(default_factory=list, description="Pressed buttons, 1-based HID button numbers")
    timestamp: float = Field(0.0, description="time.monotonic() of the last report, on the reading machine")
    connected: bool = False
    device: Optional[DeviceInfo] = None

    @property
    def button_mask(self) -> int:
        """Pressed buttons as a bit mask: button 1 -> bit 0 (1), button 2 -> bit 1 (2)."""
        mask = 0
        for b in self.buttons:
            if b >= 1:
                mask |= 1 << (b - 1)
        return mask

    @property
    def is_idle(self) -> bool:
        return not any(self.translation) and not any(self.rotation) and not self.buttons

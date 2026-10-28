"""Platform-independent HID: parse a report descriptor into a report layout, decode input reports, normalize axes.

Written from the USB-IF *Device Class Definition for HID 1.11* (§6.2.2 report descriptor items: short-item prefix
`bSize`/`bType`/`bTag`; Main items Input/Collection/End Collection; Global items Usage Page, Logical Minimum/Maximum,
Report Size, Report ID, Report Count, Push, Pop; Local items Usage, Usage Minimum/Maximum; long items `0xFE`) and the
*HID Usage Tables* (Generic Desktop page 0x01: Multi-axis Controller 0x08, X/Y/Z 0x30-0x32, Rx/Ry/Rz 0x33-0x35; Button
page 0x09). https://www.usb.org/hid ; V for the item encoding as used here, which the tests exercise with descriptors
built item by item from the specification.

Only what a multi-axis controller needs is supported: Input items (variable and array), report IDs, Push/Pop, extended
(32-bit) usages. Output and Feature items are parsed past but not decoded.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

USAGE_PAGE_GENERIC_DESKTOP = 0x01
USAGE_PAGE_BUTTON = 0x09
USAGE_MULTI_AXIS_CONTROLLER = 0x08
TRANSLATION_USAGES = (0x30, 0x31, 0x32)  # X, Y, Z
ROTATION_USAGES = (0x33, 0x34, 0x35)  # Rx, Ry, Rz
AXIS_NAMES = {0x30: "X", 0x31: "Y", 0x32: "Z", 0x33: "Rx", 0x34: "Ry", 0x35: "Rz"}

# Item types (bType) and tags (bTag), HID 1.11 §6.2.2.4-6.2.2.8
_MAIN, _GLOBAL, _LOCAL = 0, 1, 2
_INPUT, _OUTPUT, _FEATURE, _COLLECTION, _END_COLLECTION = 0x8, 0x9, 0xB, 0xA, 0xC
_USAGE_PAGE, _LOGICAL_MIN, _LOGICAL_MAX, _PHYSICAL_MIN, _PHYSICAL_MAX = 0x0, 0x1, 0x2, 0x3, 0x4
_REPORT_SIZE, _REPORT_ID, _REPORT_COUNT, _PUSH, _POP = 0x7, 0x8, 0x9, 0xA, 0xB
_USAGE, _USAGE_MIN, _USAGE_MAX = 0x0, 0x1, 0x2
_COLLECTION_APPLICATION = 0x01


class HidDescriptorError(ValueError):
    """The report descriptor is malformed or uses something this parser doesn't support."""


@dataclass(frozen=True)
class Field:
    """One value (variable item) or one array slot in an input report."""
    report_id: int
    bit_offset: int  # from the first data byte (after the report ID byte, if the device uses report IDs)
    bit_size: int
    logical_min: int
    logical_max: int
    usage_page: int
    usage: int  # variable item: this field's usage; array item: unused (see `usages`)
    is_array: bool = False
    usages: Tuple[Tuple[int, int], ...] = ()  # array item: (page, usage) selected by value - logical_min

    @property
    def signed(self) -> bool:
        return self.logical_min < 0


@dataclass
class ReportLayout:
    """The input fields of a device, by report ID (0 when the device doesn't use report IDs)."""
    fields: Dict[int, List[Field]] = field(default_factory=dict)
    uses_report_ids: bool = False
    application: Optional[Tuple[int, int]] = None  # (page, usage) of the first top-level application collection

    def axis_fields(self) -> List[Field]:
        return [f for fs in self.fields.values() for f in fs
                if not f.is_array and f.usage_page == USAGE_PAGE_GENERIC_DESKTOP and f.usage in AXIS_NAMES]

    def has_buttons(self) -> bool:
        return any(f.usage_page == USAGE_PAGE_BUTTON or any(p == USAGE_PAGE_BUTTON for p, _ in f.usages)
                   for fs in self.fields.values() for f in fs)


@dataclass
class ReportValues:
    """What one input report carried: raw axis values by usage, and (if the report has buttons) the pressed ones."""
    axes: Dict[int, int] = field(default_factory=dict)
    axis_ranges: Dict[int, Tuple[int, int]] = field(default_factory=dict)  # usage -> (logical min, logical max)
    buttons: Optional[Set[int]] = None  # None: this report has no buttons; set(): none pressed


def _items(descriptor: bytes) -> Iterator[Tuple[int, int, int, int]]:
    """Yield (type, tag, size in bytes, unsigned data) for each short item; skip long items."""
    i, n = 0, len(descriptor)
    while i < n:
        prefix = descriptor[i]
        if prefix == 0xFE:  # long item: 0xFE, bDataSize, bLongItemTag, data
            if i + 2 >= n:
                raise HidDescriptorError("truncated long item")
            i += 3 + descriptor[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        if i + 1 + size > n:
            raise HidDescriptorError(f"truncated item at byte {i}")
        data = int.from_bytes(descriptor[i + 1:i + 1 + size], "little")
        yield (prefix >> 2) & 0x03, (prefix >> 4) & 0x0F, size, data
        i += 1 + size


def _signed(data: int, size: int) -> int:
    if size == 0:
        return 0
    bits = 8 * size
    return data - (1 << bits) if data & (1 << (bits - 1)) else data


def parse_report_descriptor(descriptor: bytes) -> ReportLayout:
    """Parse a HID report descriptor into the layout of its input reports."""
    layout = ReportLayout()
    glob = {"page": 0, "lmin": 0, "lmax": 0, "lmax_size": 0, "lmax_raw": 0, "size": 0, "count": 0, "id": 0}
    stack: List[dict] = []
    usages: List[Tuple[int, int]] = []
    usage_min: Optional[Tuple[int, int]] = None
    offsets: Dict[int, int] = {}
    depth = 0

    def full_usage(data: int, size: int) -> Tuple[int, int]:
        return (data >> 16, data & 0xFFFF) if size == 4 else (glob["page"], data)

    for typ, tag, size, data in _items(descriptor):
        if typ == _GLOBAL:
            if tag == _USAGE_PAGE:
                glob["page"] = data
            elif tag == _LOGICAL_MIN:
                glob["lmin"] = _signed(data, size)
            elif tag == _LOGICAL_MAX:
                glob["lmax"], glob["lmax_size"], glob["lmax_raw"] = _signed(data, size), size, data
            elif tag == _REPORT_SIZE:
                glob["size"] = data
            elif tag == _REPORT_COUNT:
                glob["count"] = data
            elif tag == _REPORT_ID:
                if data == 0:
                    raise HidDescriptorError("report ID 0 is reserved")
                glob["id"] = data
                layout.uses_report_ids = True
            elif tag == _PUSH:
                stack.append(dict(glob))
            elif tag == _POP:
                if not stack:
                    raise HidDescriptorError("Pop without Push")
                glob = stack.pop()
        elif typ == _LOCAL:
            if tag == _USAGE:
                usages.append(full_usage(data, size))
            elif tag == _USAGE_MIN:
                usage_min = full_usage(data, size)
            elif tag == _USAGE_MAX and usage_min is not None:
                page, last = full_usage(data, size)
                usages.extend((usage_min[0], u) for u in range(usage_min[1], last + 1))
                usage_min = None
        elif typ == _MAIN:
            if tag == _COLLECTION:
                if depth == 0 and data == _COLLECTION_APPLICATION and layout.application is None and usages:
                    layout.application = usages[0]
                depth += 1
            elif tag == _END_COLLECTION:
                depth = max(0, depth - 1)
            elif tag in (_INPUT, _OUTPUT, _FEATURE):
                bits = glob["size"] * glob["count"]
                if tag == _INPUT:
                    _add_input(layout, data, glob, usages, offsets)
                    offsets[glob["id"]] = offsets.get(glob["id"], 0) + bits
            # Local items apply to the next Main item only (§6.2.2.8)
            usages, usage_min = [], None
    return layout


def _add_input(layout: ReportLayout, flags: int, glob: dict, usages: List[Tuple[int, int]],
               offsets: Dict[int, int]) -> None:
    constant, variable = bool(flags & 0x01), bool(flags & 0x02)
    if constant or glob["size"] == 0:
        return  # padding
    lmin, lmax = glob["lmin"], glob["lmax"]
    if lmax < lmin and glob["lmax_size"]:
        lmax = glob["lmax_raw"]  # a maximum written as an unsigned value (e.g. 0xFF in one byte)
    start = offsets.get(glob["id"], 0)
    fields = layout.fields.setdefault(glob["id"], [])
    for k in range(glob["count"]):
        offset = start + k * glob["size"]
        if variable:
            if not usages:
                continue
            page, usage = usages[min(k, len(usages) - 1)]
            fields.append(Field(glob["id"], offset, glob["size"], lmin, lmax, page, usage))
        else:
            fields.append(Field(glob["id"], offset, glob["size"], lmin, lmax, usages[0][0] if usages else 0, 0,
                                is_array=True, usages=tuple(usages)))


def extract(data: bytes, bit_offset: int, bit_size: int, signed: bool) -> int:
    """The little-endian bit field at `bit_offset` (HID reports are little-endian, §5.8 and §6.2.2.7)."""
    end = bit_offset + bit_size
    if end > 8 * len(data):
        raise ValueError("field extends past the end of the report")
    value = (int.from_bytes(data, "little") >> bit_offset) & ((1 << bit_size) - 1)
    if signed and value & (1 << (bit_size - 1)):
        value -= 1 << bit_size
    return value


def decode_report(layout: ReportLayout, report: bytes) -> Optional[ReportValues]:
    """Decode one input report as read from the device (with the report ID byte first when the device uses report
    IDs). Returns None for a report this layout doesn't describe."""
    if layout.uses_report_ids:
        if not report:
            return None
        report_id, data = report[0], report[1:]
    else:
        report_id, data = 0, report
    fields = layout.fields.get(report_id)
    if fields is None:
        return None
    values = ReportValues()
    for f in fields:
        try:
            raw = extract(data, f.bit_offset, f.bit_size, f.signed)
        except ValueError:
            continue  # a short report: keep what it does carry
        if f.is_array:
            values.buttons = set() if values.buttons is None else values.buttons
            index = raw - f.logical_min
            if 0 <= index < len(f.usages) and raw != 0 and f.usages[index][0] == USAGE_PAGE_BUTTON:
                values.buttons.add(f.usages[index][1])
        elif f.usage_page == USAGE_PAGE_BUTTON:
            values.buttons = set() if values.buttons is None else values.buttons
            if raw:
                values.buttons.add(f.usage)
        elif f.usage_page == USAGE_PAGE_GENERIC_DESKTOP and f.usage in AXIS_NAMES:
            values.axes[f.usage] = raw
            values.axis_ranges[f.usage] = (f.logical_min, f.logical_max)
    return values


def normalize(value: int, logical_min: int, logical_max: int) -> float:
    """Map a raw axis value to [-1, 1] from the field's logical range (center -> 0), clamped."""
    if logical_max <= logical_min:
        return 0.0
    center = (logical_min + logical_max) / 2.0
    half = (logical_max - logical_min) / 2.0
    return max(-1.0, min(1.0, (value - center) / half))


def is_multi_axis_controller(page: int, usage: int) -> bool:
    return page == USAGE_PAGE_GENERIC_DESKTOP and usage == USAGE_MULTI_AXIS_CONTROLLER


def buttons_to_mask(buttons: Sequence[int]) -> int:
    """Button numbers (1-based HID Button usages) as a bit mask: button 1 -> bit 0."""
    mask = 0
    for b in buttons:
        if b >= 1:
            mask |= 1 << (b - 1)
    return mask

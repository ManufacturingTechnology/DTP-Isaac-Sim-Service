"""The pure HID layer: descriptor parsing, report decoding, normalization, shaping."""
import pytest

from amt.dtp.ise.lib.devices.spacemouse import shape_axis
from amt.dtp.ise.lib.devices.spacemouse.hid import (
    HidDescriptorError,
    buttons_to_mask,
    decode_report,
    extract,
    is_multi_axis_controller,
    normalize,
    parse_report_descriptor,
)

from . import hid_descriptors as d

# Real reports from a SpaceMouse Compact (Windows, 2026-10-27) and their values as decoded by Windows' HID parser.
CAPTURED = [
    ("01 e4 ff 00 00 00 00", {0x30: -28, 0x31: 0, 0x32: 0}, None),
    ("01 00 00 96 ff 32 00", {0x30: 0, 0x31: -106, 0x32: 50}, None),
    ("02 f4 ff 00 00 dc ff", {0x33: -12, 0x34: 0, 0x35: -36}, None),
    ("02 00 00 2c ff 06 00", {0x33: 0, 0x34: -212, 0x35: 6}, None),
    ("03 01 00 00 00 00 00", {}, {1}),
    ("03 03 00 00 00 00 00", {}, {1, 2}),
    ("03 00 00 00 00 00 00", {}, set()),
]


def test_compact_layout():
    layout = parse_report_descriptor(d.compact_like())
    assert layout.application == (0x01, 0x08) and is_multi_axis_controller(*layout.application)
    assert layout.uses_report_ids and sorted(layout.fields) == [1, 2, 3]
    assert [(f.usage, f.bit_offset, f.bit_size, f.logical_min, f.logical_max) for f in layout.fields[1]] == [
        (0x30, 0, 16, -350, 350), (0x31, 16, 16, -350, 350), (0x32, 32, 16, -350, 350)]
    assert layout.has_buttons() and len(layout.axis_fields()) == 6


@pytest.mark.parametrize("hexstr, axes, buttons", CAPTURED)
def test_decodes_captured_reports_like_windows(hexstr, axes, buttons):
    values = decode_report(parse_report_descriptor(d.compact_like()), bytes.fromhex(hexstr))
    assert values.axes == axes
    assert values.buttons == buttons
    assert all(values.axis_ranges[u] == (-350, 350) for u in axes)


def test_unknown_report_id_and_empty_report():
    layout = parse_report_descriptor(d.compact_like())
    assert decode_report(layout, bytes.fromhex("07 00 00")) is None
    assert decode_report(layout, b"") is None


def test_short_report_keeps_what_it_carries():
    values = decode_report(parse_report_descriptor(d.compact_like()), bytes.fromhex("01 e4 ff"))
    assert values.axes == {0x30: -28}


def test_single_report_without_ids_and_array_buttons():
    layout = parse_report_descriptor(d.single_report_no_ids())
    assert not layout.uses_report_ids and list(layout.fields) == [0]
    values = decode_report(layout, bytes([0x7F, 0x81, 0, 10, 0xF6, 0, 3]))
    assert values.axes == {0x30: 127, 0x31: -127, 0x32: 0, 0x33: 10, 0x34: -10, 0x35: 0}
    assert values.buttons == {3}
    assert decode_report(layout, bytes([0, 0, 0, 0, 0, 0, 0])).buttons == set()  # 0 is outside logical [1, 4]


def test_push_pop_extended_usage_and_long_item():
    descriptor = b"".join([
        d.usage_page(0x01), d.usage(0x08), d.collection(d.APPLICATION),
        bytes([0xFE, 2, 0x10, 0xAA, 0xBB]),  # a long item, skipped
        d.logical_min(-8), d.logical_max(7), d.report_size(4), d.push(),
        d.usage_page(0xFF00), d.report_count(1), d.usage(1), d.input_(d.DATA_VAR_ABS),  # vendor field
        d.pop(), d.report_count(1), d.usage(0x0001_0030, size=4), d.input_(d.DATA_VAR_ABS),  # extended usage: X
        d.end_collection(),
    ])
    layout = parse_report_descriptor(descriptor)
    x = layout.axis_fields()
    assert [(f.usage_page, f.usage, f.bit_offset, f.logical_min) for f in x] == [(0x01, 0x30, 4, -8)]
    assert decode_report(layout, bytes([0x90])).axes == {0x30: -7}


def test_unsigned_logical_maximum():
    descriptor = b"".join([d.usage_page(0x01), d.usage(0x08), d.collection(d.APPLICATION), d.logical_min(0),
                           d.item(d.GLOBAL, 0x2, 0xFF, 1), d.report_size(8), d.report_count(1), d.usage(0x30),
                           d.input_(d.DATA_VAR_ABS), d.end_collection()])
    field = parse_report_descriptor(descriptor).axis_fields()[0]
    assert (field.logical_min, field.logical_max, field.signed) == (0, 255, False)
    assert decode_report(parse_report_descriptor(descriptor), bytes([200])).axes == {0x30: 200}


def test_malformed_descriptors():
    with pytest.raises(HidDescriptorError):
        parse_report_descriptor(bytes([0x26, 0x5E]))  # truncated two-byte item
    with pytest.raises(HidDescriptorError):
        parse_report_descriptor(d.pop())
    with pytest.raises(HidDescriptorError):
        parse_report_descriptor(d.item(d.GLOBAL, 0x8, 0, 1))  # report ID 0 is reserved


def test_extract_bits():
    assert extract(bytes([0b1011_0110]), 1, 3, signed=False) == 0b011
    assert extract(bytes([0xE4, 0xFF]), 0, 16, signed=True) == -28
    with pytest.raises(ValueError):
        extract(b"\x00", 4, 8, signed=False)


def test_normalize_and_masks():
    assert normalize(350, -350, 350) == 1.0 and normalize(-175, -350, 350) == -0.5
    assert normalize(400, -350, 350) == 1.0 and normalize(0, 5, 5) == 0.0
    assert normalize(255, 0, 255) == 1.0 and normalize(0, 0, 255) == -1.0
    assert buttons_to_mask([1, 2]) == 3 and buttons_to_mask([]) == 0


def test_shape_axis():
    assert shape_axis(0.03) == 0.0 and shape_axis(1.0) == pytest.approx(1.0) and shape_axis(-1.0) == pytest.approx(-1.0)
    assert 0.0 < shape_axis(0.5) < 0.5  # expo softens the middle
    assert shape_axis(0.5, deadzone=0.0, expo=0.0) == pytest.approx(0.5)

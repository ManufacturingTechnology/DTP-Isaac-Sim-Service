"""HID report descriptors built item by item (USB HID 1.11 §6.2.2), for the SpaceMouse tests."""
from typing import List

MAIN, GLOBAL, LOCAL = 0, 1, 2


def item(typ: int, tag: int, value: int = 0, size: int = None) -> bytes:
    """One short item. `size` in bytes (0, 1, 2 or 4); by default the smallest that holds a signed `value`."""
    if size is None:
        size = 0 if value == 0 else 1 if -128 <= value <= 127 else 2 if -32768 <= value <= 32767 else 4
    code = {0: 0, 1: 1, 2: 2, 4: 3}[size]
    data = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "little") if size else b""
    return bytes([(tag << 4) | (typ << 2) | code]) + data


def usage_page(page): return item(GLOBAL, 0x0, page)
def logical_min(v): return item(GLOBAL, 0x1, v)
def logical_max(v): return item(GLOBAL, 0x2, v)
def report_size(v): return item(GLOBAL, 0x7, v)
def report_id(v): return item(GLOBAL, 0x8, v)
def report_count(v): return item(GLOBAL, 0x9, v)
def push(): return item(GLOBAL, 0xA)
def pop(): return item(GLOBAL, 0xB)
def usage(u, size=None): return item(LOCAL, 0x0, u, size)
def usage_min(u): return item(LOCAL, 0x1, u)
def usage_max(u): return item(LOCAL, 0x2, u)
def collection(kind): return item(MAIN, 0xA, kind, 1)
def end_collection(): return item(MAIN, 0xC)
def input_(flags): return item(MAIN, 0x8, flags, 1)


DATA_VAR_REL, DATA_VAR_ABS, DATA_ARRAY_ABS, CONSTANT = 0x06, 0x02, 0x00, 0x01
APPLICATION, PHYSICAL = 0x01, 0x00


def compact_like() -> bytes:
    """Shaped like the SpaceMouse Compact as Windows reports it: translation in report 1, rotation in report 2 (16-bit,
    logical ±350), two buttons in report 3."""
    parts: List[bytes] = [usage_page(0x01), usage(0x08), collection(APPLICATION)]
    for rid, usages in ((1, (0x30, 0x31, 0x32)), (2, (0x33, 0x34, 0x35))):
        parts += [collection(PHYSICAL), report_id(rid), logical_min(-350), logical_max(350)]
        parts += [usage(u) for u in usages]
        parts += [report_size(16), report_count(3), input_(DATA_VAR_REL), end_collection()]
    parts += [report_id(3), usage_page(0x09), usage_min(1), usage_max(2), logical_min(0), logical_max(1),
              report_size(1), report_count(2), input_(DATA_VAR_ABS),
              report_size(6), report_count(1), input_(CONSTANT), end_collection()]
    return b"".join(parts)


def single_report_no_ids() -> bytes:
    """All six axes and buttons in one report, no report IDs, 8-bit axes, and an array-style button field."""
    return b"".join([
        usage_page(0x01), usage(0x08), collection(APPLICATION),
        logical_min(-127), logical_max(127), report_size(8), report_count(6),
        usage(0x30), usage(0x31), usage(0x32), usage(0x33), usage(0x34), usage(0x35), input_(DATA_VAR_ABS),
        usage_page(0x09), usage_min(1), usage_max(4), logical_min(1), logical_max(4), report_size(8),
        report_count(1), input_(DATA_ARRAY_ABS),
        end_collection(),
    ])

"""Windows backend: SetupAPI to find HID interfaces, the HID client API (hid.dll) to describe and decode them.

Written from Microsoft's documentation of SetupAPI (`SetupDiGetClassDevsW`, `SetupDiEnumDeviceInterfaces`,
`SetupDiGetDeviceInterfaceDetailW`, `SetupDiGetDeviceRegistryPropertyW`), the HID client API in `hidsdi.h` / `hidpi.h`
(`HidD_GetHidGuid`, `HidD_GetAttributes`, `HidD_GetProductString`, `HidD_GetManufacturerString`,
`HidD_GetPreparsedData`, `HidP_GetCaps`, `HidP_GetValueCaps`, `HidP_GetButtonCaps`, `HidP_GetUsageValue`,
`HidP_GetUsages`), and the Win32 file and process APIs (`CreateFileW`, overlapped `ReadFile`, `CancelIoEx`,
`CreateToolhelp32Snapshot`). https://learn.microsoft.com/windows-hardware/drivers/hid/ ; V by running it against a
SpaceMouse Compact on Windows 11 (see `TESTED_DEVICES`).

Windows parses the report descriptor itself (the "preparsed data"), so this backend decodes through HidP_* rather
than the descriptor parser in `hid.py`. Reads are shared (other readers, such as 3Dconnexion's software, keep
working) and overlapped, so a pending read never blocks `close()`.
"""
import ctypes
from ctypes import wintypes as wt
from typing import Dict, List, Optional, Tuple

from .backend import DeviceDisconnected, SpaceMouseUnavailable
from .hid import AXIS_NAMES, USAGE_PAGE_BUTTON, USAGE_PAGE_GENERIC_DESKTOP, ReportValues, is_multi_axis_controller
from .models import DeviceInfo

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_hid = ctypes.WinDLL("hid", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_HIDP_STATUS_SUCCESS = 0x00110000
_HIDP_INPUT = 0
_DIGCF_PRESENT, _DIGCF_DEVICEINTERFACE = 0x02, 0x10
_SPDRP_SERVICE = 0x04
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ_WRITE = 0x01 | 0x02
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0, _WAIT_TIMEOUT = 0, 0x102
_TH32CS_SNAPPROCESS = 0x02
_INVALID_HANDLE = wt.HANDLE(-1).value


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _DeviceInterfaceData(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", _GUID), ("Flags", wt.DWORD),
                ("Reserved", ctypes.c_size_t)]


class _DevInfoData(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", _GUID), ("DevInst", wt.DWORD), ("Reserved", ctypes.c_size_t)]


class _HiddAttributes(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT), ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class _HidpCaps(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT), ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT), ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17), ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT), ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT), ("NumberOutputButtonCaps", wt.USHORT),
                ("NumberOutputValueCaps", wt.USHORT), ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT), ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


class _Range(ctypes.Structure):
    _fields_ = [("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT), ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT), ("DataIndexMin", wt.USHORT),
                ("DataIndexMax", wt.USHORT)]


class _NotRange(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("Reserved1", wt.USHORT), ("StringIndex", wt.USHORT),
                ("Reserved2", wt.USHORT), ("DesignatorIndex", wt.USHORT), ("Reserved3", wt.USHORT),
                ("DataIndex", wt.USHORT), ("Reserved4", wt.USHORT)]


class _RangeOrNot(ctypes.Union):
    _fields_ = [("Range", _Range), ("NotRange", _NotRange)]


class _HidpValueCaps(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", wt.BOOLEAN),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", wt.BOOLEAN), ("IsStringRange", wt.BOOLEAN),
                ("IsDesignatorRange", wt.BOOLEAN), ("IsAbsolute", wt.BOOLEAN), ("HasNull", wt.BOOLEAN),
                ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT), ("ReportCount", wt.USHORT),
                ("Reserved2", wt.USHORT * 5), ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
                ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG), ("PhysicalMin", wt.LONG),
                ("PhysicalMax", wt.LONG), ("u", _RangeOrNot)]


class _HidpButtonCaps(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", wt.BOOLEAN),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", wt.BOOLEAN), ("IsStringRange", wt.BOOLEAN),
                ("IsDesignatorRange", wt.BOOLEAN), ("IsAbsolute", wt.BOOLEAN), ("ReportCount", wt.USHORT),
                ("Reserved2", wt.USHORT), ("Reserved", wt.ULONG * 9), ("u", _RangeOrNot)]


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", wt.DWORD),
                ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


assert ctypes.sizeof(_HidpValueCaps) == 72 and ctypes.sizeof(_HidpButtonCaps) == 72  # hidpi.h layouts

_setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
_setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
_setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), wt.DWORD,
                                                  ctypes.POINTER(_DeviceInterfaceData)]
_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(_DeviceInterfaceData),
                                                       ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                                       ctypes.POINTER(_DevInfoData)]
_setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [wt.HANDLE, ctypes.POINTER(_DevInfoData), wt.DWORD,
                                                        ctypes.POINTER(wt.DWORD), ctypes.c_void_p, wt.DWORD,
                                                        ctypes.POINTER(wt.DWORD)]
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
_kernel32.CreateFileW.restype = wt.HANDLE
_kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                               ctypes.POINTER(_Overlapped)]
_kernel32.CreateEventW.restype = wt.HANDLE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
_kernel32.ResetEvent.argtypes = [wt.HANDLE]
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wt.DWORD), wt.BOOL]
_kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(_Overlapped)]
_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
_kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
_hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
_hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(_HiddAttributes)]
_hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
_hid.HidD_GetManufacturerString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
_hid.HidP_GetCaps.restype = wt.LONG
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HidpCaps)]
_hid.HidP_GetValueCaps.restype = wt.LONG
_hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(_HidpValueCaps), ctypes.POINTER(wt.USHORT),
                                   ctypes.c_void_p]
_hid.HidP_GetButtonCaps.restype = wt.LONG
_hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.POINTER(_HidpButtonCaps), ctypes.POINTER(wt.USHORT),
                                    ctypes.c_void_p]
_hid.HidP_GetUsageValue.restype = wt.LONG
_hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT, ctypes.POINTER(wt.ULONG),
                                    ctypes.c_void_p, ctypes.c_char_p, wt.ULONG]
_hid.HidP_GetUsages.restype = wt.LONG
_hid.HidP_GetUsages.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, ctypes.POINTER(wt.USHORT),
                                ctypes.POINTER(wt.ULONG), ctypes.c_void_p, ctypes.c_char_p, wt.ULONG]


def _open(path: str, access: int = 0, overlapped: bool = False) -> Optional[int]:
    handle = _kernel32.CreateFileW(path, access, _FILE_SHARE_READ_WRITE, None, _OPEN_EXISTING,
                                   _FILE_FLAG_OVERLAPPED if overlapped else 0, None)
    return None if handle in (None, _INVALID_HANDLE) else handle


def _string(getter, handle) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    return buffer.value if getter(handle, buffer, ctypes.sizeof(buffer)) else ""


def _interfaces() -> List[Tuple[str, str]]:
    """(device path, driver service bound to it) for every present HID interface."""
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = _setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if info in (None, _INVALID_HANDLE):
        return []
    found = []
    try:
        index = 0
        while True:
            data = _DeviceInterfaceData(cbSize=ctypes.sizeof(_DeviceInterfaceData))
            if not _setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), index, ctypes.byref(data)):
                break
            index += 1
            needed = wt.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), None, 0, ctypes.byref(needed), None)
            detail = ctypes.create_string_buffer(max(needed.value, 8))
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: the fixed part's size (8 on 64-bit, 6 on 32-bit)
            ctypes.cast(detail, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            devinfo = _DevInfoData(cbSize=ctypes.sizeof(_DevInfoData))
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), detail, needed, None,
                                                              ctypes.byref(devinfo)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
            service = ctypes.create_unicode_buffer(256)
            ok = _setupapi.SetupDiGetDeviceRegistryPropertyW(info, ctypes.byref(devinfo), _SPDRP_SERVICE, None,
                                                             service, ctypes.sizeof(service), None)
            found.append((path, service.value if ok else ""))
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(info)
    return found


class _PreparsedLayout:
    """A device's input reports as Windows describes them: value and button capabilities from the preparsed data."""

    def __init__(self, handle: int):
        self.preparsed = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(self.preparsed)):
            raise SpaceMouseUnavailable(f"HidD_GetPreparsedData failed (Windows error {ctypes.get_last_error()})")
        self.caps = _HidpCaps()
        _hid.HidP_GetCaps(self.preparsed, ctypes.byref(self.caps))
        count = wt.USHORT(self.caps.NumberInputValueCaps)
        values = (_HidpValueCaps * max(count.value, 1))()
        _hid.HidP_GetValueCaps(_HIDP_INPUT, values, ctypes.byref(count), self.preparsed)
        self.value_caps = list(values)[:count.value]
        count = wt.USHORT(self.caps.NumberInputButtonCaps)
        buttons = (_HidpButtonCaps * max(count.value, 1))()
        _hid.HidP_GetButtonCaps(_HIDP_INPUT, buttons, ctypes.byref(count), self.preparsed)
        self.button_caps = list(buttons)[:count.value]

    def free(self) -> None:
        if self.preparsed:
            _hid.HidD_FreePreparsedData(self.preparsed)
            self.preparsed = ctypes.c_void_p()

    @staticmethod
    def _usages(cap) -> range:
        return range(cap.Range.UsageMin, cap.Range.UsageMax + 1) if cap.IsRange else \
            range(cap.NotRange.Usage, cap.NotRange.Usage + 1)

    def decode(self, report: bytes) -> Optional[ReportValues]:
        if not report:
            return None
        report_id = report[0]  # Windows always prefixes the report ID byte (0 if the device doesn't use IDs)
        matched = False
        values = ReportValues()
        for cap in self.value_caps:
            if cap.ReportID != report_id or cap.UsagePage != USAGE_PAGE_GENERIC_DESKTOP:
                continue
            matched = True
            for usage in self._usages(cap):
                if usage not in AXIS_NAMES:
                    continue
                raw = wt.ULONG()
                if _hid.HidP_GetUsageValue(_HIDP_INPUT, cap.UsagePage, cap.LinkCollection, usage, ctypes.byref(raw),
                                           self.preparsed, report, len(report)) != _HIDP_STATUS_SUCCESS:
                    continue
                value = raw.value
                if cap.LogicalMin < 0 and cap.BitSize and value & (1 << (cap.BitSize - 1)):
                    value -= 1 << cap.BitSize  # HidP_GetUsageValue returns the raw bits; sign-extend
                values.axes[usage] = value
                values.axis_ranges[usage] = (cap.LogicalMin, cap.LogicalMax)
        for cap in self.button_caps:
            if cap.ReportID != report_id or cap.UsagePage != USAGE_PAGE_BUTTON:
                continue
            matched = True
            pressed = (wt.USHORT * 128)()
            length = wt.ULONG(128)
            if _hid.HidP_GetUsages(_HIDP_INPUT, cap.UsagePage, cap.LinkCollection, pressed, ctypes.byref(length),
                                   self.preparsed, report, len(report)) == _HIDP_STATUS_SUCCESS:
                values.buttons = (values.buttons or set()) | set(pressed[:length.value])
            elif values.buttons is None:
                values.buttons = set()
        return values if matched else None

    def summary(self) -> List[str]:
        lines = [f"input report {self.caps.InputReportByteLength} bytes (including the report ID byte)"]
        for cap in self.value_caps:
            names = ",".join(AXIS_NAMES.get(u, hex(u)) for u in self._usages(cap)) \
                if cap.UsagePage == USAGE_PAGE_GENERIC_DESKTOP else ",".join(hex(u) for u in self._usages(cap))
            lines.append(f"report {cap.ReportID}: page {cap.UsagePage:#04x} {names}, {cap.BitSize} bits, logical "
                         f"[{cap.LogicalMin}, {cap.LogicalMax}], {'absolute' if cap.IsAbsolute else 'relative'}")
        for cap in self.button_caps:
            usages = self._usages(cap)
            lines.append(f"report {cap.ReportID}: page {cap.UsagePage:#04x} buttons {usages.start}-{usages.stop - 1}")
        return lines


class WindowsDevice:
    """An open multi-axis controller: shared, overlapped reads."""

    def __init__(self, info: DeviceInfo):
        self.info = info
        handle = _open(info.path, _GENERIC_READ, overlapped=True)
        if handle is None:
            raise SpaceMouseUnavailable(f"Couldn't open {info.describe()} for reading "
                                        f"(Windows error {ctypes.get_last_error()})")
        self._handle = handle
        try:
            self._layout = _PreparsedLayout(handle)
        except Exception:
            _kernel32.CloseHandle(handle)
            raise
        self._length = self._layout.caps.InputReportByteLength
        self._event = _kernel32.CreateEventW(None, True, False, None)
        self._buffer = ctypes.create_string_buffer(max(self._length, 1))
        self._overlapped = _Overlapped(hEvent=self._event)
        self._pending = False

    def read(self, timeout: float) -> Optional[bytes]:
        if self._handle is None:
            raise DeviceDisconnected("device is closed")
        got = wt.DWORD()
        if not self._pending:
            _kernel32.ResetEvent(self._event)
            self._overlapped = _Overlapped(hEvent=self._event)
            if _kernel32.ReadFile(self._handle, self._buffer, self._length, ctypes.byref(got),
                                  ctypes.byref(self._overlapped)):
                return self._buffer.raw[:got.value]
            error = ctypes.get_last_error()
            if error != _ERROR_IO_PENDING:
                raise DeviceDisconnected(f"ReadFile failed (Windows error {error})")
            self._pending = True
        waited = _kernel32.WaitForSingleObject(self._event, max(0, int(timeout * 1000)))
        if waited == _WAIT_TIMEOUT:
            return None  # the read stays pending; the next call waits on it again
        self._pending = False
        if waited != _WAIT_OBJECT_0 or not _kernel32.GetOverlappedResult(self._handle, ctypes.byref(self._overlapped),
                                                                         ctypes.byref(got), False):
            raise DeviceDisconnected(f"read failed (Windows error {ctypes.get_last_error()})")
        return self._buffer.raw[:got.value]

    def decode(self, report: bytes) -> Optional[ReportValues]:
        return self._layout.decode(report)

    def layout_summary(self) -> List[str]:
        return self._layout.summary()

    def close(self) -> None:
        if self._handle is None:
            return
        if self._pending:
            _kernel32.CancelIoEx(self._handle, ctypes.byref(self._overlapped))
            got = wt.DWORD()
            _kernel32.GetOverlappedResult(self._handle, ctypes.byref(self._overlapped), ctypes.byref(got), True)
            self._pending = False
        _kernel32.CloseHandle(self._handle)
        _kernel32.CloseHandle(self._event)
        self._layout.free()
        self._handle = None


class WindowsBackend:
    name = "windows"

    def enumerate(self) -> List[DeviceInfo]:
        devices = []
        for path, service in _interfaces():
            handle = _open(path)  # no access rights needed to query attributes and capabilities
            if handle is None:
                continue
            try:
                attributes = _HiddAttributes(Size=ctypes.sizeof(_HiddAttributes))
                if not _hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
                    continue
                preparsed = ctypes.c_void_p()
                if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
                    continue
                caps = _HidpCaps()
                _hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
                _hid.HidD_FreePreparsedData(preparsed)
                if not is_multi_axis_controller(caps.UsagePage, caps.Usage):
                    continue
                devices.append(DeviceInfo(
                    name=_string(_hid.HidD_GetProductString, handle),
                    manufacturer=_string(_hid.HidD_GetManufacturerString, handle),
                    vendor_id=attributes.VendorID, product_id=attributes.ProductID,
                    version=attributes.VersionNumber, path=path, platform="windows", driver=service))
            finally:
                _kernel32.CloseHandle(handle)
        return devices

    def open(self, info: DeviceInfo) -> WindowsDevice:
        return WindowsDevice(info)

    def environment(self) -> Dict[str, str]:
        running = sorted({name for name in _process_names() if "3dx" in name.lower()})
        return {"3Dconnexion software running": ", ".join(running) if running else "no"}


def _process_names() -> List[str]:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, _INVALID_HANDLE):
        return []
    names = []
    try:
        entry = _ProcessEntry32W(dwSize=ctypes.sizeof(_ProcessEntry32W))
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile)
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return names

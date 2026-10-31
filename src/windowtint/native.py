from __future__ import annotations

import ctypes
import ntpath
import os
import sys
from dataclasses import dataclass
from ctypes import wintypes

from .safety import is_explorer_file_window, target_rejection_reason
from .theme import ThemePalette


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_CLOAKED = 14

DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
TOKEN_IS_APPCONTAINER = 29
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_saved_corners: dict[int, int] = {}


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process_name: str
    thread_id: int
    style: int
    is_64_bit: bool
    renderer: str = "injected"

    @property
    def label(self) -> str:
        renderer = "  ·  外部层" if self.renderer == "external" else ""
        return f"{self.title}  ·  {self.process_name}{renderer}"


@dataclass(frozen=True)
class ApplyResult:
    applied: tuple[int, ...]
    failures: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class _ProcessInspection:
    process_name: str
    is_64_bit: bool
    has_antcheat_module: bool
    is_appcontainer: bool
    is_elevated: bool


def supports_window_tint(build_number: int | None = None) -> bool:
    build = sys.getwindowsversion().build if build_number is None else build_number
    return build >= 22000


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32


def _dwmapi():
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmSetWindowAttribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    return dwmapi


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.IsWow64Process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel32.IsWow64Process.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Module32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Module32NextW.restype = wintypes.BOOL
    return kernel32


def _advapi32():
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    return advapi32


class _ModuleEntry32W(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    )


def _process_path(pid: int) -> str:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        length = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(length.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _is_64_bit_process(pid: int) -> bool:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        wow64 = wintypes.BOOL()
        if not kernel32.IsWow64Process(handle, ctypes.byref(wow64)):
            return False
        if ctypes.sizeof(ctypes.c_void_p) == 4:
            return False
        return not bool(wow64.value)
    finally:
        kernel32.CloseHandle(handle)


def _process_token_flag(pid: int, token_class: int, default: bool) -> bool:
    kernel32 = _kernel32()
    advapi32 = _advapi32()
    process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process_handle:
        return default
    token_handle = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token_handle)):
            return default
        value = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token_handle,
            token_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
            ctypes.byref(returned),
        ):
            return default
        return bool(value.value)
    finally:
        if token_handle:
            kernel32.CloseHandle(token_handle)
        kernel32.CloseHandle(process_handle)


def _has_antcheat_module(pid: int) -> bool:
    from .safety import is_antcheat_module

    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return True
    try:
        entry = _ModuleEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            return True
        while True:
            if is_antcheat_module(entry.szModule):
                return True
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                return False
    finally:
        kernel32.CloseHandle(snapshot)


def _window_text(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _window_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    length = _user32().GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value[:length] if length > 0 else ""


def _is_cloaked(hwnd: int) -> bool:
    try:
        value = wintypes.DWORD()
        result = _dwmapi().DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result >= 0 and value.value != 0
    except OSError:
        return False


class _WindowEnum:
    def __init__(self, excluded_pid: int):
        self.excluded_pid = excluded_pid
        self.user32 = _user32()
        self.windows: list[WindowInfo] = []
        self.inspections: dict[int, _ProcessInspection] = {}

    def collect(self) -> list[WindowInfo]:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        callback = callback_type(self._visit)
        if not self.user32.EnumWindows(ctypes.cast(callback, ctypes.c_void_p), 0):
            return []
        return sorted(self.windows, key=lambda window: window.title.casefold())

    def _visit(self, hwnd: int, _lparam: int) -> bool:
        if not self.user32.IsWindowVisible(hwnd):
            return True

        process_id = wintypes.DWORD()
        thread_id = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == self.excluded_pid:
            return True

        extended_style = self.user32.GetWindowLongPtrW(hwnd, -20)
        if extended_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return True

        if _is_cloaked(hwnd):
            return True

        title = _window_text(hwnd)
        style = int(self.user32.GetWindowLongPtrW(hwnd, -16))
        inspection = self.inspections.get(process_id.value)
        if inspection is None:
            process_path = _process_path(process_id.value)
            inspection = _ProcessInspection(
                process_name=ntpath.basename(process_path),
                is_64_bit=_is_64_bit_process(process_id.value),
                has_antcheat_module=_has_antcheat_module(process_id.value),
                is_appcontainer=_process_token_flag(
                    process_id.value,
                    TOKEN_IS_APPCONTAINER,
                    default=True,
                ),
                is_elevated=_process_token_flag(
                    process_id.value,
                    TOKEN_ELEVATION,
                    default=True,
                ),
            )
            self.inspections[process_id.value] = inspection
        if is_explorer_file_window(
            inspection.process_name,
            _window_class_name(hwnd),
        ):
            if title and not inspection.is_elevated:
                self.windows.append(
                    WindowInfo(
                        hwnd=hwnd,
                        title=title,
                        pid=process_id.value,
                        process_name=inspection.process_name,
                        thread_id=thread_id,
                        style=style,
                        is_64_bit=inspection.is_64_bit,
                        renderer="external",
                    )
                )
            return True
        rejection = target_rejection_reason(
            process_name=inspection.process_name,
            style=style,
            is_64_bit=inspection.is_64_bit,
            has_antcheat_module=inspection.has_antcheat_module,
            is_appcontainer=inspection.is_appcontainer,
            is_elevated=inspection.is_elevated,
        )
        if title and rejection is None:
            self.windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    title=title,
                    pid=process_id.value,
                    process_name=inspection.process_name,
                    thread_id=thread_id,
                    style=style,
                    is_64_bit=inspection.is_64_bit,
                )
            )
        return True


def list_windows(exclude_pid: int | None = None) -> list[WindowInfo]:
    return _WindowEnum(os.getpid() if exclude_pid is None else exclude_pid).collect()


def foreground_window() -> int:
    return int(_user32().GetForegroundWindow())


def _set_attribute(hwnd: int, attribute: int, value: int) -> None:
    data = wintypes.DWORD(value & 0xFFFFFFFF)
    result = _dwmapi().DwmSetWindowAttribute(
        hwnd,
        attribute,
        ctypes.byref(data),
        ctypes.sizeof(data),
    )
    if result < 0:
        raise OSError(f"DWM attribute {attribute} failed with HRESULT 0x{result & 0xFFFFFFFF:08X}")


def _get_attribute(hwnd: int, attribute: int) -> int | None:
    data = wintypes.DWORD()
    result = _dwmapi().DwmGetWindowAttribute(
        hwnd,
        attribute,
        ctypes.byref(data),
        ctypes.sizeof(data),
    )
    return int(data.value) if result >= 0 else None


def apply_window_theme(hwnd: int, _palette: ThemePalette, corner: str = "round") -> None:
    if not supports_window_tint():
        raise RuntimeError("WindowTint requires Windows 11 build 22000 or newer.")
    if not _user32().IsWindow(hwnd):
        raise RuntimeError("The selected window is no longer available.")

    if hwnd not in _saved_corners:
        original_corner = _get_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE)
        _saved_corners[hwnd] = DWMWCP_DEFAULT if original_corner is None else original_corner

    try:
        corner_value = {
            "system": DWMWCP_DEFAULT,
            "square": DWMWCP_DONOTROUND,
            "round": DWMWCP_ROUND,
        }.get(corner, DWMWCP_ROUND)
        _set_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, corner_value)
    except OSError:
        restore_window_theme(hwnd)
        raise


def restore_window_theme(hwnd: int) -> None:
    if not _user32().IsWindow(hwnd):
        _saved_corners.pop(hwnd, None)
        return
    try:
        original_corner = _saved_corners.pop(hwnd, DWMWCP_DEFAULT)
        _set_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, original_corner)
    except OSError:
        pass


def apply_to_windows(handles: list[int], palette: ThemePalette, corner: str = "round") -> ApplyResult:
    applied: list[int] = []
    failures: list[tuple[int, str]] = []
    for hwnd in handles:
        try:
            apply_window_theme(hwnd, palette, corner)
            applied.append(hwnd)
        except (OSError, RuntimeError) as error:
            failures.append((hwnd, str(error)))
    return ApplyResult(tuple(applied), tuple(failures))


def restore_windows(handles: list[int]) -> tuple[tuple[int, str], ...]:
    failures: list[tuple[int, str]] = []
    for hwnd in handles:
        try:
            restore_window_theme(hwnd)
        except (OSError, RuntimeError) as error:
            failures.append((hwnd, str(error)))
    return tuple(failures)

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


APP_WINDOW_TITLE = "窗色 · WindowTint"
DEFAULT_MUTEX_NAME = "Local\\WindowTint.SingleInstance"
ERROR_ALREADY_EXISTS = 183
SW_SHOW = 5
SW_RESTORE = 9


def _user32() -> ctypes.WinDLL:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    return user32


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _find_window(title: str = APP_WINDOW_TITLE) -> int:
    handle = _user32().FindWindowW(None, title)
    return int(handle or 0)


def show_existing_window(hwnd: int) -> bool:
    if not hwnd:
        return False

    user32 = _user32()
    if not user32.IsWindow(hwnd):
        return False

    command = SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW
    user32.ShowWindow(hwnd, command)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return bool(user32.IsWindowVisible(hwnd))


def activate_existing_window(
    *,
    title: str = APP_WINDOW_TITLE,
    timeout: float = 0.0,
    interval: float = 0.05,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        hwnd = _find_window(title)
        if hwnd and show_existing_window(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.0, interval))


class SingleInstanceGuard:
    def __init__(self, name: str = DEFAULT_MUTEX_NAME):
        self.name = name
        self._handle = 0

    @property
    def acquired(self) -> bool:
        return bool(self._handle)

    def acquire(self) -> bool:
        if self.acquired:
            return True

        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, self.name)
        last_error = ctypes.get_last_error()
        if not handle:
            raise OSError(last_error, "无法创建窗色单实例互斥量")
        if last_error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False

        self._handle = int(handle)
        return True

    def release(self) -> None:
        if not self._handle:
            return
        _kernel32().CloseHandle(self._handle)
        self._handle = 0

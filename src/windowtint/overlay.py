from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from .session import WindowTarget


WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008

GWL_EXSTYLE = -20
GW_HWNDPREV = 3

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

HTTRANSPARENT = -1
MA_NOACTIVATE = 3
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 3),
    )


class _BlendFunction(ctypes.Structure):
    _fields_ = (
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    )


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)


class _WindowClass(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    )


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return kernel32


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterClassExW.argtypes = (ctypes.POINTER(_WindowClass),)
    user32.RegisterClassExW.restype = wintypes.WORD
    user32.LoadCursorW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    )
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ValidateRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )
    user32.ValidateRect.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = (
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        wintypes.COLORREF,
        ctypes.POINTER(_BlendFunction),
        wintypes.DWORD,
    )
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.SetWinEventHook.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HMODULE,
        _WINEVENTPROC,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)
    user32.UnhookWinEvent.restype = wintypes.BOOL
    return user32


def _gdi32():
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(_BitmapInfo),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    )
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    return gdi32


def _dwmapi():
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    return dwmapi


@dataclass
class _Overlay:
    hwnd: int
    target: WindowTarget
    image: Image.Image
    bounds: tuple[int, int, int, int]
    visible: bool = True


class OverlayManager:
    """Owns click-through image layers without modifying target processes."""

    _class_name = "WindowTintClickThroughOverlay"

    def __init__(self, *, user32=None, gdi32=None, kernel32=None, dwmapi=None) -> None:
        self.user32 = user32 if user32 is not None else _user32()
        self.gdi32 = gdi32 if gdi32 is not None else _gdi32()
        self.kernel32 = kernel32 if kernel32 is not None else _kernel32()
        self.dwmapi = dwmapi if dwmapi is not None else _dwmapi()
        self._overlays: dict[int, _Overlay] = {}
        self._wndproc = _WNDPROC(self._window_proc)
        self._win_event_proc = _WINEVENTPROC(self._win_event_callback)
        self._location_hook = None
        self._event_sync_active = False
        self._last_event_error: Exception | None = None
        self._class_atom = self._register_class()

    def _register_class(self) -> int:
        instance = self.kernel32.GetModuleHandleW(None)
        if not instance:
            raise OSError(
                ctypes.get_last_error(),
                "GetModuleHandleW failed",
            )
        window_class = _WindowClass()
        window_class.cbSize = ctypes.sizeof(window_class)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.hCursor = self.user32.LoadCursorW(None, 32512)
        window_class.lpszClassName = self._class_name
        atom = self.user32.RegisterClassExW(ctypes.byref(window_class))
        error = ctypes.get_last_error()
        if not atom and error != 1410:
            raise OSError(error, "RegisterClassExW failed")
        return atom

    def _window_proc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == 0x0084:  # WM_NCHITTEST
            return HTTRANSPARENT
        if message == 0x0021:  # WM_MOUSEACTIVATE
            return MA_NOACTIVATE
        if message == 0x0014:  # WM_ERASEBKGND
            return 1
        if message == 0x000F:  # WM_PAINT
            self.user32.ValidateRect(hwnd, None)
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def apply(self, target: WindowTarget, backdrop_path: Path) -> None:
        if not self.user32.IsWindow(target.hwnd):
            raise RuntimeError("The selected window is no longer available.")
        path = Path(backdrop_path)
        if not path.exists():
            raise RuntimeError(f"The backdrop image does not exist: {path}")
        with Image.open(path) as opened:
            image = opened.convert("RGBA").copy()

        self.remove(target)
        bounds = self._window_bounds(target.hwnd)
        if bounds is None:
            raise RuntimeError("Could not read the target window bounds.")
        x, y, width, height = bounds
        instance = self.kernel32.GetModuleHandleW(None)
        hwnd = self.user32.CreateWindowExW(
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE,
            self._class_name,
            "",
            WS_POPUP | WS_VISIBLE,
            x,
            y,
            width,
            height,
            target.hwnd,
            None,
            instance,
            None,
        )
        if not hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")

        overlay = _Overlay(hwnd=hwnd, target=target, image=image, bounds=bounds)
        try:
            self._paint(overlay)
        except Exception:
            self.user32.DestroyWindow(hwnd)
            raise
        self._overlays[target.hwnd] = overlay
        self._install_location_hook()

    def remove(self, target: WindowTarget) -> None:
        overlay = self._overlays.pop(target.hwnd, None)
        if overlay is None:
            return
        if self.user32.IsWindow(overlay.hwnd):
            self.user32.DestroyWindow(overlay.hwnd)
        if not self._overlays:
            self._uninstall_location_hook()

    def remove_all(self) -> None:
        for overlay in list(self._overlays.values()):
            self.remove(overlay.target)

    @property
    def has_overlays(self) -> bool:
        return bool(self._overlays)

    @property
    def event_driven(self) -> bool:
        return bool(self._location_hook)

    @property
    def needs_fast_sync(self) -> bool:
        foreground = int(self.user32.GetForegroundWindow() or 0)
        return any(
            target_hwnd == foreground
            and self.user32.IsWindowVisible(target_hwnd)
            and not self.user32.IsIconic(target_hwnd)
            for target_hwnd in self._overlays
        )

    def sync(self) -> None:
        for target_hwnd, overlay in list(self._overlays.items()):
            self._sync_overlay(target_hwnd, overlay)

    def sync_target(self, target_hwnd: int) -> None:
        overlay = self._overlays.get(target_hwnd)
        if overlay is not None:
            self._sync_overlay(target_hwnd, overlay)

    def _sync_overlay(self, target_hwnd: int, overlay: _Overlay) -> None:
        if (
            not self.user32.IsWindow(target_hwnd)
            or not self.user32.IsWindow(overlay.hwnd)
        ):
            self.remove(overlay.target)
            return
        if (
            not self.user32.IsWindowVisible(target_hwnd)
            or self.user32.IsIconic(target_hwnd)
            or self._is_cloaked(target_hwnd)
        ):
            self._hide(overlay)
            return

        bounds = self._window_bounds(target_hwnd)
        if bounds is None:
            self._hide(overlay)
            return
        if not overlay.visible:
            self.user32.ShowWindow(overlay.hwnd, SW_SHOWNOACTIVATE)
            overlay.visible = True
        position_changed = bounds[:2] != overlay.bounds[:2]
        size_changed = bounds[2:] != overlay.bounds[2:]
        if size_changed:
            overlay.bounds = bounds
            self._paint(overlay)
        elif position_changed:
            overlay.bounds = bounds
            self._move(overlay)
            self._place_above_target(overlay)
        else:
            self._place_above_target(overlay)

    def _handle_location_event(self, hwnd: int, id_object: int) -> None:
        if id_object != OBJID_WINDOW or hwnd not in self._overlays:
            return
        self.sync_target(hwnd)

    def _win_event_callback(
        self,
        _hook,
        event: int,
        hwnd: int,
        id_object: int,
        _id_child: int,
        _thread: int,
        _time: int,
    ) -> None:
        if event != EVENT_OBJECT_LOCATIONCHANGE or self._event_sync_active:
            return
        self._event_sync_active = True
        try:
            self._handle_location_event(int(hwnd or 0), int(id_object))
        except Exception as error:
            self._last_event_error = error
        finally:
            self._event_sync_active = False

    def _install_location_hook(self) -> None:
        if self._location_hook:
            return
        hook = self.user32.SetWinEventHook(
            EVENT_OBJECT_LOCATIONCHANGE,
            EVENT_OBJECT_LOCATIONCHANGE,
            None,
            self._win_event_proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
        self._location_hook = hook or None

    def _uninstall_location_hook(self) -> None:
        if not self._location_hook:
            return
        self.user32.UnhookWinEvent(self._location_hook)
        self._location_hook = None

    def _hide(self, overlay: _Overlay) -> None:
        if overlay.visible:
            self.user32.ShowWindow(overlay.hwnd, SW_HIDE)
            overlay.visible = False

    def _window_bounds(self, hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        result = self.dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result < 0 and not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (rect.left, rect.top, width, height)

    def _is_cloaked(self, hwnd: int) -> bool:
        value = wintypes.DWORD()
        result = self.dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result >= 0 and bool(value.value)

    def _is_topmost(self, hwnd: int) -> bool:
        style = int(self.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        return bool(style & WS_EX_TOPMOST)

    def _move(self, overlay: _Overlay) -> None:
        x, y, _width, _height = overlay.bounds
        flags = (
            SWP_NOSIZE
            | SWP_NOZORDER
            | SWP_NOACTIVATE
            | SWP_NOOWNERZORDER
            | SWP_SHOWWINDOW
        )
        if not self.user32.SetWindowPos(
            overlay.hwnd,
            None,
            x,
            y,
            0,
            0,
            flags,
        ):
            raise OSError(ctypes.get_last_error(), "SetWindowPos move failed")

    def _place_above_target(self, overlay: _Overlay) -> None:
        target_hwnd = overlay.target.hwnd
        flags = (
            SWP_NOSIZE
            | SWP_NOMOVE
            | SWP_NOACTIVATE
            | SWP_NOOWNERZORDER
            | SWP_SHOWWINDOW
        )
        target_topmost = self._is_topmost(target_hwnd)
        overlay_topmost = self._is_topmost(overlay.hwnd)
        if target_topmost and not overlay_topmost:
            if not self.user32.SetWindowPos(
                overlay.hwnd,
                wintypes.HWND(-1),
                0,
                0,
                0,
                0,
                flags,
            ):
                raise OSError(ctypes.get_last_error(), "SetWindowPos topmost failed")
        elif not target_topmost and overlay_topmost:
            if not self.user32.SetWindowPos(
                overlay.hwnd,
                wintypes.HWND(-2),
                0,
                0,
                0,
                0,
                flags,
            ):
                raise OSError(ctypes.get_last_error(), "SetWindowPos not-topmost failed")

        previous = self.user32.GetWindow(target_hwnd, GW_HWNDPREV)
        if previous == overlay.hwnd:
            return
        insert_after = previous if previous else wintypes.HWND(0)
        if not self.user32.SetWindowPos(
            overlay.hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            flags,
        ):
            raise OSError(ctypes.get_last_error(), "SetWindowPos z-order failed")

    def _paint(self, overlay: _Overlay) -> None:
        x, y, width, height = overlay.bounds
        image = ImageOps.fit(
            overlay.image,
            (width, height),
            method=Image.Resampling.LANCZOS,
        )
        red, green, blue, alpha = image.split()
        premultiplied = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(red, alpha),
                ImageChops.multiply(green, alpha),
                ImageChops.multiply(blue, alpha),
                alpha,
            ),
        )
        pixels = premultiplied.tobytes("raw", "BGRA")
        if len(pixels) != width * height * 4:
            raise RuntimeError("Overlay bitmap has an unexpected size.")

        bitmap_info = _BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        memory_dc = self.gdi32.CreateCompatibleDC(None)
        bitmap = self.gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not memory_dc or not bitmap or not bits:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if memory_dc:
                self.gdi32.DeleteDC(memory_dc)
            raise OSError(ctypes.get_last_error(), "Could not create overlay bitmap")

        previous = self.gdi32.SelectObject(memory_dc, bitmap)
        ctypes.memmove(bits, pixels, len(pixels))
        destination = wintypes.POINT(x, y)
        size = wintypes.SIZE(width, height)
        source = wintypes.POINT(0, 0)
        blend = _BlendFunction(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        screen_dc = self.user32.GetDC(None)
        try:
            updated = self.user32.UpdateLayeredWindow(
                overlay.hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            if not updated:
                raise OSError(ctypes.get_last_error(), "UpdateLayeredWindow failed")
            self._place_above_target(overlay)
        finally:
            if screen_dc:
                self.user32.ReleaseDC(None, screen_dc)
            self.gdi32.SelectObject(memory_dc, previous)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory_dc)

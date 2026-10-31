from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.artwork import build_glass_backdrop
from windowtint.backdrop_store import BackdropStore
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.native import apply_window_theme, list_windows, restore_window_theme
from windowtint.session import ThemeSession, WindowTarget
from windowtint.theme import extract_palette


OVERLAY_CLASS_NAME = "WindowTintOverlay"


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def visible_frame_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd,
        9,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result < 0:
        return window_rect(hwnd)
    return rect.left, rect.top, rect.right, rect.bottom


def move_window(hwnd: int, dx: int, dy: int) -> None:
    left, top, _right, _bottom = window_rect(hwnd)
    if not ctypes.windll.user32.SetWindowPos(
        hwnd,
        0,
        left + dx,
        top + dy,
        0,
        0,
        0x0001 | 0x0002 | 0x0004 | 0x0010,
    ):
        raise RuntimeError("SetWindowPos failed")


def bring_to_front(hwnd: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
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
    if not user32.SetWindowPos(
        hwnd,
        wintypes.HWND(0),
        0,
        0,
        0,
        0,
        0x0001 | 0x0002 | 0x0010 | 0x0040,
    ):
        raise RuntimeError(
            "could not bring the test target to the front; "
            f"error={ctypes.get_last_error()}"
        )
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def find_overlay_windows(owner: int, pid: int, class_name: str) -> list[int]:
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.windll.user32

    def visit(hwnd: int, _lparam: int) -> bool:
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if (
            (
                buffer.value == class_name
                or buffer.value.startswith(class_name + "_")
            )
            and process_id.value == pid
            and int(user32.GetWindowLongPtrW(hwnd, -8)) == owner
        ):
            matches.append(hwnd)
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return matches


def open_probe(pid: int) -> tuple[int, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenFileMappingW.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.OpenFileMappingW.restype = wintypes.HANDLE
    kernel32.MapViewOfFile.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    mapping = kernel32.OpenFileMappingW(
        0x0004,
        False,
        f"Local\\WindowTintProbe_{pid}",
    )
    view = (
        kernel32.MapViewOfFile(mapping, 0x0004, 0, 0, ctypes.sizeof(ctypes.c_long))
        if mapping
        else 0
    )
    return int(mapping or 0), int(view or 0)


def probe_stage(view: int) -> int:
    return ctypes.c_long.from_address(view).value if view else 0


def find_target(expected_pid: int) -> object:
    deadline = time.time() + 8
    while time.time() < deadline:
        for window in list_windows():
            if (
                window.pid == expected_pid
                and window.title == "WindowTint Test Target"
            ):
                return window
        time.sleep(0.2)
    raise RuntimeError("manual target window was not found")


def wait_for_single_overlay(window: object) -> int:
    deadline = time.time() + 3
    overlays: list[int] = []
    while time.time() < deadline:
        overlays = find_overlay_windows(
            window.hwnd,
            window.pid,
            OVERLAY_CLASS_NAME,
        )
        if overlays:
            break
        time.sleep(0.05)
    if not overlays:
        raise RuntimeError("the click-through image overlay was not created")
    if len(overlays) != 1:
        raise RuntimeError(
            "one application created multiple image overlays: "
            f"{overlays}"
        )
    return overlays[0]


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([target_python, str(ROOT / "tests" / "manual_target.py")])
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bounds = window_rect(window.hwnd)
        bring_to_front(window.hwnd)
        time.sleep(0.8)
        before = ImageGrab.grab(bbox=bounds)

        if len(sys.argv) > 1:
            with Image.open(sys.argv[1]) as opened:
                source = opened.convert("RGB").copy()
        else:
            source = Image.new("RGB", (720, 420))
            for x in range(source.width):
                for y in range(source.height):
                    source.putpixel((x, y), (35 + x // 8, 95 + y // 5, 150))
        palette = extract_palette(source)
        opacity = float(os.environ.get("WINDOWTINT_SMOKE_OPACITY", "0.42"))
        blur_radius = int(os.environ.get("WINDOWTINT_SMOKE_BLUR", "2"))
        backdrop = build_glass_backdrop(source, opacity=opacity, blur_radius=blur_radius)
        path = BackdropStore(ROOT / "build" / "smoke-runtime").write(backdrop)

        injector = InjectionManager(default_injector_dll())
        session = ThemeSession(
            apply_dwm=lambda item, colors, corner: apply_window_theme(
                item.hwnd, colors, corner
            ),
            restore_dwm=lambda item: restore_window_theme(item.hwnd),
            inject_backdrop=lambda item, image_path: injector.apply(item, image_path),
            remove_backdrop=lambda item: injector.remove(item),
        )
        for _ in range(3):
            result = session.apply([target], palette, "round", path)
            if result.failures:
                raise RuntimeError(f"apply failed: {result.failures}")
            wait_for_single_overlay(window)

        probe_mapping, probe_view = open_probe(window.pid)
        if process.poll() is not None:
            raise RuntimeError(
                "target process exited immediately after injection with code "
                f"{process.returncode}; last_stage={probe_stage(probe_view)}"
            )
        if not ctypes.windll.user32.IsWindow(window.hwnd):
            raise RuntimeError(
                "target window became invalid after injection; "
                f"process={process.poll()}; last_stage={probe_stage(probe_view)}"
            )
        last_stage = 0
        deadline = time.time() + 3
        while time.time() < deadline:
            last_stage = max(last_stage, probe_stage(probe_view))
            if process.poll() is not None:
                raise RuntimeError(
                    "target process exited after injection with code "
                    f"{process.returncode}; overlay_seen={bool(overlay)}; "
                    f"last_stage={last_stage}"
                )
            overlays = find_overlay_windows(
                window.hwnd,
                window.pid,
                OVERLAY_CLASS_NAME,
            )
            if overlays:
                break
            time.sleep(0.05)
        if len(overlays) != 1:
            raise RuntimeError(
                "repeated applications did not leave exactly one image overlay: "
                f"{overlays}"
            )
        overlay = overlays[0]
        style = int(ctypes.windll.user32.GetWindowLongPtrW(overlay, -16))
        if style & 0x40000000:
            raise RuntimeError("the image overlay must be an owned top-level window")
        overlay_bounds = window_rect(overlay)
        hit_x = (overlay_bounds[0] + overlay_bounds[2]) // 2
        hit_y = (overlay_bounds[1] + overlay_bounds[3]) // 2
        hit_test = ctypes.windll.user32.SendMessageW(
            overlay,
            0x0084,
            0,
            (hit_y << 16) | (hit_x & 0xFFFF),
        )
        if hit_test != -1:
            raise RuntimeError(
                "the image overlay is not click-through: "
                f"WM_NCHITTEST returned {hit_test}"
            )
        last_stage = max(last_stage, probe_stage(probe_view))
        if probe_view:
            ctypes.windll.kernel32.UnmapViewOfFile(ctypes.c_void_p(probe_view))
        if probe_mapping:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(probe_mapping))
        move_window(window.hwnd, 80, 45)
        time.sleep(0.2)
        overlay_bounds = window_rect(overlay)
        target_frame = visible_frame_rect(window.hwnd)
        if any(abs(actual - expected) > 1 for actual, expected in zip(overlay_bounds, target_frame)):
            raise RuntimeError(
                "the injected overlay did not follow the target window: "
                f"overlay={overlay_bounds}; target_frame={target_frame}"
            )
        bounds = window_rect(window.hwnd)
        time.sleep(0.4)
        after = ImageGrab.grab(bbox=bounds)
        difference = ImageChops.difference(before, after)
        changed = sum(1 for value in difference.convert("L").getdata() if value > 3)
        before.save(ROOT / "build" / "injection-before.png")
        after.save(ROOT / "build" / "injection-after.png")

        session.remove_all()
        time.sleep(0.7)
        remaining_overlays = find_overlay_windows(
            window.hwnd,
            window.pid,
            OVERLAY_CLASS_NAME,
        )
        if remaining_overlays:
            raise RuntimeError(
                "restore left image overlays behind: "
                f"{remaining_overlays}"
            )
        restored = ImageGrab.grab(bbox=bounds)
        restored.save(ROOT / "build" / "injection-restored.png")

        if changed < 500:
            raise RuntimeError(f"backdrop did not visibly change enough pixels: {changed}")
        print(f"overlay_hwnd={overlay}")
        print(f"changed_pixels={changed}")
        print(f"before={ROOT / 'build' / 'injection-before.png'}")
        print(f"after={ROOT / 'build' / 'injection-after.png'}")
        print(f"restored={ROOT / 'build' / 'injection-restored.png'}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

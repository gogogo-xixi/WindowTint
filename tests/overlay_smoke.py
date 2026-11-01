from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab, ImageStat


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.backdrop_store import BackdropStore
from windowtint.overlay import OverlayManager
from windowtint.session import WindowTarget
from windowtint.theme import extract_palette
from windowtint.artwork import build_glass_backdrop
from windowtint.native import list_windows


OVERLAY_CLASS_NAME = "WindowTintClickThroughOverlay"
GW_OWNER = 4


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
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
    return user32


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def bring_to_front(hwnd: int) -> None:
    user32 = _user32()
    user32.ShowWindow(hwnd, 9)
    if not user32.SetWindowPos(
        hwnd,
        wintypes.HWND(0),
        0,
        0,
        0,
        0,
        0x0001 | 0x0002 | 0x0010 | 0x0040,
    ):
        raise RuntimeError("could not bring the test target to the front")
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)


def move_window(hwnd: int, x_offset: int, y_offset: int) -> None:
    left, top, right, bottom = window_rect(hwnd)
    if not _user32().SetWindowPos(
        hwnd,
        wintypes.HWND(0),
        left + x_offset,
        top + y_offset,
        right - left,
        bottom - top,
        0x0004 | 0x0010,
    ):
        raise RuntimeError("could not move the test target")
    time.sleep(0.12)


def set_minimized(hwnd: int, minimized: bool) -> None:
    if not _user32().ShowWindow(hwnd, 6 if minimized else 9):
        raise RuntimeError("could not change target minimized state")
    time.sleep(0.12)


def top_level_windows() -> list[int]:
    windows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def visit(hwnd: int, _lparam: int) -> bool:
        windows.append(hwnd)
        return True

    callback = callback_type(visit)
    _user32().EnumWindows(callback, 0)
    return windows


def find_overlay(target_hwnd: int) -> int:
    user32 = _user32()
    for hwnd in top_level_windows():
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        if (
            buffer.value == OVERLAY_CLASS_NAME
            and user32.GetWindow(hwnd, GW_OWNER) == target_hwnd
        ):
            return hwnd
    return 0


def assert_overlay_is_above(overlay_hwnd: int, target_hwnd: int) -> None:
    windows = top_level_windows()
    try:
        overlay_index = windows.index(overlay_hwnd)
        target_index = windows.index(target_hwnd)
    except ValueError as error:
        raise RuntimeError("overlay or target missing from top-level z-order") from error
    if overlay_index >= target_index:
        raise RuntimeError(
            f"overlay is not above target: overlay={overlay_index}, "
            f"target={target_index}"
        )


def assert_overlay_follows(overlay_hwnd: int, target_hwnd: int) -> None:
    overlay_left, overlay_top, overlay_right, overlay_bottom = window_rect(overlay_hwnd)
    target_left, target_top, target_right, target_bottom = window_rect(target_hwnd)
    if (
        abs(overlay_left - target_left) > 12
        or abs(overlay_top - target_top) > 12
        or abs((overlay_right - overlay_left) - (target_right - target_left)) > 20
        or abs((overlay_bottom - overlay_top) - (target_bottom - target_top)) > 20
    ):
        raise RuntimeError(
            "overlay did not follow target bounds: "
            f"overlay={(overlay_left, overlay_top, overlay_right, overlay_bottom)}, "
            f"target={(target_left, target_top, target_right, target_bottom)}"
        )


def assert_covered_by(blocker_hwnd: int, overlay_hwnd: int, target_hwnd: int) -> None:
    windows = top_level_windows()
    try:
        blocker_index = windows.index(blocker_hwnd)
        overlay_index = windows.index(overlay_hwnd)
        target_index = windows.index(target_hwnd)
    except ValueError as error:
        raise RuntimeError("blocker, overlay or target missing from z-order") from error
    if not (blocker_index < overlay_index < target_index):
        raise RuntimeError(
            "overlay did not stay below the covering window: "
            f"blocker={blocker_index}, overlay={overlay_index}, "
            f"target={target_index}"
        )


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


def make_backdrop() -> Path:
    source = Image.new("RGB", (720, 420), "#1d7f78")
    for y in range(source.height):
        for x in range(source.width):
            source.putpixel(
                (x, y),
                (25 + x // 9, 80 + y // 6, 120 + (x + y) // 12),
            )
    palette = extract_palette(source)
    backdrop = build_glass_backdrop(
        source,
        opacity=0.48,
        blur_radius=16,
        content_focus=True,
    )
    if palette.dominant == (0, 0, 0):
        raise RuntimeError("test backdrop palette was not generated")
    return BackdropStore(ROOT / "build" / "overlay-smoke").write(backdrop)


def changed_pixels(before: Image.Image, after: Image.Image) -> int:
    difference = ImageChops.difference(before, after).convert("L")
    return sum(1 for value in difference.getdata() if value > 3)


def central_contrast(image: Image.Image) -> float:
    width, height = image.size
    crop = image.crop(
        (
            round(width * 0.32),
            round(height * 0.29),
            round(width * 0.68),
            round(height * 0.73),
        )
    ).convert("L")
    return sum(ImageStat.Stat(crop).stddev)


def assert_recovered(before: Image.Image, restored: Image.Image) -> None:
    width, height = before.size
    client_box = (10, 45, width - 10, height - 10)
    difference = ImageChops.difference(
        before.crop(client_box),
        restored.crop(client_box),
    ).convert("L")
    changed = sum(1 for value in difference.getdata() if value > 10)
    if changed > 500:
        raise RuntimeError(f"window did not recover cleanly: {changed} pixels changed")


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    blocker = None
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(target.hwnd)
        bounds = window_rect(target.hwnd)
        before = ImageGrab.grab(bbox=bounds)
        backdrop_path = make_backdrop()
        manager = OverlayManager()

        for index in range(8):
            if process.poll() is not None:
                raise RuntimeError(
                    f"target exited before cycle {index + 1}: {process.returncode}"
                )
            manager.apply(target, backdrop_path)
            overlay_hwnd = find_overlay(target.hwnd)
            if not overlay_hwnd:
                raise RuntimeError("click-through overlay was not created")
            assert_overlay_is_above(overlay_hwnd, target.hwnd)
            manager.sync()
            assert_overlay_is_above(overlay_hwnd, target.hwnd)

            if index == 0:
                move_window(target.hwnd, 37, 29)
                manager.sync()
                assert_overlay_follows(overlay_hwnd, target.hwnd)
                move_window(target.hwnd, -37, -29)
                manager.sync()
                assert_overlay_follows(overlay_hwnd, target.hwnd)

                set_minimized(target.hwnd, True)
                manager.sync()
                if _user32().IsWindowVisible(overlay_hwnd):
                    raise RuntimeError("overlay stayed visible while target was minimized")
                set_minimized(target.hwnd, False)
                manager.sync()
                if not _user32().IsWindowVisible(overlay_hwnd):
                    raise RuntimeError("overlay did not return after target restore")

                bring_to_front(target.hwnd)
                after = ImageGrab.grab(bbox=bounds)
                changed = changed_pixels(before, after)
                contrast = central_contrast(after)
                before.save(ROOT / "build" / "overlay-before.png")
                after.save(ROOT / "build" / "overlay-after.png")
                if changed < 500:
                    raise RuntimeError(
                        f"backdrop did not visibly cover the target: {changed} pixels"
                    )
                if contrast < 24:
                    raise RuntimeError(
                        f"native content is not readable through the overlay: {contrast}"
                    )

                blocker = subprocess.Popen(
                    [target_python, str(ROOT / "tests" / "manual_target.py")]
                )
                blocker_window = find_target(blocker.pid)
                bring_to_front(blocker_window.hwnd)
                manager.sync()
                assert_covered_by(
                    blocker_window.hwnd,
                    overlay_hwnd,
                    target.hwnd,
                )
                blocker.terminate()
                blocker.wait(timeout=3)
                blocker = None
                bring_to_front(target.hwnd)
                manager.sync()
                assert_overlay_is_above(overlay_hwnd, target.hwnd)

            manager.remove(target)
            if find_overlay(target.hwnd):
                raise RuntimeError("overlay was not destroyed by remove")

        manager.apply(target, backdrop_path)
        manager.remove_all()
        bring_to_front(target.hwnd)
        time.sleep(0.25)
        restored = ImageGrab.grab(bbox=bounds)
        restored.save(ROOT / "build" / "overlay-restored.png")
        assert_recovered(before, restored)

        if process.poll() is not None:
            raise RuntimeError(f"target process exited: {process.returncode}")
        print(f"target_hwnd={target.hwnd}")
        print("cycles=8")
        print(f"before={ROOT / 'build' / 'overlay-before.png'}")
        print(f"after={ROOT / 'build' / 'overlay-after.png'}")
        print(f"restored={ROOT / 'build' / 'overlay-restored.png'}")
    finally:
        if blocker is not None and blocker.poll() is None:
            blocker.terminate()
            try:
                blocker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                blocker.kill()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

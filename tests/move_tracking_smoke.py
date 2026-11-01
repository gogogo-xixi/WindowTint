from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_target,
    visible_frame_rect,
    wait_for_single_overlay,
    window_rect,
)
from motion_smoke import (
    pump_messages,
    wait_until_event_overlay_matches,
    wait_until_overlay_matches,
)
from overlay_smoke import make_backdrop
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.overlay import OverlayManager
from windowtint.session import WindowTarget


MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def drag_without_release(
    hwnd: int,
    offset_x: int,
    offset_y: int,
    sample: Callable[[], int] | None = None,
) -> tuple[int, int, int]:
    user32 = ctypes.windll.user32
    left, top, right, _bottom = visible_frame_rect(hwnd)
    start_x = (left + right) // 2
    start_y = top + 18
    previous = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(previous)):
        raise RuntimeError("could not read the cursor position")

    user32.SetForegroundWindow(hwnd)
    user32.SetCursorPos(start_x, start_y)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    max_offset = 0
    try:
        for index in range(1, 13):
            user32.SetCursorPos(
                start_x + round(offset_x * index / 12),
                start_y + round(offset_y * index / 12),
            )
            pump_messages()
            time.sleep(0.02)
            if sample is not None:
                max_offset = max(max_offset, sample())
        for _ in range(15):
            pump_messages()
            time.sleep(0.01)
            if sample is not None:
                max_offset = max(max_offset, sample())
    except Exception:
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(previous.x, previous.y)
        raise
    return previous.x, previous.y, max_offset


def release_drag(previous_x: int, previous_y: int) -> None:
    user32 = ctypes.windll.user32
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    user32.SetCursorPos(previous_x, previous_y)


def measure_drag(
    target_hwnd: int,
    overlay_hwnd: int,
    *,
    event_driven: bool,
    sync: Callable[[], None] | None = None,
) -> int:
    user32 = ctypes.windll.user32

    def sample() -> int:
        if sync is not None:
            sync()
        if not user32.IsWindowVisible(overlay_hwnd):
            raise RuntimeError("overlay disappeared during an interactive move")
        overlay_bounds = window_rect(overlay_hwnd)
        target_bounds = visible_frame_rect(target_hwnd)
        return max(
            abs(actual - expected)
            for actual, expected in zip(overlay_bounds, target_bounds)
        )

    previous_x, previous_y, max_offset = drag_without_release(
        target_hwnd,
        72,
        36,
        sample,
    )
    release_drag(previous_x, previous_y)

    if event_driven:
        wait_until_event_overlay_matches(overlay_hwnd, target_hwnd)
    else:
        wait_until_overlay_matches(overlay_hwnd, target_hwnd)
    if not user32.IsWindowVisible(overlay_hwnd):
        raise RuntimeError("overlay did not return after the interactive move")
    return max_offset


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([target_python, str(ROOT / "tests" / "manual_target.py")])
    manager = None
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(target.hwnd, topmost=True)
        backdrop = make_backdrop()

        injector = InjectionManager(default_injector_dll())
        injector.apply(target, backdrop)
        try:
            injected_overlay = wait_for_single_overlay(window)
            injected_offset = measure_drag(
                target.hwnd,
                injected_overlay,
                event_driven=False,
            )
        finally:
            injector.remove(target)

        manager = OverlayManager()
        manager.apply(target, backdrop)
        external_overlay = next(iter(manager._overlays.values())).hwnd
        external_offset = measure_drag(
            target.hwnd,
            external_overlay,
            event_driven=True,
            sync=lambda: manager.sync_target(target.hwnd),
        )
        if injected_offset > 3 or external_offset > 3:
            raise RuntimeError("overlay lag exceeded 3 px during an interactive move")
        print(
            "PASS: overlays stay visible during interactive moves "
            f"(injected={injected_offset}px, external={external_offset}px)"
        )
    finally:
        if manager is not None:
            manager.remove_all()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

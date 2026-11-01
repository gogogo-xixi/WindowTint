from __future__ import annotations

import ctypes
import math
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image


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
from motion_smoke import percentile
from windowtint.artwork import build_glass_backdrop
from windowtint.backdrop_store import BackdropStore
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.session import WindowTarget


RESIZE_FLAGS = 0x0002 | 0x0004 | 0x0010
MOVE_FLAGS = 0x0001 | 0x0004 | 0x0010
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
STEPS = 80
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232


def resize_target(hwnd: int, x: int, y: int, width: int, height: int) -> float:
    started = time.perf_counter()
    if not ctypes.windll.user32.SetWindowPos(
        hwnd,
        0,
        x,
        y,
        width,
        height,
        RESIZE_FLAGS,
    ):
        raise RuntimeError("could not resize the test target")
    return (time.perf_counter() - started) * 1000


def move_target(hwnd: int, x: int, y: int) -> None:
    if not ctypes.windll.user32.SetWindowPos(
        hwnd,
        0,
        x,
        y,
        0,
        0,
        MOVE_FLAGS,
    ):
        raise RuntimeError("could not move the test target")


def drag_target(hwnd: int, offset_x: int, offset_y: int) -> None:
    user32 = ctypes.windll.user32
    left, top, right, _bottom = visible_frame_rect(hwnd)
    start_x = (left + right) // 2
    start_y = top + 18
    previous = ctypes.wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(previous)):
        raise RuntimeError("could not read the cursor position")

    user32.SetForegroundWindow(hwnd)
    user32.SetCursorPos(start_x, start_y)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        for index in range(1, 13):
            user32.SetCursorPos(
                start_x + round(offset_x * index / 12),
                start_y + round(offset_y * index / 12),
            )
            time.sleep(0.02)
    finally:
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        user32.SetCursorPos(previous.x, previous.y)


def wait_until_overlay_matches(
    overlay: int,
    target: int,
    *,
    timeout: float = 0.25,
) -> None:
    deadline = time.perf_counter() + timeout
    while True:
        overlay_bounds = window_rect(overlay)
        target_bounds = visible_frame_rect(target)
        if all(
            abs(actual - expected) <= 1
            for actual, expected in zip(overlay_bounds, target_bounds)
        ):
            return
        if time.perf_counter() >= deadline:
            raise RuntimeError(
                "resized overlay did not settle within 250 ms: "
                f"overlay={overlay_bounds}, target={target_bounds}"
            )
        time.sleep(0.0005)


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(target.hwnd, topmost=True)
        time.sleep(0.4)
        left, top, _right, _bottom = window_rect(target.hwnd)

        source = Image.linear_gradient("L").resize(
            (1600, 1000),
            Image.Resampling.BILINEAR,
        ).convert("RGB")
        backdrop = build_glass_backdrop(
            source,
            opacity=0.48,
            blur_radius=2,
        )
        path = BackdropStore(ROOT / "build" / "resize-runtime").write(backdrop)
        injector = InjectionManager(default_injector_dll())
        injector.apply(target, path)
        overlay = wait_for_single_overlay(window)

        latencies = []
        settle_ms = 0.0
        interactive_latencies = []
        interactive_settle_ms = 0.0
        try:
            drag_target(target.hwnd, 60, 36)
            wait_until_overlay_matches(overlay, target.hwnd)
            if not ctypes.windll.user32.IsWindowVisible(overlay):
                raise RuntimeError(
                    "overlay disappeared during a real interactive move"
                )
            move_target(target.hwnd, left, top)
            wait_until_overlay_matches(overlay, target.hwnd)

            for index in range(STEPS):
                width = 680 + round(360 * abs(math.sin(index / 13)))
                height = 390 + round(260 * abs(math.cos(index / 11)))
                latencies.append(
                    resize_target(target.hwnd, left, top, width, height)
                )
                time.sleep(0.004)
            settle_started = time.perf_counter()
            wait_until_overlay_matches(overlay, target.hwnd, timeout=1.0)
            settle_ms = (time.perf_counter() - settle_started) * 1000

            ctypes.windll.user32.SendMessageW(
                target.hwnd,
                WM_ENTERSIZEMOVE,
                0,
                0,
            )
            for index in range(40):
                width = 760 + round(260 * abs(math.sin(index / 9)))
                height = 430 + round(180 * abs(math.cos(index / 8)))
                interactive_latencies.append(
                    resize_target(target.hwnd, left, top, width, height)
                )
                time.sleep(0.004)
            time.sleep(0.25)
            if ctypes.windll.user32.IsWindowVisible(overlay):
                raise RuntimeError("overlay reappeared while an interactive resize was active")
            ctypes.windll.user32.SendMessageW(
                target.hwnd,
                WM_EXITSIZEMOVE,
                0,
                0,
            )
            interactive_settle_started = time.perf_counter()
            wait_until_overlay_matches(overlay, target.hwnd, timeout=1.0)
            interactive_settle_ms = (
                time.perf_counter() - interactive_settle_started
            ) * 1000
            if not ctypes.windll.user32.IsWindowVisible(overlay):
                raise RuntimeError("overlay did not return after an interactive resize")
        finally:
            injector.remove(target)

        p50 = percentile(latencies, 0.50)
        p95 = percentile(latencies, 0.95)
        print(
            f"injected_resize: p50={p50:.2f}ms "
            f"p95={p95:.2f}ms max={max(latencies):.2f}ms "
            f"settle={settle_ms:.2f}ms"
        )
        interactive_p95 = percentile(interactive_latencies, 0.95)
        print(
            f"interactive_resize: p50={percentile(interactive_latencies, 0.50):.2f}ms "
            f"p95={interactive_p95:.2f}ms "
            f"max={max(interactive_latencies):.2f}ms "
            f"settle={interactive_settle_ms:.2f}ms"
        )
        if p95 > 16:
            raise RuntimeError("injected overlay resize p95 exceeded 16 ms")
        if interactive_p95 > 16:
            raise RuntimeError("interactive overlay resize p95 exceeded 16 ms")
        if settle_ms > 500:
            raise RuntimeError("injected overlay took longer than 500 ms to settle")
        if interactive_settle_ms > 500:
            raise RuntimeError(
                "interactive overlay took longer than 500 ms to settle"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

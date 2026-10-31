from __future__ import annotations

import ctypes
import math
import subprocess
import sys
import time
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
from overlay_smoke import assert_overlay_follows, make_backdrop
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.overlay import OverlayManager
from windowtint.session import WindowTarget


MOVE_FLAGS = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
PM_REMOVE = 0x0001
STEPS = 120


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * ratio))
    return ordered[index]


def move_target(hwnd: int, x: int, y: int) -> None:
    if not ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, MOVE_FLAGS):
        raise RuntimeError("could not move the test target")


def wait_until_overlay_matches(overlay: int, target: int) -> float:
    started = time.perf_counter()
    deadline = started + 0.1
    while True:
        overlay_bounds = window_rect(overlay)
        target_bounds = visible_frame_rect(target)
        if all(
            abs(actual - expected) <= 1
            for actual, expected in zip(overlay_bounds, target_bounds)
        ):
            return (time.perf_counter() - started) * 1000
        if time.perf_counter() >= deadline:
            raise RuntimeError(
                "injected overlay did not settle within 100 ms: "
                f"overlay={overlay_bounds}, target={target_bounds}"
            )
        time.sleep(0.001)


def pump_messages() -> None:
    message = wintypes.MSG()
    while ctypes.windll.user32.PeekMessageW(
        ctypes.byref(message),
        None,
        0,
        0,
        PM_REMOVE,
    ):
        ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))


def wait_until_event_overlay_matches(overlay: int, target: int) -> float:
    started = time.perf_counter()
    deadline = started + 0.1
    while True:
        pump_messages()
        overlay_bounds = window_rect(overlay)
        target_bounds = visible_frame_rect(target)
        if all(
            abs(actual - expected) <= 1
            for actual, expected in zip(overlay_bounds, target_bounds)
        ):
            return (time.perf_counter() - started) * 1000
        if time.perf_counter() >= deadline:
            raise RuntimeError(
                "event-driven overlay did not settle within 100 ms: "
                f"overlay={overlay_bounds}, target={target_bounds}"
            )
        time.sleep(0.0005)


def movement_positions(base_x: int, base_y: int):
    for index in range(STEPS):
        yield (
            base_x + round(48 * math.sin(index / 8)),
            base_y + round(32 * math.cos(index / 6)),
        )


def summarize(label: str, values: list[float]) -> None:
    print(
        f"{label}: p50={percentile(values, 0.50):.2f}ms "
        f"p95={percentile(values, 0.95):.2f}ms "
        f"max={max(values):.2f}ms"
    )


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(target.hwnd)
        backdrop = make_backdrop()
        base_x, base_y, _right, _bottom = window_rect(target.hwnd)
        positions = list(movement_positions(base_x, base_y))

        injector = InjectionManager(default_injector_dll())
        injector.apply(target, backdrop)
        injected_overlay = wait_for_single_overlay(window)

        injected_latency = []
        for x, y in positions:
            move_target(target.hwnd, x, y)
            injected_latency.append(
                wait_until_overlay_matches(injected_overlay, target.hwnd)
            )
        injector.remove(target)
        summarize("injected_follow", injected_latency)

        manager = OverlayManager()
        manager.apply(target, backdrop)
        bring_to_front(target.hwnd)
        if not manager.event_driven:
            raise RuntimeError("Explorer-style overlay did not install its location hook")
        print(f"event_driven={manager.event_driven}")

        external_latency = []
        overlay_hwnd = next(iter(manager._overlays.values())).hwnd
        for x, y in positions:
            move_target(target.hwnd, x, y)
            external_latency.append(
                wait_until_event_overlay_matches(overlay_hwnd, target.hwnd)
            )
            assert_overlay_follows(overlay_hwnd, target.hwnd)
        manager.remove_all()
        if manager.event_driven:
            raise RuntimeError("Explorer-style location hook was not released")
        summarize("external_sync", external_latency)

        if percentile(injected_latency, 0.95) > 25:
            raise RuntimeError("injected overlay movement p95 exceeded 25 ms")
        if percentile(external_latency, 0.95) > 25:
            raise RuntimeError("external overlay sync p95 exceeded 25 ms")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

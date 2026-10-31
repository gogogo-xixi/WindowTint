from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_overlay_windows,
    move_window,
    visible_frame_rect,
    wait_for_single_overlay,
    window_rect,
)
from overlay_smoke import make_backdrop
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.native import list_windows
from windowtint.session import WindowTarget


BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}


def changed_pixels(before, after) -> int:
    difference = ImageChops.difference(before, after).convert("L")
    return sum(1 for value in difference.getdata() if value > 3)


def main() -> None:
    candidates = [
        window
        for window in list_windows()
        if window.process_name.casefold() in BROWSERS
    ]
    if not candidates:
        raise RuntimeError("no supported browser window is open")

    backdrop = make_backdrop()
    injector = InjectionManager(default_injector_dll())
    for window in candidates:
        target = WindowTarget(window.hwnd, window.pid)
        stem = window.process_name.removesuffix(".exe").replace(".", "-")
        bring_to_front(window.hwnd)
        time.sleep(0.4)
        bounds = window_rect(window.hwnd)
        before = ImageGrab.grab(bbox=bounds)
        try:
            injector.apply(target, backdrop)
            overlay = wait_for_single_overlay(window)
            time.sleep(0.6)
            after = ImageGrab.grab(bbox=bounds)
            changed = changed_pixels(before, after)
            before.save(ROOT / "build" / f"browser-{stem}-before.png")
            after.save(ROOT / "build" / f"browser-{stem}-after.png")

            move_window(window.hwnd, 20, 12)
            time.sleep(0.15)
            overlay_bounds = window_rect(overlay)
            target_bounds = visible_frame_rect(window.hwnd)
            follows = all(
                abs(actual - expected) <= 1
                for actual, expected in zip(overlay_bounds, target_bounds)
            )
            move_window(window.hwnd, -20, -12)
            print(
                f"{window.process_name} hwnd={window.hwnd:08X} "
                f"overlay={overlay:08X} changed_pixels={changed} follows={follows}"
            )
        finally:
            injector.remove(target)
            time.sleep(0.3)
            remaining = find_overlay_windows(
                window.hwnd,
                window.pid,
                "WindowTintOverlay",
            )
            if remaining:
                raise RuntimeError(
                    f"{window.process_name} left overlays behind: {remaining}"
                )
            restored = ImageGrab.grab(bbox=window_rect(window.hwnd))
            restored_changed = changed_pixels(before, restored)
            restored.save(ROOT / "build" / f"browser-{stem}-restored.png")
            print(
                f"{window.process_name} restored_changed_pixels={restored_changed}"
            )


if __name__ == "__main__":
    main()

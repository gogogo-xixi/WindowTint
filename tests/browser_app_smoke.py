from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_overlay_windows,
    move_window,
    open_probe,
    probe_stage,
    wait_for_single_overlay,
    window_rect,
)
from overlay_smoke import assert_overlay_is_above
from windowtint.app import WindowTintApp
from windowtint.injection import INJECTOR_RELEASE_NAME, InjectionManager
from windowtint.native import list_windows
from windowtint.theme import extract_palette


BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
WS_EX_TOPMOST = 0x00000008
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010


def changed_pixels(before, after) -> int:
    difference = ImageChops.difference(before, after).convert("L")
    return sum(1 for value in difference.getdata() if value > 3)


def set_topmost(hwnd: int, enabled: bool) -> None:
    user32 = ctypes.windll.user32
    if not user32.SetWindowPos(
        hwnd,
        wintypes.HWND(-1 if enabled else -2),
        0,
        0,
        0,
        0,
        SWP_FLAGS,
    ):
        raise RuntimeError("could not update the test window z-order")


def is_topmost(hwnd: int) -> bool:
    style = int(ctypes.windll.user32.GetWindowLongPtrW(hwnd, -20))
    return bool(style & WS_EX_TOPMOST)


def main() -> None:
    browser_windows = [
        window
        for window in list_windows()
        if window.process_name.casefold() in BROWSERS
    ]
    if not browser_windows:
        raise RuntimeError("no supported browser window is open")

    app = WindowTintApp()
    try:
        release_dll = (
            ROOT
            / "dist"
            / "WindowTint-0.4.11-win64"
            / INJECTOR_RELEASE_NAME
        )
        if not release_dll.exists():
            raise RuntimeError(f"release injector is missing: {release_dll}")
        app.injection = InjectionManager(release_dll)
        app.withdraw()
        app.resident_var.set(False)
        app.scope_var.set("selected")
        app._scope_changed()
        app.image = Image.new("RGB", (1280, 800), "#5d9a8b")
        app.palette = extract_palette(app.image)

        for expected in browser_windows:
            was_topmost = is_topmost(expected.hwnd)
            try:
                app.refresh_windows()
                label = next(
                    (
                        label
                        for label, candidate in app.window_by_label.items()
                        if candidate.hwnd == expected.hwnd
                    ),
                    None,
                )
                if label is None:
                    raise RuntimeError(
                        f"{expected.process_name} disappeared from the app window list"
                    )
                bring_to_front(expected.hwnd)
                app.target_var.set(label)
                bounds = window_rect(expected.hwnd)
                before = ImageGrab.grab(bbox=bounds)

                result = app.apply_theme(manual=False)
                if (
                    result is None
                    or result.failures
                    or result.applied != (expected.hwnd,)
                ):
                    raise RuntimeError(
                        f"{expected.process_name} app apply failed: {result}; "
                        f"status={app.status_label.cget('text')}"
                    )
                overlay = wait_for_single_overlay(expected)
                assert_overlay_is_above(overlay, expected.hwnd)
                probe_mapping, probe_view = open_probe(expected.pid)
                stage_deadline = time.time() + 3
                stage = 0
                while time.time() < stage_deadline:
                    stage = probe_stage(probe_view)
                    if stage >= 9:
                        break
                    time.sleep(0.05)
                if probe_view:
                    ctypes.windll.kernel32.UnmapViewOfFile(
                        ctypes.c_void_p(probe_view)
                    )
                if probe_mapping:
                    ctypes.windll.kernel32.CloseHandle(
                        ctypes.c_void_p(probe_mapping)
                    )
                time.sleep(0.5)
                after = ImageGrab.grab(bbox=bounds)
                changed = changed_pixels(before, after)
                stem = expected.process_name.removesuffix(".exe")
                before.save(ROOT / "build" / f"browser-app-{stem}-before.png")
                after.save(ROOT / "build" / f"browser-app-{stem}-after.png")

                move_window(expected.hwnd, 18, 10)
                time.sleep(0.15)
                followed = bool(
                    find_overlay_windows(
                        expected.hwnd,
                        expected.pid,
                        "WindowTintOverlay",
                    )
                )
                move_window(expected.hwnd, -18, -10)
                app.restore_theme()
                time.sleep(0.3)
                remaining = find_overlay_windows(
                    expected.hwnd,
                    expected.pid,
                    "WindowTintOverlay",
                )
                if stage < 9:
                    raise RuntimeError(
                        f"{expected.process_name} injector stopped at stage {stage}"
                    )
                if changed < 500:
                    raise RuntimeError(
                        f"{expected.process_name} produced too little visible change: "
                        f"{changed}"
                    )
                if not followed:
                    raise RuntimeError(
                        f"{expected.process_name} overlay did not follow movement"
                    )
                if remaining:
                    raise RuntimeError(
                        f"{expected.process_name} left overlays behind: {remaining}"
                    )
                print(
                    f"{expected.process_name} dll={release_dll.name} "
                    f"app_applied=True overlay={overlay:08X} "
                    f"z_order=above stage={stage} "
                    f"changed_pixels={changed} followed={followed} "
                    f"remaining={len(remaining)}"
                )
            finally:
                app.restore_theme()
                if is_topmost(expected.hwnd) != was_topmost:
                    set_topmost(expected.hwnd, was_topmost)
                if is_topmost(expected.hwnd) != was_topmost:
                    raise RuntimeError(
                        f"{expected.process_name} topmost state was not restored"
                    )
    finally:
        app.exit_app()


if __name__ == "__main__":
    main()

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from overlay_smoke import (
    assert_overlay_follows,
    bring_to_front,
    changed_pixels,
    find_overlay,
    move_window,
    window_rect,
)
from windowtint.app import WindowTintApp
from windowtint.native import list_windows
from windowtint.theme import extract_palette


def find_explorer_window(folder_name: str):
    deadline = time.time() + 15
    while time.time() < deadline:
        for window in list_windows():
            if folder_name in window.title and window.renderer == "external":
                return window
        time.sleep(0.2)
    raise RuntimeError("explorer file window did not enter the eligible window list")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="WindowTint Explorer Smoke ") as directory:
        folder = Path(directory)
        (folder / "sample.txt").write_text("WindowTint Explorer overlay\n", encoding="utf-8")
        process = subprocess.Popen(["explorer.exe", str(folder)])
        target_hwnd = 0
        app = None
        try:
            window = find_explorer_window(folder.name)
            target_hwnd = window.hwnd
            bring_to_front(window.hwnd)
            time.sleep(0.5)
            bounds = window_rect(window.hwnd)
            before = ImageGrab.grab(bbox=bounds)

            app = WindowTintApp()
            app.withdraw()
            app.resident_var.set(False)
            app.scope_var.set("selected")
            app._scope_changed()
            app.image = Image.new("RGB", (1280, 800), (58, 126, 112))
            app.palette = extract_palette(app.image)
            app.refresh_windows()
            label = next(
                (
                    label
                    for label, candidate in app.window_by_label.items()
                    if candidate.hwnd == window.hwnd
                ),
                None,
            )
            if label is None:
                raise RuntimeError("explorer window disappeared from the app list")
            app.target_var.set(label)
            try:
                result = app.apply_theme(manual=False)
                if result is None or result.failures or result.applied != (window.hwnd,):
                    raise RuntimeError(
                        f"app did not apply the explorer overlay: {result}; "
                        f"status={app.status_label.cget('text')}"
                    )
                overlay_hwnd = find_overlay(window.hwnd)
                if not overlay_hwnd:
                    raise RuntimeError("explorer external overlay was not created")
                move_window(window.hwnd, 40, 24)
                deadline = time.time() + 2
                while True:
                    app.update()
                    try:
                        assert_overlay_follows(overlay_hwnd, window.hwnd)
                        break
                    except RuntimeError:
                        if time.time() >= deadline:
                            raise
                        time.sleep(0.05)
                bounds = window_rect(window.hwnd)
                time.sleep(0.5)
                after = ImageGrab.grab(bbox=bounds)
                changed = changed_pixels(before, after)
                output = ROOT / "build" / "explorer-overlay.png"
                after.save(output)
                if changed < 500:
                    raise RuntimeError(
                        f"explorer overlay did not visibly cover the window: {changed}"
                    )
            finally:
                app.restore_theme()

            if find_overlay(window.hwnd):
                raise RuntimeError("explorer external overlay was not removed")
            if app.session.active_targets:
                raise RuntimeError("explorer remained active after restore")
            print(f"explorer_hwnd={window.hwnd}")
            print(f"changed_pixels={changed}")
            print(f"screenshot={output}")
        finally:
            if app is not None:
                app.exit_app()
            if target_hwnd:
                ctypes.windll.user32.PostMessageW(target_hwnd, 0x0010, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()


if __name__ == "__main__":
    main()

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.app import WindowTintApp
from windowtint.native import list_windows
from windowtint.theme import extract_palette


def find_target():
    deadline = time.time() + 8
    while time.time() < deadline:
        for window in list_windows():
            if window.title == "WindowTint Test Target":
                return window
        time.sleep(0.2)
    raise RuntimeError("manual target window was not found")


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    target_process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    app = None
    try:
        target = find_target()
        app = WindowTintApp()
        app.resident_var.set(True)
        app.scope_var.set("selected")
        app._scope_changed()
        app.image = Image.new("RGB", (900, 600), "#4f8e82")
        app.palette = extract_palette(app.image)
        app.refresh_windows()
        labels = [
            label
            for label, window in app.window_by_label.items()
            if window.hwnd == target.hwnd
        ]
        if not labels:
            raise RuntimeError("target disappeared from eligible window list")
        app.target_var.set(labels[0])

        result = app.apply_theme(manual=False)
        if result is None or result.applied != (target.hwnd,):
            raise RuntimeError(f"apply failed: {result}; status={app.status_label.cget('text')}")
        app._close()
        app.update()
        time.sleep(0.6)
        app.update()
        if app.state() != "withdrawn":
            raise RuntimeError(f"window did not hide to tray: {app.state()}")
        if not app.session.active_targets:
            raise RuntimeError("resident mode lost its active target")

        app._show_window()
        app.update()
        app.restore_theme()
        app.update()
        if app.session.active_targets:
            raise RuntimeError("restore left active targets")
        print("resident_hide=ok")
        print("restore=ok")
    finally:
        if app is not None:
            app.exit_app()
        target_process.terminate()
        try:
            target_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            target_process.kill()


if __name__ == "__main__":
    main()

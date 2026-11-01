from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_target,
    window_rect,
    wait_for_single_overlay,
)
from resize_motion_smoke import resize_target
from windowtint.advanced import InstallationProbe, classify_installation
from windowtint.app import WindowTintApp
from windowtint.session import WindowTarget
from windowtint.theme import extract_palette


def main() -> None:
    theme_path = Path(r"C:\Windows\Web\Wallpaper\ThemeB\img24.jpg")
    if not theme_path.exists():
        raise RuntimeError(f"smoke-test wallpaper is missing: {theme_path}")

    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    app = None
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(window.hwnd, topmost=True)
        time.sleep(0.4)
        left, top, _right, _bottom = window_rect(window.hwnd)

        with (
            mock.patch("windowtint.app.list_windows", return_value=[]),
            mock.patch(
                "windowtint.app.detect_installation",
                return_value=classify_installation(InstallationProbe()),
            ),
        ):
            app = WindowTintApp()
        app.resident_var.set(False)
        app.withdraw()
        with Image.open(theme_path) as opened:
            app.image = opened.convert("RGB").copy()

        result = app.session.apply(
            [target],
            extract_palette(app.image),
            "round",
            ROOT / "build" / "unused-app-resize-backdrop.png",
        )
        if result.failures:
            raise RuntimeError(f"initial apply failed: {result.failures}")
        wait_for_single_overlay(window)
        first_backdrop = app._target_backdrops[target.hwnd]
        seen_backdrops = {first_backdrop}

        for index in range(36):
            width = 720 + round(420 * abs(math.sin(index / 7)))
            height = 440 + round(260 * abs(math.cos(index / 5)))
            resize_target(target.hwnd, left, top, width, height)
            app.update()
            time.sleep(0.015)

        deadline = time.monotonic() + 8
        changed_at = None
        while time.monotonic() < deadline:
            app.update()
            current = app._target_backdrops.get(target.hwnd, first_backdrop)
            if current != first_backdrop:
                seen_backdrops.add(current)
                if changed_at is None:
                    changed_at = time.monotonic()
                elif time.monotonic() - changed_at >= 0.9:
                    break
            time.sleep(0.02)
        else:
            raise RuntimeError("resized target did not settle on one new backdrop")

        if len(seen_backdrops) != 2:
            raise RuntimeError(
                f"rapid resize produced {len(seen_backdrops)} backdrops, expected 2"
            )
        print("PASS: rapid resize rendered exactly one final backdrop after settling")
    finally:
        if app is not None:
            app.exit_app()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

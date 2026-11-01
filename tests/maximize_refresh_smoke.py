from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from PIL import Image, ImageGrab


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
from windowtint.advanced import InstallationProbe, classify_installation
from windowtint.app import WindowTintApp
from windowtint.session import WindowTarget
from windowtint.theme import extract_palette


SW_MAXIMIZE = 3


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

        initial_path = ROOT / "build" / "unused-initial-backdrop.png"
        result = app.session.apply(
            [target],
            extract_palette(app.image),
            "round",
            initial_path,
        )
        if result.failures:
            raise RuntimeError(f"initial apply failed: {result.failures}")
        first_backdrop = app._target_backdrops[target.hwnd]
        overlay = wait_for_single_overlay(window)

        ctypes.windll.user32.ShowWindow(target.hwnd, SW_MAXIMIZE)
        deadline = time.monotonic() + 10
        current_backdrop = first_backdrop
        frame = visible_frame_rect(target.hwnd)
        expected_size = app._render_size(
            (frame[2] - frame[0], frame[3] - frame[1])
        )
        while time.monotonic() < deadline:
            app.update()
            current_backdrop = app._target_backdrops.get(
                target.hwnd,
                first_backdrop,
            )
            frame = visible_frame_rect(target.hwnd)
            if current_backdrop != first_backdrop:
                with Image.open(current_backdrop) as rendered:
                    if rendered.size == expected_size:
                        break
            time.sleep(0.02)
        else:
            raise RuntimeError(
                "maximized window did not receive a backdrop rendered at its new size"
            )

        with Image.open(current_backdrop) as rendered:
            if rendered.size != expected_size:
                raise RuntimeError(
                    f"refreshed backdrop size {rendered.size} != {expected_size}"
                )
        if window_rect(overlay) != frame:
            raise RuntimeError(
                f"overlay bounds {window_rect(overlay)} != target frame {frame}"
            )
        if not ctypes.windll.user32.IsWindowVisible(overlay):
            raise RuntimeError("overlay was not visible after maximizing")

        # Apply posts its image update to the target window, so let the target
        # message loop consume the new bitmap before taking the evidence shot.
        for _ in range(25):
            app.update()
            time.sleep(0.02)
        ImageGrab.grab(bbox=frame).save(ROOT / "build" / "maximize-refresh-final.png")
        print(
            "PASS: maximized window refreshed at the exact frame size "
            f"({expected_size[0]}x{expected_size[1]})"
        )
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

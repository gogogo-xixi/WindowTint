from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.artwork import render_glass_backdrop
from windowtint.backdrop_store import BackdropStore
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.native import list_windows, window_frame_rect
from windowtint.session import WindowTarget


def find_winver() -> WindowTarget:
    for window in list_windows():
        if (
            window.process_name.casefold() == "winver.exe"
            and window.renderer == "painted"
        ):
            return WindowTarget(window.hwnd, window.pid, window.renderer)
    raise RuntimeError("winver.exe standard dialog was not classified as painted")


def screenshot(target: WindowTarget) -> Image.Image:
    bounds = window_frame_rect(target.hwnd)
    return ImageGrab.grab(
        bbox=(
            bounds[0],
            bounds[1],
            bounds[2],
            bounds[3],
        )
    ).convert("RGB")


def main() -> None:
    process = subprocess.Popen(
        [r"C:\Windows\System32\winver.exe"]
    )
    manager = InjectionManager(default_injector_dll())
    target = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                target = find_winver()
                break
            except RuntimeError:
                time.sleep(0.1)
        if target is None:
            raise RuntimeError("winver window did not appear")

        time.sleep(0.4)
        before = screenshot(target)
        bounds = window_frame_rect(target.hwnd)
        backdrop = render_glass_backdrop(
            Image.new("RGB", (800, 500), "#315a55"),
            (bounds[2] - bounds[0], bounds[3] - bounds[1]),
            opacity=0.42,
            blur_radius=2,
            content_focus=False,
        )
        path = BackdropStore(ROOT / "build" / "painted-runtime").write(backdrop)
        manager.apply_painted(target, path)
        time.sleep(0.7)
        after = screenshot(target)

        changed = sum(
            ImageChops.difference(before, after).convert("L").point(
                lambda value: 1 if value > 12 else 0
            ).getdata()
        )
        if changed < before.width * before.height * 0.02:
            raise RuntimeError("painted backdrop did not visibly change the dialog")

        manager.remove(target)
        time.sleep(1.2)
        restored = screenshot(target)
        restored_distance = sum(
            ImageChops.difference(before, restored).convert("L").point(
                lambda value: 1 if value > 20 else 0
            ).getdata()
        )
        if restored_distance > before.width * before.height * 0.08:
            raise RuntimeError("painted backdrop did not restore the dialog background")

        print("PASS: painted backdrop changed and restored a standard Win32 dialog")
    finally:
        if target is not None:
            try:
                manager.remove(target)
            except Exception:
                pass
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

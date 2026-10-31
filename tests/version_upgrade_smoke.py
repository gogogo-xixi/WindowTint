from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_overlay_windows,
    find_target,
    open_probe,
    probe_stage,
    window_rect,
)
from overlay_smoke import make_backdrop
from windowtint.injection import InjectionManager, default_injector_dll
from windowtint.session import WindowTarget


def changed_pixels(before: Image.Image, after: Image.Image) -> int:
    difference = ImageChops.difference(before, after).convert("L")
    return sum(1 for value in difference.getdata() if value > 3)


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    try:
        window = find_target(process.pid)
        target = WindowTarget(window.hwnd, window.pid)
        bring_to_front(window.hwnd)
        time.sleep(0.5)
        before = ImageGrab.grab(bbox=window_rect(window.hwnd))
        backdrop = make_backdrop()

        for version in ("0.4.6", "0.4.7"):
            old_dll = ROOT / "bin" / f"WindowTintInjector-{version}.dll"
            if not old_dll.exists():
                raise RuntimeError(f"legacy injector is missing: {old_dll}")
            InjectionManager(old_dll).apply(target, backdrop)
            time.sleep(0.5)

        injector = InjectionManager(default_injector_dll())
        injector.apply(target, backdrop)
        mapping, probe_view = open_probe(window.pid)
        try:
            deadline = time.time() + 4
            stage = 0
            while time.time() < deadline:
                stage = probe_stage(probe_view)
                if stage >= 9:
                    break
                time.sleep(0.1)
            time.sleep(0.8)
            deadline = time.time() + 2
            stage = 0
            while time.time() < deadline:
                stage = probe_stage(probe_view)
                if stage >= 9:
                    break
                time.sleep(0.05)
            overlays = find_overlay_windows(
                window.hwnd,
                window.pid,
                "WindowTintOverlay",
            )
            after = ImageGrab.grab(bbox=window_rect(window.hwnd))
            changed = changed_pixels(before, after)

            if stage < 9:
                raise RuntimeError(
                    f"upgraded injector stopped at stage {stage}; "
                    f"overlays={overlays}"
                )
            if len(overlays) != 1:
                raise RuntimeError(
                    f"upgrade left multiple overlay layers: {overlays}"
                )
            if changed < 500:
                raise RuntimeError(
                    f"upgrade did not visibly change enough pixels: {changed}"
                )
            print(
                f"legacy=0.4.6,0.4.7 current={injector.dll_path.name} "
                f"stage={stage} overlays={len(overlays)} changed_pixels={changed}"
            )
        finally:
            injector.remove(target)
            if probe_view:
                ctypes.windll.kernel32.UnmapViewOfFile(ctypes.c_void_p(probe_view))
            if mapping:
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mapping))
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

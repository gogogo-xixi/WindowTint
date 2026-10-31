from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab, ImageStat


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.artwork import build_glass_backdrop
from windowtint.backdrop_store import BackdropStore
from windowtint.injection import default_injector_dll
from windowtint.native import list_windows


OVERLAY_CLASS_NAME = "WindowTintOverlay"


def find_target():
    deadline = time.time() + 8
    while time.time() < deadline:
        for window in list_windows():
            if window.title == "WindowTint Test Target":
                return window
        time.sleep(0.2)
    raise RuntimeError("manual target window was not found")


def capture(hwnd: int) -> Image.Image:
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))


def find_overlays(owner: int, pid: int) -> list[int]:
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        name = ctypes.create_unicode_buffer(64)
        process_id = wintypes.DWORD()
        ctypes.windll.user32.GetClassNameW(hwnd, name, len(name))
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if (
            (
                name.value == OVERLAY_CLASS_NAME
                or name.value.startswith(OVERLAY_CLASS_NAME + "_")
            )
            and process_id.value == pid
            and int(ctypes.windll.user32.GetWindowLongPtrW(hwnd, -8)) == owner
        ):
            matches.append(hwnd)
        return True

    callback = callback_type(visit)
    ctypes.windll.user32.EnumWindows(callback, 0)
    return matches


def main() -> None:
    target_python = getattr(sys, "_base_executable", sys.executable)
    target_process = subprocess.Popen(
        [target_python, str(ROOT / "tests" / "manual_target.py")]
    )
    owner = None
    try:
        target = find_target()
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetWindowPos(
            target.hwnd,
            wintypes.HWND(-1),
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0040,
        )
        user32.SetForegroundWindow(target.hwnd)
        time.sleep(0.8)
        before = capture(target.hwnd)
        before.save(ROOT / "build" / "watchdog-before.png")

        source = Image.new("RGB", (720, 420), "#3f8f89")
        backdrop = build_glass_backdrop(source, opacity=0.19, blur_radius=14)
        path = BackdropStore(ROOT / "build" / "watchdog-runtime").write(backdrop)
        owner = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests" / "injection_owner.py"),
                "--hwnd",
                hex(target.hwnd),
                "--pid",
                str(target.pid),
                "--backdrop",
                str(path),
                "--dll",
                str(default_injector_dll()),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready = owner.stdout.readline().strip() if owner.stdout else ""
        if ready != "ready":
            error = owner.stderr.read() if owner.stderr else ""
            raise RuntimeError(f"owner did not become ready: {ready} {error}")

        time.sleep(0.7)
        applied = capture(target.hwnd)
        applied.save(ROOT / "build" / "watchdog-applied.png")
        owner.terminate()
        owner.wait(timeout=5)
        time.sleep(1.4)
        restored = capture(target.hwnd)
        restored.save(ROOT / "build" / "watchdog-restored.png")
        applied_changed = sum(
            1 for value in ImageChops.difference(before, applied).convert("L").getdata()
            if value > 3
        )
        remaining_overlays = find_overlays(target.hwnd, target.pid)
        restore_crop = (10, 58, before.width - 10, before.height - 10)
        restore_delta = sum(
            ImageStat.Stat(
                ImageChops.difference(
                    before.crop(restore_crop),
                    restored.crop(restore_crop),
                )
            ).mean
        )
        if applied_changed < 500:
            raise RuntimeError(f"theme did not visibly apply: {applied_changed}")
        if remaining_overlays:
            raise RuntimeError(
                f"owner cleanup left overlays behind: {remaining_overlays}"
            )
        if restore_delta > 3:
            raise RuntimeError(f"watchdog restore left too much change: {restore_delta:.2f}")
        print(f"applied_pixels={applied_changed}")
        print(f"restore_delta={restore_delta:.2f}")
    finally:
        if owner is not None and owner.poll() is None:
            owner.kill()
        target_process.terminate()
        try:
            target_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            target_process.kill()


if __name__ == "__main__":
    main()

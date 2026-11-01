from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.single_instance import _find_window


WM_CLOSE = 0x0010


def window_pid(hwnd: int) -> int:
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(process_id),
    )
    return process_id.value


def wait_until(predicate, *, timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError(message)


def main() -> None:
    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_path
        else source_path + os.pathsep + existing_path
    )
    executable = None
    if len(sys.argv) == 3 and sys.argv[1] == "--exe":
        executable = Path(sys.argv[2]).resolve()
        if not executable.is_file():
            raise RuntimeError(f"packaged executable does not exist: {executable}")
        command = [str(executable)]
    else:
        target_python = getattr(sys, "_base_executable", sys.executable)
        command = [target_python, str(ROOT / "run.py")]
    match_process = executable is None

    if executable is not None and _find_window():
        raise RuntimeError(
            "a WindowTint window is already open; close it before the packaged smoke test"
        )

    first = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        hwnd = 0

        def first_window_ready() -> bool:
            nonlocal hwnd
            candidate = _find_window()
            if candidate and (
                not match_process or window_pid(candidate) == first.pid
            ):
                hwnd = candidate
                return bool(ctypes.windll.user32.IsWindowVisible(candidate))
            return False

        wait_until(
            first_window_ready,
            timeout=15,
            message="first instance did not create its window",
        )
        if first.poll() is not None:
            raise RuntimeError(
                f"first instance exited unexpectedly: {first.returncode}"
            )

        if not ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            raise RuntimeError("could not close the first instance to the tray")
        wait_until(
            lambda: not ctypes.windll.user32.IsWindowVisible(hwnd),
            timeout=2,
            message="first instance did not hide to the tray",
        )

        second = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if second.returncode != 0:
            raise RuntimeError(
                "second instance did not exit cleanly: "
                f"code={second.returncode} stderr={second.stderr.strip()}"
            )
        if first.poll() is not None:
            raise RuntimeError(
                f"first instance exited unexpectedly: {first.returncode}"
            )
        wait_until(
            lambda: bool(ctypes.windll.user32.IsWindowVisible(hwnd)),
            timeout=3,
            message="second launch did not reopen the existing window",
        )
        print(
            "PASS: second launch reopened the existing window and exited "
            f"(pid={first.pid}, hwnd={hwnd})"
        )
    finally:
        if executable is not None:
            subprocess.run(
                ["taskkill", "/PID", str(first.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                first.wait(timeout=3)
            except subprocess.TimeoutExpired:
                first.kill()
        elif first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=3)
            except subprocess.TimeoutExpired:
                first.kill()


if __name__ == "__main__":
    main()

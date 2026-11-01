from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from injection_smoke import (
    bring_to_front,
    find_target,
)
from overlay_smoke import make_backdrop
from windowtint.injection import (
    InjectionError,
    InjectionManager,
    RemoteProcessBridge,
    default_injector_dll,
)
from windowtint.session import WindowTarget


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
        backdrop = make_backdrop()

        old_version = "0.4.12"
        old_dll = ROOT / "bin" / f"WindowTintInjector-{old_version}.dll"
        if not old_dll.exists():
            raise RuntimeError(f"legacy injector is missing: {old_dll}")
        legacy = InjectionManager(old_dll)
        legacy.apply(target, backdrop)
        time.sleep(0.5)

        injector = InjectionManager(default_injector_dll())
        try:
            injector.apply(target, backdrop)
        except InjectionError as error:
            message = str(error)
            if old_dll.name not in message or "重启" not in message:
                raise RuntimeError(
                    f"unexpected version-conflict error: {message}"
                ) from error
        else:
            raise RuntimeError("different injector version was not rejected")

        try:
            loaded = {
                name.casefold()
                for name, _path, _base in RemoteProcessBridge()._remote_module_entries(
                    window.pid
                )
                if name.casefold().startswith("windowtintinjector")
            }
            if old_dll.name.casefold() not in loaded:
                raise RuntimeError(
                    f"legacy injector is not loaded after apply: {sorted(loaded)}"
                )
            if injector.dll_path.name.casefold() in loaded:
                raise RuntimeError(
                    "the rejected injector was still loaded into the target process"
                )
            if process.poll() is not None:
                raise RuntimeError(
                    f"target process exited after version conflict: {process.returncode}"
                )
            print(
                f"legacy={old_dll.name} current={injector.dll_path.name} "
                f"conflict=rejected modules={sorted(loaded)} process=alive"
            )
        finally:
            legacy.remove(target)
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

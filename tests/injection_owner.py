from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.injection import InjectionManager
from windowtint.native import apply_window_theme
from windowtint.session import WindowTarget
from windowtint.theme import ThemePalette


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--backdrop", type=Path, required=True)
    parser.add_argument("--dll", type=Path, required=True)
    args = parser.parse_args()

    palette = ThemePalette(
        dominant=(48, 104, 94),
        caption=(36, 75, 69),
        border=(60, 107, 99),
        accent=(207, 112, 82),
        text=(248, 246, 238),
    )
    target = WindowTarget(args.hwnd, args.pid)
    apply_window_theme(args.hwnd, palette, "round")
    InjectionManager(args.dll).apply(target, args.backdrop)
    print("ready", flush=True)
    time.sleep(60)


if __name__ == "__main__":
    main()

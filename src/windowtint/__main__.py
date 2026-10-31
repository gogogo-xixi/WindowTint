from __future__ import annotations

import sys

from .app import main
from .native import list_windows


if __name__ == "__main__":
    if "--list-windows" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for window in list_windows():
            print(f"{window.hwnd:08X}\t{window.title}")
    else:
        main()

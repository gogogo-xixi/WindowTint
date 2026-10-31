from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image


class TrayController:
    def __init__(
        self,
        *,
        icon_path: Path,
        is_paused: Callable[[], bool],
        on_open: Callable[[], None],
        on_toggle_pause: Callable[[], None],
        on_exit: Callable[[], None],
    ):
        self._is_paused = is_paused
        self._on_open = on_open
        self._on_toggle_pause = on_toggle_pause
        self._on_exit = on_exit
        self._icon_path = icon_path
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        image = Image.open(self._icon_path).convert("RGBA")
        menu = pystray.Menu(
            pystray.MenuItem("打开设置", self._open, default=True),
            pystray.MenuItem(
                lambda _item: "继续应用" if self._is_paused() else "暂停应用",
                self._toggle_pause,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("恢复并退出", self._exit),
        )
        self._icon = pystray.Icon("WindowTint", image, "窗色 · WindowTint", menu)
        self._thread = threading.Thread(
            target=self._icon.run,
            name="WindowTintTray",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
        if (
            self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=2)
        self._thread = None
        self._icon = None

    def _open(self, _icon=None, _item=None) -> None:
        self._on_open()

    def _toggle_pause(self, _icon=None, _item=None) -> None:
        self._on_toggle_pause()

    def _exit(self, _icon=None, _item=None) -> None:
        self._on_exit()

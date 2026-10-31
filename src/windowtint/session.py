from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .theme import ThemePalette


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int
    renderer: str = "injected"


@dataclass(frozen=True)
class SessionResult:
    applied: tuple[int, ...]
    failures: tuple[tuple[int, str], ...]


class ThemeSession:
    def __init__(
        self,
        *,
        apply_dwm: Callable[[WindowTarget, ThemePalette, str], None],
        restore_dwm: Callable[[WindowTarget], None],
        inject_backdrop: Callable[[WindowTarget, Path], None],
        remove_backdrop: Callable[[WindowTarget], None],
    ):
        self._apply_dwm = apply_dwm
        self._restore_dwm = restore_dwm
        self._inject_backdrop = inject_backdrop
        self._remove_backdrop = remove_backdrop
        self._active: dict[int, WindowTarget] = {}

    @property
    def active_targets(self) -> dict[int, WindowTarget]:
        return dict(self._active)

    def apply(
        self,
        targets: list[WindowTarget],
        palette: ThemePalette,
        corner: str,
        backdrop_path: Path,
    ) -> SessionResult:
        applied: list[int] = []
        failures: list[tuple[int, str]] = []

        for target in targets:
            previous = self._active.get(target.hwnd)
            if previous is not None and previous.pid != target.pid:
                self._remove_target(previous)
                self._active.pop(target.hwnd, None)

            try:
                self._apply_dwm(target, palette, corner)
                self._inject_backdrop(target, backdrop_path)
            except (OSError, RuntimeError) as error:
                self._restore_target(target)
                self._active.pop(target.hwnd, None)
                failures.append((target.hwnd, str(error)))
                continue

            self._active[target.hwnd] = target
            applied.append(target.hwnd)

        return SessionResult(tuple(applied), tuple(failures))

    def remove(self, targets: list[WindowTarget]) -> tuple[tuple[int, str], ...]:
        failures: list[tuple[int, str]] = []
        seen: set[int] = set()

        for requested in targets:
            if requested.hwnd in seen:
                continue
            seen.add(requested.hwnd)
            active = self._active.get(requested.hwnd)
            if active is None:
                continue
            error = self._remove_target(active)
            self._active.pop(active.hwnd, None)
            if error:
                failures.append((active.hwnd, error))

        return tuple(failures)

    def remove_all(self) -> tuple[tuple[int, str], ...]:
        return self.remove(list(self._active.values()))

    def _remove_target(self, target: WindowTarget) -> str:
        messages = []
        try:
            self._remove_backdrop(target)
        except (OSError, RuntimeError) as error:
            messages.append(str(error))
        try:
            self._restore_dwm(target)
        except (OSError, RuntimeError) as error:
            messages.append(str(error))
        return "; ".join(messages)

    def _restore_target(self, target: WindowTarget) -> None:
        try:
            self._restore_dwm(target)
        except (OSError, RuntimeError):
            pass

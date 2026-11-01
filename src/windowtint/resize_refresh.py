from __future__ import annotations


class ResizeStabilizer:
    def __init__(self, *, stable_seconds: float = 0.2):
        self._stable_seconds = max(0.0, stable_seconds)
        self._observations: dict[int, tuple[tuple[int, int], float]] = {}

    def observe(
        self,
        hwnd: int,
        size: tuple[int, int],
        *,
        now: float,
    ) -> tuple[int, int] | None:
        previous = self._observations.get(hwnd)
        if previous is None or previous[0] != size:
            self._observations[hwnd] = (size, now)
            return None
        if now - previous[1] < self._stable_seconds:
            return None
        self._observations.pop(hwnd, None)
        return size

    def forget(self, hwnd: int) -> None:
        self._observations.pop(hwnd, None)

from __future__ import annotations

from .session import WindowTarget


def monitor_plan(
    *,
    current: list[WindowTarget],
    active: dict[int, WindowTarget],
) -> tuple[list[WindowTarget], list[WindowTarget]]:
    current_by_handle = {target.hwnd: target for target in current}
    add = [
        target
        for hwnd, target in current_by_handle.items()
        if hwnd not in active or active[hwnd].pid != target.pid
    ]
    remove = [
        target
        for hwnd, target in active.items()
        if hwnd not in current_by_handle
        or current_by_handle[hwnd].pid != target.pid
    ]
    return add, remove

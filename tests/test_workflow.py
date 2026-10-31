import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.session import WindowTarget
from windowtint.workflow import monitor_plan


class MonitorPlanTests(unittest.TestCase):
    def test_monitor_adds_only_new_windows_and_removes_closed_ones(self):
        first = WindowTarget(101, 201)
        closed = WindowTarget(202, 302)
        newly_opened = WindowTarget(303, 403)

        add, remove = monitor_plan(
            current=[first, newly_opened],
            active={first.hwnd: first, closed.hwnd: closed},
        )

        self.assertEqual(add, [newly_opened])
        self.assertEqual(remove, [closed])

    def test_monitor_does_not_replace_a_window_when_its_process_id_changes(self):
        old = WindowTarget(101, 201)
        replacement = WindowTarget(101, 999)

        add, remove = monitor_plan(current=[replacement], active={101: old})

        self.assertEqual(add, [replacement])
        self.assertEqual(remove, [old])


if __name__ == "__main__":
    unittest.main()

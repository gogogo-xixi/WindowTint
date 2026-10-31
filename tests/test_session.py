import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.session import ThemeSession, WindowTarget
from windowtint.theme import ThemePalette


PALETTE = ThemePalette(
    dominant=(20, 30, 40),
    caption=(30, 40, 50),
    border=(60, 70, 80),
    accent=(90, 100, 110),
    text=(240, 240, 240),
)


class ThemeSessionTests(unittest.TestCase):
    def test_injection_failure_rolls_back_the_window_and_keeps_no_record(self):
        calls = []
        session = ThemeSession(
            apply_dwm=lambda target, palette, corner: calls.append(("dwm", target.hwnd)),
            restore_dwm=lambda target: calls.append(("restore", target.hwnd)),
            inject_backdrop=lambda target, path: (_ for _ in ()).throw(RuntimeError("inject failed")),
            remove_backdrop=lambda target: calls.append(("remove", target.hwnd)),
        )
        target = WindowTarget(hwnd=101, pid=202)

        result = session.apply([target], PALETTE, "round", Path("backdrop.png"))

        self.assertEqual(result.applied, ())
        self.assertEqual(result.failures, ((101, "inject failed"),))
        self.assertEqual(calls, [("dwm", 101), ("restore", 101)])
        self.assertEqual(session.active_targets, {})

    def test_remove_only_touches_windows_that_were_applied(self):
        calls = []
        session = ThemeSession(
            apply_dwm=lambda target, palette, corner: None,
            restore_dwm=lambda target: calls.append(("dwm", target.hwnd)),
            inject_backdrop=lambda target, path: None,
            remove_backdrop=lambda target: calls.append(("inject", target.hwnd)),
        )
        first = WindowTarget(hwnd=101, pid=202)
        second = WindowTarget(hwnd=303, pid=404)
        session.apply([first], PALETTE, "round", Path("backdrop.png"))

        failures = session.remove([first, second])

        self.assertEqual(failures, ())
        self.assertEqual(calls, [("inject", 101), ("dwm", 101)])
        self.assertEqual(session.active_targets, {})

    def test_reapplying_a_target_replaces_its_backdrop_without_leaking_state(self):
        calls = []
        session = ThemeSession(
            apply_dwm=lambda target, palette, corner: calls.append(("dwm", target.hwnd)),
            restore_dwm=lambda target: calls.append(("restore", target.hwnd)),
            inject_backdrop=lambda target, path: calls.append(("inject", str(path))),
            remove_backdrop=lambda target: calls.append(("remove", target.hwnd)),
        )
        target = WindowTarget(hwnd=101, pid=202)

        session.apply([target], PALETTE, "round", Path("first.png"))
        result = session.apply([target], PALETTE, "round", Path("second.png"))

        self.assertEqual(result.applied, (101,))
        self.assertEqual(calls.count(("dwm", 101)), 2)
        self.assertEqual(calls.count(("inject", "second.png")), 1)
        self.assertEqual(session.active_targets, {101: target})


if __name__ == "__main__":
    unittest.main()

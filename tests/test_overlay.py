import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.overlay import OverlayManager, _Overlay
from windowtint.session import WindowTarget


class FakeUser32:
    def __init__(self):
        self.set_window_pos = []
        self.foreground = 101

    def IsWindow(self, _hwnd):
        return True

    def IsWindowVisible(self, _hwnd):
        return True

    def IsIconic(self, _hwnd):
        return False

    def ShowWindow(self, _hwnd, _command):
        return True

    def GetForegroundWindow(self):
        return self.foreground

    def GetWindowLongPtrW(self, _hwnd, _index):
        return 0

    def GetWindow(self, _hwnd, _index):
        return 0

    def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
        self.set_window_pos.append(
            (hwnd, insert_after, x, y, width, height, flags)
        )
        return True


class OverlayMoveTests(unittest.TestCase):
    def make_manager(self, bounds):
        manager = OverlayManager.__new__(OverlayManager)
        manager.user32 = FakeUser32()
        manager._overlays = {
            101: _Overlay(
                hwnd=201,
                target=WindowTarget(101, 202),
                image=None,
                bounds=bounds,
            )
        }
        manager._window_bounds = lambda _hwnd: (17, 23, 800, 600)
        manager._is_cloaked = lambda _hwnd: False
        manager._paint = mock.Mock()
        return manager

    def test_position_change_moves_without_repainting_the_bitmap(self):
        manager = self.make_manager((0, 0, 800, 600))

        manager.sync()

        manager._paint.assert_not_called()
        self.assertEqual(manager._overlays[101].bounds, (17, 23, 800, 600))
        self.assertGreaterEqual(len(manager.user32.set_window_pos), 1)

    def test_size_change_repaints_the_bitmap(self):
        manager = self.make_manager((0, 0, 640, 480))

        manager.sync()

        manager._paint.assert_called_once()

    def test_foreground_overlay_requests_fast_sync(self):
        manager = self.make_manager((17, 23, 800, 600))

        self.assertTrue(manager.needs_fast_sync)

    def test_background_overlay_does_not_request_fast_sync(self):
        manager = self.make_manager((17, 23, 800, 600))
        manager.user32.foreground = 999

        self.assertFalse(manager.needs_fast_sync)

    def test_location_event_syncs_only_the_matching_top_level_window(self):
        manager = self.make_manager((17, 23, 800, 600))
        manager.sync_target = mock.Mock()

        manager._handle_location_event(101, 0)
        manager._handle_location_event(101, 3)
        manager._handle_location_event(999, 0)

        manager.sync_target.assert_called_once_with(101)


if __name__ == "__main__":
    unittest.main()

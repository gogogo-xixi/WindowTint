import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.single_instance import (
    ERROR_ALREADY_EXISTS,
    SW_RESTORE,
    SW_SHOW,
    SingleInstanceGuard,
    activate_existing_window,
    show_existing_window,
)


class SingleInstanceWindowTests(unittest.TestCase):
    def test_minimized_existing_window_is_restored_and_brought_to_front(self):
        user32 = mock.MagicMock()
        user32.IsWindow.return_value = True
        user32.IsIconic.return_value = True
        user32.ShowWindow.return_value = True
        user32.BringWindowToTop.return_value = True
        user32.SetForegroundWindow.return_value = True
        user32.IsWindowVisible.return_value = True

        with mock.patch(
            "windowtint.single_instance._user32",
            return_value=user32,
        ):
            activated = show_existing_window(1234)

        self.assertTrue(activated)
        user32.ShowWindow.assert_called_once_with(1234, SW_RESTORE)
        user32.BringWindowToTop.assert_called_once_with(1234)
        user32.SetForegroundWindow.assert_called_once_with(1234)

    def test_hidden_existing_window_is_shown(self):
        user32 = mock.MagicMock()
        user32.IsWindow.return_value = True
        user32.IsIconic.return_value = False
        user32.ShowWindow.return_value = True
        user32.BringWindowToTop.return_value = True
        user32.SetForegroundWindow.return_value = True
        user32.IsWindowVisible.return_value = True

        with mock.patch(
            "windowtint.single_instance._user32",
            return_value=user32,
        ):
            activated = show_existing_window(4321)

        self.assertTrue(activated)
        user32.ShowWindow.assert_called_once_with(4321, SW_SHOW)

    def test_activation_waits_for_a_starting_instance_to_create_its_window(self):
        with (
            mock.patch(
                "windowtint.single_instance._find_window",
                side_effect=[0, 9876],
            ),
            mock.patch(
                "windowtint.single_instance.show_existing_window",
                return_value=True,
            ) as show_window,
            mock.patch(
                "windowtint.single_instance.time.monotonic",
                side_effect=[0.0, 0.01, 0.02],
            ),
            mock.patch("windowtint.single_instance.time.sleep"),
        ):
            activated = activate_existing_window(timeout=0.1)

        self.assertTrue(activated)
        show_window.assert_called_once_with(9876)


class SingleInstanceGuardTests(unittest.TestCase):
    def test_existing_mutex_rejects_the_second_instance(self):
        kernel32 = mock.MagicMock()
        kernel32.CreateMutexW.return_value = 456
        guard = SingleInstanceGuard("Local\\WindowTintTest")

        with (
            mock.patch(
                "windowtint.single_instance._kernel32",
                return_value=kernel32,
            ),
            mock.patch(
                "windowtint.single_instance.ctypes.get_last_error",
                return_value=ERROR_ALREADY_EXISTS,
            ),
        ):
            acquired = guard.acquire()

        self.assertFalse(acquired)
        self.assertFalse(guard.acquired)
        kernel32.CloseHandle.assert_called_once_with(456)

    def test_new_mutex_is_held_until_release(self):
        kernel32 = mock.MagicMock()
        kernel32.CreateMutexW.return_value = 789
        guard = SingleInstanceGuard("Local\\WindowTintTest")

        with (
            mock.patch(
                "windowtint.single_instance._kernel32",
                return_value=kernel32,
            ),
            mock.patch(
                "windowtint.single_instance.ctypes.get_last_error",
                return_value=0,
            ),
        ):
            self.assertTrue(guard.acquire())
            kernel32.CloseHandle.assert_not_called()
            guard.release()

        kernel32.CloseHandle.assert_called_once_with(789)


class ApplicationStartupTests(unittest.TestCase):
    def test_main_activates_existing_window_without_creating_another_app(self):
        from windowtint.app import main

        with (
            mock.patch("windowtint.app.activate_existing_window", return_value=True),
            mock.patch("windowtint.app.WindowTintApp") as app_type,
            mock.patch("windowtint.app.SingleInstanceGuard") as guard_type,
        ):
            main()

        app_type.assert_not_called()
        guard_type.assert_not_called()

    def test_main_holds_the_guard_for_the_lifetime_of_the_first_app(self):
        from windowtint.app import main

        guard = mock.MagicMock()
        guard.acquire.return_value = True
        app = mock.MagicMock()

        with (
            mock.patch("windowtint.app.activate_existing_window", return_value=False),
            mock.patch(
                "windowtint.app.SingleInstanceGuard",
                return_value=guard,
            ),
            mock.patch("windowtint.app.WindowTintApp", return_value=app),
        ):
            main()

        guard.acquire.assert_called_once_with()
        app.mainloop.assert_called_once_with()
        guard.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

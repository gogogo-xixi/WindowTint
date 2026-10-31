import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint import native
from windowtint.app import restore_targets
from windowtint.theme import ThemePalette


class RestoreTargetTests(unittest.TestCase):
    def test_all_scope_does_not_reset_windows_that_were_never_touched(self):
        self.assertEqual(restore_targets({1, 2}, [3, 4], "all"), [1, 2])

    def test_selected_scope_can_restore_selected_and_applied_windows(self):
        self.assertEqual(restore_targets({1, 2}, [3], "selected"), [1, 2, 3])


class NativeRollbackTests(unittest.TestCase):
    def test_corner_change_failure_restores_the_window(self):
        palette = ThemePalette(
            dominant=(1, 2, 3),
            caption=(10, 20, 30),
            border=(40, 50, 60),
            accent=(70, 80, 90),
            text=(240, 240, 240),
        )
        with (
            mock.patch.object(native, "supports_window_tint", return_value=True),
            mock.patch.object(native, "_user32") as user32,
            mock.patch.object(native, "_get_attribute", return_value=None),
            mock.patch.object(native, "_set_attribute", side_effect=OSError("failed")),
            mock.patch.object(native, "restore_window_theme") as restore,
        ):
            user32.return_value.IsWindow.return_value = True

            with self.assertRaises(OSError):
                native.apply_window_theme(123, palette)

            restore.assert_called_once_with(123)

    def test_apply_does_not_recolor_the_native_caption_border_or_text(self):
        palette = ThemePalette(
            dominant=(1, 2, 3),
            caption=(10, 20, 30),
            border=(40, 50, 60),
            accent=(70, 80, 90),
            text=(240, 240, 240),
        )
        native._saved_corners.clear()
        try:
            with (
                mock.patch.object(native, "supports_window_tint", return_value=True),
                mock.patch.object(native, "_user32") as user32,
                mock.patch.object(native, "_get_attribute", return_value=native.DWMWCP_DEFAULT),
                mock.patch.object(native, "_set_attribute") as set_attribute,
            ):
                user32.return_value.IsWindow.return_value = True

                native.apply_window_theme(789, palette)

            set_attribute.assert_called_once_with(
                789,
                native.DWMWA_WINDOW_CORNER_PREFERENCE,
                native.DWMWCP_ROUND,
            )
        finally:
            native._saved_corners.clear()

    def test_restore_preserves_the_original_corner_preference(self):
        palette = ThemePalette(
            dominant=(1, 2, 3),
            caption=(10, 20, 30),
            border=(40, 50, 60),
            accent=(70, 80, 90),
            text=(240, 240, 240),
        )
        native._saved_corners.clear()
        try:
            with (
                mock.patch.object(native, "supports_window_tint", return_value=True),
                mock.patch.object(native, "_user32") as user32,
                mock.patch.object(native, "_get_attribute", return_value=native.DWMWCP_DONOTROUND),
                mock.patch.object(native, "_set_attribute") as set_attribute,
            ):
                user32.return_value.IsWindow.return_value = True

                native.apply_window_theme(456, palette)
                native.restore_window_theme(456)

            self.assertEqual(
                set_attribute.call_args_list[-1],
                mock.call(456, native.DWMWA_WINDOW_CORNER_PREFERENCE, native.DWMWCP_DONOTROUND),
            )
        finally:
            native._saved_corners.clear()


if __name__ == "__main__":
    unittest.main()

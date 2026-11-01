import sys
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.advanced import InstallationProbe, classify_installation
from windowtint.app import (
    ACCENT,
    BG,
    INK,
    LINE,
    SURFACE,
    SURFACE_ELEVATED,
    _ModernScrollbar,
    WindowTintApp,
)
from windowtint.session import WindowTarget


class ControlPanelLayoutTests(unittest.TestCase):
    def setUp(self):
        status = classify_installation(InstallationProbe())
        self.patchers = [
            mock.patch("windowtint.app.list_windows", return_value=[]),
            mock.patch("windowtint.app.detect_installation", return_value=status),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.app = WindowTintApp()
        self.app.resident_var.set(False)
        self.app.geometry("980x680+40+40")
        self.app.attributes("-topmost", True)
        self.app.update()

    def tearDown(self):
        try:
            self.app.exit_app()
        finally:
            for patcher in reversed(self.patchers):
                patcher.stop()

    def test_actions_stay_visible_when_settings_content_overflows(self):
        self.app.image_label.configure(
            text=(
                "very-long-local-image-name-for-windowtint-demo-2026-11-01-"
                "final-version-with-extra-description.jpg\n"
                "4096 × 2160 · 仅在本地处理 · additional metadata that wraps"
            )
        )
        self.app.status_label.configure(
            text=(
                "当前找到 18 个合格窗口；部分窗口正在最小化或切换状态，"
                "列表刷新可能需要更长时间。请选择当前窗口后再次应用。"
            )
        )
        self.app.update_idletasks()
        self.app.update()

        window_bottom = self.app.winfo_rooty() + self.app.winfo_height()
        for button in (self.app.apply_button, self.app.restore_button):
            self.assertLessEqual(
                button.winfo_rooty() + button.winfo_height(),
                window_bottom,
            )

    def test_controls_can_scroll_when_content_is_taller_than_viewport(self):
        self.app.image_label.configure(
            text=(
                "very-long-local-image-name-for-windowtint-demo-2026-11-01-"
                "final-version-with-extra-description.jpg\n"
                "4096 × 2160 · 仅在本地处理 · additional metadata that wraps"
            )
        )
        self.app.update_idletasks()
        self.app.update()

        top, bottom = self.app.controls_canvas.yview()
        self.assertLess(bottom - top, 1.0)

        self.app.controls_canvas.yview_moveto(1.0)
        self.app.update_idletasks()
        scrolled_top, _scrolled_bottom = self.app.controls_canvas.yview()
        self.assertGreater(scrolled_top, top)

    def test_exit_cancels_a_pending_preview_redraw(self):
        self.app._schedule_preview()
        preview_job = self.app._preview_job

        with mock.patch.object(
            self.app,
            "after_cancel",
            wraps=self.app.after_cancel,
        ) as after_cancel:
            self.app.exit_app()

        after_cancel.assert_any_call(preview_job)

    def test_stable_resize_queues_a_new_backdrop_for_that_window(self):
        target = WindowTarget(101, 202)
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")
        self.app.session._active[target.hwnd] = target
        self.app._target_frame_sizes[target.hwnd] = (700, 500)

        with (
            mock.patch(
                "windowtint.app.window_frame_size",
                return_value=(1200, 800),
            ),
            mock.patch.object(
                self.app,
                "_start_resize_refresh",
            ) as start_refresh,
        ):
            self.app._refresh_resized_targets(now=1.0)
            start_refresh.assert_not_called()
            self.app._refresh_resized_targets(now=1.5)

        start_refresh.assert_called_once_with(target, (1200, 800))

    def test_large_backdrops_are_rendered_at_a_capped_working_size(self):
        self.assertEqual(
            self.app._render_size((3440, 1440)),
            (1920, 804),
        )
        self.assertEqual(
            self.app._render_size((1182, 852)),
            (1182, 852),
        )

    def test_apply_backdrop_falls_back_when_a_window_is_not_measurable(self):
        target = WindowTarget(101, 202)
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")

        with (
            mock.patch(
                "windowtint.app.window_frame_size",
                side_effect=RuntimeError("not measurable"),
            ),
            mock.patch(
                "windowtint.app.window_client_rect",
                side_effect=RuntimeError("no client rect"),
            ),
            mock.patch.object(
                self.app,
                "_render_backdrop_file",
                return_value=Path("rendered.png"),
            ) as render_backdrop,
            mock.patch.object(self.app.injection, "apply"),
        ):
            self.app._apply_backdrop(target, Path("unused.png"))

        self.assertEqual(self.app._target_frame_sizes[target.hwnd], (640, 360))
        render_backdrop.assert_called_once_with((640, 360), None)

    def test_external_targets_do_not_queue_a_resize_refresh(self):
        target = WindowTarget(101, 202, "external")
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")
        self.app.session._active[target.hwnd] = target
        self.app._target_frame_sizes[target.hwnd] = (700, 500)

        with mock.patch.object(
            self.app,
            "_start_resize_refresh",
        ) as start_refresh:
            self.app._refresh_resized_targets(now=0.8)

        start_refresh.assert_not_called()

    def test_painted_targets_do_not_queue_a_resize_refresh(self):
        target = WindowTarget(101, 202, "painted")
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")
        self.app.session._active[target.hwnd] = target
        self.app._target_frame_sizes[target.hwnd] = (700, 500)

        with mock.patch.object(
            self.app,
            "_start_resize_refresh",
        ) as start_refresh:
            self.app._refresh_resized_targets(now=0.8)

        start_refresh.assert_not_called()

    def test_painted_backdrop_uses_a_clean_image_without_workspace_frost(self):
        target = WindowTarget(101, 202, "painted")
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")

        with (
            mock.patch(
                "windowtint.app.window_frame_size",
                return_value=(800, 500),
            ),
            mock.patch(
                "windowtint.app.window_client_rect",
                return_value=(0, 0, 800, 500),
            ),
            mock.patch(
                "windowtint.app.render_glass_backdrop",
                return_value=Image.new("RGBA", (10, 10)),
            ) as render_backdrop,
            mock.patch.object(self.app.injection, "apply_painted"),
        ):
            self.app._apply_backdrop(target, Path("unused.png"))

        kwargs = render_backdrop.call_args.kwargs
        self.assertFalse(kwargs["content_focus"])
        self.assertLess(kwargs["opacity"], 0.35)
        self.assertLess(kwargs["blur_radius"], 18)

    def test_default_scope_is_all_eligible_windows(self):
        self.assertEqual(self.app.scope_var.get(), "all")
        self.assertEqual(str(self.app.target_combo.cget("state")), "disabled")
        self.assertFalse(hasattr(self.app, "material_mode_check"))

    def test_shell_uses_the_light_surface_ladder_and_single_accent(self):
        def brightness(color: str) -> int:
            return sum(int(color[index : index + 2], 16) for index in (1, 3, 5))

        self.assertEqual(str(self.app.cget("background")), BG)
        self.assertGreater(brightness(BG), brightness(SURFACE))
        self.assertLess(brightness(BG), brightness(SURFACE_ELEVATED))
        self.assertLess(brightness(INK), brightness(SURFACE_ELEVATED))
        self.assertEqual(ACCENT, "#2563eb")
        self.assertEqual(LINE, "#dfe3e8")

    def test_corner_options_are_localized(self):
        self.assertEqual(
            self.app.corner_combo.values,
            ("圆角", "跟随系统", "直角"),
        )
        self.assertEqual(self.app.corner_var.get(), "圆角")
        self.assertEqual(self.app._corner_mode(), "round")

    def test_controls_use_a_custom_rounded_scrollbar(self):
        self.assertIsInstance(self.app.controls_scrollbar, _ModernScrollbar)
        self.app.controls_scrollbar.set(0.2, 0.6)
        self.app.update_idletasks()
        self.assertGreater(len(self.app.controls_scrollbar.find_all()), 0)

    def test_content_focus_toggle_refreshes_an_active_theme(self):
        self.app.image = Image.new("RGB", (640, 360), "#4f8f72")
        self.app._theme_armed = True

        with mock.patch.object(self.app, "apply_theme") as apply_theme:
            self.app.content_focus_var.set(False)
            self.app._content_focus_changed()
            self.app.update()

        apply_theme.assert_called_once_with(manual=False)


if __name__ == "__main__":
    unittest.main()

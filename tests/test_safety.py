import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.safety import (
    is_antcheat_module,
    is_blocked_process,
    is_explorer_file_window,
    is_supported_window_style,
    target_rejection_reason,
)


class ProcessSafetyTests(unittest.TestCase):
    def test_system_processes_are_blocked(self):
        for name in ("explorer.exe", "winlogon.exe", "lsass.exe", "dwm.exe"):
            self.assertTrue(is_blocked_process(name), name)

    def test_normal_desktop_processes_are_not_blocked(self):
        for name in ("notepad.exe", "Code.exe", "Telegram.exe"):
            self.assertFalse(is_blocked_process(name), name)

    def test_antcheat_modules_are_blocked(self):
        for name in ("EasyAntiCheat_EOS.exe", "BEService_x64.dll", "ACE-Guard Client.exe"):
            self.assertTrue(is_antcheat_module(name), name)

    def test_window_without_standard_caption_is_not_supported(self):
        self.assertFalse(is_supported_window_style(0))
        self.assertTrue(is_supported_window_style(0x00C00000))

    def test_only_explorer_file_windows_use_the_external_overlay(self):
        self.assertTrue(
            is_explorer_file_window("explorer.exe", "CabinetWClass")
        )
        self.assertTrue(
            is_explorer_file_window("Explorer.EXE", "ExploreWClass")
        )
        self.assertFalse(
            is_explorer_file_window("explorer.exe", "Progman")
        )
        self.assertFalse(
            is_explorer_file_window("notepad.exe", "CabinetWClass")
        )

    def test_eligibility_reports_the_first_safety_reason(self):
        self.assertEqual(
            target_rejection_reason(
                process_name="explorer.exe",
                style=0x00C00000,
                is_64_bit=True,
                has_antcheat_module=False,
                is_appcontainer=False,
                is_elevated=False,
            ),
            "系统或安全软件进程",
        )
        self.assertEqual(
            target_rejection_reason(
                process_name="game.exe",
                style=0x00C00000,
                is_64_bit=True,
                has_antcheat_module=True,
                is_appcontainer=False,
                is_elevated=False,
            ),
            "检测到反作弊模块",
        )
        self.assertEqual(
            target_rejection_reason(
                process_name="notepad.exe",
                style=0x00C00000,
                is_64_bit=True,
                has_antcheat_module=False,
                is_appcontainer=False,
                is_elevated=False,
            ),
            None,
        )


if __name__ == "__main__":
    unittest.main()

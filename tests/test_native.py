import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint import native
from windowtint.native import supports_window_tint


class NativeCompatibilityTests(unittest.TestCase):
    def test_windows_11_build_is_supported(self):
        self.assertTrue(supports_window_tint(22000))
        self.assertTrue(supports_window_tint(26100))

    def test_windows_10_build_is_not_claimed_as_supported(self):
        self.assertFalse(supports_window_tint(19045))


class ProcessTokenTests(unittest.TestCase):
    def test_token_flags_are_read_through_advapi32(self):
        process_handle = 101
        token_handle = 202
        kernel32 = mock.Mock()
        kernel32.OpenProcess.return_value = process_handle
        advapi32 = mock.Mock()

        def open_token(_process, _access, output):
            output._obj.value = token_handle
            return True

        def read_token(_token, _class, output, _size, _returned):
            output._obj.value = 1
            return True

        advapi32.OpenProcessToken.side_effect = open_token
        advapi32.GetTokenInformation.side_effect = read_token

        with (
            mock.patch.object(native, "_kernel32", return_value=kernel32),
            mock.patch.object(native, "_advapi32", return_value=advapi32),
        ):
            result = native._process_token_flag(123, native.TOKEN_ELEVATION, default=False)

        self.assertTrue(result)
        advapi32.OpenProcessToken.assert_called_once()
        advapi32.GetTokenInformation.assert_called_once()


if __name__ == "__main__":
    unittest.main()

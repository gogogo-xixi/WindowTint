import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.advanced import InstallationProbe, InstallationState, classify_installation
from windowtint.advanced import OFFICIAL_RELEASE_URL
from windowtint.app import WindowTintApp


class SecureUxThemeInstallationTests(unittest.TestCase):
    def test_complete_installation_requires_dll_proxy_and_ifeo_verifier(self):
        status = classify_installation(
            InstallationProbe(
                system32_dll=True,
                system32_proxy=True,
                verifier_dlls="SecureUxTheme4.dll",
                global_flag=256,
            )
        )

        self.assertEqual(status.state, InstallationState.INSTALLED)
        self.assertIn("已安装", status.title)

    def test_unconfigured_files_are_reported_as_partial_instead_of_installed(self):
        status = classify_installation(
            InstallationProbe(
                system32_dll=True,
                system32_proxy=True,
                verifier_dlls="",
                global_flag=0,
            )
        )

        self.assertEqual(status.state, InstallationState.PARTIAL)
        self.assertIn("未启用", status.detail)

    def test_detection_never_treats_a_missing_component_as_installed(self):
        status = classify_installation(InstallationProbe())

        self.assertEqual(status.state, InstallationState.MISSING)
        self.assertFalse(status.installed)


class ExternalPageConsentTests(unittest.TestCase):
    def test_declining_notice_does_not_open_a_browser(self):
        app = WindowTintApp.__new__(WindowTintApp)
        app._advanced_status = classify_installation(InstallationProbe())

        with (
            mock.patch("windowtint.app.messagebox.askyesno", return_value=False),
            mock.patch("windowtint.app.webbrowser.open_new_tab") as open_page,
        ):
            app._open_secureuxtheme_page()

        open_page.assert_not_called()

    def test_confirming_notice_opens_only_the_official_release_page(self):
        app = WindowTintApp.__new__(WindowTintApp)
        app._advanced_status = classify_installation(InstallationProbe())

        with (
            mock.patch("windowtint.app.messagebox.askyesno", return_value=True),
            mock.patch(
                "windowtint.app.webbrowser.open_new_tab",
                return_value=True,
            ) as open_page,
        ):
            app._open_secureuxtheme_page()

        open_page.assert_called_once_with(OFFICIAL_RELEASE_URL)


if __name__ == "__main__":
    unittest.main()

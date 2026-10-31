import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.injection import (
    INJECTOR_RELEASE_NAME,
    InjectionError,
    InjectionManager,
)
from windowtint.session import WindowTarget


class FakeBridge:
    def __init__(self):
        self.loads = []
        self.applies = []
        self.removes = []

    def ensure_module(self, pid, dll_path):
        self.loads.append((pid, dll_path))
        return 0x50000000

    def call_apply(self, pid, module_base, hwnd, backdrop_path):
        self.applies.append((pid, module_base, hwnd, backdrop_path))
        return 1

    def call_remove(self, pid, module_base, hwnd):
        self.removes.append((pid, module_base, hwnd))


class InjectionManagerTests(unittest.TestCase):
    def test_release_injector_version_is_consistent_across_build_and_docs(self):
        version = INJECTOR_RELEASE_NAME.removeprefix("WindowTintInjector-").removesuffix(".dll")
        root = Path(__file__).resolve().parents[1]
        files = {
            "build.ps1": root / "build.ps1",
            "manifest": root / "native" / "injector" / "WindowTintInjector.manifest",
            "README.md": root / "README.md",
            "COMPLIANCE.md": root / "COMPLIANCE.md",
            "design.md": root / "docs" / "design.md",
        }

        for label, path in files.items():
            with self.subTest(file=label):
                self.assertIn(version, path.read_text(encoding="utf-8"))

    def test_native_injector_never_writes_dwm_colors(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "injector"
            / "WindowTintInjector.cpp"
        ).read_text(encoding="utf-8")

        self.assertNotIn("DwmSetWindowAttribute", source)

    def test_native_overlay_class_is_isolated_per_injector_version(self):
        version = INJECTOR_RELEASE_NAME.removeprefix("WindowTintInjector-").removesuffix(".dll")
        class_suffix = version.replace(".", "_")
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "injector"
            / "WindowTintInjector.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn(f'L"WindowTintOverlay_{class_suffix}"', source)
        self.assertIn('L"WindowTintOverlay"', source)

    def test_same_process_is_only_loaded_once_for_multiple_windows(self):
        bridge = FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "injector.dll"
            image = Path(directory) / "backdrop.png"
            dll.write_bytes(b"dll")
            image.write_bytes(b"png")
            manager = InjectionManager(dll, bridge=bridge)
            first = WindowTarget(101, 202)
            second = WindowTarget(303, 202)

            manager.apply(first, image)
            manager.apply(second, image)

        self.assertEqual(bridge.loads, [(202, dll)])
        self.assertEqual(
            bridge.applies,
            [(202, 0x50000000, 101, image), (202, 0x50000000, 303, image)],
        )

    def test_remove_only_calls_the_remote_remove_export_for_known_windows(self):
        bridge = FakeBridge()
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "injector.dll"
            image = Path(directory) / "backdrop.png"
            dll.write_bytes(b"dll")
            image.write_bytes(b"png")
            manager = InjectionManager(dll, bridge=bridge)
            target = WindowTarget(101, 202)
            manager.apply(target, image)

            manager.remove(target)
            manager.remove(WindowTarget(999, 202))

        self.assertEqual(bridge.removes, [(202, 0x50000000, 101)])
        self.assertNotIn(101, manager.active_handles)

    def test_missing_dll_is_rejected_before_opening_a_target_process(self):
        bridge = FakeBridge()
        manager = InjectionManager(Path("missing.dll"), bridge=bridge)

        with self.assertRaisesRegex(RuntimeError, "injector DLL"):
            manager.apply(WindowTarget(101, 202), Path("backdrop.png"))

        self.assertEqual(bridge.loads, [])

    def test_remote_apply_rejection_is_reported_and_not_recorded(self):
        class RejectingBridge(FakeBridge):
            def call_apply(self, pid, module_base, hwnd, backdrop_path):
                super().call_apply(pid, module_base, hwnd, backdrop_path)
                return 0

        bridge = RejectingBridge()
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "injector.dll"
            image = Path(directory) / "backdrop.png"
            dll.write_bytes(b"dll")
            image.write_bytes(b"png")
            manager = InjectionManager(dll, bridge=bridge)

            with self.assertRaisesRegex(InjectionError, "拒绝"):
                manager.apply(WindowTarget(101, 202), image)

        self.assertNotIn(101, manager.active_handles)


if __name__ == "__main__":
    unittest.main()

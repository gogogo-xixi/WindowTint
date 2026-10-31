import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.backdrop_store import BackdropStore, default_runtime_dir


class BackdropStoreTests(unittest.TestCase):
    @mock.patch.dict(
        "os.environ",
        {
            "LOCALAPPDATA": r"C:\blocked-local-app-data",
            "TEMP": r"D:\accessible-temp",
        },
    )
    @mock.patch(
        "windowtint.backdrop_store.tempfile.gettempdir",
        return_value=r"D:\accessible-temp",
    )
    def test_default_runtime_uses_a_browser_accessible_temp_directory(
        self,
        _gettempdir,
    ):
        self.assertEqual(
            default_runtime_dir(),
            Path(r"D:\accessible-temp") / "WindowTint" / "runtime",
        )

    def test_write_uses_a_new_png_so_injected_processes_do_not_lock_an_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BackdropStore(Path(directory))
            first = Image.new("RGBA", (20, 10), (255, 0, 0, 32))
            second = Image.new("RGBA", (30, 15), (0, 255, 0, 40))

            first_path = store.write(first)
            second_path = store.write(second)

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(second_path.suffix, ".png")
            self.assertTrue(first_path.exists())
            with Image.open(second_path) as saved:
                self.assertEqual(saved.size, (30, 15))

    def test_clear_removes_generated_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = BackdropStore(root)
            generated = store.write(Image.new("RGBA", (8, 8), (0, 0, 0, 24)))
            unrelated = root / "keep.txt"
            unrelated.write_text("keep", encoding="ascii")

            store.clear()

            self.assertFalse(generated.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()

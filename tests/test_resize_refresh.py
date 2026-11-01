import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.resize_refresh import ResizeStabilizer


class ResizeStabilizerTests(unittest.TestCase):
    def test_large_resize_refreshes_only_after_the_new_size_is_stable(self):
        stabilizer = ResizeStabilizer(stable_seconds=0.2)

        self.assertIsNone(stabilizer.observe(101, (700, 500), now=0.0))
        self.assertIsNone(stabilizer.observe(101, (1200, 800), now=0.05))
        self.assertIsNone(stabilizer.observe(101, (1200, 800), now=0.20))
        self.assertEqual(
            stabilizer.observe(101, (1200, 800), now=0.25),
            (1200, 800),
        )

    def test_a_changed_size_restarts_the_stability_window(self):
        stabilizer = ResizeStabilizer(stable_seconds=0.2)

        stabilizer.observe(101, (1200, 800), now=0.0)
        self.assertIsNone(stabilizer.observe(101, (1400, 900), now=0.18))
        self.assertIsNone(stabilizer.observe(101, (1400, 900), now=0.30))
        self.assertEqual(
            stabilizer.observe(101, (1400, 900), now=0.39),
            (1400, 900),
        )

    def test_forget_stops_tracking_a_removed_window(self):
        stabilizer = ResizeStabilizer(stable_seconds=0.2)

        stabilizer.observe(101, (1200, 800), now=0.0)
        stabilizer.forget(101)

        self.assertIsNone(stabilizer.observe(101, (1200, 800), now=0.3))
        self.assertEqual(
            stabilizer.observe(101, (1200, 800), now=0.51),
            (1200, 800),
        )


if __name__ == "__main__":
    unittest.main()

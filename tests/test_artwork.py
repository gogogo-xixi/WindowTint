import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.artwork import build_glass_backdrop


class GlassBackdropTests(unittest.TestCase):
    def test_backdrop_keeps_the_full_image_and_softens_detail(self):
        source = Image.new("RGB", (320, 200))
        for x in range(320):
            for y in range(200):
                source.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))

        result = build_glass_backdrop(source, opacity=0.14, blur_radius=18)

        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.size, source.size)
        self.assertLess(
            sum(ImageStat.Stat(result.convert("RGB")).stddev),
            sum(ImageStat.Stat(source).stddev),
        )
        self.assertIsNotNone(ImageChops.difference(result.convert("RGB"), source).getbbox())

    def test_backdrop_alpha_stays_in_the_subtle_range(self):
        source = Image.new("RGB", (64, 64), "#d2543c")

        result = build_glass_backdrop(source, opacity=0.14, blur_radius=12)

        alpha = result.getchannel("A")
        self.assertGreaterEqual(alpha.getextrema()[0], 12)
        self.assertLessEqual(alpha.getextrema()[1], 48)

    def test_low_blur_preserves_image_color_and_visible_detail(self):
        source = Image.new("RGB", (200, 120), "#e84b35")
        for y in range(120):
            for x in range(100, 200):
                source.putpixel((x, y), (32, 90, 232))

        result = build_glass_backdrop(source, opacity=0.42, blur_radius=1)

        left = result.crop((20, 20, 80, 100)).convert("RGB")
        right = result.crop((120, 20, 180, 100)).convert("RGB")
        left_red = sum(pixel[0] for pixel in left.getdata()) / (left.width * left.height)
        right_blue = sum(pixel[2] for pixel in right.getdata()) / (right.width * right.height)
        self.assertGreater(left_red, 210)
        self.assertGreater(right_blue, 200)
        self.assertGreaterEqual(result.getchannel("A").getextrema()[1], 96)


if __name__ == "__main__":
    unittest.main()

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

        result = build_glass_backdrop(
            source,
            opacity=0.14,
            blur_radius=12,
            content_focus=False,
        )

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

    def test_content_focus_keeps_the_workspace_lighter_than_the_window_frame(self):
        source = Image.new("RGB", (1000, 800), "#dfc8a3")

        result = build_glass_backdrop(
            source,
            opacity=0.60,
            blur_radius=0,
            content_focus=True,
        )

        alpha = result.getchannel("A")
        frame = alpha.crop((0, 0, 1000, 16))
        title_area = alpha.crop((100, 20, 900, 80))
        workspace = alpha.crop((200, 240, 800, 650))
        frame_alpha = sum(frame.getdata()) / (frame.width * frame.height)
        title_alpha = sum(title_area.getdata()) / (
            title_area.width * title_area.height
        )
        workspace_alpha = sum(workspace.getdata()) / (
            workspace.width * workspace.height
        )

        self.assertGreater(frame_alpha, 110)
        self.assertLess(title_alpha, frame_alpha * 0.65)
        self.assertLess(workspace_alpha, title_alpha * 0.65)
        self.assertGreater(workspace_alpha, 4)

    def test_content_focus_can_be_disabled_for_a_uniform_tint(self):
        source = Image.new("RGB", (240, 160), "#7f9f96")

        result = build_glass_backdrop(
            source,
            opacity=0.30,
            blur_radius=0,
            content_focus=False,
        )

        alpha = result.getchannel("A")
        top = alpha.crop((20, 20, 220, 60))
        center = alpha.crop((60, 60, 180, 120))
        top_alpha = sum(top.getdata()) / (top.width * top.height)
        center_alpha = sum(center.getdata()) / (center.width * center.height)

        self.assertLess(abs(top_alpha - center_alpha), 4)


if __name__ == "__main__":
    unittest.main()

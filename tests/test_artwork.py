import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.artwork import (
    build_glass_backdrop,
    cover_image,
    prepare_theme_image,
    render_glass_backdrop,
)


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

    def test_content_focus_keeps_the_center_clear_and_frosts_edges(self):
        source = Image.new("RGB", (1000, 800))
        for y in range(source.height):
            for x in range(source.width):
                value = 40 if (x // 5 + y // 5) % 2 else 210
                source.putpixel((x, y), (value, value, value))

        result = build_glass_backdrop(
            source,
            opacity=0.60,
            blur_radius=18,
            content_focus=True,
        )

        edge = result.crop((0, 0, 70, 70)).convert("RGB")
        center = result.crop((250, 220, 750, 600)).convert("RGB")
        source_center = source.crop((250, 220, 750, 600))
        edge_detail = sum(ImageStat.Stat(edge).stddev)
        center_detail = sum(ImageStat.Stat(center).stddev)
        source_detail = sum(ImageStat.Stat(source_center).stddev)
        edge_brightness = sum(ImageStat.Stat(edge).mean)
        center_brightness = sum(ImageStat.Stat(center).mean)

        self.assertGreater(center_detail, source_detail * 0.7)
        self.assertLess(edge_detail, center_detail * 0.35)
        self.assertGreater(
            edge_brightness,
            center_brightness + 20,
        )

    def test_render_glass_backdrop_is_created_at_the_target_window_size(self):
        source = Image.new("RGB", (800, 500), "#b42828")

        result = render_glass_backdrop(
            source,
            (420, 700),
            opacity=0.42,
            blur_radius=18,
            content_focus=True,
        )

        self.assertEqual(result.size, (420, 700))

    def test_theme_subject_keeps_the_same_width_scale_in_tall_and_wide_windows(self):
        source = Image.new("RGB", (1600, 1000), "#20262a")
        source.paste("#f02532", (700, 0, 900, 1000))

        wide = prepare_theme_image(source, (800, 400))
        tall = prepare_theme_image(source, (400, 800))

        def red_span(image: Image.Image, y: int) -> int:
            row = [image.getpixel((x, y)) for x in range(image.width)]
            return sum(1 for red, green, _blue in row if red > 190 and green < 80)

        wide_span = red_span(wide, wide.height // 2)
        tall_span = red_span(tall, tall.height // 2)
        self.assertAlmostEqual(
            wide_span / wide.width,
            tall_span / tall.width,
            delta=0.025,
        )

    def test_workspace_frost_keeps_a_tint_instead_of_flat_milk(self):
        source = Image.new("RGB", (1000, 800), "#b42828")

        result = build_glass_backdrop(
            source,
            opacity=0.42,
            blur_radius=18,
            content_focus=True,
        )
        center = result.crop((280, 260, 720, 640)).convert("RGB")
        center_red, center_green, _center_blue = ImageStat.Stat(center).mean

        self.assertGreater(center_red - center_green, 60)

    def test_edge_glass_is_more_opaque_at_the_edges_than_the_center(self):
        source = Image.new("RGB", (1000, 800), "#252a31")

        result = build_glass_backdrop(
            source,
            opacity=0.52,
            blur_radius=18,
            content_focus=True,
        )

        edge_alpha = ImageStat.Stat(
            result.getchannel("A").crop((0, 0, 100, 100))
        ).mean[0]
        center_alpha = ImageStat.Stat(
            result.getchannel("A").crop((400, 320, 600, 480))
        ).mean[0]

        self.assertGreater(edge_alpha, center_alpha + 35)

    def test_edge_glass_keeps_the_center_nearly_clear(self):
        source = Image.new("RGB", (1000, 800), "#252a31")

        result = build_glass_backdrop(
            source,
            opacity=0.52,
            blur_radius=18,
            content_focus=True,
        )

        center_alpha = ImageStat.Stat(
            result.getchannel("A").crop((400, 320, 600, 480))
        ).mean[0]
        self.assertLess(center_alpha, 120)

    def test_edge_glass_is_white_instead_of_a_dark_tinted_image(self):
        source = Image.new("RGB", (1000, 800), "#252a31")

        result = build_glass_backdrop(
            source,
            opacity=0.52,
            blur_radius=18,
            content_focus=True,
        )

        edge = result.crop((0, 0, 90, 90)).convert("RGB")
        edge_mean = ImageStat.Stat(edge).mean

        self.assertGreater(min(edge_mean), 190)

    def test_glass_fades_down_from_the_top_without_a_horizontal_seam(self):
        source = Image.new("RGB", (1000, 800), "#252a31")

        result = build_glass_backdrop(
            source,
            opacity=0.52,
            blur_radius=18,
            content_focus=True,
        )

        alpha = result.getchannel("A")
        top_alpha = alpha.getpixel((500, 20))
        lower_alpha = alpha.getpixel((500, 720))
        vertical = [alpha.getpixel((500, y)) for y in range(alpha.height)]
        max_vertical_step = max(
            abs(current - previous)
            for previous, current in zip(vertical, vertical[1:])
        )
        left = alpha.getpixel((10, 300))
        right = alpha.getpixel((990, 300))

        self.assertGreater(top_alpha, lower_alpha + 40)
        self.assertLessEqual(max_vertical_step, 2)
        self.assertLessEqual(abs(left - right), 1)

    def test_cover_image_crops_instead_of_stretching_a_theme(self):
        source = Image.new("RGB", (400, 200), "#da3b32")
        source.paste("#245fca", (200, 0, 400, 200))

        result = cover_image(source, (200, 200))

        left = result.getpixel((10, 100))
        right = result.getpixel((190, 100))
        self.assertGreater(left[0], left[2])
        self.assertGreater(right[2], right[0])

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

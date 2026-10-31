import sys
import unittest
from pathlib import Path

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from windowtint.theme import contrast_ratio, extract_palette, to_colorref


class PaletteTests(unittest.TestCase):
    def test_light_image_gets_dark_text_and_readable_contrast(self):
        palette = extract_palette(Image.new("RGB", (32, 32), "#f2eee4"))

        self.assertFalse(palette.is_dark)
        self.assertGreaterEqual(contrast_ratio(palette.caption, palette.text), 4.5)

    def test_dark_image_gets_light_text_and_readable_contrast(self):
        palette = extract_palette(Image.new("RGB", (32, 32), "#101816"))

        self.assertTrue(palette.is_dark)
        self.assertGreaterEqual(contrast_ratio(palette.caption, palette.text), 4.5)

    def test_vivid_color_wins_accent(self):
        image = Image.new("RGB", (40, 40), "#d8d5cc")
        for x in range(10):
            for y in range(40):
                image.putpixel((x, y), (224, 82, 48))

        palette = extract_palette(image)

        self.assertGreater(palette.accent[0], palette.accent[1])
        self.assertGreater(palette.accent[0], palette.accent[2])

    def test_border_uses_the_image_accent_instead_of_a_neutral_mix(self):
        image = Image.new("RGB", (80, 60), "#d8d5cc")
        for x in range(12):
            for y in range(60):
                image.putpixel((x, y), (224, 82, 48))

        palette = extract_palette(image)

        self.assertGreater(palette.border[0], palette.border[1] + 18)
        self.assertGreater(palette.border[0], palette.border[2] + 18)

    def test_colorref_uses_windows_byte_order(self):
        self.assertEqual(to_colorref((18, 52, 86)), 0x563412)


if __name__ == "__main__":
    unittest.main()

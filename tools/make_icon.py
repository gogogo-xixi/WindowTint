from pathlib import Path

from PIL import Image, ImageDraw


root = Path(__file__).resolve().parents[1]
assets = root / "assets"
assets.mkdir(exist_ok=True)

image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((12, 12, 244, 244), radius=48, fill="#16221f")
draw.rounded_rectangle((42, 58, 214, 198), radius=18, fill="#f8f6ee")
draw.rounded_rectangle((42, 58, 214, 104), radius=18, fill="#0b6b57")
draw.rectangle((42, 88, 214, 104), fill="#0b6b57")
draw.rounded_rectangle((60, 70, 75, 85), radius=4, fill="#f8f6ee")
draw.rounded_rectangle((84, 70, 99, 85), radius=4, fill="#df5b3f")
draw.rounded_rectangle((108, 70, 123, 85), radius=4, fill="#df5b3f")
draw.rounded_rectangle((60, 124, 196, 136), radius=5, fill="#cbd7d2")
draw.rounded_rectangle((60, 150, 166, 162), radius=5, fill="#dfe6e3")
draw.rounded_rectangle((60, 176, 182, 188), radius=5, fill="#dfe6e3")

image.save(
    assets / "windowtint.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
image.save(assets / "windowtint.png")

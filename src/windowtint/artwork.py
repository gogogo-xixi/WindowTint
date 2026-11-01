from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter


def _light_sheen(size: tuple[int, int]) -> Image.Image:
    width, height = size
    gradient = Image.linear_gradient("L").resize((width, height), Image.Resampling.BILINEAR)
    gradient = gradient.rotate(24, expand=False, resample=Image.Resampling.BICUBIC)
    blue_white = Image.new("RGBA", size, (218, 240, 237, 0))
    alpha = gradient.point(lambda value: 2 + int(value * 0.025))
    blue_white.putalpha(alpha)
    return blue_white.filter(ImageFilter.GaussianBlur(max(4, min(width, height) // 36)))


def _content_focus_mask(size: tuple[int, int]) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 255)
    draw = ImageDraw.Draw(mask)
    frame_inset = max(7, min(width, height) // 70)
    radius = max(8, min(width, height) // 20)
    draw.rounded_rectangle(
        (
            frame_inset,
            frame_inset,
            width - frame_inset - 1,
            height - frame_inset - 1,
        ),
        radius=radius,
        fill=110,
    )
    draw.rounded_rectangle(
        (
            round(width * 0.055),
            round(height * 0.135),
            round(width * 0.945),
            round(height * 0.955),
        ),
        radius=max(8, round(min(width, height) * 0.045)),
        fill=48,
    )
    draw.rounded_rectangle(
        (
            round(width * 0.11),
            round(height * 0.18),
            round(width * 0.89),
            round(height * 0.91),
        ),
        radius=max(8, round(min(width, height) * 0.055)),
        fill=30,
    )
    return mask.filter(
        ImageFilter.GaussianBlur(max(5, round(min(width, height) * 0.014)))
    )


def build_glass_backdrop(
    source: Image.Image,
    *,
    opacity: float = 0.14,
    blur_radius: int = 18,
    content_focus: bool = False,
) -> Image.Image:
    """Create the soft image used by the click-through glass overlay."""
    rgba = source.convert("RGBA")
    size = rgba.size
    softened = rgba.convert("RGB")
    if blur_radius > 0:
        softened = softened.filter(ImageFilter.GaussianBlur(blur_radius))
    softened = ImageEnhance.Contrast(softened).enhance(0.94)
    softened = ImageEnhance.Color(softened).enhance(1.04)

    sheen = _light_sheen(size)
    softened = Image.alpha_composite(softened.convert("RGBA"), sheen)
    softened = softened.convert("RGB")

    capped_opacity = max(0.08, min(0.6, opacity))
    max_alpha = round(capped_opacity * 255)
    vertical = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    alpha = vertical.point(
        lambda value: max(10, round(max_alpha * (0.9 + (255 - value) / 255 * 0.1)))
    )
    if content_focus:
        alpha = ImageChops.multiply(alpha, _content_focus_mask(size))
    softened.putalpha(alpha)
    return softened

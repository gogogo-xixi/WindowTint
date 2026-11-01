from __future__ import annotations

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def cover_image(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale an image to fill a window without changing its aspect ratio."""
    return ImageOps.fit(
        source,
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def prepare_theme_image(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit a theme by width and extend short sides with a soft blur."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("Theme size must be positive.")

    rgba = source.convert("RGBA")
    scaled_width = width
    scaled_height = max(
        1,
        round(rgba.height * scaled_width / rgba.width),
    )
    scaled = rgba.resize(
        (scaled_width, scaled_height),
        Image.Resampling.LANCZOS,
    )

    if scaled_height >= height:
        top = (scaled_height - height) // 2
        return scaled.crop((0, top, width, top + height)).convert("RGB")

    background = cover_image(rgba, size).filter(
        ImageFilter.GaussianBlur(max(24, min(width, height) // 10))
    )
    centered = Image.new("RGBA", size, (0, 0, 0, 0))
    top = (height - scaled_height) // 2
    centered.alpha_composite(scaled, (0, top))

    mask = Image.new("L", size, 0)
    inset = max(10, min(width, height) // 28)
    ImageDraw.Draw(mask).rectangle(
        (
            0,
            top + inset,
            width - 1,
            top + scaled_height - inset - 1,
        ),
        fill=255,
    )
    mask = mask.filter(
        ImageFilter.GaussianBlur(max(18, min(width, height) // 18))
    )
    return Image.composite(centered, background, mask).convert("RGB")


def _vertical_frost_mask(size: tuple[int, int]) -> Image.Image:
    """Fade the white glass downward without creating horizontal seams."""
    width, height = size
    progress = Image.linear_gradient("L").resize(
        size,
        Image.Resampling.BILINEAR,
    )
    fade_end = max(1, round(height * 0.82))

    def fade(value: int) -> int:
        distance = min(1.0, (value / 255) * height / fade_end)
        eased = distance * distance * (3.0 - 2.0 * distance)
        return round(255 * (1.0 - eased))

    mask = progress.point(fade)
    return mask.filter(ImageFilter.GaussianBlur(max(4, round(height * 0.01))))


def build_glass_backdrop(
    source: Image.Image,
    *,
    opacity: float = 0.14,
    blur_radius: int = 18,
    content_focus: bool = False,
    focus_rect: tuple[float, float, float, float] | None = None,
) -> Image.Image:
    """Create the soft image used by the click-through glass overlay."""
    rgba = source.convert("RGBA")
    size = rgba.size
    clear = rgba.convert("RGB")
    if content_focus:
        workspace_mask = _vertical_frost_mask(size)
        clouded = clear
        if blur_radius > 0:
            clouded = clouded.filter(ImageFilter.GaussianBlur(blur_radius))
        frost = Image.blend(
            clouded,
            Image.new("RGB", size, (255, 255, 255)),
            0.92,
        )
        softened = Image.composite(frost, clear, workspace_mask)
    else:
        workspace_mask = None
        softened = clear
        if blur_radius > 0:
            softened = softened.filter(ImageFilter.GaussianBlur(blur_radius))
    softened = ImageEnhance.Contrast(softened).enhance(1.0)
    softened = ImageEnhance.Color(softened).enhance(1.03)
    softened = ImageEnhance.Brightness(softened).enhance(1.0)

    capped_opacity = max(0.08, min(0.6, opacity))
    max_alpha = round(capped_opacity * 255)
    if workspace_mask is not None:
        outside_alpha = max(8, round(max_alpha * 0.24))
        workspace_alpha = min(176, round(max_alpha * 0.88))
        alpha = Image.composite(
            Image.new("L", size, workspace_alpha),
            Image.new("L", size, outside_alpha),
            workspace_mask,
        )
    else:
        alpha = Image.new("L", size, max_alpha)
    softened.putalpha(alpha)
    return softened


def render_glass_backdrop(
    source: Image.Image,
    size: tuple[int, int],
    *,
    opacity: float = 0.14,
    blur_radius: int = 18,
    content_focus: bool = False,
    focus_rect: tuple[float, float, float, float] | None = None,
) -> Image.Image:
    """Render the complete glass layer at the target window's real size."""
    prepared = cover_image(source, size)
    return build_glass_backdrop(
        prepared,
        opacity=opacity,
        blur_radius=blur_radius,
        content_focus=content_focus,
        focus_rect=focus_rect,
    )

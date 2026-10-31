from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageStat


Color = tuple[int, int, int]


@dataclass(frozen=True)
class ThemePalette:
    dominant: Color
    caption: Color
    border: Color
    accent: Color
    text: Color

    @property
    def is_dark(self) -> bool:
        return relative_luminance(self.caption) < 0.42

    def as_hex(self) -> dict[str, str]:
        return {
            "dominant": to_hex(self.dominant),
            "caption": to_hex(self.caption),
            "border": to_hex(self.border),
            "accent": to_hex(self.accent),
            "text": to_hex(self.text),
        }


def _clamp_channel(value: float) -> int:
    return max(0, min(255, round(value)))


def _clamp(color: Iterable[float]) -> Color:
    red, green, blue = color
    return (
        _clamp_channel(red),
        _clamp_channel(green),
        _clamp_channel(blue),
    )


def mix(first: Color, second: Color, amount: float) -> Color:
    amount = max(0.0, min(1.0, amount))
    return _clamp(
        first[index] * (1.0 - amount) + second[index] * amount
        for index in range(3)
    )


def relative_luminance(color: Color) -> float:
    channels = []
    for channel in color:
        value = channel / 255.0
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: Color, second: Color) -> float:
    lighter = max(relative_luminance(first), relative_luminance(second))
    darker = min(relative_luminance(first), relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _saturation(color: Color) -> float:
    high = max(color)
    low = min(color)
    return 0.0 if high == 0 else (high - low) / high


def _quantized_colors(image: Image.Image) -> list[tuple[Color, int]]:
    rgba = image.convert("RGBA").resize((96, 96), Image.Resampling.LANCZOS)
    buckets: dict[tuple[int, int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])

    for red, green, blue, alpha in rgba.getdata():
        if alpha < 24:
            continue
        blend = alpha / 255.0
        red = round(red * blend + 246 * (1.0 - blend))
        green = round(green * blend + 245 * (1.0 - blend))
        blue = round(blue * blend + 241 * (1.0 - blend))
        key = (red >> 4, green >> 4, blue >> 4)
        bucket = buckets[key]
        bucket[0] += red
        bucket[1] += green
        bucket[2] += blue
        bucket[3] += 1

    colors = []
    for red_sum, green_sum, blue_sum, count in buckets.values():
        if not count:
            continue
        colors.append(
            (
                (
                    round(red_sum / count),
                    round(green_sum / count),
                    round(blue_sum / count),
                ),
                round(count),
            )
        )
    return sorted(colors, key=lambda item: item[1], reverse=True)


def _average_color(image: Image.Image) -> Color:
    statistics = ImageStat.Stat(image.convert("RGB"))
    return _clamp(statistics.mean)


def _edge_colors(image: Image.Image) -> list[Color]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    band_x = max(2, width // 12)
    band_y = max(2, height // 12)
    return [
        _average_color(rgb.crop((0, 0, width, band_y))),
        _average_color(rgb.crop((0, height - band_y, width, height))),
        _average_color(rgb.crop((0, 0, band_x, height))),
        _average_color(rgb.crop((width - band_x, 0, width, height))),
    ]


def _most_vivid(colors: Iterable[Color]) -> Color | None:
    candidates = [color for color in colors if _saturation(color) >= 0.12]
    return max(candidates, key=_saturation) if candidates else None


def _readable_text(caption: Color) -> Color:
    dark = (20, 25, 24)
    light = (248, 246, 238)
    return dark if contrast_ratio(caption, dark) >= contrast_ratio(caption, light) else light


def _readable_caption(caption: Color, text: Color) -> Color:
    result = caption
    away_from = (255, 255, 255) if relative_luminance(text) < 0.5 else (0, 0, 0)
    for _ in range(24):
        if contrast_ratio(result, text) >= 4.5:
            break
        result = mix(result, away_from, 0.05)
    return result


def extract_palette(image: Image.Image) -> ThemePalette:
    colors = _quantized_colors(image)
    if not colors:
        colors = [((232, 230, 223), 1)]

    dominant = colors[0][0]

    vivid = [
        (color, count)
        for color, count in colors[:24]
        if _saturation(color) >= 0.2 and 24 < sum(color) / 3 < 232
    ]
    if vivid:
        accent = max(vivid, key=lambda item: _saturation(item[0]) * (0.55 + min(item[1], 1000) / 1800))[0]
    else:
        accent = mix(dominant, (224, 82, 48), 0.58)

    edge_vivid = _most_vivid(_edge_colors(image))
    if edge_vivid is not None:
        accent = mix(accent, edge_vivid, 0.45)

    caption_base = mix(dominant, accent, 0.32)
    text = _readable_text(caption_base)
    caption = _readable_caption(caption_base, text)
    border = mix(accent, caption, 0.22)
    if _saturation(border) < 0.16:
        border = mix(border, accent, 0.55)

    return ThemePalette(
        dominant=dominant,
        caption=caption,
        border=border,
        accent=accent,
        text=text,
    )


def to_hex(color: Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def to_colorref(color: Color) -> int:
    red, green, blue = color
    return red | (green << 8) | (blue << 16)

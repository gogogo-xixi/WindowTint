from __future__ import annotations

import atexit
import ctypes
import os
import sys
import time
import tkinter as tk
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .artwork import render_glass_backdrop
from .advanced import (
    OFFICIAL_RELEASE_URL,
    PERSONALIZATION_URI,
    InstallationState,
    InstallationStatus,
    detect_installation,
)
from .backdrop_store import BackdropStore
from .injection import InjectionManager
from .native import (
    WindowInfo,
    apply_window_theme,
    list_windows,
    restore_window_theme,
    supports_window_tint,
    window_client_rect,
    window_frame_size,
)
from .overlay import OverlayManager
from .resize_refresh import ResizeStabilizer
from .session import SessionResult, ThemeSession, WindowTarget
from .single_instance import (
    SingleInstanceGuard,
    activate_existing_window,
)
from .theme import ThemePalette, extract_palette, mix, to_hex
from .tray import TrayController
from .workflow import monitor_plan


BG = "#f4f6f8"
SURFACE = "#eef2f6"
SURFACE_ELEVATED = "#ffffff"
SURFACE_CARD = "#f8fafc"
INK = "#101114"
BODY = "#30343a"
MUTED = "#6b7280"
LINE = "#dfe3e8"
ACCENT = "#2563eb"
ACCENT_STRONG = "#1d4ed8"
ACCENT_SOFT = "#eaf1ff"
WARNING = "#b45309"
WARNING_SOFT = "#fff7ed"

CORNER_MODE_BY_LABEL = {
    "圆角": "round",
    "跟随系统": "system",
    "直角": "square",
}

DEFAULT_PALETTE = ThemePalette(
    dominant=(205, 211, 207),
    caption=(29, 43, 39),
    border=(86, 104, 98),
    accent=(213, 102, 72),
    text=(248, 246, 238),
)


def restore_targets(
    applied_handles: set[int],
    selected_handles: list[int],
    scope: str,
) -> list[int]:
    if scope == "all":
        return sorted(applied_handles)
    return sorted(applied_handles | set(selected_handles))


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def _asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[2]
    return base / "assets" / name


def _rounded_shape(
    canvas: tk.Canvas,
    box: tuple[float, float, float, float],
    radius: float,
    *,
    fill: str,
    outline: str = "",
    width: int = 1,
) -> int:
    x1, y1, x2, y2 = box
    pixel_width = max(1, round(x2 - x1))
    pixel_height = max(1, round(y2 - y1))
    scale = 4
    image = Image.new(
        "RGBA",
        (pixel_width * scale, pixel_height * scale),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1),
        radius=max(0, min(radius, pixel_width / 2, pixel_height / 2) * scale),
        fill=fill or None,
        outline=outline or None,
        width=max(1, round(width * scale)),
    )
    image = image.resize(
        (pixel_width, pixel_height),
        Image.Resampling.LANCZOS,
    )
    photo = ImageTk.PhotoImage(image, master=canvas)
    photos = getattr(canvas, "_rounded_photos", None)
    if photos is None:
        photos = []
        setattr(canvas, "_rounded_photos", photos)
    photos.append(photo)
    return canvas.create_image(
        round(x1),
        round(y1),
        image=photo,
        anchor="nw",
    )


def _clear_canvas(canvas: tk.Canvas) -> None:
    canvas.delete("all")
    setattr(canvas, "_rounded_photos", [])


class _CanvasButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        primary: bool,
        height: int = 38,
        width: int = 120,
    ) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            height=height,
            width=width,
            background=background,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._primary = primary
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._focused = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.bind("<Return>", self._invoke_event)
        self.bind("<space>", self._invoke_event)
        self.after_idle(self._draw)

    def set_state(self, state: str, *, cursor: str | None = None) -> None:
        self._state = state
        if cursor is not None:
            super().configure(cursor=cursor)
        self._draw()

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if self._state == "normal":
            self._pressed = True
            self.focus_set()
            self._draw()

    def _on_release(self, event=None) -> None:
        if self._state != "normal" or not self._pressed:
            return
        self._pressed = False
        self._draw()
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if 0 <= event.x <= width and 0 <= event.y <= height:
            self._command()

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _invoke_event(self, _event=None) -> str:
        if self._state == "normal":
            self._command()
        return "break"

    def _draw(self) -> None:
        _clear_canvas(self)
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        if self._state != "normal":
            fill = "#eef1f4" if self._primary else SURFACE_CARD
            outline = "#e4e8ed"
            foreground = "#a3a9b2"
        elif self._primary:
            fill = "#2a2d33" if self._pressed else ("#25282e" if self._hover else INK)
            outline = fill
            foreground = "#ffffff"
        else:
            fill = SURFACE_CARD if self._pressed else ("#f3f6f9" if self._hover else SURFACE_ELEVATED)
            outline = LINE
            foreground = INK
        _rounded_shape(
            self,
            (1, 1, width - 1, height - 1),
            8,
            fill=fill,
            outline=outline,
            width=1,
        )
        if self._focused and self._state == "normal":
            _rounded_shape(
                self,
                (3, 3, width - 3, height - 3),
                6,
                fill="",
                outline=ACCENT,
                width=1,
            )
        self.create_text(
            width / 2,
            height / 2,
            text=self._text,
            fill=foreground,
            font=("Microsoft YaHei UI", 10, "bold"),
        )


class _CustomSelect(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        variable: tk.StringVar,
        values: tuple[str, ...] = (),
        state: str = "readonly",
        height: int = 36,
        width: int = 180,
    ) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            height=height,
            width=width,
            background=background,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self.variable = variable
        self.values = tuple(values)
        self.state = state
        self._focused = False
        self._font = tkfont.Font(family="Microsoft YaHei UI", size=10)
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._open_menu)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.bind("<space>", self._open_menu)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.after_idle(self._draw)

    def cget(self, key: str):
        if key == "state":
            return self.state
        return super().cget(key)

    def set_values(self, values: tuple[str, ...] | list[str]) -> None:
        self.values = tuple(values)
        self._draw()

    def set_state(self, state: str, *, cursor: str | None = None) -> None:
        self.state = state
        if cursor is not None:
            super().configure(cursor=cursor)
        self._draw()

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _open_menu(self, _event=None) -> str:
        if self.state == "disabled" or not self.values:
            return "break"
        self.focus_set()
        menu = tk.Menu(
            self,
            tearoff=False,
            background=SURFACE_ELEVATED,
            foreground=INK,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT_STRONG,
            borderwidth=0,
            font=("Microsoft YaHei UI", 10),
        )
        for value in self.values:
            menu.add_command(
                label=value,
                command=lambda selected=value: self.variable.set(selected),
            )
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()
        return "break"

    def _draw(self) -> None:
        _clear_canvas(self)
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        disabled = self.state == "disabled"
        fill = "#f1f3f5" if disabled else SURFACE_CARD
        foreground = "#9aa1aa" if disabled else INK
        outline = ACCENT if self._focused and not disabled else LINE
        _rounded_shape(
            self,
            (1, 1, width - 1, height - 1),
            7,
            fill=fill,
            outline=outline,
            width=1,
        )
        max_text_width = max(20, width - 42)
        text = self.variable.get() or ("请选择" if self.values else "暂无可用窗口")
        while text and self._font.measure(text) > max_text_width:
            text = text[:-1]
        if text != (self.variable.get() or ("请选择" if self.values else "暂无可用窗口")):
            text = text[:-1] + "…" if len(text) > 1 else "…"
        self.create_text(
            12,
            height / 2,
            text=text,
            fill=foreground,
            anchor="w",
            font=self._font,
        )
        self.create_text(
            width - 17,
            height / 2 - 1,
            text="⌄",
            fill="#9aa1aa" if disabled else MUTED,
            font=("Microsoft YaHei UI", 11),
        )


class _ToggleSwitch(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        variable: tk.BooleanVar,
        command,
        height: int = 34,
    ) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            height=height,
            background=background,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self._text = text
        self.variable = variable
        self._command = command
        self._focused = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.after_idle(self._draw)

    def _invoke(self, _event=None) -> str:
        self.focus_set()
        self.variable.set(not self.variable.get())
        self._command()
        return "break"

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _draw(self) -> None:
        _clear_canvas(self)
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        track_left = 8
        track_right = 48
        track_top = (height - 20) / 2
        track_bottom = track_top + 20
        selected = self.variable.get()
        track = ACCENT if selected else "#cbd2da"
        _rounded_shape(
            self,
            (track_left, track_top, track_right, track_bottom),
            10,
            fill=track,
            outline=ACCENT if selected else "#c2cad3",
            width=1,
        )
        knob_x = track_right - 10 if selected else track_left + 10
        knob_outline = ACCENT if self._focused else LINE
        _rounded_shape(
            self,
            (
                knob_x - 7,
                height / 2 - 7,
                knob_x + 7,
                height / 2 + 7,
            ),
            7,
            fill="#ffffff",
            outline=knob_outline,
            width=2 if self._focused else 1,
        )
        self.create_text(
            62,
            height / 2,
            text=self._text,
            fill=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 10),
        )


class _RangeSlider(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        variable: tk.DoubleVar,
        from_: float,
        to: float,
        command,
        height: int = 34,
    ) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            height=height,
            background=background,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.variable = variable
        self.from_ = float(from_)
        self.to = float(to)
        self._command = command
        self._hover = False
        self._dragging = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._set_from_event)
        self.bind("<B1-Motion>", self._set_from_event)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.after_idle(self._draw)

    def _set_from_event(self, event) -> str:
        self._dragging = True
        width = max(1, self.winfo_width())
        ratio = min(1.0, max(0.0, (event.x - 9) / max(1, width - 18)))
        value = self.from_ + ratio * (self.to - self.from_)
        self.variable.set(value)
        self._command(str(value))
        return "break"

    def _release(self, _event=None) -> str:
        self._dragging = False
        self._draw()
        return "break"

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._dragging = False
        self._draw()

    def _draw(self) -> None:
        _clear_canvas(self)
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        left = 9
        right = width - 9
        center_y = height / 2
        ratio = (float(self.variable.get()) - self.from_) / max(
            0.001,
            self.to - self.from_,
        )
        ratio = min(1.0, max(0.0, ratio))
        knob_x = left + (right - left) * ratio
        _rounded_shape(
            self,
            (left, center_y - 2, right, center_y + 2),
            2,
            fill="#dfe4ea",
        )
        if knob_x > left + 2:
            _rounded_shape(
                self,
                (left, center_y - 2, knob_x, center_y + 2),
                2,
                fill=ACCENT,
            )
        knob_radius = 8.5 if self._hover or self._dragging else 8
        _rounded_shape(
            self,
            (
                knob_x - knob_radius,
                center_y - knob_radius,
                knob_x + knob_radius,
                center_y + knob_radius,
            ),
            knob_radius,
            fill="#ffffff",
            outline=ACCENT,
            width=2,
        )


class _SegmentedChoice(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        variable: tk.StringVar,
        options: tuple[tuple[str, str], ...],
        command,
        height: int = 38,
        width: int = 320,
    ) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            height=height,
            width=width,
            background=background,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self.variable = variable
        self.options = options
        self._command = command
        self._focused = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._select_from_event)
        self.bind("<Left>", lambda _event: self._step(-1))
        self.bind("<Right>", lambda _event: self._step(1))
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_blur)
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.after_idle(self._draw)

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _set_value(self, value: str) -> None:
        self.variable.set(value)
        self._command(value)

    def _select_from_event(self, event) -> str:
        self.focus_set()
        width = max(1, self.winfo_width())
        index = min(
            len(self.options) - 1,
            max(0, int(event.x / (width / len(self.options)))),
        )
        self._set_value(self.options[index][0])
        return "break"

    def _step(self, direction: int) -> str:
        values = [value for value, _label in self.options]
        try:
            index = values.index(self.variable.get())
        except ValueError:
            index = 0
        index = min(len(values) - 1, max(0, index + direction))
        self._set_value(values[index])
        return "break"

    def _draw(self) -> None:
        _clear_canvas(self)
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        _rounded_shape(
            self,
            (1, 1, width - 1, height - 1),
            8,
            fill=SURFACE_CARD,
            outline=ACCENT if self._focused else LINE,
            width=1,
        )
        segment_width = (width - 6) / len(self.options)
        selected = self.variable.get()
        for index, (value, label) in enumerate(self.options):
            x1 = 3 + index * segment_width
            x2 = x1 + segment_width
            if value == selected:
                _rounded_shape(
                    self,
                    (x1 + 1, 4, x2 - 1, height - 4),
                    6,
                    fill=SURFACE_ELEVATED,
                    outline=LINE,
                    width=1,
                )
            self.create_text(
                (x1 + x2) / 2,
                height / 2,
                text=label,
                fill=INK if value == selected else MUTED,
                font=("Microsoft YaHei UI", 10, "bold" if value == selected else "normal"),
            )


class _ModernScrollbar(tk.Canvas):
    """Clash Verge style: transparent 8 px track with a rounded gray thumb."""

    def __init__(self, parent: tk.Misc, *, target: tk.Canvas) -> None:
        background = str(parent.cget("background"))
        super().__init__(
            parent,
            width=8,
            background=background,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.target = target
        self._first = 0.0
        self._last = 1.0
        self._dragging = False
        self._drag_offset = 0.0
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first, last) -> None:
        try:
            first_value = max(0.0, min(1.0, float(first)))
            last_value = max(first_value, min(1.0, float(last)))
        except (TypeError, ValueError):
            return
        self._first = first_value
        self._last = last_value
        self._draw()

    def _geometry(self) -> tuple[float, float, float]:
        height = max(1, self.winfo_height())
        track_top = 4.0
        track_bottom = max(track_top + 1, height - 4)
        track_height = track_bottom - track_top
        visible = max(0.06, self._last - self._first)
        thumb_height = max(24.0, track_height * visible)
        travel = max(0.0, track_height - thumb_height)
        scrollable = max(0.0, 1.0 - visible)
        ratio = self._first / scrollable if scrollable > 0 else 0.0
        thumb_top = track_top + travel * min(1.0, max(0.0, ratio))
        return track_top, track_bottom, thumb_top

    def _thumb_contains(self, y: float) -> bool:
        _track_top, _track_bottom, thumb_top = self._geometry()
        visible = max(0.06, self._last - self._first)
        track_height = max(1.0, self.winfo_height() - 8)
        thumb_height = max(24.0, track_height * visible)
        return thumb_top <= y <= thumb_top + thumb_height

    def _scroll_to(self, y: float, *, centered: bool) -> None:
        height = max(1, self.winfo_height())
        _track_top, _track_bottom, thumb_top = self._geometry()
        visible = max(0.06, self._last - self._first)
        thumb_height = max(24.0, max(1.0, height - 8) * visible)
        travel = max(1.0, height - 8 - thumb_height)
        if centered:
            self._drag_offset = thumb_height / 2
        target_top = y - self._drag_offset
        ratio = min(1.0, max(0.0, (target_top - 4.0) / travel))
        self.target.yview_moveto(ratio)

    def _on_press(self, event) -> str:
        self._dragging = True
        if self._thumb_contains(event.y):
            _track_top, _track_bottom, thumb_top = self._geometry()
            self._drag_offset = event.y - thumb_top
        else:
            self._scroll_to(event.y, centered=True)
        self._draw()
        return "break"

    def _on_drag(self, event) -> str:
        if self._dragging:
            self._scroll_to(event.y, centered=False)
        return "break"

    def _on_release(self, _event=None) -> str:
        self._dragging = False
        self._draw()
        return "break"

    def _draw(self) -> None:
        _clear_canvas(self)
        if self._last - self._first >= 0.999:
            return
        width = max(2, self.winfo_width())
        _track_top, _track_bottom, thumb_top = self._geometry()
        visible = max(0.06, self._last - self._first)
        thumb_height = max(24.0, max(1.0, self.winfo_height() - 8) * visible)
        center_x = width / 2
        _rounded_shape(
            self,
            (
                center_x - 4,
                thumb_top,
                center_x + 4,
                thumb_top + thumb_height,
            ),
            4,
            fill="#8c8c8c",
        )


def _button(
    parent: tk.Misc,
    text: str,
    command,
    *,
    primary: bool = True,
) -> _CanvasButton:
    return _CanvasButton(parent, text, command, primary=primary)


class WindowTintApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("窗色 · WindowTint")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self.configure(background=BG)
        self._set_window_icon()

        self.image: Image.Image | None = None
        self.image_name = "尚未选择图片"
        self.palette = DEFAULT_PALETTE
        self.windows: list[WindowInfo] = []
        self.window_by_label: dict[str, WindowInfo] = {}

        self.opacity_var = tk.DoubleVar(value=40.0)
        self.blur_var = tk.DoubleVar(value=4.0)
        self.content_focus_var = tk.BooleanVar(value=True)
        self.scope_var = tk.StringVar(value="all")
        self.page_var = tk.StringVar(value="basic")
        self.target_var = tk.StringVar()
        self.corner_var = tk.StringVar(value="圆角")
        self.resident_var = tk.BooleanVar(value=True)
        self.opacity_text = tk.StringVar(value="40%")
        self.blur_text = tk.StringVar(value="4 px")

        self._preview_photo: ImageTk.PhotoImage | None = None
        self._preview_job: str | None = None
        self._monitor_job: str | None = None
        self._overlay_job: str | None = None
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._tray: TrayController | None = None
        self._shutdown_done = False
        self._paused = False
        self._theme_armed = False
        self._advanced_status: InstallationStatus | None = None
        self._last_backdrop_path: Path | None = None
        self._last_palette = DEFAULT_PALETTE
        self._last_corner = "round"
        self._target_backdrops: dict[int, Path] = {}
        self._target_frame_sizes: dict[int, tuple[int, int]] = {}
        self._resize_stabilizer = ResizeStabilizer(stable_seconds=0.4)
        self._resize_backdrop_store = BackdropStore()
        self._resize_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="WindowTintResize",
        )
        self._resize_jobs: dict[
            int,
            tuple[WindowTarget, tuple[int, int], Future[Path]],
        ] = {}

        self.backdrop_store = BackdropStore()
        self.injection = InjectionManager()
        self.external_overlay = OverlayManager()
        self.session = ThemeSession(
            apply_dwm=lambda target, palette, corner: apply_window_theme(
                target.hwnd,
                palette,
                corner,
            ),
            restore_dwm=lambda target: restore_window_theme(target.hwnd),
            inject_backdrop=self._apply_backdrop,
            remove_backdrop=self._remove_backdrop,
        )

        self._build_layout()
        self._set_initial_state()

        self.bind("<Control-o>", lambda _event: self.open_image())
        self.bind("<F5>", lambda _event: self.refresh_windows())
        self.bind("<Control-Return>", lambda _event: self.apply_theme())
        self.bind("<Control-r>", lambda _event: self.restore_theme())
        self.protocol("WM_DELETE_WINDOW", self._close)
        atexit.register(self._atexit_cleanup)
        self._monitor_job = self.after(2500, self._monitor_tick)
        self._overlay_job = self.after(100, self._overlay_tick)

    def _set_window_icon(self) -> None:
        icon = _asset_path("windowtint.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _build_layout(self) -> None:
        tk.Frame(self, background=ACCENT, height=2).pack(fill="x")

        header = tk.Frame(self, background=BG)
        header.pack(fill="x", padx=30, pady=(22, 20))

        logo = tk.Canvas(header, width=44, height=44, background=BG, highlightthickness=0)
        logo.create_rectangle(0, 0, 44, 44, fill=INK, outline="")
        logo.create_text(22, 21, text="窗", fill=BG, font=("Microsoft YaHei UI", 12, "bold"))
        logo.pack(side="left")

        title_block = tk.Frame(header, background=BG)
        title_block.pack(side="left", padx=(14, 0))
        tk.Label(
            title_block,
            text="窗色",
            background=BG,
            foreground=INK,
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="一张图，给任意非受保护窗口铺上稳定的进程内背景",
            background=BG,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(2, 0))

        header_status = tk.Frame(
            header,
            background=SURFACE_ELEVATED,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        header_status.pack(side="right")
        tk.Label(
            header_status,
            text="仅本机处理",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            padx=12,
            pady=7,
            font=("Consolas", 9, "bold"),
        ).pack(side="left")
        tk.Label(
            header_status,
            text="64 位安全",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            padx=12,
            pady=7,
            font=("Consolas", 9),
        ).pack(side="left")

        self.page_switch = _SegmentedChoice(
            self,
            variable=self.page_var,
            options=(
                ("basic", "基础换肤"),
                ("advanced", "高阶系统主题"),
            ),
            command=self._switch_page,
            width=340,
            height=42,
        )
        self.page_switch.pack(anchor="w", padx=30, pady=(0, 14))

        page_host = tk.Frame(self, background=BG)
        page_host.pack(fill="both", expand=True, padx=30, pady=(0, 26))
        page_host.grid_rowconfigure(0, weight=1)
        page_host.grid_columnconfigure(0, weight=1)

        basic_page = tk.Frame(page_host, background=BG)
        advanced_page = tk.Frame(page_host, background=BG)
        basic_page.grid(row=0, column=0, sticky="nsew")
        advanced_page.grid(row=0, column=0, sticky="nsew")
        self._pages = {
            "basic": basic_page,
            "advanced": advanced_page,
        }

        basic_page.grid_columnconfigure(0, weight=6, uniform="main")
        basic_page.grid_columnconfigure(1, weight=5, uniform="main")
        basic_page.grid_rowconfigure(0, weight=1)
        self._build_preview(basic_page)
        self._build_controls(basic_page)
        self._build_advanced_page(advanced_page)
        self._switch_page("basic")

    def _build_preview(self, parent: tk.Misc) -> None:
        panel = tk.Frame(
            parent,
            background=SURFACE_ELEVATED,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 11))

        head = tk.Frame(panel, background=SURFACE_ELEVATED)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(
            head,
            text="整窗预览",
            background=SURFACE_ELEVATED,
            foreground=INK,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text="实时预览",
            background=ACCENT_SOFT,
            foreground=ACCENT,
            padx=9,
            pady=4,
            font=("Consolas", 9, "bold"),
        ).pack(side="right")

        self.preview = tk.Canvas(
            panel,
            background=BG,
            highlightthickness=0,
            bd=0,
        )
        self.preview.pack(fill="both", expand=True, padx=14, pady=(4, 10))
        self.preview.bind("<Configure>", self._schedule_preview)

        tk.Label(
            panel,
            text="背景图保持清晰，顶部白色柔焦向下渐隐；原有按钮、文字和交互保持不变。",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=540,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", padx=22, pady=(0, 17))

    def _build_controls(self, parent: tk.Misc) -> None:
        panel = tk.Frame(
            parent,
            background=SURFACE_ELEVATED,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        panel.grid(row=0, column=1, sticky="nsew", padx=(11, 0))

        footer = tk.Frame(panel, background=SURFACE_ELEVATED)
        footer.pack(side="bottom", fill="x", padx=24, pady=(8, 13))
        tk.Frame(panel, background=LINE, height=1).pack(
            side="bottom",
            fill="x",
            padx=24,
        )

        body = tk.Frame(panel, background=SURFACE_ELEVATED)
        body.pack(fill="both", expand=True, padx=(24, 10), pady=(17, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self.controls_canvas = tk.Canvas(
            body,
            background=SURFACE_ELEVATED,
            highlightthickness=0,
            bd=0,
        )
        self.controls_canvas.grid(row=0, column=0, sticky="nsew")
        self.controls_scrollbar = _ModernScrollbar(
            body,
            target=self.controls_canvas,
        )
        self.controls_scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        self.controls_scrollbar.grid_remove()
        self.controls_canvas.configure(yscrollcommand=self.controls_scrollbar.set)

        content = tk.Frame(self.controls_canvas, background=SURFACE_ELEVATED)
        content.grid_columnconfigure(0, weight=1)
        self.controls_content = content
        self.controls_window = self.controls_canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
        )
        content.bind("<Configure>", self._sync_controls_scroll)
        self.controls_canvas.bind("<Configure>", self._sync_controls_scroll)

        tk.Label(
            content,
            text="01  图片",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.choose_button = _button(content, "选择本地图片", self.open_image, primary=True)
        self.choose_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.image_label = tk.Label(
            content,
            text=self.image_name,
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 9),
        )
        self.image_label.grid(row=2, column=0, sticky="ew", pady=(6, 11))

        self._separator(content, 3, pady=(10, 0))

        tk.Label(
            content,
            text="02  玻璃质感",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).grid(row=4, column=0, sticky="w", pady=(10, 0))

        opacity_row = tk.Frame(content, background=SURFACE_ELEVATED)
        opacity_row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        opacity_row.grid_columnconfigure(1, weight=1)
        tk.Label(
            opacity_row,
            text="浓度",
            background=SURFACE_ELEVATED,
            foreground=BODY,
            font=("Microsoft YaHei UI", 10),
        ).grid(row=0, column=0, sticky="w")
        _RangeSlider(
            opacity_row,
            from_=8,
            to=60,
            variable=self.opacity_var,
            command=lambda _value: self._settings_changed(),
        ).grid(row=0, column=1, sticky="ew", padx=12)
        tk.Label(
            opacity_row,
            textvariable=self.opacity_text,
            width=5,
            anchor="e",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            font=("Consolas", 9),
        ).grid(row=0, column=2, sticky="e")

        blur_row = tk.Frame(content, background=SURFACE_ELEVATED)
        blur_row.grid(row=6, column=0, sticky="ew", pady=(6, 2))
        blur_row.grid_columnconfigure(1, weight=1)
        tk.Label(
            blur_row,
            text="柔化",
            background=SURFACE_ELEVATED,
            foreground=BODY,
            font=("Microsoft YaHei UI", 10),
        ).grid(row=0, column=0, sticky="w")
        _RangeSlider(
            blur_row,
            from_=0,
            to=30,
            variable=self.blur_var,
            command=lambda _value: self._settings_changed(),
        ).grid(row=0, column=1, sticky="ew", padx=12)
        tk.Label(
            blur_row,
            textvariable=self.blur_text,
            width=5,
            anchor="e",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            font=("Consolas", 9),
        ).grid(row=0, column=2, sticky="e")

        self.content_focus_check = _ToggleSwitch(
            content,
            text="顶部白色柔焦：向下平滑渐隐",
            variable=self.content_focus_var,
            command=self._content_focus_changed,
        )
        self.content_focus_check.grid(row=7, column=0, sticky="ew", pady=(2, 0))

        self._separator(content, 8, pady=(8, 0))

        tk.Label(
            content,
            text="03  应用范围",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).grid(row=9, column=0, sticky="w", pady=(10, 0))

        self.scope_switch = _SegmentedChoice(
            content,
            variable=self.scope_var,
            options=(
                ("all", "所有合格窗口"),
                ("selected", "当前窗口"),
            ),
            command=lambda _value: self._scope_changed(),
            width=390,
            height=38,
        )
        self.scope_switch.grid(row=10, column=0, sticky="ew", pady=(5, 3))

        target_row = tk.Frame(content, background=SURFACE_ELEVATED)
        target_row.grid(row=11, column=0, sticky="ew", pady=(1, 0))
        target_row.grid_columnconfigure(0, weight=1)
        self.target_combo = _CustomSelect(
            target_row,
            variable=self.target_var,
            state="disabled",
        )
        self.target_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.refresh_button = tk.Button(
            target_row,
            text="刷新",
            command=self.refresh_windows,
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT_STRONG,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.refresh_button.grid(row=0, column=1, sticky="ns")

        corner_row = tk.Frame(content, background=SURFACE_ELEVATED)
        corner_row.grid(row=12, column=0, sticky="ew", pady=(5, 0))
        tk.Label(
            corner_row,
            text="窗口圆角",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        self.corner_combo = _CustomSelect(
            corner_row,
            variable=self.corner_var,
            values=("圆角", "跟随系统", "直角"),
            state="readonly",
            width=140,
        )
        self.corner_combo.pack(side="right")

        self._separator(content, 13, pady=(9, 0))

        self.resident_check = _ToggleSwitch(
            content,
            text="关闭设置后继续应用，并监视新窗口",
            variable=self.resident_var,
            command=self._resident_changed,
        )
        self.resident_check.grid(row=14, column=0, sticky="ew", pady=(2, 0))

        self.status_label = tk.Label(
            content,
            text="",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.grid(row=15, column=0, sticky="ew", pady=(7, 6))

        actions = tk.Frame(footer, background=SURFACE_ELEVATED)
        actions.pack(fill="x")
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        self.apply_button = _button(actions, "应用到窗口", self.apply_theme, primary=True)
        self.apply_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.restore_button = _button(actions, "恢复默认", self.restore_theme, primary=False)
        self.restore_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        tk.Label(
            content,
            text="仅处理普通 64 位进程；系统、安全软件、反作弊与提权窗口会跳过。",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 8),
        ).grid(row=16, column=0, sticky="ew", pady=(7, 0))

        self._bind_controls_mousewheel(content)

    def _sync_controls_scroll(self, _event=None) -> None:
        canvas = self.controls_canvas
        width = max(1, canvas.winfo_width())
        current_width = float(canvas.itemcget(self.controls_window, "width") or 0)
        if abs(current_width - width) > 1:
            canvas.itemconfigure(self.controls_window, width=width)

        bounds = canvas.bbox("all")
        if bounds is not None:
            canvas.configure(scrollregion=bounds)

        content_height = self.controls_content.winfo_reqheight()
        viewport_height = canvas.winfo_height()
        if content_height > viewport_height + 1:
            self.controls_scrollbar.grid()
        else:
            self.controls_scrollbar.grid_remove()
            canvas.yview_moveto(0)

    def _bind_controls_mousewheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._scroll_controls_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_controls_mousewheel(child)

    def _scroll_controls_mousewheel(self, event) -> str | None:
        if self.controls_canvas.yview() == (0.0, 1.0):
            return None
        direction = -1 if event.delta > 0 else 1
        self.controls_canvas.yview_scroll(direction * 3, "units")
        return "break"

    def _build_advanced_page(self, parent: tk.Misc) -> None:
        parent.grid_columnconfigure(0, weight=7, uniform="advanced")
        parent.grid_columnconfigure(1, weight=5, uniform="advanced")
        parent.grid_rowconfigure(0, weight=1)

        explanation_panel = tk.Frame(
            parent,
            background=SURFACE_ELEVATED,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        explanation_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 11))

        explanation = tk.Frame(explanation_panel, background=SURFACE_ELEVATED)
        explanation.pack(fill="both", expand=True, padx=28, pady=24)

        tk.Label(
            explanation,
            text="高阶 · 全局 Windows 主题",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            explanation,
            text="SecureUxTheme 是另一条路线",
            background=SURFACE_ELEVATED,
            foreground=INK,
            font=("Microsoft YaHei UI", 19, "bold"),
        ).pack(anchor="w", pady=(10, 8))
        tk.Label(
            explanation,
            text=(
                "基础换肤把图片铺进普通窗口，不改变 Windows 视觉样式。"
                "高阶模式只负责检测并引导你使用 SecureUxTheme，"
                "让系统接受用户自行准备的 .msstyles 主题。"
            ),
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=560,
            font=("Microsoft YaHei UI", 10),
        ).pack(fill="x", anchor="w")

        self._advanced_fact(
            explanation,
            "01",
            "不是图片转主题",
            "SecureUxTheme 不会把一张图片自动转换成 Windows 主题，也不会让不同程序使用不同皮肤。",
        )
        self._advanced_fact(
            explanation,
            "02",
            "系统级修改",
            "官方安装器会写入 System32 和 HKLM 注册表，并要求管理员权限与重启。",
        )
        self._advanced_fact(
            explanation,
            "03",
            "WindowTint 不代装",
            "本程序不下载安装包、不静默安装、不提升权限；只打开官方发布页和 Windows 个性化。",
        )

        warning = tk.Frame(
            explanation,
            background=WARNING_SOFT,
            highlightbackground="#fed7aa",
            highlightthickness=1,
        )
        warning.pack(fill="x", pady=(22, 0))
        tk.Label(
            warning,
            text="使用前需了解的风险",
            background=WARNING_SOFT,
            foreground=WARNING,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(12, 4))
        tk.Label(
            warning,
            text=(
                "第三方主题可能与当前 Windows 版本不兼容，严重时会导致登录循环。"
                "安装前应创建还原点，并避免在依赖稳定性的工作环境中使用。"
            ),
            background=WARNING_SOFT,
            foreground="#8a4b12",
            justify="left",
            anchor="w",
            wraplength=530,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", padx=15, pady=(0, 12))

        license_row = tk.Frame(explanation, background=SURFACE_ELEVATED)
        license_row.pack(fill="x", pady=(18, 0))
        tk.Label(
            license_row,
            text="第三方组件",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        tk.Label(
            license_row,
            text="SecureUxTheme 4.x · LGPL-2.1 · 官方 GitHub",
            background=SURFACE_ELEVATED,
            foreground=INK,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", padx=(10, 0))

        status_panel = tk.Frame(
            parent,
            background=SURFACE_ELEVATED,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        status_panel.grid(row=0, column=1, sticky="nsew", padx=(11, 0))

        status_content = tk.Frame(status_panel, background=SURFACE_ELEVATED)
        status_content.pack(fill="both", expand=True, padx=26, pady=24)

        head = tk.Frame(status_content, background=SURFACE_ELEVATED)
        head.pack(fill="x")
        tk.Label(
            head,
            text="安装状态",
            background=SURFACE_ELEVATED,
            foreground=INK,
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text="本机检测",
            background=ACCENT_SOFT,
            foreground=ACCENT,
            padx=8,
            pady=4,
            font=("Consolas", 9, "bold"),
        ).pack(side="right")

        self.advanced_status_title = tk.Label(
            status_content,
            text="正在检查…",
            background=SURFACE_ELEVATED,
            foreground=INK,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self.advanced_status_title.pack(fill="x", pady=(20, 6))
        self.advanced_status_detail = tk.Label(
            status_content,
            text="",
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 9),
        )
        self.advanced_status_detail.pack(fill="x")

        status_actions = tk.Frame(status_content, background=SURFACE_ELEVATED)
        status_actions.pack(fill="x", pady=(15, 0))
        self.advanced_refresh_button = tk.Button(
            status_actions,
            text="重新检测",
            command=self._refresh_advanced_status,
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            activebackground=ACCENT_SOFT,
            activeforeground=ACCENT_STRONG,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.advanced_refresh_button.pack(side="right")

        tk.Frame(status_content, background=LINE, height=1).pack(fill="x", pady=(17, 16))

        self.official_button = _button(
            status_content,
            "前往官方下载页",
            self._open_secureuxtheme_page,
            primary=True,
        )
        self.official_button.pack(fill="x")

        self.personalization_button = _button(
            status_content,
            "打开 Windows 个性化",
            self._open_personalization,
            primary=False,
        )
        self.personalization_button.pack(fill="x", pady=(9, 0))
        self.personalization_button.set_state("disabled", cursor="arrow")

        disclosure = tk.Frame(
            status_content,
            background=SURFACE_CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        disclosure.pack(fill="x", pady=(20, 0))
        tk.Label(
            disclosure,
            text="打开外链前会再次确认",
            background=SURFACE_CARD,
            foreground=INK,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=13, pady=(11, 4))
        tk.Label(
            disclosure,
            text=(
                "WindowTint 只打开官方 GitHub 页面，不自动下载文件。"
                "后续安装由你自行核对版本、来源和 SHA-256 后完成。"
            ),
            background=SURFACE_CARD,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=350,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", padx=13, pady=(0, 11))

        tk.Label(
            status_content,
            text=(
                "项目：GitHub / namazso / SecureUxTheme\n"
                "许可证：LGPL-2.1（官方副本）"
            ),
            background=SURFACE_ELEVATED,
            foreground="#8b929c",
            justify="left",
            anchor="w",
            wraplength=390,
            font=("Consolas", 8),
        ).pack(fill="x", pady=(16, 0))

    def _advanced_fact(
        self,
        parent: tk.Misc,
        number: str,
        title: str,
        detail: str,
    ) -> None:
        row = tk.Frame(parent, background=SURFACE_ELEVATED)
        row.pack(fill="x", pady=(20, 0))
        tk.Label(
            row,
            text=number,
            width=3,
            anchor="nw",
            background=SURFACE_ELEVATED,
            foreground=ACCENT,
            font=("Consolas", 10, "bold"),
        ).pack(side="left", anchor="n")
        text = tk.Frame(row, background=SURFACE_ELEVATED)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text,
            text=title,
            background=SURFACE_ELEVATED,
            foreground=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill="x")
        tk.Label(
            text,
            text=detail,
            background=SURFACE_ELEVATED,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=500,
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", pady=(3, 0))

    def _separator(self, parent: tk.Misc, row: int, *, pady=(15, 0)) -> None:
        tk.Frame(parent, background=LINE, height=1).grid(
            row=row,
            column=0,
            sticky="ew",
            pady=pady,
        )

    def _set_initial_state(self) -> None:
        self._settings_changed()
        self.refresh_windows()
        self._scope_changed()
        self._refresh_advanced_status()
        if not supports_window_tint():
            self._set_status("需要 Windows 11 22000 或更高版本。", "error")
            self.apply_button.set_state("disabled", cursor="arrow")

    def _settings_changed(self) -> None:
        self.opacity_text.set(f"{round(float(self.opacity_var.get()))}%")
        self.blur_text.set(f"{round(float(self.blur_var.get()))} px")
        self._schedule_preview()

    def _content_focus_changed(self) -> None:
        self._settings_changed()
        if self._theme_armed and self.image is not None and not self._paused:
            self.after_idle(lambda: self.apply_theme(manual=False))

    def _scope_changed(self) -> None:
        state = "disabled" if self.scope_var.get() == "all" else "readonly"
        self.target_combo.set_state(
            state,
            cursor="arrow" if state == "disabled" else "hand2",
        )
        if self.scope_var.get() == "all":
            count = len(self.windows)
            self._set_status(f"当前找到 {count} 个合格窗口；新窗口会自动应用。")

    def _resident_changed(self) -> None:
        if self.resident_var.get():
            self._set_status("常驻已开启；关闭设置窗口后会留在托盘继续运行。", "success")
        else:
            self._set_status("常驻已关闭；关闭设置窗口时会恢复所有窗口并退出。")

    def _switch_page(self, page: str | None = None) -> None:
        selected = page or self.page_var.get()
        if selected not in self._pages:
            selected = "basic"
        for name, frame in self._pages.items():
            if name == selected:
                frame.grid()
            else:
                frame.grid_remove()
        if selected == "advanced":
            self._refresh_advanced_status()

    def _refresh_advanced_status(self) -> None:
        status = detect_installation()
        self._advanced_status = status
        colors = {
            InstallationState.INSTALLED: ACCENT,
            InstallationState.PARTIAL: WARNING,
            InstallationState.MISSING: INK,
        }
        self.advanced_status_title.configure(
            text=status.title,
            foreground=colors[status.state],
        )
        self.advanced_status_detail.configure(text=status.detail)
        if status.installed:
            self.personalization_button.set_state("normal", cursor="hand2")
        else:
            self.personalization_button.set_state("disabled", cursor="arrow")

    def _open_secureuxtheme_page(self) -> None:
        if self._advanced_status is None:
            self._refresh_advanced_status()
        confirmed = messagebox.askyesno(
            "打开第三方官方页面",
            "即将打开 SecureUxTheme 的官方 GitHub Releases 页面。\n\n"
            "· 组件：SecureUxTheme 4.x\n"
            "· 许可证：LGPL-2.1\n"
            "· WindowTint 不会自动下载文件\n"
            "· WindowTint 不会运行安装器或申请管理员权限\n\n"
            "安装器会修改 System32 和 HKLM 注册表，并要求重启。"
            "请自行核对发布者、版本、签名和 SHA-256。\n\n"
            "是否继续在浏览器中打开官方页面？",
            parent=self,
            icon="warning",
        )
        if not confirmed:
            return
        try:
            opened = webbrowser.open_new_tab(OFFICIAL_RELEASE_URL)
        except Exception as error:
            messagebox.showerror("无法打开页面", str(error), parent=self)
            return
        if not opened:
            messagebox.showerror(
                "无法打开页面",
                f"系统没有找到可用的浏览器。\n\n{OFFICIAL_RELEASE_URL}",
                parent=self,
            )

    def _open_personalization(self) -> None:
        if self._advanced_status is None or not self._advanced_status.installed:
            self._set_advanced_error("请先完成 SecureUxTheme 安装并重新检测。")
            return
        try:
            os.startfile(PERSONALIZATION_URI)
        except OSError as error:
            messagebox.showerror("无法打开个性化", str(error), parent=self)

    def _set_advanced_error(self, message: str) -> None:
        messagebox.showinfo("高阶系统主题", message, parent=self)

    def _schedule_preview(self, _event=None) -> None:
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(55, self._draw_preview)

    def _draw_preview(self) -> None:
        self._preview_job = None
        canvas = self.preview
        width = max(420, canvas.winfo_width())
        height = max(420, canvas.winfo_height())
        image = Image.new("RGBA", (width, height), BG)
        draw = ImageDraw.Draw(image, "RGBA")

        window_x = 24
        window_y = 18
        window_width = max(340, width - 48)
        window_height = max(300, height - 82)
        title_height = 42
        radius = 13

        draw.rounded_rectangle(
            (
                window_x + 8,
                window_y + 11,
                window_x + window_width + 8,
                window_y + window_height + 11,
            ),
            radius=radius,
            fill=(41, 57, 52, 38),
        )

        window = Image.new("RGBA", (window_width, window_height), (247, 249, 248, 255))

        if self.image is not None:
            glass = render_glass_backdrop(
                self.image.convert("RGB"),
                (window_width, window_height),
                opacity=float(self.opacity_var.get()) / 100,
                blur_radius=round(float(self.blur_var.get())),
                content_focus=self.content_focus_var.get(),
                focus_rect=(0.0, title_height / window_height, 1.0, 1.0),
            )
            window.alpha_composite(glass)

        client = Image.new(
            "RGBA",
            (window_width, window_height - title_height),
            (0, 0, 0, 0),
        )
        if self.image is not None:
            self._draw_fake_content(client, title_height)
        else:
            self._draw_preview_placeholder(client)
        window.alpha_composite(client, (0, title_height))

        title = Image.new("RGBA", (window_width, title_height), (0, 0, 0, 0))
        title_draw = ImageDraw.Draw(title)
        title_text = to_hex(self.palette.text) if self.image is not None else INK
        title_draw.text(
            (18, 12),
            "示例窗口",
            fill=title_text,
            font=self._font(10, True),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 90) if self.image is not None else (255, 255, 255, 0),
        )
        for index, color in enumerate(("#e98e73", "#e4c168", "#7dc49f")):
            center_x = window_width - 27 - index * 22
            title_draw.ellipse(
                (center_x - 5, 16, center_x + 5, 26),
                fill=color,
            )

        window.alpha_composite(title)
        mask = Image.new("L", (window_width, window_height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, window_width - 1, window_height - 1),
            radius=radius,
            fill=255,
        )
        image.paste(window, (window_x, window_y), mask)
        draw.rounded_rectangle(
            (window_x, window_y, window_x + window_width, window_y + window_height),
            radius=radius,
            outline=(255, 255, 255, 110),
            width=1,
        )

        label = "背景等比铺满 · 顶部柔焦向下渐隐"
        draw.rounded_rectangle(
            (window_x + 17, window_y + window_height + 18, window_x + 270, window_y + window_height + 42),
            radius=10,
            fill=(255, 255, 255, 210),
            outline=(206, 218, 213, 255),
        )
        draw.text(
            (window_x + 28, window_y + window_height + 23),
            label,
            fill=MUTED,
            font=self._font(8, False),
        )

        self._preview_photo = ImageTk.PhotoImage(image)
        canvas.delete("all")
        canvas.configure(background=BG)
        canvas.create_image(0, 0, image=self._preview_photo, anchor="nw")

    def _draw_fake_content(self, client: Image.Image, _title_height: int) -> None:
        width, height = client.size
        layer = Image.new("RGBA", client.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        sidebar_width = min(132, max(96, width // 4))
        draw.rectangle((0, 0, sidebar_width, height), fill=(255, 255, 255, 76))
        draw.text(
            (17, 20),
            "工作区",
            fill=(37, 54, 48, 255),
            font=self._font(9, True),
        )
        for index, label in enumerate(("概览", "项目文件", "最近打开")):
            top = 50 + index * 32
            if index == 1:
                draw.rounded_rectangle(
                    (13, top, sidebar_width - 13, top + 24),
                    radius=6,
                    fill=self.palette.accent + (210,),
                )
                color = (255, 255, 255, 255)
            else:
                draw.rounded_rectangle(
                    (13, top, sidebar_width - 13, top + 24),
                    radius=6,
                    fill=(255, 255, 255, 105),
                )
                color = (50, 66, 60, 255)
            draw.text((23, top + 6), label, fill=color, font=self._font(7, False))

        content_x = sidebar_width + 24
        content_width = max(130, width - content_x - 22)
        draw.text(
            (content_x, 20),
            "窗口外观",
            fill=(31, 45, 40, 255),
            font=self._font(13, True),
        )
        draw.text(
            (content_x, 43),
            "背景来自本地图片，原有控件仍由软件自己绘制。",
            fill=(91, 107, 101, 255),
            font=self._font(8, False),
        )
        draw.rounded_rectangle(
            (content_x, 71, content_x + content_width, min(height - 20, 71 + max(90, height - 96))),
            radius=9,
            fill=(255, 255, 255, 88),
            outline=(218, 228, 224, 170),
            width=1,
        )
        draw.line(
            (content_x + 18, 100, content_x + content_width - 18, 100),
            fill=(198, 212, 207, 210),
            width=1,
        )
        draw.text(
            (content_x + 18, 113),
            "标题栏与背景使用同一张图片的色调",
            fill=(70, 85, 79, 255),
            font=self._font(8, True),
        )
        draw.rounded_rectangle(
            (content_x + 18, 139, content_x + 120, 165),
            radius=6,
            fill=self.palette.accent + (225,),
        )
        draw.text(
            (content_x + 35, 146),
            "原有按钮",
            fill=(255, 255, 255, 255),
            font=self._font(7, True),
        )
        draw.rounded_rectangle(
            (content_x + 132, 139, content_x + 232, 165),
            radius=6,
            fill=(255, 255, 255, 210),
            outline=(210, 221, 216, 255),
            width=1,
        )
        draw.text(
            (content_x + 151, 146),
            "保持不变",
            fill=(59, 75, 69, 255),
            font=self._font(7, False),
        )

        client.alpha_composite(layer)

    def _draw_preview_placeholder(self, client: Image.Image) -> None:
        draw = ImageDraw.Draw(client)
        width, height = client.size
        draw.rounded_rectangle(
            (28, 28, width - 28, height - 28),
            radius=10,
            fill=(255, 255, 255, 170),
            outline=(215, 225, 221, 255),
            width=1,
        )
        draw.text(
            (width // 2 - 82, height // 2 - 18),
            "选择图片后预览整窗效果",
            fill=(111, 126, 120, 255),
            font=self._font(10, False),
        )

    def _font(self, size: int, bold: bool) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        name = "msyhbd.ttc" if bold else "msyh.ttc"
        path = Path("C:/Windows/Fonts") / name
        try:
            font = ImageFont.truetype(str(path), size=max(7, size))
        except OSError:
            font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    def _set_status(self, message: str, kind: str = "info") -> None:
        colors = {
            "info": MUTED,
            "success": ACCENT,
            "error": WARNING,
        }
        self.status_label.configure(text=message, foreground=colors.get(kind, MUTED))

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择一张本地图片",
            filetypes=(
                ("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("所有文件", "*.*"),
            ),
        )
        if not path:
            return
        try:
            with Image.open(path) as opened:
                self.image = opened.convert("RGB").copy()
        except Exception as error:
            messagebox.showerror("无法读取图片", str(error), parent=self)
            return

        self.image_name = Path(path).name
        self.image_label.configure(
            text=f"{self.image_name}\n{self.image.width} × {self.image.height} · 仅在本地处理"
        )
        self.palette = extract_palette(self.image)
        self._schedule_preview()
        self._set_status("图片已载入。可先调整浓度和柔化，再应用到窗口。", "success")

    def refresh_windows(self, silent: bool = False) -> list[WindowInfo]:
        previous = self.target_var.get()
        try:
            self.windows = list_windows()
        except Exception as error:
            self.windows = []
            if not silent:
                self._set_status(f"窗口列表读取失败：{error}", "error")

        labels = []
        self.window_by_label = {}
        used = set()
        for window in self.windows:
            label = window.label
            if label in used:
                label = f"{label}  [{window.hwnd:X}]"
            used.add(label)
            labels.append(label)
            self.window_by_label[label] = window

        self.target_combo.set_values(labels)
        if previous in self.window_by_label:
            self.target_var.set(previous)
        elif labels:
            self.target_var.set(labels[0])
        else:
            self.target_var.set("")

        if not silent:
            if labels:
                self._set_status(
                    f"找到 {len(labels)} 个合格窗口。资源管理器使用外部兼容层；"
                    "系统、安全软件与反作弊目标不会出现在列表中。"
                )
            elif supports_window_tint():
                self._set_status("暂未找到可操作窗口，请打开普通桌面软件后刷新。", "error")
        return self.windows

    def _selected_target(self) -> WindowTarget | None:
        window = self.window_by_label.get(self.target_var.get())
        return (
            WindowTarget(window.hwnd, window.pid, window.renderer)
            if window
            else None
        )

    def _candidate_targets(self) -> list[WindowTarget]:
        if self.scope_var.get() == "all":
            return [
                WindowTarget(window.hwnd, window.pid, window.renderer)
                for window in self.windows
            ]
        selected = self._selected_target()
        return [selected] if selected else []

    def _apply_backdrop(self, target: WindowTarget, path: Path) -> None:
        size = self._target_frame_sizes.get(target.hwnd)
        if size is None:
            try:
                size = window_frame_size(target.hwnd)
            except (OSError, RuntimeError):
                size = self._fallback_backdrop_size()
        focus_rect = self._focus_rect_for(target.hwnd, size)
        if self.image is not None:
            if target.renderer == "painted":
                path = self._render_painted_backdrop_file(size)
            else:
                path = self._render_backdrop_file(size, focus_rect)
        self._install_backdrop_path(target, path, size)

    def _remove_backdrop(self, target: WindowTarget) -> None:
        self._target_frame_sizes.pop(target.hwnd, None)
        self._resize_stabilizer.forget(target.hwnd)
        path = self._target_backdrops.pop(target.hwnd, None)
        try:
            if target.renderer == "external":
                self.external_overlay.remove(target)
            elif target.renderer == "painted":
                self.injection.remove(target)
            else:
                self.injection.remove(target)
        finally:
            if path is not None:
                self._remove_runtime_backdrop(path)

    def _install_backdrop_path(
        self,
        target: WindowTarget,
        path: Path,
        size: tuple[int, int],
    ) -> None:
        if target.renderer == "external":
            self.external_overlay.apply(target, path)
        elif target.renderer == "painted":
            self.injection.apply_painted(target, path)
        else:
            self.injection.apply(target, path)

        previous = self._target_backdrops.get(target.hwnd)
        self._target_backdrops[target.hwnd] = path
        self._target_frame_sizes[target.hwnd] = size
        self._resize_stabilizer.forget(target.hwnd)
        if previous is not None and previous != path:
            self._remove_runtime_backdrop(previous)

    @staticmethod
    def _remove_runtime_backdrop(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _practical_image(self) -> Image.Image:
        assert self.image is not None
        source = self.image.copy()
        source.thumbnail((1920, 1200), Image.Resampling.LANCZOS)
        return source

    def _fallback_backdrop_size(self) -> tuple[int, int]:
        if self.image is not None:
            source = self._practical_image()
            return max(1, source.width), max(1, source.height)
        return 1280, 800

    @staticmethod
    def _render_size(size: tuple[int, int]) -> tuple[int, int]:
        width, height = size
        longest = max(width, height)
        if longest <= 1920:
            return max(1, width), max(1, height)
        scale = 1920 / longest
        return max(1, round(width * scale)), max(1, round(height * scale))

    def _focus_rect_for(
        self,
        hwnd: int,
        size: tuple[int, int],
    ) -> tuple[float, float, float, float] | None:
        try:
            left, top, right, bottom = window_client_rect(hwnd)
        except (OSError, RuntimeError):
            return None
        width, height = size
        if width <= 0 or height <= 0:
            return None
        if right <= left or bottom <= top:
            return None
        return (
            left / width,
            top / height,
            right / width,
            bottom / height,
        )

    def _render_backdrop_file(
        self,
        size: tuple[int, int],
        focus_rect: tuple[float, float, float, float] | None = None,
    ) -> Path:
        backdrop = render_glass_backdrop(
            self._practical_image(),
            self._render_size(size),
            opacity=float(self.opacity_var.get()) / 100,
            blur_radius=round(float(self.blur_var.get())),
            content_focus=self.content_focus_var.get(),
            focus_rect=focus_rect,
        )
        return self.backdrop_store.write(backdrop)

    def _render_painted_backdrop_file(self, size: tuple[int, int]) -> Path:
        backdrop = render_glass_backdrop(
            self._practical_image(),
            self._render_size(size),
            opacity=max(0.08, float(self.opacity_var.get()) / 100 * 0.72),
            blur_radius=max(
                0,
                round(float(self.blur_var.get()) * 0.45),
            ),
            content_focus=False,
        )
        return self.backdrop_store.write(backdrop)

    def _build_backdrop_file(self) -> Path:
        source = self._practical_image()
        return self._render_backdrop_file(source.size)

    def _corner_mode(self) -> str:
        return CORNER_MODE_BY_LABEL.get(self.corner_var.get(), "round")

    def apply_theme(self, manual: bool = True) -> SessionResult | None:
        if self.image is None:
            if manual:
                messagebox.showinfo("先选择图片", "请先选择一张本地图片。", parent=self)
            return None
        if not supports_window_tint():
            if manual:
                messagebox.showerror(
                    "系统不支持",
                    "当前版本需要 Windows 11 build 22000 或更高版本。",
                    parent=self,
                )
            return None

        self.refresh_windows(silent=True)
        targets = self._candidate_targets()
        if not targets:
            if manual:
                messagebox.showinfo("没有目标窗口", "请先打开普通桌面软件并点击刷新。", parent=self)
            return None

        self._set_status("正在生成柔和背景并应用到合格窗口…")
        self.update_idletasks()
        try:
            backdrop_path = self._build_backdrop_file()
            corner_mode = self._corner_mode()
            result = self.session.apply(
                targets,
                self.palette,
                corner_mode,
                backdrop_path,
            )
        except Exception as error:
            self._theme_armed = False
            self._set_status(f"应用失败：{error}", "error")
            if manual:
                messagebox.showerror("应用失败", str(error), parent=self)
            return None

        self._last_backdrop_path = backdrop_path
        self._last_palette = self.palette
        self._last_corner = corner_mode
        self._theme_armed = bool(result.applied)
        self._paused = False
        self._report_apply_result(result, manual=manual)
        return result

    def _report_apply_result(self, result: SessionResult, *, manual: bool) -> None:
        if result.applied:
            scope = "合格窗口" if self.scope_var.get() == "all" else "当前窗口"
            self._set_status(
                f"已把玻璃背景应用到 {len(result.applied)} 个{scope}；"
                "资源管理器使用外部兼容层。"
                + (" 关闭设置后会留在托盘继续运行。" if self.resident_var.get() else ""),
                "success",
            )
        if result.failures:
            detail = "\n".join(f"{hwnd:08X}: {message}" for hwnd, message in result.failures[:5])
            self._set_status(
                f"{len(result.failures)} 个窗口应用失败，已回滚对应改动。",
                "error",
            )
            if manual and not result.applied:
                messagebox.showwarning(
                    "目标窗口不支持",
                    "这些窗口无法建立玻璃背景层，已保持原样。\n\n"
                    "常见原因是窗口已关闭、最小化或不再接受普通窗口访问。\n\n"
                    f"{detail}",
                    parent=self,
                )

    def restore_theme(self) -> None:
        active = self.session.active_targets
        if self.scope_var.get() == "all":
            targets = list(active.values())
        else:
            selected = self._selected_target()
            targets = [selected] if selected else list(active.values())
        if not targets:
            self._set_status("当前没有本工具应用过的窗口。")
            self._theme_armed = False
            return

        failures = self.session.remove(targets)
        self._theme_armed = bool(self.session.active_targets)
        if failures:
            self._set_status(f"已恢复其余窗口；{len(failures)} 个窗口已关闭或无法访问。")
        else:
            self._set_status(f"已恢复 {len(targets)} 个窗口的默认外观。", "success")

    def _monitor_tick(self) -> None:
        self._monitor_job = None
        if self._shutdown_done:
            return
        if (
            self._theme_armed
            and not self._paused
            and self.resident_var.get()
            and self.scope_var.get() == "all"
            and self._last_backdrop_path is not None
        ):
            self.refresh_windows(silent=True)
            current = [
                WindowTarget(window.hwnd, window.pid, window.renderer)
                for window in self.windows
            ]
            active = self.session.active_targets
            add, remove = monitor_plan(current=current, active=active)
            if remove:
                self.session.remove(remove)
            if add:
                result = self.session.apply(
                    add,
                    self._last_palette,
                    self._last_corner,
                    self._last_backdrop_path,
                )
                if result.applied and self.state() == "normal":
                    self._set_status(
                        f"自动应用到 {len(result.applied)} 个新打开的合格窗口。",
                        "success",
                    )
        self._monitor_job = self.after(2500, self._monitor_tick)

    def _overlay_tick(self) -> None:
        self._overlay_job = None
        if self._shutdown_done:
            return
        try:
            self._refresh_resized_targets()
        except Exception as error:
            self._set_status(f"窗口尺寸刷新失败：{error}", "error")
        try:
            self.external_overlay.sync()
        except Exception as error:
            self._set_status(f"资源管理器外部层同步失败：{error}", "error")
            try:
                self.external_overlay.remove_all()
            except Exception:
                pass
        if self._resize_jobs:
            delay = 25
        elif self.external_overlay.needs_fast_sync:
            delay = 8
        elif self.external_overlay.event_driven:
            delay = 100
        elif self.external_overlay.has_overlays:
            delay = 100
        elif self.session.active_targets:
            delay = 80
        else:
            delay = 500
        self._overlay_job = self.after(delay, self._overlay_tick)

    def _refresh_resized_targets(self, *, now: float | None = None) -> None:
        if self.image is None or self._shutdown_done:
            return
        observed_at = time.monotonic() if now is None else now
        active = self.session.active_targets
        for hwnd in tuple(self._target_frame_sizes):
            if hwnd not in active:
                self._target_frame_sizes.pop(hwnd, None)
                self._resize_stabilizer.forget(hwnd)

        for hwnd, (target, requested_size, future) in tuple(
            self._resize_jobs.items()
        ):
            if not future.done():
                continue
            self._resize_jobs.pop(hwnd, None)
            if future.cancelled():
                continue
            try:
                path = future.result()
            except Exception as error:
                self._set_status(f"窗口尺寸刷新失败：{error}", "error")
                continue

            current_target = active.get(hwnd)
            if current_target is None or current_target.pid != target.pid:
                self._remove_runtime_backdrop(path)
                continue
            try:
                current_size = window_frame_size(hwnd)
            except (OSError, RuntimeError):
                self._remove_runtime_backdrop(path)
                continue
            if current_size != requested_size:
                self._remove_runtime_backdrop(path)
                self._resize_stabilizer.observe(
                    hwnd,
                    current_size,
                    now=observed_at,
                )
                continue
            try:
                self._install_backdrop_path(current_target, path, current_size)
            except Exception as error:
                self._remove_runtime_backdrop(path)
                self._set_status(f"窗口尺寸刷新失败：{error}", "error")

        for hwnd, target in active.items():
            if target.renderer in {"external", "painted"}:
                continue
            if hwnd in self._resize_jobs:
                continue
            expected_size = self._target_frame_sizes.get(hwnd)
            if expected_size is None:
                continue
            try:
                current_size = window_frame_size(hwnd)
            except (OSError, RuntimeError):
                continue
            if current_size == expected_size:
                self._resize_stabilizer.forget(hwnd)
                continue
            stable_size = self._resize_stabilizer.observe(
                hwnd,
                current_size,
                now=observed_at,
            )
            if stable_size is not None:
                self._start_resize_refresh(target, stable_size)

    def _start_resize_refresh(
        self,
        target: WindowTarget,
        size: tuple[int, int],
    ) -> None:
        if self.image is None or self._shutdown_done:
            return
        source = self._practical_image()
        opacity = float(self.opacity_var.get()) / 100
        blur_radius = round(float(self.blur_var.get()))
        content_focus = self.content_focus_var.get()
        focus_rect = self._focus_rect_for(target.hwnd, size)
        future = self._resize_executor.submit(
            self._render_resize_backdrop,
            source,
            self._render_size(size),
            opacity,
            blur_radius,
            content_focus,
            focus_rect,
        )
        self._resize_jobs[target.hwnd] = (target, size, future)

    def _render_resize_backdrop(
        self,
        source: Image.Image,
        size: tuple[int, int],
        opacity: float,
        blur_radius: int,
        content_focus: bool,
        focus_rect: tuple[float, float, float, float] | None,
    ) -> Path:
        backdrop = render_glass_backdrop(
            source,
            size,
            opacity=opacity,
            blur_radius=blur_radius,
            content_focus=content_focus,
            focus_rect=focus_rect,
        )
        return self._resize_backdrop_store.write(backdrop)

    def toggle_pause(self) -> None:
        if self._paused:
            self._paused = False
            if self.image is not None:
                self.apply_theme(manual=False)
            else:
                self._set_status("已继续运行，但还没有选择图片。", "success")
        else:
            self._paused = True
            self.session.remove_all()
            self._set_status("已暂停；现有窗口已恢复，常驻后台仍在运行。")

    def _close(self) -> None:
        if self.resident_var.get():
            self._hide_to_tray()
            return
        self.exit_app()

    def _hide_to_tray(self) -> None:
        try:
            if self._tray is None:
                self._tray = TrayController(
                    icon_path=_asset_path("windowtint.png"),
                    is_paused=lambda: self._paused,
                    on_open=lambda: self.after(0, self._show_window),
                    on_toggle_pause=lambda: self.after(0, self.toggle_pause),
                    on_exit=lambda: self.after(0, self.exit_app),
                )
            self._tray.start()
        except Exception as error:
            messagebox.showerror(
                "托盘启动失败",
                f"无法进入常驻模式，将恢复窗口并退出。\n\n{error}",
                parent=self,
            )
            self.exit_app()
            return
        self.withdraw()

    def _show_window(self) -> None:
        if self._shutdown_done:
            return
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(180, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def exit_app(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        for _target, _size, future in self._resize_jobs.values():
            future.cancel()
        self._resize_jobs.clear()
        self._resize_executor.shutdown(wait=True, cancel_futures=True)
        if self._monitor_job:
            try:
                self.after_cancel(self._monitor_job)
            except tk.TclError:
                pass
            self._monitor_job = None
        if self._overlay_job:
            try:
                self.after_cancel(self._overlay_job)
            except tk.TclError:
                pass
            self._overlay_job = None
        if self._preview_job:
            try:
                self.after_cancel(self._preview_job)
            except tk.TclError:
                pass
            self._preview_job = None
        try:
            self.session.remove_all()
        finally:
            self.external_overlay.remove_all()
        self.backdrop_store.clear()
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _atexit_cleanup(self) -> None:
        if self._shutdown_done:
            return
        self._resize_executor.shutdown(wait=False, cancel_futures=True)
        try:
            self.session.remove_all()
        except Exception:
            pass
        try:
            self.external_overlay.remove_all()
        except Exception:
            pass
        try:
            self.backdrop_store.clear()
        except Exception:
            pass


def main() -> None:
    _enable_dpi_awareness()
    if activate_existing_window():
        return

    guard = SingleInstanceGuard()
    if not guard.acquire():
        activate_existing_window(timeout=3.0)
        return

    try:
        app = WindowTintApp()
        app.mainloop()
    finally:
        guard.release()

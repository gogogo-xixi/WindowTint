from __future__ import annotations

import atexit
import ctypes
import os
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .artwork import build_glass_backdrop
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
)
from .overlay import OverlayManager
from .session import SessionResult, ThemeSession, WindowTarget
from .theme import ThemePalette, extract_palette, mix, to_hex
from .tray import TrayController
from .workflow import monitor_plan


BG = "#eef2f0"
SURFACE = "#ffffff"
SURFACE_SOFT = "#f7faf8"
INK = "#17221f"
MUTED = "#66736f"
LINE = "#d7dfdc"
TEAL = "#0b6b57"
TEAL_DARK = "#075242"
ORANGE = "#d66747"
ORANGE_DARK = "#a94a31"
SOFT_TEAL = "#e4f0eb"
SOFT_ORANGE = "#f7e8e2"

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


def _button(
    parent: tk.Misc,
    text: str,
    command,
    *,
    primary: bool = True,
) -> tk.Button:
    background = TEAL if primary else "#edf4f1"
    active = TEAL_DARK if primary else "#dcebe5"
    foreground = "#ffffff" if primary else INK
    border = TEAL if primary else "#abc1b9"
    return tk.Button(
        parent,
        text=text,
        command=command,
        background=background,
        foreground=foreground,
        activebackground=active,
        activeforeground=foreground,
        disabledforeground="#9aa5a1",
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=border,
        highlightcolor=border,
        padx=16,
        pady=9,
        cursor="hand2",
        font=("Microsoft YaHei UI", 10, "bold"),
    )


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

        self.opacity_var = tk.DoubleVar(value=42.0)
        self.blur_var = tk.DoubleVar(value=2.0)
        self.content_focus_var = tk.BooleanVar(value=True)
        self.scope_var = tk.StringVar(value="selected")
        self.target_var = tk.StringVar()
        self.corner_var = tk.StringVar(value="round")
        self.resident_var = tk.BooleanVar(value=True)
        self.opacity_text = tk.StringVar(value="42%")
        self.blur_text = tk.StringVar(value="2 px")

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

        self._configure_styles()
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

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "WindowTint.TCombobox",
            fieldbackground=SURFACE,
            background=SURFACE,
            foreground=INK,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            arrowcolor=INK,
            padding=8,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "WindowTint.TCombobox",
            fieldbackground=[
                ("readonly", SURFACE),
                ("disabled", "#f2f5f4"),
            ],
            foreground=[("disabled", "#9aa5a1")],
            selectbackground=[("readonly", SURFACE)],
            selectforeground=[("readonly", INK)],
        )
        style.configure(
            "WindowTint.TRadiobutton",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "WindowTint.TRadiobutton",
            background=[("active", SURFACE)],
            indicatorcolor=[("selected", TEAL), ("!selected", LINE)],
        )
        style.configure(
            "Tint.Horizontal.TScale",
            background=SURFACE,
            troughcolor="#dfe8e4",
            bordercolor=SURFACE,
            lightcolor=TEAL,
            darkcolor=TEAL,
            sliderrelief="flat",
        )
        style.map(
            "Tint.Horizontal.TScale",
            background=[("active", SURFACE)],
        )
        style.configure(
            "WindowTint.TNotebook",
            background=BG,
            borderwidth=0,
            tabmargins=(0, 0, 0, 8),
        )
        style.configure(
            "WindowTint.TNotebook.Tab",
            background="#dde7e3",
            foreground=MUTED,
            padding=(24, 10),
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "WindowTint.TNotebook.Tab",
            background=[("selected", SURFACE), ("active", "#e9f0ed")],
            foreground=[("selected", TEAL_DARK), ("active", INK)],
        )

    def _build_layout(self) -> None:
        header = tk.Frame(self, background=BG)
        header.pack(fill="x", padx=30, pady=(22, 18))

        logo = tk.Canvas(header, width=44, height=44, background=BG, highlightthickness=0)
        logo.create_rectangle(0, 0, 44, 44, fill=INK, outline="")
        logo.create_text(22, 21, text="WT", fill="#ffffff", font=("Segoe UI", 11, "bold"))
        logo.pack(side="left")

        title_block = tk.Frame(header, background=BG)
        title_block.pack(side="left", padx=(14, 0))
        tk.Label(
            title_block,
            text="窗色",
            background=BG,
            foreground=INK,
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="一张图，给任意非受保护窗口铺上稳定的进程内背景",
            background=BG,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        tk.Label(
            header,
            text="本地处理 · 普通窗口安全筛选",
            background=SOFT_TEAL,
            foreground=TEAL_DARK,
            padx=13,
            pady=7,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="right")

        self.notebook = ttk.Notebook(self, style="WindowTint.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=30, pady=(0, 26))

        basic_page = tk.Frame(self.notebook, background=BG)
        advanced_page = tk.Frame(self.notebook, background=BG)
        self.notebook.add(basic_page, text="基础换肤")
        self.notebook.add(advanced_page, text="高阶系统主题")

        basic_page.grid_columnconfigure(0, weight=6, uniform="main")
        basic_page.grid_columnconfigure(1, weight=5, uniform="main")
        basic_page.grid_rowconfigure(0, weight=1)
        self._build_preview(basic_page)
        self._build_controls(basic_page)
        self._build_advanced_page(advanced_page)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _build_preview(self, parent: tk.Misc) -> None:
        panel = tk.Frame(
            parent,
            background=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 11))

        head = tk.Frame(panel, background=SURFACE)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(
            head,
            text="整窗预览",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text="IN-PROCESS GLASS",
            background=SOFT_ORANGE,
            foreground=ORANGE_DARK,
            padx=9,
            pady=4,
            font=("Consolas", 8, "bold"),
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
            text="图片以柔和玻璃层进入目标窗口，跟随窗口原生移动；原有按钮、文字和图片仍然可见。",
            background=SURFACE,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=540,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=22, pady=(0, 17))

    def _build_controls(self, parent: tk.Misc) -> None:
        panel = tk.Frame(
            parent,
            background=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        panel.grid(row=0, column=1, sticky="nsew", padx=(11, 0))

        content = tk.Frame(panel, background=SURFACE)
        content.pack(fill="both", expand=True, padx=24, pady=(17, 13))
        content.grid_columnconfigure(0, weight=1)

        tk.Label(
            content,
            text="01  图片",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.choose_button = _button(content, "选择本地图片", self.open_image, primary=True)
        self.choose_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.image_label = tk.Label(
            content,
            text=self.image_name,
            background=SURFACE,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 8),
        )
        self.image_label.grid(row=2, column=0, sticky="ew", pady=(6, 11))

        self._separator(content, 3, pady=(10, 0))

        tk.Label(
            content,
            text="02  玻璃质感",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).grid(row=4, column=0, sticky="w", pady=(10, 0))

        opacity_row = tk.Frame(content, background=SURFACE)
        opacity_row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        opacity_row.grid_columnconfigure(1, weight=1)
        tk.Label(
            opacity_row,
            text="浓度",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w")
        ttk.Scale(
            opacity_row,
            from_=8,
            to=60,
            variable=self.opacity_var,
            command=lambda _value: self._settings_changed(),
            style="Tint.Horizontal.TScale",
        ).grid(row=0, column=1, sticky="ew", padx=12)
        tk.Label(
            opacity_row,
            textvariable=self.opacity_text,
            width=5,
            anchor="e",
            background=SURFACE,
            foreground=MUTED,
            font=("Consolas", 8),
        ).grid(row=0, column=2, sticky="e")

        blur_row = tk.Frame(content, background=SURFACE)
        blur_row.grid(row=6, column=0, sticky="ew", pady=(6, 2))
        blur_row.grid_columnconfigure(1, weight=1)
        tk.Label(
            blur_row,
            text="柔化",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w")
        ttk.Scale(
            blur_row,
            from_=0,
            to=30,
            variable=self.blur_var,
            command=lambda _value: self._settings_changed(),
            style="Tint.Horizontal.TScale",
        ).grid(row=0, column=1, sticky="ew", padx=12)
        tk.Label(
            blur_row,
            textvariable=self.blur_text,
            width=5,
            anchor="e",
            background=SURFACE,
            foreground=MUTED,
            font=("Consolas", 8),
        ).grid(row=0, column=2, sticky="e")

        self.content_focus_check = tk.Checkbutton(
            content,
            text="工作区优先：中央内容保持清晰",
            variable=self.content_focus_var,
            command=self._settings_changed,
            background=SURFACE,
            foreground=INK,
            activebackground=SURFACE,
            activeforeground=INK,
            selectcolor=SOFT_TEAL,
            anchor="w",
            padx=0,
            pady=5,
            font=("Microsoft YaHei UI", 9),
        )
        self.content_focus_check.grid(row=7, column=0, sticky="ew", pady=(2, 0))

        self._separator(content, 8, pady=(8, 0))

        tk.Label(
            content,
            text="03  应用范围",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).grid(row=9, column=0, sticky="w", pady=(10, 0))

        scope_row = tk.Frame(content, background=SURFACE)
        scope_row.grid(row=10, column=0, sticky="w", pady=(5, 3))
        ttk.Radiobutton(
            scope_row,
            text="所有合格窗口",
            value="all",
            variable=self.scope_var,
            command=self._scope_changed,
            style="WindowTint.TRadiobutton",
        ).pack(side="left", padx=(0, 18))
        ttk.Radiobutton(
            scope_row,
            text="当前窗口",
            value="selected",
            variable=self.scope_var,
            command=self._scope_changed,
            style="WindowTint.TRadiobutton",
        ).pack(side="left")

        target_row = tk.Frame(content, background=SURFACE)
        target_row.grid(row=11, column=0, sticky="ew", pady=(1, 0))
        target_row.grid_columnconfigure(0, weight=1)
        self.target_combo = ttk.Combobox(
            target_row,
            textvariable=self.target_var,
            state="disabled",
            style="WindowTint.TCombobox",
        )
        self.target_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.refresh_button = tk.Button(
            target_row,
            text="刷新",
            command=self.refresh_windows,
            background=SURFACE,
            foreground=TEAL_DARK,
            activebackground=SOFT_TEAL,
            activeforeground=TEAL_DARK,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.refresh_button.grid(row=0, column=1, sticky="ns")

        corner_row = tk.Frame(content, background=SURFACE)
        corner_row.grid(row=12, column=0, sticky="ew", pady=(5, 0))
        tk.Label(
            corner_row,
            text="窗口圆角",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        corner_combo = ttk.Combobox(
            corner_row,
            textvariable=self.corner_var,
            values=("round", "system", "square"),
            state="readonly",
            width=10,
            style="WindowTint.TCombobox",
        )
        corner_combo.pack(side="right")

        self._separator(content, 13, pady=(9, 0))

        self.resident_check = tk.Checkbutton(
            content,
            text="关闭设置后继续应用，并监视新窗口",
            variable=self.resident_var,
            command=self._resident_changed,
            background=SURFACE,
            foreground=INK,
            activebackground=SURFACE,
            activeforeground=INK,
            selectcolor=SOFT_TEAL,
            anchor="w",
            padx=0,
            pady=6,
            font=("Microsoft YaHei UI", 9),
        )
        self.resident_check.grid(row=14, column=0, sticky="ew", pady=(2, 0))

        self.status_label = tk.Label(
            content,
            text="",
            background=SURFACE,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 8),
        )
        self.status_label.grid(row=15, column=0, sticky="ew", pady=(7, 6))

        actions = tk.Frame(content, background=SURFACE)
        actions.grid(row=16, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        self.apply_button = _button(actions, "应用到窗口", self.apply_theme, primary=True)
        self.apply_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.restore_button = _button(actions, "恢复默认", self.restore_theme, primary=False)
        self.restore_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        tk.Label(
            content,
            text="仅处理普通 64 位进程；系统、安全软件、反作弊与提权窗口会跳过。",
            background=SURFACE,
            foreground="#8b9691",
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 7),
        ).grid(row=17, column=0, sticky="ew", pady=(7, 0))

    def _build_advanced_page(self, parent: tk.Misc) -> None:
        parent.grid_columnconfigure(0, weight=7, uniform="advanced")
        parent.grid_columnconfigure(1, weight=5, uniform="advanced")
        parent.grid_rowconfigure(0, weight=1)

        explanation_panel = tk.Frame(
            parent,
            background=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        explanation_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 11))

        explanation = tk.Frame(explanation_panel, background=SURFACE)
        explanation.pack(fill="both", expand=True, padx=28, pady=24)

        tk.Label(
            explanation,
            text="高阶 · 全局 Windows 主题",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            explanation,
            text="SecureUxTheme 是另一条路线",
            background=SURFACE,
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
            background=SURFACE,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=560,
            font=("Microsoft YaHei UI", 9),
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
            background=SOFT_ORANGE,
            highlightbackground="#eccabd",
            highlightthickness=1,
        )
        warning.pack(fill="x", pady=(22, 0))
        tk.Label(
            warning,
            text="使用前需了解的风险",
            background=SOFT_ORANGE,
            foreground=ORANGE_DARK,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=15, pady=(12, 4))
        tk.Label(
            warning,
            text=(
                "第三方主题可能与当前 Windows 版本不兼容，严重时会导致登录循环。"
                "安装前应创建还原点，并避免在依赖稳定性的工作环境中使用。"
            ),
            background=SOFT_ORANGE,
            foreground="#764637",
            justify="left",
            anchor="w",
            wraplength=530,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=15, pady=(0, 12))

        license_row = tk.Frame(explanation, background=SURFACE)
        license_row.pack(fill="x", pady=(18, 0))
        tk.Label(
            license_row,
            text="第三方组件",
            background=SURFACE,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        tk.Label(
            license_row,
            text="SecureUxTheme 4.x · LGPL-2.1 · 官方 GitHub",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left", padx=(10, 0))

        status_panel = tk.Frame(
            parent,
            background=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        status_panel.grid(row=0, column=1, sticky="nsew", padx=(11, 0))

        status_content = tk.Frame(status_panel, background=SURFACE)
        status_content.pack(fill="both", expand=True, padx=26, pady=24)

        head = tk.Frame(status_content, background=SURFACE)
        head.pack(fill="x")
        tk.Label(
            head,
            text="安装状态",
            background=SURFACE,
            foreground=INK,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text="LOCAL CHECK",
            background=SOFT_TEAL,
            foreground=TEAL_DARK,
            padx=8,
            pady=4,
            font=("Consolas", 8, "bold"),
        ).pack(side="right")

        self.advanced_status_title = tk.Label(
            status_content,
            text="正在检查…",
            background=SURFACE,
            foreground=INK,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.advanced_status_title.pack(fill="x", pady=(20, 6))
        self.advanced_status_detail = tk.Label(
            status_content,
            text="",
            background=SURFACE,
            foreground=MUTED,
            anchor="w",
            justify="left",
            wraplength=390,
            font=("Microsoft YaHei UI", 8),
        )
        self.advanced_status_detail.pack(fill="x")

        status_actions = tk.Frame(status_content, background=SURFACE)
        status_actions.pack(fill="x", pady=(15, 0))
        self.advanced_refresh_button = tk.Button(
            status_actions,
            text="重新检测",
            command=self._refresh_advanced_status,
            background=SURFACE,
            foreground=TEAL_DARK,
            activebackground=SOFT_TEAL,
            activeforeground=TEAL_DARK,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8, "bold"),
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
        self.personalization_button.configure(state="disabled", cursor="arrow")

        disclosure = tk.Frame(
            status_content,
            background=SURFACE_SOFT,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        disclosure.pack(fill="x", pady=(20, 0))
        tk.Label(
            disclosure,
            text="打开外链前会再次确认",
            background=SURFACE_SOFT,
            foreground=INK,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(anchor="w", padx=13, pady=(11, 4))
        tk.Label(
            disclosure,
            text=(
                "WindowTint 只打开官方 GitHub 页面，不自动下载文件。"
                "后续安装由你自行核对版本、来源和 SHA-256 后完成。"
            ),
            background=SURFACE_SOFT,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=350,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=13, pady=(0, 11))

        tk.Label(
            status_content,
            text=(
                "项目：GitHub / namazso / SecureUxTheme\n"
                "许可证：LGPL-2.1（官方副本）"
            ),
            background=SURFACE,
            foreground="#8b9691",
            justify="left",
            anchor="w",
            wraplength=390,
            font=("Consolas", 7),
        ).pack(fill="x", pady=(16, 0))

    def _advanced_fact(
        self,
        parent: tk.Misc,
        number: str,
        title: str,
        detail: str,
    ) -> None:
        row = tk.Frame(parent, background=SURFACE)
        row.pack(fill="x", pady=(20, 0))
        tk.Label(
            row,
            text=number,
            width=3,
            anchor="nw",
            background=SURFACE,
            foreground=TEAL,
            font=("Consolas", 9, "bold"),
        ).pack(side="left", anchor="n")
        text = tk.Frame(row, background=SURFACE)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text,
            text=title,
            background=SURFACE,
            foreground=INK,
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(fill="x")
        tk.Label(
            text,
            text=detail,
            background=SURFACE,
            foreground=MUTED,
            justify="left",
            anchor="w",
            wraplength=500,
            font=("Microsoft YaHei UI", 8),
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
        self._refresh_advanced_status()
        if not supports_window_tint():
            self._set_status("需要 Windows 11 22000 或更高版本。", "error")
            self.apply_button.configure(state="disabled", cursor="arrow")

    def _settings_changed(self) -> None:
        self.opacity_text.set(f"{round(float(self.opacity_var.get()))}%")
        self.blur_text.set(f"{round(float(self.blur_var.get()))} px")
        self._schedule_preview()

    def _scope_changed(self) -> None:
        state = "disabled" if self.scope_var.get() == "all" else "readonly"
        self.target_combo.configure(state=state)
        if self.scope_var.get() == "all":
            count = len(self.windows)
            self._set_status(f"当前找到 {count} 个合格窗口；新窗口会自动应用。")

    def _resident_changed(self) -> None:
        if self.resident_var.get():
            self._set_status("常驻已开启；关闭设置窗口后会留在托盘继续运行。", "success")
        else:
            self._set_status("常驻已关闭；关闭设置窗口时会恢复所有窗口并退出。")

    def _tab_changed(self, _event=None) -> None:
        selected = self.notebook.select()
        if selected and self.notebook.index(selected) == 1:
            self._refresh_advanced_status()

    def _refresh_advanced_status(self) -> None:
        status = detect_installation()
        self._advanced_status = status
        colors = {
            InstallationState.INSTALLED: TEAL_DARK,
            InstallationState.PARTIAL: ORANGE_DARK,
            InstallationState.MISSING: INK,
        }
        self.advanced_status_title.configure(
            text=status.title,
            foreground=colors[status.state],
        )
        self.advanced_status_detail.configure(text=status.detail)
        if status.installed:
            self.personalization_button.configure(state="normal", cursor="hand2")
        else:
            self.personalization_button.configure(state="disabled", cursor="arrow")

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
            source = self.image.convert("RGB").resize(
                (window_width, window_height),
                Image.Resampling.LANCZOS,
            )
            glass = build_glass_backdrop(
                source,
                opacity=float(self.opacity_var.get()) / 100,
                blur_radius=round(float(self.blur_var.get())),
                content_focus=self.content_focus_var.get(),
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

        label = "整图覆盖 · 原生文字和按钮透出"
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
        draw.rectangle((0, 0, sidebar_width, height), fill=(255, 255, 255, 118))
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
            fill=(255, 255, 255, 192),
            outline=(218, 228, 224, 220),
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
            "success": TEAL_DARK,
            "error": ORANGE_DARK,
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

        self.target_combo.configure(values=labels)
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
        if target.renderer == "external":
            self.external_overlay.apply(target, path)
        else:
            self.injection.apply(target, path)

    def _remove_backdrop(self, target: WindowTarget) -> None:
        if target.renderer == "external":
            self.external_overlay.remove(target)
        else:
            self.injection.remove(target)

    def _practical_image(self) -> Image.Image:
        assert self.image is not None
        source = self.image.copy()
        source.thumbnail((1920, 1200), Image.Resampling.LANCZOS)
        return source

    def _build_backdrop_file(self) -> Path:
        backdrop = build_glass_backdrop(
            self._practical_image(),
            opacity=float(self.opacity_var.get()) / 100,
            blur_radius=round(float(self.blur_var.get())),
            content_focus=self.content_focus_var.get(),
        )
        return self.backdrop_store.write(backdrop)

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
            result = self.session.apply(
                targets,
                self.palette,
                self.corner_var.get(),
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
        self._last_corner = self.corner_var.get()
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
            self.external_overlay.sync()
        except Exception as error:
            self._set_status(f"资源管理器外部层同步失败：{error}", "error")
            try:
                self.external_overlay.remove_all()
            except Exception:
                pass
        if self.external_overlay.event_driven:
            delay = 100
        elif self.external_overlay.needs_fast_sync:
            delay = 16
        elif self.external_overlay.has_overlays:
            delay = 100
        else:
            delay = 500
        self._overlay_job = self.after(delay, self._overlay_tick)

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
    app = WindowTintApp()
    app.mainloop()

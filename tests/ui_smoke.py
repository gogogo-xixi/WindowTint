from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from windowtint.app import WindowTintApp
from windowtint.theme import extract_palette


def sample_image() -> Image.Image:
    image = Image.new("RGB", (1400, 900), "#ccded7")
    draw = ImageDraw.Draw(image)
    draw.ellipse((650, -240, 1280, 390), fill="#e8b08b")
    draw.rounded_rectangle((90, 500, 660, 930), radius=110, fill="#568a79")
    draw.polygon(((780, 840), (1040, 370), (1320, 840)), fill="#315a55")
    draw.ellipse((280, 190, 560, 470), fill="#f1e9d3")
    return image


def main() -> None:
    app = WindowTintApp()
    app.geometry("1120x760+20+20")
    app.attributes("-topmost", True)
    app.resident_var.set(False)
    app.image = sample_image()
    app.palette = extract_palette(app.image)
    app.image_name = "glass-garden.jpg"
    app.image_label.configure(
        text=f"{app.image_name}\n{app.image.width} × {app.image.height} · 仅在本地处理"
    )
    app._schedule_preview()

    def capture() -> None:
        app.target_var.set("示例窗口 · 本地应用")
        app.target_combo.configure(values=("示例窗口 · 本地应用",))
        app.status_label.configure(text="示例窗口 · 仅作界面示意")
        app.update()
        app.after(150, save_capture)

    def save_capture() -> None:
        app.update_idletasks()
        x = app.winfo_rootx()
        y = app.winfo_rooty()
        width = app.winfo_width()
        height = app.winfo_height()
        result = ImageGrab.grab(bbox=(x, y, x + width, y + height))
        basic_output = ROOT / "build" / "windowtint-ui.png"
        result.save(basic_output)

        app.notebook.select(1)
        app.update_idletasks()
        advanced_result = ImageGrab.grab(bbox=(x, y, x + width, y + height))
        advanced_output = ROOT / "build" / "windowtint-advanced.png"
        advanced_result.save(advanced_output)

        print(basic_output)
        print(advanced_output)
        app.exit_app()

    app.after(900, capture)
    app.mainloop()


if __name__ == "__main__":
    main()

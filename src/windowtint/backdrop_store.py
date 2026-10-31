from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from PIL import Image


def default_runtime_dir() -> Path:
    return Path(tempfile.gettempdir()) / "WindowTint" / "runtime"


class BackdropStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_runtime_dir()
        self.path = self.root / "backdrop.png"

    def write(self, image: Image.Image) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"backdrop-{uuid.uuid4().hex}.png"
        image.save(self.path, format="PNG")
        return self.path

    def clear(self) -> None:
        if not self.root.exists():
            return
        for pattern in ("backdrop*.png", "backdrop*.tmp"):
            for path in self.root.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    pass

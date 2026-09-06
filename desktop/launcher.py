from __future__ import annotations

import threading
import time
import webbrowser

import pystray
import uvicorn
from PIL import Image, ImageDraw

from app.config import settings


def _icon() -> Image.Image:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="black")
    draw.rectangle((20, 20, 44, 44), fill="white")
    return image


def _serve() -> None:
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


def open_control_center(_: pystray.Icon | None = None, __=None) -> None:
    webbrowser.open(f"http://{settings.host}:{settings.port}/docs")


def main() -> None:
    server = threading.Thread(target=_serve, name="homeserver-api", daemon=True)
    server.start()
    time.sleep(0.8)
    tray = pystray.Icon(
        "homeserver",
        _icon(),
        "HomeServer",
        menu=pystray.Menu(
            pystray.MenuItem("Open HomeServer", open_control_center, default=True),
            pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
        ),
    )
    tray.run()


if __name__ == "__main__":
    main()

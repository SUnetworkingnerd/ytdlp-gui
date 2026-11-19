"""Regenerate square app icons from ytdlp_gui/assets/logo.png (needs Pillow).

The logo is wider than tall, so it is centred on a transparent square canvas.
Outputs: assets/icon.png (256px, used as the window icon), assets/icons/<n>x<n>.png
(Linux hicolor theme) and assets/icon.ico (Windows, multi-size).
"""
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "ytdlp_gui" / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256, 512]
MARGIN = 0.06  # fraction of the canvas left empty around the logo


def square(logo: Image.Image, size: int) -> Image.Image:
    inner = int(size * (1 - 2 * MARGIN))
    scale = inner / max(logo.size)
    resized = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
                          Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return canvas


def main() -> None:
    logo = Image.open(ASSETS / "logo.png").convert("RGBA")
    (ASSETS / "icons").mkdir(exist_ok=True)
    for size in SIZES:
        square(logo, size).save(ASSETS / "icons" / f"{size}x{size}.png")
    square(logo, 256).save(ASSETS / "icon.png")
    ico_sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    square(logo, 256).save(ASSETS / "icon.ico", format="ICO", sizes=ico_sizes)
    print("icons written to", ASSETS)


if __name__ == "__main__":
    main()

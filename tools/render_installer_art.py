"""Render the WiX installer bitmaps from the WhisperTray logo.

Dev-only tool; the release workflow consumes the committed BMP files and never
runs it. Requires Pillow (``python -m pip install pillow``).

    python tools/render_installer_art.py [--preview DIR]

WixUI layout constraints (100% scale, 1 dialog unit ~ 1.33 px):

* ``dialog.bmp`` (493x312) fills the Welcome and Finish dialogs. Their black
  text starts at x=180, so the brand panel stays left of ``PANEL_WIDTH``. The
  text side is flat ``DIALOG_FACE``: the Finish dialog's "launch" checkbox is
  an opaque control painted with the system button-face colour, so any other
  colour there shows up as a grey box.
* ``banner.bmp`` (493x58) sits behind the black title and description of every
  inner dialog, which end before x=406; the mark lives to the right of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - developer convenience
    sys.exit("Pillow is required: python -m pip install pillow")

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "whispertray-512.png"
OUT = ROOT / "installer"

SS = 4  # supersampling factor for anti-aliased shapes

INK_TOP = (0x2A, 0x2D, 0x34)
INK_BOTTOM = (0x1A, 0x1C, 0x21)
CORAL = (0xFF, 0x76, 0x5D)
CREAM = (0xFF, 0xF8, 0xEB)
MUTED = (0xAA, 0xA4, 0x9A)
DIALOG_FACE = (0xF0, 0xF0, 0xF0)  # Windows COLOR_BTNFACE
BANNER_FACE = (0xFF, 0xFF, 0xFF)

DIALOG_SIZE = (493, 312)
BANNER_SIZE = (493, 58)
PANEL_WIDTH = 164

# Mirrored speech envelope for the waveform motif; deterministic on purpose.
WAVE = (0.22, 0.38, 0.62, 0.44, 0.82, 1.0, 0.7, 1.0, 0.82, 0.44, 0.62, 0.38, 0.22)

FONT_DIR = Path("C:/Windows/Fonts")


def _font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in names:
        for path in (FONT_DIR / name, Path(name)):
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _scaled(box: tuple[float, ...]) -> tuple[int, ...]:
    return tuple(round(value * SS) for value in box)


def _vertical_gradient(size: tuple[int, int], top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    width, height = size
    column = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize(size)


def _glow(size: tuple[int, int], center: tuple[float, float], radius: float, color, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", size, (*color, 0))
    cx, cy = center
    ImageDraw.Draw(layer).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*color, alpha))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.45))


def _logo_tile(size: int, radius: float, logo_ratio: float = 0.8) -> Image.Image:
    """The app mark on a cream squircle, with a soft drop shadow (RGBA, SS scale)."""
    pad = round(size * 0.25)
    tile = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (pad, pad + size * 0.06, pad + size, pad + size * 1.06), radius, fill=(0, 0, 0, 120)
    )
    tile.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.07)))
    ImageDraw.Draw(tile).rounded_rectangle((pad, pad, pad + size, pad + size), radius, fill=(*CREAM, 255))
    mark_size = round(size * logo_ratio)
    mark = Image.open(LOGO).convert("RGBA").resize((mark_size, mark_size), Image.LANCZOS)
    offset = pad + (size - mark_size) // 2
    tile.alpha_composite(mark, (offset, offset))
    return tile


def _waveform(draw: ImageDraw.ImageDraw, center_x: float, center_y: float, bar: float, gap: float, height: float,
              fade_to=None) -> None:
    total = len(WAVE) * bar + (len(WAVE) - 1) * gap
    x = center_x - total / 2
    middle = (len(WAVE) - 1) / 2
    for index, level in enumerate(WAVE):
        h = max(bar, height * level)
        color = CORAL
        if fade_to is not None:
            t = abs(index - middle) / middle * 0.55
            color = tuple(round(a + (b - a) * t) for a, b in zip(CORAL, fade_to))
        draw.rounded_rectangle(_scaled((x, center_y - h / 2, x + bar, center_y + h / 2)), bar * SS / 2, fill=color)
        x += bar + gap


def render_dialog() -> Image.Image:
    width, height = DIALOG_SIZE
    canvas = Image.new("RGBA", (width * SS, height * SS), (*DIALOG_FACE, 255))
    panel = _vertical_gradient((PANEL_WIDTH * SS, height * SS), INK_TOP, INK_BOTTOM).convert("RGBA")
    panel.alpha_composite(_glow(panel.size, (PANEL_WIDTH * SS / 2, 104 * SS), 62 * SS, CORAL, 46))
    canvas.alpha_composite(panel)

    tile_size = 84 * SS
    tile = _logo_tile(tile_size, 20 * SS)
    canvas.alpha_composite(tile, ((PANEL_WIDTH * SS - tile.width) // 2, 104 * SS - tile.height // 2))

    draw = ImageDraw.Draw(canvas)
    _waveform(draw, PANEL_WIDTH / 2, 196, bar=4, gap=4, height=30, fade_to=INK_BOTTOM)
    # Thin coral seam where the brand panel meets the text side.
    draw.rectangle(_scaled((PANEL_WIDTH - 2, 0, PANEL_WIDTH, height)), fill=CORAL)

    image = canvas.convert("RGB").resize(DIALOG_SIZE, Image.LANCZOS)
    text = ImageDraw.Draw(image)
    wordmark = _font(("segoeuisb.ttf", "seguisb.ttf", "segoeuib.ttf"), 17)
    tagline = _font(("segoeui.ttf",), 11)
    center = (PANEL_WIDTH - 2) / 2
    text.text((center, 262), "WhisperTray", font=wordmark, fill=CREAM, anchor="ms")
    text.text((center, 281), "Desktop dictation", font=tagline, fill=MUTED, anchor="ms")
    return image


def _tab_mask(size: tuple[int, int], left: float, curve: float) -> Image.Image:
    """Right-corner tab whose left edge bulges into the banner as a soft arc."""
    width, height = size
    mask = Image.new("L", (width * SS, height * SS), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(_scaled((left, -height * 0.15, left + curve * 2, height * 1.15)), fill=255)
    draw.rectangle(_scaled((left + curve, 0, width, height)), fill=255)
    return mask


def render_banner() -> Image.Image:
    width, height = BANNER_SIZE
    canvas = Image.new("RGBA", (width * SS, height * SS), (*BANNER_FACE, 255))
    # Dark brand tab in the right corner, clear of the title/description text (x < 406).
    tab_left, curve = 428, 34
    seam = Image.new("RGBA", canvas.size, (*CORAL, 255))
    canvas.paste(seam, (0, 0), _tab_mask(BANNER_SIZE, tab_left - 2, curve + 1))
    ink = _vertical_gradient(canvas.size, INK_TOP, INK_BOTTOM).convert("RGBA")
    ink.alpha_composite(_glow(canvas.size, ((tab_left + 36) * SS, height * SS / 2), 26 * SS, CORAL, 55))
    canvas.paste(ink, (0, 0), _tab_mask(BANNER_SIZE, tab_left, curve))

    tile = _logo_tile(40 * SS, 10 * SS, logo_ratio=0.82)
    tile_x = (tab_left + 8 + (width - tab_left - 8) / 2) * SS - tile.width / 2
    canvas.alpha_composite(tile, (round(tile_x), (height * SS - tile.height) // 2))
    return canvas.convert("RGB").resize(BANNER_SIZE, Image.LANCZOS)


# ----------------------------------------------------------------- previews


def _button(draw, x, y, label, font, default=False):
    w, h = 75, 23
    draw.rectangle((x, y, x + w, y + h), fill=(0xFD, 0xFD, 0xFD), outline=(0x00, 0x78, 0xD4) if default else (0xAD,) * 3)
    draw.text((x + w / 2, y + h / 2), label, font=font, fill=(0, 0, 0), anchor="mm")


def _frame(body: Image.Image, title: str) -> Image.Image:
    """Wrap a 493x360 dialog client area in a simple Windows 11 style window."""
    normal = _font(("segoeui.ttf",), 12)
    window = Image.new("RGB", (body.width + 2, body.height + 33), (0xF3, 0xF3, 0xF3))
    draw = ImageDraw.Draw(window)
    draw.rectangle((0, 0, window.width - 1, window.height - 1), outline=(0x9A,) * 3)
    draw.text((12, 16), title, font=normal, fill=(0, 0, 0), anchor="lm")
    window.paste(body, (1, 32))
    return window


def _footer(image: Image.Image, buttons=("< Back", "Next >", "Cancel")) -> None:
    font = _font(("tahoma.ttf",), 11)
    draw = ImageDraw.Draw(image)
    draw.line((0, 312, 493, 312), fill=(0xA0,) * 3)
    draw.line((0, 313, 493, 313), fill=(0xFF,) * 3)
    for x, label in zip((240, 315, 405), buttons):
        _button(draw, x, 324, label, font, default=label.startswith("Next") or label in {"Install", "Finish"})


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for paragraph in text.split("\n"):
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
        line = ""
    return lines


def _text_block(draw, xy, text, font, width, leading=14):
    x, y = xy
    for line in _wrap(draw, text, font, width):
        draw.text((x, y), line, font=font, fill=(0, 0, 0))
        y += leading


def preview_welcome(dialog: Image.Image, title: str, description: str, checkbox: str | None = None,
                    optional: str | None = None) -> Image.Image:
    body = Image.new("RGB", (493, 360), DIALOG_FACE)
    body.paste(dialog, (0, 0))
    draw = ImageDraw.Draw(body)
    bigger = _font(("tahoma.ttf",), 16)
    normal = _font(("tahoma.ttf",), 11)
    _text_block(draw, (180, 27), title, bigger, 290, leading=20)
    _text_block(draw, (180, 94 if checkbox else 107), description, normal, 290)
    if optional:
        _text_block(draw, (180, 147), optional, normal, 290)
    if checkbox:
        # MSI paints check boxes opaquely with COLOR_BTNFACE.
        draw.rectangle((180, 253, 473, 306), fill=DIALOG_FACE)
        draw.rectangle((180, 255, 192, 267), fill=(0x00, 0x67, 0xC0))
        draw.line((182, 261, 185, 264, 190, 257), fill=(255, 255, 255), width=2)
        draw.text((198, 261), checkbox, font=normal, fill=(0, 0, 0), anchor="lm")
    _footer(body, ("< Back", "Finish", "Cancel") if checkbox else ("< Back", "Next >", "Cancel"))
    return _frame(body, "WhisperTray Setup")


def preview_inner(banner: Image.Image, title: str, description: str, draw_body) -> Image.Image:
    body = Image.new("RGB", (493, 360), DIALOG_FACE)
    body.paste(banner, (0, 0))
    draw = ImageDraw.Draw(body)
    title_font = _font(("tahomabd.ttf",), 12)
    normal = _font(("tahoma.ttf",), 11)
    draw.text((20, 8), title, font=title_font, fill=(0, 0, 0))
    draw.text((33, 31), description, font=normal, fill=(0, 0, 0))
    draw.line((0, 58, 493, 58), fill=(0xA0,) * 3)
    draw.line((0, 59, 493, 59), fill=(0xFF,) * 3)
    draw_body(draw, normal)
    _footer(body)
    return _frame(body, "WhisperTray Setup")


def _folder_body(draw, font):
    emphasized = _font(("tahomabd.ttf",), 11)
    draw.text((27, 80), "Install WhisperTray to:", font=font, fill=(0, 0, 0))
    draw.rectangle((27, 101, 453, 124), fill=(255, 255, 255), outline=(0x7A,) * 3)
    draw.text((31, 112), r"C:\Users\you\AppData\Local\Programs\KROU4\WhisperTray\\", font=font, fill=(0, 0, 0),
              anchor="lm")
    _button(draw, 27, 131, "Change...", font)
    draw.text((27, 176), "Shortcuts", font=emphasized, fill=(0, 0, 0))
    draw.rectangle((33, 198, 45, 210), fill=(0x00, 0x67, 0xC0))
    draw.line((35, 204, 38, 207, 43, 200), fill=(255, 255, 255), width=2)
    draw.text((51, 204), "Add a desktop shortcut", font=font, fill=(0, 0, 0), anchor="lm")
    draw.text((33, 224), "WhisperTray is always added to the Start menu.", font=font, fill=(0x55,) * 3)


def write_previews(directory: Path, dialog: Image.Image, banner: Image.Image) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    shots = {
        "welcome": preview_welcome(
            dialog,
            "Welcome to the WhisperTray Setup Wizard",
            "Setup will install WhisperTray, privacy-aware dictation for your desktop.\n"
            "Click Next to continue, or Cancel to exit.",
        ),
        "finish": preview_welcome(
            dialog,
            "Completed the WhisperTray Setup Wizard",
            "Click the Finish button to exit the Setup Wizard.",
            checkbox="Open WhisperTray now",
            optional="WhisperTray runs in the system tray. Look for the microphone icon next to the clock.",
        ),
        "folder": preview_inner(
            banner,
            "Install location",
            "Choose where to install WhisperTray and which shortcuts to add.",
            _folder_body,
        ),
    }
    paths = []
    for name, image in shots.items():
        path = directory / f"preview_{name}.png"
        image.resize((image.width * 2, image.height * 2), Image.LANCZOS).save(path)
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--preview", type=Path, help="also write PNG previews with sample WiX text here")
    args = parser.parse_args()
    dialog = render_dialog()
    banner = render_banner()
    args.output.mkdir(parents=True, exist_ok=True)
    dialog.save(args.output / "dialog.bmp")
    banner.save(args.output / "banner.bmp")
    if args.preview:
        for path in write_previews(args.preview, dialog, banner):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

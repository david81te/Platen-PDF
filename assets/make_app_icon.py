"""Turn assets/app_icon_source.png into assets/pdfstudio.ico.

Windows chooses the closest embedded size, so a .ico is an icon set rather than
one image. The source art is trimmed to its content, padded to a square and
resampled once per size, with a light sharpen on the small entries -- detailed
artwork goes soft below about 32px otherwise.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageEnhance

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "app_icon_source.png")
ICO = os.path.join(HERE, "pdfstudio.ico")
ICO_SIZES = (256, 128, 64, 48, 32, 24, 16)
MARGIN = 0.035           # breathing room so the art is not flush to the edge


def prepare(path: str) -> Image.Image:
    """Trim to the artwork, centre it on a transparent square."""
    art = Image.open(path).convert("RGBA")
    box = art.getbbox()
    if box:
        art = art.crop(box)
    side = int(max(art.size) * (1 + MARGIN * 2))
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    return square


def resize(square: Image.Image, size: int) -> Image.Image:
    out = square.resize((size, size), Image.LANCZOS)
    if size <= 48:
        out = ImageEnhance.Sharpness(out).enhance(1.35 if size <= 24 else 1.2)
    return out


def build() -> str:
    if not os.path.isfile(SOURCE):
        sys.exit("missing source artwork: " + SOURCE)
    square = prepare(SOURCE)
    layers = [resize(square, s) for s in ICO_SIZES]
    layers[0].save(ICO, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                   append_images=layers[1:])
    layers[0].save(os.path.join(HERE, "pdfstudio_256.png"))

    # Preview strip on a mid grey, so both light and dark edges are visible.
    strip = Image.new("RGB", (sum(s + 18 for s in ICO_SIZES) + 18, 300), (238, 241, 246))
    x = 18
    for size, layer in zip(ICO_SIZES, layers):
        strip.paste(layer, (x, 18 + (256 - size) // 2), layer)
        ImageDraw.Draw(strip).text((x, 286), "%dpx" % size, fill=(40, 52, 74))
        x += size + 18
    strip.save(os.path.join(HERE, "icon_sizes.png"))
    return ICO


if __name__ == "__main__":
    path = build()
    print("wrote %s (%d bytes)" % (path, os.path.getsize(path)))

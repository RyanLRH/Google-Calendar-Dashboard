"""
make_logo.py — regenerates every icon file from one definition.

Run it if you ever want to recolour the logo:
    python make_logo.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SS = 8          # supersampling factor for smooth edges
BASE = 64       # design grid

BG = "#171a21"
EDGE = "#343a48"
RAIL = "#2c313d"
BLUE = "#5b8dee"
GREEN = "#3ddc97"

# x, y, w, h, radius, fill, alpha   (all on the 64x64 grid)
SHAPES = [
    (12, 15, 4, 34, 2, RAIL, 255),        # the time rail
    (22, 15, 30, 9, 4.5, BLUE, 255),      # morning block
    (22, 27.5, 20, 9, 4.5, GREEN, 255),   # done block
    (22, 40, 26, 9, 4.5, BLUE, 115),      # upcoming block, faded
]


def render(size: int, transparent_bg=False) -> Image.Image:
    n = size * SS
    k = n / BASE

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if not transparent_bg:
        d.rounded_rectangle(
            [2 * k, 2 * k, 62 * k, 62 * k],
            radius=14 * k, fill=BG,
            outline=EDGE, width=max(1, int(1 * k)),
        )

    for x, y, w, h, r, fill, alpha in SHAPES:
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [x * k, y * k, (x + w) * k, (y + h) * k],
            radius=r * k, fill=fill,
        )
        if alpha < 255:
            a = layer.getchannel("A").point(lambda v: v * alpha // 255)
            layer.putalpha(a)
        img = Image.alpha_composite(img, layer)

    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]

    frames[-1].save(OUT / "icon.ico", format="ICO",
                    sizes=[(s, s) for s in sizes])
    for s, im in zip(sizes, frames):
        if s in (16, 32, 48, 256):
            im.save(OUT / f"logo_{s}.png")
    render(512).save(OUT / "logo.png")
    render(512, transparent_bg=True).save(OUT / "logo_transparent.png")

    # contact sheet so you can eyeball every size at once
    sheet = Image.new("RGBA", (sum(s + 16 for s in sizes) + 16, 288), (28, 30, 36, 255))
    x = 16
    for s, im in zip(sizes, frames):
        sheet.paste(im, (x, 256 - s), im)
        x += s + 16
    sheet.save(OUT / "logo_sizes.png")

    print("wrote icon.ico, logo.png, logo_transparent.png, logo_{16,32,48,256}.png")


if __name__ == "__main__":
    main()

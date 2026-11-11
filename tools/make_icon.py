"""
<summary>Draws the Pixelate application icon: a dark LED panel lighting up a pixel heart.</summary>
<remarks>Run with <c>.venv/bin/python tools/make_icon.py</c>. It writes
<c>pixelate/assets/pixelate.svg</c> plus one PNG per size the freedesktop icon theme
expects. Each PNG is drawn fresh at its own size rather than scaled, so the pixel
edges stay crisp, and sizes of 48 and above get a soft glow behind the lit pixels
to suggest the real panel. The window icon in <see cref="pixelate.gui.main"/> and
the desktop entry written by <c>tools/install-desktop.sh</c> both use these files.</remarks>
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "pixelate" / "assets"
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
GRID = 8

# An 8 by 8 heart. "#" is lit, "." is an unlit LED, "*" is the specular highlight.
HEART = (
    ".##..##.",
    "#*######",
    "########",
    "########",
    ".######.",
    "..####..",
    "...##...",
    "........",
)

BEZEL = (21, 23, 28)
BEZEL_EDGE = (46, 50, 60)
OFF_LED = (38, 42, 51)
HIGHLIGHT = (255, 214, 232)
# Row by row colour, hot pink at the top warming to orange at the point.
ROW_COLOURS = (
    (255, 59, 141),
    (255, 64, 130),
    (255, 76, 112),
    (255, 92, 92),
    (255, 112, 72),
    (255, 134, 52),
    (255, 159, 28),
    (255, 159, 28),
)


def lit_cells() -> list[tuple[int, int, tuple[int, int, int]]]:
    """
    <summary>Lists every lit LED as (column, row, colour).</summary>
    <returns>Cells in reading order; unlit cells are left out.</returns>
    """
    cells = []
    for row, line in enumerate(HEART):
        for col, ch in enumerate(line):
            if ch == "#":
                cells.append((col, row, ROW_COLOURS[row]))
            elif ch == "*":
                cells.append((col, row, HIGHLIGHT))
    return cells


def draw_png(size: int) -> Image.Image:
    """
    <summary>Renders the icon at one pixel size.</summary>
    <param name="size">Width and height in pixels.</param>
    <returns>An RGBA image with a transparent background outside the rounded bezel.</returns>
    <remarks>Below 48 px the gaps between LEDs would be under a pixel wide, so the
    small sizes drop the gaps and the glow and keep only the bezel and the heart.</remarks>
    """
    scale = 4  # draw big, then downsample for smooth corners
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = s * 0.18
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=BEZEL, outline=BEZEL_EDGE, width=max(1, s // 64))

    margin = s * 0.11
    inner = s - 2 * margin
    cell = inner / GRID
    small = size < 48
    gap = 0 if small else cell * 0.18

    def box(col: int, row: int) -> tuple[float, float, float, float]:
        """
        <summary>The pixel rectangle of one LED cell, inset by half the gap on each side.</summary>
        """
        x0 = margin + col * cell + gap / 2
        y0 = margin + row * cell + gap / 2
        return (x0, y0, x0 + cell - gap, y0 + cell - gap)

    if not small:
        for row in range(GRID):
            for col in range(GRID):
                d.rounded_rectangle(box(col, row), radius=cell * 0.12, fill=OFF_LED)

    leds = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ld = ImageDraw.Draw(leds)
    for col, row, colour in lit_cells():
        ld.rounded_rectangle(box(col, row), radius=0 if small else cell * 0.12, fill=colour + (255,))

    if not small:
        glow = leds.filter(ImageFilter.GaussianBlur(cell * 0.6))
        glow.putalpha(glow.getchannel("A").point(lambda a: int(a * 0.55)))
        img.alpha_composite(glow)
    img.alpha_composite(leds)

    return img.resize((size, size), Image.LANCZOS)


def write_svg(path: Path) -> None:
    """
    <summary>Writes a scalable version of the same icon as plain rect elements.</summary>
    <param name="path">Destination file; overwritten.</param>
    """
    s = 512
    margin = s * 0.11
    cell = (s - 2 * margin) / GRID
    gap = cell * 0.18
    r = cell * 0.12

    def rect(col: int, row: int, colour: tuple[int, int, int]) -> str:
        """
        <summary>One SVG rect element for an LED cell, matching <c>box</c> in the PNG renderer.</summary>
        """
        x = margin + col * cell + gap / 2
        y = margin + row * cell + gap / 2
        return (f'  <rect x="{x:.1f}" y="{y:.1f}" width="{cell - gap:.1f}" height="{cell - gap:.1f}" '
                f'rx="{r:.1f}" fill="rgb{colour}"/>')

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s}" height="{s}">',
        f'  <rect x="0" y="0" width="{s}" height="{s}" rx="{s * 0.18:.1f}" fill="rgb{BEZEL}" '
        f'stroke="rgb{BEZEL_EDGE}" stroke-width="8"/>',
    ]
    lines += [rect(c, rw, OFF_LED) for rw in range(GRID) for c in range(GRID)]
    lines += [rect(c, rw, colour) for c, rw, colour in lit_cells()]
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """<summary>Writes the SVG and every PNG size into <c>pixelate/assets</c>.</summary>"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    write_svg(ASSETS / "pixelate.svg")
    for size in SIZES:
        draw_png(size).save(ASSETS / f"pixelate-{size}.png", optimize=True)
    print(f"wrote {len(SIZES)} PNGs and pixelate.svg to {ASSETS}")


if __name__ == "__main__":
    main()

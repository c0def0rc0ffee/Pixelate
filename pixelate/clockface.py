"""
<summary>A round (analogue) clock face for the panel.</summary>
<remarks>The panel cannot keep an analogue clock itself, so the app renders one of these
every minute and sends it as a still image. While the app is closed the image freezes on
the last minute sent.</remarks>
"""

from __future__ import annotations

import datetime as dt
import math

from PIL import Image, ImageDraw

from .devices import SIZE

Color = tuple[int, int, int]
SS = 4  # supersample, then shrink, so the hands come out smooth


def render_round_clock(now: dt.datetime, hands: Color = (255, 255, 255), accent: Color = (220, 40, 40),
                       ticks: Color = (90, 90, 110), seconds: bool = False) -> Image.Image:
    """
    <summary>Draws a 32x32 analogue clock face for the given time.</summary>
    <param name="now">The time to show; only hour, minute and second are read.</param>
    <param name="hands">Colour of the hour and minute hands.</param>
    <param name="accent">Colour of the quarter hour ticks, the centre dot and the second hand.</param>
    <param name="ticks">Colour of the rim and the other hour ticks.</param>
    <param name="seconds">Draw a second hand. Only worth it when the face is re-sent every second.</param>
    <returns>An RGB image of the panel's size.</returns>
    <remarks>Drawn at four times the size and shrunk with LANCZOS so the thin hands stay
    smooth on 32 pixels.</remarks>
    """
    big = Image.new("RGB", (SIZE * SS, SIZE * SS))
    d = ImageDraw.Draw(big)
    c = SIZE * SS / 2
    r = c - 1.5 * SS

    d.ellipse([c - r, c - r, c + r, c + r], outline=ticks, width=SS)
    for k in range(12):
        a = math.radians(k * 30)
        inner = r - (3.2 if k % 3 == 0 else 1.8) * SS
        d.line([(c + inner * math.sin(a), c - inner * math.cos(a)), (c + r * math.sin(a), c - r * math.cos(a))],
               fill=accent if k % 3 == 0 else ticks, width=SS)

    def hand(angle_deg: float, length: float, width: float, colour: Color):
        """
        <summary>Draws one hand from the centre at a clockwise angle from twelve.</summary>
        """
        a = math.radians(angle_deg)
        d.line([(c, c), (c + length * math.sin(a), c - length * math.cos(a))], fill=colour, width=int(width * SS))

    hour = (now.hour % 12 + now.minute / 60) * 30
    minute = (now.minute + now.second / 60) * 6
    hand(hour, r * 0.5, 2.2, hands)
    hand(minute, r * 0.8, 1.6, hands)
    if seconds:
        hand(now.second * 6, r * 0.85, 0.8, accent)
    d.ellipse([c - 1.3 * SS, c - 1.3 * SS, c + 1.3 * SS, c + 1.3 * SS], fill=accent)
    return big.resize((SIZE, SIZE), Image.Resampling.LANCZOS)

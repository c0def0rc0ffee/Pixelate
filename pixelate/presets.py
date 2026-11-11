"""
<summary>Built in animations for a baby's room, and the GIF and flash safety helpers.</summary>
<remarks>
Soothing presets are slow (5 to 13 s loops), dim and mostly warm coloured.
Development presets use high contrast, faces, slow movement and simple
counting, the kinds of things infant development guides suggest for
visual tracking, contrast sensitivity and object permanence.

Every preset is generated in code (no image files) and must pass
`flashing_fraction()`: no meaningful area of the panel may flash more than
twice a second. That limit is stricter than the WCAG "three flashes" rule.
Sleepy presets must also stay dim and red; the tests hold every preset to both.

    python -m pixelate.presets OUT_DIR   # export every preset as a GIF

Each builder takes n, the number of frames in its loop, and returns that many 32x32 RGB
frames. Builders whose n defaults to None make a fixed sequence and ignore it.
</remarks>
"""

from __future__ import annotations

import colorsys
import io
import math
import random
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .devices import SIZE

TAU = 2 * math.pi
MAX_FRAMES = 64  # keeps GIFs under about 100 KB; the real link is slow (about 1.4 KB/s), not short of memory
LONG_STORY_FRAMES = 128  # allowed for a Preset that opts in with max_frames; still held under 100 KB by the tests
C = SIZE / 2  # centre, in pixel-centre coordinates

Color = tuple[int, int, int]
BLACK: Color = (0, 0, 0)


# --------------------------------------------------------------------------- helpers


def clamp01(v: float) -> float:
    """
    <summary>Clamp a number into 0 to 1.</summary>
    """
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def smoothstep(e0: float, e1: float, x: float) -> float:
    """
    <summary>Smooth 0 to 1 ramp as x goes from e0 to e1; e0 may be larger than e1 to ramp down.</summary>
    """
    t = clamp01((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def mix(a: Color, b: Color, t: float) -> Color:
    """
    <summary>Blend two colours: t of 0 gives a, 1 gives b.</summary>
    """
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def scale(c: Color, k: float) -> Color:
    """
    <summary>Multiply a colour's channels by k, capped at 255.</summary>
    """
    return tuple(int(round(min(255, v * k))) for v in c)


def add(a: Color, b: Color) -> Color:
    """
    <summary>Add two colours channel by channel, capped at 255.</summary>
    """
    return tuple(min(255, a[i] + b[i]) for i in range(3))


def shade(fn: Callable[[float, float], Color]) -> Image.Image:
    """
    <summary>Build a frame by calling fn for every pixel.</summary>
    <param name="fn">Takes pixel centre coordinates (x + 0.5, y + 0.5) and returns a colour.</param>
    <returns>A new 32x32 RGB frame.</returns>
    <remarks>1024 Python calls a frame, which is why presets are built once and cached.</remarks>
    """
    img = Image.new("RGB", (SIZE, SIZE))
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            px[x, y] = fn(x + 0.5, y + 0.5)
    return img


def disc(x: float, y: float, cx: float, cy: float, r: float) -> float:
    """
    <summary>Anti aliased disc coverage at one point, from 0 to 1.</summary>
    <param name="x">Point x.</param>
    <param name="y">Point y.</param>
    <param name="cx">Disc centre x.</param>
    <param name="cy">Disc centre y.</param>
    <param name="r">Radius; the edge is softened over one pixel.</param>
    """
    return smoothstep(r + 0.5, r - 0.5, math.hypot(x - cx, y - cy))


def glow_dot(img: Image.Image, x: float, y: float, color: Color, halo: float = 0.3):
    """
    <summary>Add a bright pixel with a faint halo onto the frame, in place.</summary>
    <param name="img">The frame to draw on.</param>
    <param name="x">Pixel centre x; rounded to the nearest pixel.</param>
    <param name="y">Pixel centre y.</param>
    <param name="color">The dot's colour, added to what is there.</param>
    <param name="halo">Strength of the four neighbours, as a fraction of the colour.</param>
    <remarks>Parts that fall off the panel are skipped.</remarks>
    """
    px = img.load()
    ix, iy = int(round(x - 0.5)), int(round(y - 0.5))
    for dx, dy, k in [(0, 0, 1)] + [(d[0], d[1], halo) for d in ((1, 0), (-1, 0), (0, 1), (0, -1))]:
        if 0 <= ix + dx < SIZE and 0 <= iy + dy < SIZE:
            px[ix + dx, iy + dy] = add(px[ix + dx, iy + dy], scale(color, k))


def stamp(img: Image.Image, rows: list[str], x: int, y: int, color: Color, additive: bool = False):
    """
    <summary>Draw a small bitmap given as text rows, where # marks a lit pixel.</summary>
    <param name="img">The frame to draw on, in place.</param>
    <param name="rows">Rows of the bitmap, top first.</param>
    <param name="x">Left edge; parts off the panel are clipped.</param>
    <param name="y">Top edge.</param>
    <param name="color">The colour of lit pixels.</param>
    <param name="additive">Add to the frame instead of replacing, for fades over a background.</param>
    """
    px = img.load()
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch == "#" and 0 <= x + i < SIZE and 0 <= y + j < SIZE:
                px[x + i, y + j] = add(px[x + i, y + j], color) if additive else color


def crossfade_sequence(cards: list[Image.Image], hold: int, fade: int) -> list[Image.Image]:
    """
    <summary>Show each card for hold frames, then blend into the next over fade frames.</summary>
    <param name="cards">The still frames, in order; the last blends back into the first.</param>
    <param name="hold">Frames each card stays fully shown.</param>
    <param name="fade">Frames spent blending into the next card.</param>
    <returns>The whole loop.</returns>
    """
    frames = []
    for i, card in enumerate(cards):
        nxt = cards[(i + 1) % len(cards)]
        frames += [card] * hold
        frames += [Image.blend(card, nxt, (k + 1) / (fade + 1)) for k in range(fade)]
    return frames


def fade_through_black(cards: list[Image.Image], hold: int, fade: int) -> list[Image.Image]:
    """
    <summary>Show each card for hold frames, fading out to black and in again between them.</summary>
    <param name="cards">The still frames, in order.</param>
    <param name="hold">Frames each card stays fully shown.</param>
    <param name="fade">Frames for each fade in and each fade out.</param>
    <returns>The whole loop.</returns>
    """
    black = Image.new("RGB", (SIZE, SIZE))
    frames = []
    for card in cards:
        frames += [Image.blend(black, card, (k + 1) / (fade + 1)) for k in range(fade)]
        frames += [card] * hold
        frames += [Image.blend(card, black, (k + 1) / (fade + 1)) for k in range(fade)]
    return frames


def vertical_gradient(top: Color, bottom: Color) -> Callable[[float], Color]:
    """
    <summary>A function giving the colour at height y, from top at y 0 to bottom at y 32.</summary>
    """
    return lambda y: mix(top, bottom, y / SIZE)


# --------------------------------------------------------------------------- soothing


def breathing_glow(n=40):
    """
    <summary>Soothing: an amber glow that swells and shrinks once per loop.</summary>
    """
    amber = (255, 120, 30)
    frames = []
    for i in range(n):
        s = (1 - math.cos(TAU * i / n)) / 2
        r, k = 4 + 8 * s, 0.15 + 0.5 * s
        frames.append(shade(lambda x, y: scale(amber, k * smoothstep(r, r * 0.3, math.hypot(x - C, y - C)))))
    return frames


def starry_night(n=48):
    """
    <summary>Soothing: a crescent moon over hills, with sixteen twinkling stars.</summary>
    <remarks>The stars are placed with a fixed seed and kept clear of the moon and of each
    other, so the picture is the same every run. <see cref="night_garden"/> copies this scene.</remarks>
    """
    rng = random.Random(7)
    stars = []
    while len(stars) < 16:
        x, y = rng.randrange(1, 31), rng.randrange(1, 25)
        if math.hypot(x - 22, y - 9) > 8 and all(abs(x - s[0]) + abs(y - s[1]) > 3 for s in stars):
            stars.append((x, y, rng.random(), rng.choice((1, 2))))
    sky = vertical_gradient((2, 3, 16), (6, 8, 28))

    def background(x, y):
        """
        <summary>The still scene: hills, then the crescent moon over the sky gradient.</summary>
        """
        if y > 27.5 + 1.5 * math.sin(x / 5):
            return (3, 9, 6)  # hills
        m = disc(x, y, 22, 9, 5) * (1 - disc(x, y, 24.5, 7.5, 4.6))
        return mix(sky(y), (200, 180, 110), m)

    base = shade(background)
    frames = []
    for i in range(n):
        t = i / n
        img = base.copy()
        for x, y, phase, speed in stars:
            b = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(TAU * (speed * t + phase)))
            glow_dot(img, x + 0.5, y + 0.5, scale((210, 200, 160), b), halo=0.15)
        frames.append(img)
    return frames


def ocean_waves(n=48):
    """
    <summary>Soothing: rolling waves with a foam line under a night sky.</summary>
    """
    sky = vertical_gradient((4, 6, 22), (14, 18, 40))
    frames = []
    for i in range(n):
        t = i / n

        def px(x, y):
            """
            <summary>Colour of one pixel: sky above the wave line, foam on it, banded water below.</summary>
            """
            h = 13 + 2.2 * math.sin(TAU * (x / 32 + t)) + 0.9 * math.sin(TAU * (2 * x / 32 - 2 * t))
            if y < h - 0.6:
                return sky(y)
            if y < h + 0.6:
                return (90, 130, 150)  # foam line
            depth = (y - h) / (SIZE - h)
            band = 0.18 * math.sin(TAU * (y / 10 - t) + x / 6)
            return scale(mix((20, 90, 140), (4, 20, 50), depth), 0.8 + band)

        frames.append(shade(px))
    return frames


def lava_lamp(n=64):
    """
    <summary>Soothing: three warm blobs that merge and drift apart, drawn as a metaball field.</summary>
    """
    blobs = [(7, 8, 1, 1, 0.0, 0.3, 4.5), (6, 10, 1, 2, 1.7, 2.1, 4.0), (5, 10, 2, 1, 3.1, 0.9, 3.5)]
    frames = []
    for i in range(n):
        t = i / n
        centres = [
            (C + ax * math.sin(TAU * kx * t + px), C + ay * math.sin(TAU * ky * t + py), r * r)
            for ax, ay, kx, ky, px, py, r in blobs
        ]

        def px(x, y):
            """
            <summary>Colour of one pixel from the summed blob field.</summary>
            """
            f = sum(r2 / ((x - cx) ** 2 + (y - cy) ** 2 + 0.5) for cx, cy, r2 in centres)
            hot = mix((200, 50, 15), (255, 150, 40), smoothstep(1.3, 3, f))
            return mix((30, 4, 24), hot, smoothstep(0.8, 1.3, f))

        frames.append(shade(px))
    return frames


Z_GLYPH = ["####", "..#.", ".#..", "####"]


def sleepy_moon(n=48):
    """
    <summary>Soothing: a sleeping moon face bobbing gently, with little z's drifting up.</summary>
    """
    sky = vertical_gradient((2, 3, 16), (8, 8, 30))
    eyes = [(-5, -1), (-4, 0), (-3, 0), (-2, -1), (2, -1), (3, 0), (4, 0), (5, -1)]
    smile = [(-2, 3), (-1, 4), (0, 4), (1, 4), (2, 3)]
    frames = []
    for i in range(n):
        t = i / n
        cx, cy = 13, 17 + math.sin(TAU * t)
        img = shade(lambda x, y: mix(sky(y), (170, 145, 70), disc(x, y, cx, cy, 9)))
        px = img.load()
        for dx, dy in eyes + smile:
            px[cx + dx, int(round(cy - 0.5)) + dy] = (70, 55, 25)
        for phase in (0, 0.5):
            p = (t + phase) % 1
            stamp(img, Z_GLYPH, round(23 + 4 * p), round(12 - 10 * p), scale((150, 160, 220), math.sin(math.pi * p)), additive=True)
        frames.append(img)
    return frames


def fireflies(n=48):
    """
    <summary>Soothing: seven fireflies drifting over grass, each glowing in its own time.</summary>
    <remarks>Fixed seed 3. <see cref="night_garden"/> and <see cref="fireworks_to_fireflies"/>
    use the same seed and drift so the flies match.</remarks>
    """
    rng = random.Random(3)
    flies = [
        (rng.uniform(5, 27), rng.uniform(5, 22), rng.uniform(2, 5), rng.uniform(1, 3),
         rng.choice((1, 2)), rng.choice((1, 2)), rng.random() * TAU, rng.random() * TAU, rng.random())
        for _ in range(7)
    ]
    sky = vertical_gradient((0, 2, 6), (0, 7, 4))
    base = shade(lambda x, y: (6, 32, 10) if y > 29 - (int(x) * 7 % 3) else sky(y))
    frames = []
    for i in range(n):
        t = i / n
        img = base.copy()
        for x0, y0, ax, ay, kx, ky, px, py, pp in flies:
            b = smoothstep(0.2, 1, 0.5 + 0.5 * math.sin(TAU * (t + pp)))
            x, y = x0 + ax * math.sin(TAU * kx * t + px), y0 + ay * math.sin(TAU * ky * t + py)
            glow_dot(img, x, y, scale((200, 235, 70), b))
        frames.append(img)
    return frames


def night_garden(n=64):
    """
    <summary>Starry night, Fireflies and Fireworks combined into one story: the moon and
    stars alone, fireflies arrive, fireworks send them scattering, the fireworks stop and
    the fireflies slowly drift back.</summary>
    <param name="n">Frames in the loop; 64 at 4 fps gives the 16 second story below.</param>
    <returns>RGB frames.</returns>
    <remarks>The sky, crescent moon, hills and stars are drawn exactly as in
    <see cref="starry_night"/>, the flies as in <see cref="fireflies"/> (same seed, colour
    and drift) and the shells as in <see cref="fireworks"/> (same colours, spark count and
    burst size), launched over the left half so they clear the moon. Timeline as a fraction
    of the loop: moon and stars alone to 0.12, fireflies drift in from the right until 0.30,
    scatter upward from 0.34 as the first rocket rises, three shells go up between 0.38 and
    0.72, the fireflies glide back from 0.70 to 0.90 and doze off (fade) in the last few
    frames so the loop seam is clean. Star twinkle and firefly drift run at the originals'
    pace even though this loop is longer. At 4 fps nothing can exceed the flash limit.</remarks>
    """
    rng = random.Random(7)
    stars = []
    while len(stars) < 16:
        x, y = rng.randrange(1, 31), rng.randrange(1, 25)
        if math.hypot(x - 22, y - 9) > 8 and all(abs(x - s[0]) + abs(y - s[1]) > 3 for s in stars):
            stars.append((x, y, rng.random(), rng.choice((1, 2))))
    sky = vertical_gradient((2, 3, 16), (6, 8, 28))

    def background(x, y):
        """
        <summary>The still scene, drawn exactly as in <see cref="starry_night"/>.</summary>
        """
        if y > 27.5 + 1.5 * math.sin(x / 5):
            return (3, 9, 6)  # hills
        m = disc(x, y, 22, 9, 5) * (1 - disc(x, y, 24.5, 7.5, 4.6))
        return mix(sky(y), (200, 180, 110), m)

    base = shade(background)
    rng = random.Random(3)
    flies = [
        (rng.uniform(5, 27), rng.uniform(5, 22), rng.uniform(2, 5), rng.uniform(1, 3),
         rng.choice((1, 2)), rng.choice((1, 2)), rng.random() * TAU, rng.random() * TAU, rng.random(),
         rng.uniform(-0.03, 0.03))
        for _ in range(7)
    ]
    shells = [(8, 11, 0.38, (255, 80, 80)), (14, 6, 0.45, (120, 160, 255)), (6, 7, 0.52, (255, 220, 60))]
    frames = []
    for i in range(n):
        t = i / n
        img = base.copy()
        for x, y, phase, speed in stars:
            b = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(TAU * (speed * 2 * t + phase)))
            glow_dot(img, x + 0.5, y + 0.5, scale((210, 200, 160), b), halo=0.15)
        for cx, cy, launch, col in shells:
            p = t - launch
            if 0 <= p < 0.06:  # rising
                glow_dot(img, cx, 31 - (31 - cy) * (p / 0.06), (200, 170, 120), halo=0.2)
            elif 0.06 <= p < 0.20:  # bursting
                q = (p - 0.06) / 0.14
                r, fade = 1 + 11 * (1 - (1 - q) ** 2), (1 - q) ** 1.5
                for k in range(12):
                    a = TAU * k / 12
                    glow_dot(img, cx + r * math.cos(a), cy + r * math.sin(a) + 6 * q * q, scale(col, fade), halo=0.3)
        tf = 3 * t  # the Fireflies loop is about a third as long as this one
        for x0, y0, ax, ay, kx, ky, p1, p2, pp, jit in flies:
            arrive = smoothstep(0.12 + jit, 0.30 + jit, t)
            away = smoothstep(0.34 + jit, 0.46 + jit, t) * (1 - smoothstep(0.70 + jit, 0.90 + jit, t))
            doze = smoothstep(0.94, 1.0, t)
            x = x0 + ax * math.sin(TAU * kx * tf + p1) + (1 - arrive) * 28 + away * 12
            y = y0 + ay * math.sin(TAU * ky * tf + p2) - away * 26
            b = smoothstep(0.2, 1, 0.5 + 0.5 * math.sin(TAU * (tf + pp))) * arrive * (1 - away) * (1 - doze)
            glow_dot(img, x, y, scale((200, 235, 70), b))
        frames.append(img)
    return frames


def fireworks_to_fireflies(n=120):
    """
    <summary>A fireworks show over the Fireflies garden, then the fireflies come up out of
    the grass and glow until they doze off.</summary>
    <param name="n">Frames in the loop; 120 at 4 fps gives the 30 second story below.</param>
    <returns>RGB frames.</returns>
    <remarks>The sky and grass are drawn as in <see cref="fireflies"/>, the shells as in
    <see cref="night_garden"/> (same colours, spark count, burst size, 1 s rise and 2.25 s
    burst) and the flies with the same seed, colour and drift as <see cref="fireflies"/>.
    Timeline in seconds, so the pace stays the same if n changes: seven shells go up 1.8 s
    apart from 0 s, with two together as the finale, all gone by about 14 s; the fireflies
    rise from the grass and brighten between 13.5 and 18 s, glow until 27.5 s and fade out
    by 30 s so the loop seam drops back to a dark sky before the next rocket. Needs
    LONG_STORY_FRAMES; the GIF is about 21 KB because most frames are dark. At 4 fps
    nothing can exceed the flash limit.</remarks>
    """
    fps, length = 4, n / 4
    sky = vertical_gradient((0, 2, 6), (0, 7, 4))
    base = shade(lambda x, y: (6, 32, 10) if y > 29 - (int(x) * 7 % 3) else sky(y))
    rng = random.Random(3)
    flies = [
        (rng.uniform(5, 27), rng.uniform(5, 22), rng.uniform(2, 5), rng.uniform(1, 3),
         rng.choice((1, 2)), rng.choice((1, 2)), rng.random() * TAU, rng.random() * TAU, rng.random(),
         rng.uniform(-0.5, 0.5))
        for _ in range(7)
    ]
    red, blue, green, gold = (255, 80, 80), (120, 160, 255), (80, 255, 120), (255, 220, 60)
    shells = [(10, 10, 0.0, red), (22, 7, 1.8, blue), (15, 12, 3.6, green), (20, 9, 5.4, gold),
              (8, 7, 7.2, blue), (24, 11, 9.0, red), (10, 9, 10.8, gold), (22, 9, 10.8, green)]
    frames = []
    for i in range(n):
        s = i / fps  # seconds into the loop
        img = base.copy()
        for cx, cy, launch, col in shells:
            p = s - launch
            if 0 <= p < 1.0:  # rising
                glow_dot(img, cx, 31 - (31 - cy) * p, (200, 170, 120), halo=0.2)
            elif 1.0 <= p < 3.25:  # bursting
                q = (p - 1.0) / 2.25
                r, fade = 1 + 11 * (1 - (1 - q) ** 2), (1 - q) ** 1.5
                for k in range(12):
                    a = TAU * k / 12
                    glow_dot(img, cx + r * math.cos(a), cy + r * math.sin(a) + 6 * q * q, scale(col, fade), halo=0.3)
        tf = s / 6  # the Fireflies loop lasts 6 seconds
        for x0, y0, ax, ay, kx, ky, p1, p2, pp, jit in flies:
            arrive = smoothstep(13.5 + jit, 18 + jit, s)
            doze = smoothstep(length - 2.5, length, s)
            x = x0 + ax * math.sin(TAU * kx * tf + p1)
            y = y0 + ay * math.sin(TAU * ky * tf + p2) + (1 - arrive) * (29 - y0)
            b = smoothstep(0.2, 1, 0.5 + 0.5 * math.sin(TAU * (tf + pp))) * arrive * (1 - doze)
            glow_dot(img, x, y, scale((200, 235, 70), b))
        frames.append(img)
    return frames


FISH = ["...###..", "#.#####.", "########", "#.#####.", "...###.."]


def fish_tank(n=64):
    """
    <summary>Soothing: two fish swimming past in opposite directions, with seaweed and bubbles.</summary>
    """
    water = vertical_gradient((6, 40, 90), (2, 14, 40))
    base = shade(lambda x, y: (45, 38, 20) if y > 30 else water(y))
    frames = []
    for i in range(n):
        t = i / n
        img = base.copy()
        px = img.load()
        for sx in (5, 26):  # swaying seaweed
            for y in range(18, 30):
                x = sx + round(1.2 * math.sin(TAU * t + y / 3))
                px[x, y] = (15, 80, 30)
        for bx, k, ph in ((8, 1, 0.0), (20, 2, 0.3), (14, 1, 0.6)):
            p = (k * t + ph) % 1
            glow_dot(img, bx + 0.6 * math.sin(TAU * 3 * p), 29.5 - 30 * p, (60, 90, 120), halo=0)
        x1 = round(-9 + 41 * t)
        stamp(img, FISH, x1, 9, (200, 100, 25))
        if 0 <= x1 + 5 < SIZE:
            px[x1 + 5, 10] = BLACK  # eye
        x2 = round(32 - 41 * ((t + 0.5) % 1))
        stamp(img, [row[::-1] for row in FISH], x2, 19, (190, 165, 40))
        if 0 <= x2 + 2 < SIZE:
            px[x2 + 2, 20] = BLACK
        frames.append(img)
    return frames


def colour_drift(n=60):
    """
    <summary>Soothing: dim rainbow colours shifting slowly, with nothing to follow.</summary>
    """
    frames = []
    for i in range(n):
        t = i / n
        frames.append(shade(lambda x, y: tuple(
            int(v * 255) for v in colorsys.hsv_to_rgb((t + (x + y) / 96) % 1, 0.7, 0.3))))
    return frames


# --------------------------------------------------------------------------- development

WHITE: Color = (230, 230, 230)


def contrast_cards(n=None):
    """
    <summary>Development: bullseye, checkerboard, stripes and squares in black and white, crossfading.</summary>
    """
    bullseye = shade(lambda x, y: WHITE if int(math.hypot(x - C, y - C) / 4) % 2 == 0 else BLACK)
    checker = shade(lambda x, y: WHITE if (int(x) // 8 + int(y) // 8) % 2 == 0 else BLACK)
    stripes = shade(lambda x, y: WHITE if int(x + y) // 8 % 2 == 0 else BLACK)
    squares = shade(lambda x, y: WHITE if int(max(abs(x - C), abs(y - C)) / 4) % 2 == 0 else BLACK)
    return crossfade_sequence([bullseye, checker, stripes, squares], hold=10, fade=6)


SMILE = [(11, 20), (12, 21), (13, 22), (14, 22), (15, 22), (16, 22), (17, 22), (18, 22), (19, 21), (20, 20)]


def happy_face(n=24):
    """
    <summary>Development: a white face that blinks once and changes its smile to an "oh!".</summary>
    """
    face = shade(lambda x, y: scale(WHITE, disc(x, y, C, C, 13)))
    frames = []
    for i in range(n):
        img = face.copy()
        d = ImageDraw.Draw(img)
        blink = i in (16, 17)
        for ex in (11, 20):
            if blink:
                d.line([(ex - 1, 13), (ex + 1, 13)], fill=BLACK)
            else:
                d.rectangle([ex - 1, 12, ex + 1, 14], fill=BLACK)
        if i < n // 2:
            d.point(SMILE, fill=BLACK)
        else:
            d.ellipse([14, 19, 17, 22], outline=BLACK)  # "oh!"
        frames.append(img)
    return frames


def tracking_ball(n=64):
    """
    <summary>Development: a red ball gliding from side to side for eye tracking.</summary>
    """
    frames = []
    for i in range(n):
        bx = C - 11 * math.cos(TAU * i / n)
        frames.append(shade(lambda x, y: scale((230, 30, 30), disc(x, y, bx, C, 3.5))))
    return frames


def _shape_mask(draw_fn) -> Image.Image:
    """
    <summary>Draw a shape at four times the size and shrink it to a soft edged mask.</summary>
    <param name="draw_fn">Called with an ImageDraw and the scale factor 4; it draws in white.</param>
    <returns>A 32x32 greyscale mask.</returns>
    """
    big = Image.new("L", (SIZE * 4, SIZE * 4))
    draw_fn(ImageDraw.Draw(big), 4)
    return big.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def _star(d, s):
    """
    <summary>Draws a five pointed star at scale s, for <see cref="_shape_mask"/>.</summary>
    """
    pts = []
    for k in range(10):
        r = 13 if k % 2 == 0 else 5.5
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(((C + r * math.cos(a)) * s, (C + 1 + r * math.sin(a)) * s))
    d.polygon(pts, fill=255)


def shapes_and_colours(n=None):
    """
    <summary>Development: a red circle, blue square, yellow triangle and green star in turn.</summary>
    """
    shapes = [
        (lambda d, s: d.ellipse([5 * s, 5 * s, 27 * s, 27 * s], fill=255), (220, 30, 30)),
        (lambda d, s: d.rectangle([6 * s, 6 * s, 26 * s, 26 * s], fill=255), (30, 80, 230)),
        (lambda d, s: d.polygon([(16 * s, 4 * s), (28 * s, 27 * s), (4 * s, 27 * s)], fill=255), (240, 200, 20)),
        (_star, (30, 180, 60)),
    ]
    cards = []
    for draw_fn, colour in shapes:
        cards.append(Image.composite(Image.new("RGB", (SIZE, SIZE), colour), Image.new("RGB", (SIZE, SIZE)),
                                     _shape_mask(draw_fn)))
    return crossfade_sequence(cards, hold=10, fade=6)


DIGITS = {
    1: [".#.", "##.", ".#.", ".#.", "###"],
    2: ["##.", "..#", ".#.", "#..", "###"],
    3: ["##.", "..#", ".#.", "..#", "##."],
    4: ["#.#", "#.#", "###", "..#", "..#"],
    5: ["###", "#..", "##.", "..#", "##."],
}
DOT_COLOURS = [(230, 40, 40), (240, 150, 20), (230, 210, 30), (40, 190, 60), (40, 110, 240)]


def counting(n=None):
    """
    <summary>Development: the digits 1 to 5, each with that many coloured dots, then a fade out.</summary>
    """
    def card(count, fade=1.0):
        """
        <summary>One counting card: the digit and that many dots, at the given fade.</summary>
        <param name="count">1 to 5.</param>
        <param name="fade">Brightness from 0 to 1, for the fade out at the end.</param>
        """
        img = Image.new("RGB", (SIZE, SIZE))
        big = ["".join(ch * 2 for ch in r) for r in DIGITS[count] for _ in (0, 1)]  # 2x scale
        stamp(img, big, 13, 3, scale(WHITE, fade))
        dots = shade(lambda x, y: max(
            ((scale(DOT_COLOURS[k], fade * disc(x, y, 4 + 6 * k, 22, 2.3))) for k in range(count)),
            key=sum, default=BLACK))
        return Image.composite(img, dots, img.convert("L").point(lambda v: 255 if v else 0))

    frames = []
    for count in range(1, 6):
        frames += [card(count)] * 6
    frames += [card(5)] * 3
    frames += [card(5, 1 - (k + 1) / 7) for k in range(6)]
    frames += [Image.new("RGB", (SIZE, SIZE))] * 3
    return frames


def peekaboo_ball(n=64):
    """
    <summary>Development: a ball rolling behind a wall and out the other side.</summary>
    """
    frames = []
    for i in range(n):
        bx = C - 12 * math.cos(TAU * i / n)
        img = shade(lambda x, y: scale((240, 180, 30), disc(x, y, bx, C, 3.5)))
        ImageDraw.Draw(img).rounded_rectangle([11, 8, 20, 23], radius=2, fill=(80, 55, 130))
        frames.append(img)
    return frames


# --------------------------------------------------------------------------- baby, 0 to 3 months
#
# Tuned for the first three months. At this age babies see bold black-and-white
# and red best, and large shapes better than detail. They are just starting to
# follow slow movement. So these presets use one huge shape at a time, long
# holds, gentle fades, and very slow motion. Later age ranges get their own
# category constant and collection as they are needed.

BABY_0_3 = "Baby, 0 to 3 months"
"""<summary>Category name for the newborn collection; the default collection in the app.</summary>"""

RED: Color = (220, 20, 20)


def _on_black(mask: Image.Image, colour: Color) -> Image.Image:
    """
    <summary>Fill a mask with one colour on black.</summary>
    """
    return Image.composite(Image.new("RGB", (SIZE, SIZE), colour), Image.new("RGB", (SIZE, SIZE)), mask)


def _heart(d, s):
    """
    <summary>Draws a heart at scale s, for <see cref="_shape_mask"/>.</summary>
    """
    d.ellipse([3 * s, 5 * s, 17 * s, 19 * s], fill=255)
    d.ellipse([15 * s, 5 * s, 29 * s, 19 * s], fill=255)
    d.polygon([(4 * s, 15 * s), (28 * s, 15 * s), (16 * s, 29 * s)], fill=255)


def bold_shapes(n=None):
    """
    <summary>Baby: one huge shape at a time (circle, square, triangle, heart), fading through black.</summary>
    <remarks>Made for 2 fps: each shape holds 4 s with a 1 s fade either side.</remarks>
    """
    cards = [
        _on_black(_shape_mask(lambda d, s: d.ellipse([2 * s, 2 * s, 30 * s, 30 * s], fill=255)), WHITE),
        _on_black(_shape_mask(lambda d, s: d.rectangle([4 * s, 4 * s, 28 * s, 28 * s], fill=255)), RED),
        _on_black(_shape_mask(lambda d, s: d.polygon([(16 * s, 2 * s), (31 * s, 29 * s), (1 * s, 29 * s)], fill=255)),
                  WHITE),
        _on_black(_shape_mask(_heart), RED),
    ]
    return fade_through_black(cards, hold=8, fade=2)  # 4 s on, 1 s fade each way, at 2 fps


def big_checks(n=None):
    """
    <summary>Baby: four giant black and white squares that slowly swap over.</summary>
    """
    a = shade(lambda x, y: WHITE if (x < C) == (y < C) else BLACK)
    b = shade(lambda x, y: BLACK if (x < C) == (y < C) else WHITE)
    return crossfade_sequence([a, b], hold=10, fade=4)  # 5 s hold, 2 s swap at 2 fps


def red_target(n=32):
    """
    <summary>Baby: black and white rings with a red centre that glows up and down.</summary>
    """
    rings = shade(lambda x, y: WHITE if int(math.hypot(x - C, y - C) / 4) % 2 == 1 else BLACK)
    frames = []
    for i in range(n):
        k = 0.6 + 0.4 * (1 - math.cos(TAU * i / n)) / 2
        frames.append(shade(lambda x, y, k=k: mix(rings.getpixel((int(x), int(y))), scale(RED, k),
                                                     disc(x, y, C, C, 3.6))))
    return frames


def slow_red_ball(n=64):
    """
    <summary>Baby: a big red ball drifting slowly from side to side.</summary>
    """
    frames = []
    for i in range(n):
        bx = C - 8 * math.cos(TAU * i / n)
        frames.append(shade(lambda x, y: scale(RED, disc(x, y, bx, C, 7))))
    return frames


def ball_round_and_round(n=64):
    """
    <summary>Baby: a red ball on a slow circle, 16 seconds a lap.</summary>
    <remarks>Circular tracking comes in a little later than two months, so this one is slow.</remarks>
    """
    frames = []
    for i in range(n):
        a = TAU * i / n
        bx, by = C + 8 * math.cos(a), C + 8 * math.sin(a)
        frames.append(shade(lambda x, y: scale(RED, disc(x, y, bx, by, 6))))
    return frames


def big_face(n=48):
    """
    <summary>Baby: a huge simple white face that blinks once and smiles gently.</summary>
    <remarks>Newborns look at faces longest. The smile widens and relaxes but never goes flat.</remarks>
    """
    frames = []
    for i in range(n):
        t = i / n
        img = shade(lambda x, y: scale(WHITE, disc(x, y, C, C, 15)))
        d = ImageDraw.Draw(img)
        blink = 1 - smoothstep(0.0, 0.08, abs(t - 0.5))  # closed for a moment at the halfway point
        for ex in (10, 22):
            d.ellipse([ex - 2, 11, ex + 2, 15], fill=BLACK)
            if blink > 0.5:
                d.rectangle([ex - 3, 10, ex + 3, 13], fill=WHITE)
                d.line([(ex - 2, 13), (ex + 2, 13)], fill=BLACK)
        curve = 3.2 + 1.2 * math.sin(TAU * t)  # smile gently widens and relaxes, never flat
        pts = [(x, 21 + curve * (1 - ((x - C) / 7) ** 2)) for x in range(9, 24)]
        d.line(pts, fill=BLACK, width=2)
        frames.append(img)
    return frames


def red_glow(n=48):
    """
    <summary>Baby, sleepy: a soft red light breathing slowly.</summary>
    """
    frames = []
    for i in range(n):
        s = (1 - math.cos(TAU * i / n)) / 2
        r, k = 6 + 7 * s, 0.2 + 0.45 * s
        frames.append(shade(lambda x, y: scale((200, 25, 10), k * smoothstep(r, r * 0.35, math.hypot(x - C, y - C)))))
    return frames


# Sleepy ones: dim red/amber only (no blue), soft edges, nothing to track, and
# nothing faster than one slow breath.

EMBER: Color = (200, 25, 10)
AMBER: Color = (210, 120, 30)


def nightlight(n=None):
    """
    <summary>Baby, sleepy: a steady dim red with nothing moving.</summary>
    <remarks>Two identical frames rather than one, so it goes to the panel as a GIF like the others.</remarks>
    """
    return [Image.new("RGB", (SIZE, SIZE), (70, 8, 3))] * 2


def deep_breathing(n=60):
    """
    <summary>Baby, sleepy: the whole panel glows and dims at five breaths a minute.</summary>
    <remarks>Slower than a sleeping baby's breathing, to slow theirs down. Eased at the top
    and bottom of each breath.</remarks>
    """
    frames = []
    for i in range(n):
        s = (1 - math.cos(TAU * i / n)) / 2
        s = s * s * (3 - 2 * s)  # ease, so the turn at the top and bottom of each breath is soft
        k = 0.08 + 0.3 * s
        frames.append(shade(lambda x, y: scale(EMBER, k * smoothstep(24, 6, math.hypot(x - C, y - C)))))
    return frames


def slow_ripple(n=40):
    """
    <summary>Baby, sleepy: faint red rings spreading outward from the centre.</summary>
    """
    frames = []
    for i in range(n):
        t = i / n
        frames.append(shade(lambda x, y: scale(EMBER, (0.08 + 0.22 * (0.5 + 0.5 * math.cos(TAU * (
            math.hypot(x - C, y - C) / 12 - t)))) * smoothstep(23, 10, math.hypot(x - C, y - C)))))
    return frames


def sleepy_stars(n=64):
    """
    <summary>Baby, sleepy: nine dim amber stars fading in and out on a dark sky.</summary>
    """
    rng = random.Random(11)
    stars = [(rng.uniform(2, 30), rng.uniform(2, 30), rng.random()) for _ in range(9)]
    frames = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (SIZE, SIZE), (6, 2, 0))
        for x, y, ph in stars:
            b = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(TAU * (t + ph)))
            glow_dot(img, x, y, scale(AMBER, b), halo=0.4)
        frames.append(img)
    return frames


def setting_sun(n=60):
    """
    <summary>Baby, sleepy: a warm sun sinking below the horizon while the sky goes dark.</summary>
    <remarks>The loop ends dark, so the seam back to the start is a gentle rise rather than a jump.</remarks>
    """
    frames = []
    for i in range(n):
        t = i / n
        cy = 4 + 29 * t
        vis = smoothstep(0, 0.12, t) * (1 - smoothstep(0.55, 0.92, t))

        def px(x, y):
            """
            <summary>Colour of one pixel: ground below 25, otherwise the fading sky plus the sun.</summary>
            """
            if y > 25:
                return (8, 3, 1)
            sky = scale((120, 30, 8), 0.35 * vis * (1 - y / 26))
            sun = scale(AMBER, 0.75 * vis * smoothstep(8.5, 6, math.hypot(x - C, y - cy)))
            return add(sky, sun)

        frames.append(shade(px))
    return frames


def red_tide(n=48):
    """
    <summary>Baby, sleepy: a dim red glow washing slowly down the panel.</summary>
    """
    frames = []
    for i in range(n):
        t = i / n
        frames.append(shade(lambda x, y: scale(EMBER, 0.06 + 0.26 * (0.5 + 0.5 * math.sin(TAU * (y / SIZE - t))))))
    return frames


def embers(n=48):
    """
    <summary>Baby, sleepy: dull red embers slowly brightening and fading.</summary>
    """
    frames = []
    for i in range(n):
        t = i / n

        def px(x, y):
            """
            <summary>Colour of one pixel from two crossing sine waves.</summary>
            """
            a = math.sin(TAU * t + x / 6 + y / 9)
            b = math.sin(TAU * 2 * t - y / 5 + x / 11)
            v = (a + b + 2) / 4
            return mix((30, 3, 0), (150, 35, 8), v * v)

        frames.append(shade(px))
    return frames


# --------------------------------------------------------------------------- fun


def _palette_fire(v: float) -> Color:
    """
    <summary>Map heat from 0 to 1 to a fire colour: black, red, orange, then pale yellow.</summary>
    """
    v = clamp01(v)
    if v < 0.35:
        return mix(BLACK, (170, 20, 0), v / 0.35)
    if v < 0.7:
        return mix((170, 20, 0), (255, 140, 0), (v - 0.35) / 0.35)
    return mix((255, 140, 0), (255, 240, 170), (v - 0.7) / 0.3)


def fire(n=48):
    """
    <summary>Fun: flames licking up from the bottom, as a classic heat diffusion fire.</summary>
    <remarks>Runs 40 frames before recording so the first frame is already alight.</remarks>
    """
    rng = random.Random(5)
    heat = [[0.0] * SIZE for _ in range(SIZE + 2)]
    frames = []
    for i in range(n + 40):  # run in before recording so the loop starts hot
        for x in range(SIZE):
            heat[SIZE][x] = heat[SIZE + 1][x] = rng.uniform(0.6, 1.0)
        for y in range(SIZE):
            for x in range(SIZE):
                below = heat[y + 1]
                heat[y][x] = max(0.0, (below[max(0, x - 1)] + below[x] + below[min(SIZE - 1, x + 1)]
                                       + heat[y + 2][x]) / 4 - 0.035)
        if i >= 40:
            img = Image.new("RGB", (SIZE, SIZE))
            px = img.load()
            for y in range(SIZE):
                for x in range(SIZE):
                    px[x, y] = _palette_fire(heat[y][x] * 1.1)
            frames.append(img)
    return frames


def plasma(n=48):
    """
    <summary>Fun: a demo scene plasma, swirling through every colour.</summary>
    """
    frames = []
    for i in range(n):
        t = TAU * i / n

        def px(x, y):
            """
            <summary>Colour of one pixel from four summed sine waves mapped to hue.</summary>
            """
            v = (math.sin(x / 4 + t) + math.sin((y + 3 * math.sin(t)) / 5) +
                 math.sin((x + y) / 6 + 2 * t) + math.sin(math.hypot(x - C, y - C) / 4 - t)) / 4
            return tuple(int(255 * c) for c in colorsys.hsv_to_rgb((v + 1) / 2, 0.85, 0.85))

        frames.append(shade(px))
    return frames


def code_rain(n=48):
    """
    <summary>Fun: green glyph trails falling down the screen, with bright heads.</summary>
    """
    rng = random.Random(9)
    cols = [(rng.randrange(SIZE), rng.choice((1, 2)), rng.randrange(10, 22), rng.random()) for _ in range(22)]
    frames = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (SIZE, SIZE))
        px = img.load()
        for x, speed, length, ph in cols:
            head = ((t * speed + ph) % 1) * (SIZE + length) - length
            for k in range(length):
                y = int(head + k)
                if 0 <= y < SIZE:
                    f = (k + 1) / length
                    px[x, y] = (200, 255, 200) if k == length - 1 else (0, int(40 + 180 * f * f), 0)
        frames.append(img)
    return frames


def starfield(n=48):
    """
    <summary>Fun: flying through space, with stars streaming past and growing as they near.</summary>
    """
    rng = random.Random(21)
    stars = [(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.random()) for _ in range(70)]
    frames = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (SIZE, SIZE))
        for sx, sy, ph in stars:
            z = 1 - (t + ph) % 1  # 1 = far away, 0 = at the viewer
            if z < 0.05:
                continue
            x, y = C + sx * 16 / z, C + sy * 16 / z
            if 0 <= x < SIZE and 0 <= y < SIZE:
                b = clamp01(0.25 + (1 - z) * 1.1)
                glow_dot(img, x, y, scale((220, 230, 255), b), halo=0.4 * (1 - z))
        frames.append(img)
    return frames


def rainbow_spiral(n=40):
    """
    <summary>Fun: a rainbow spiral slowly turning.</summary>
    """
    frames = []
    for i in range(n):
        t = i / n
        frames.append(shade(lambda x, y: tuple(int(255 * c) for c in colorsys.hsv_to_rgb(
            (math.atan2(y - C, x - C) / TAU + math.hypot(x - C, y - C) / 16 - t) % 1, 0.9, 0.8))))
    return frames


def game_of_life(n=48):
    """
    <summary>Fun: Conway's Game of Life from a random soup, on a board that wraps at the edges.</summary>
    <remarks>Fixed seed 42, so it plays the same each time. The loop is not seamless.</remarks>
    """
    rng = random.Random(42)
    cells = {(x, y) for x in range(SIZE) for y in range(SIZE) if rng.random() < 0.3}
    frames = []
    for i in range(n):
        img = Image.new("RGB", (SIZE, SIZE))
        px = img.load()
        for x, y in cells:
            px[x, y] = (40, 220, 120)
        frames.append(img)
        counts = {}
        for x, y in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        k = ((x + dx) % SIZE, (y + dy) % SIZE)
                        counts[k] = counts.get(k, 0) + 1
        cells = {k for k, c in counts.items() if c == 3 or (c == 2 and k in cells)}
    return frames


def bouncing_balls(n=64):
    """
    <summary>Fun: three coloured balls bouncing round the panel.</summary>
    """
    balls = [(3, 5, 0.37, 0.23, (255, 60, 60)), (20, 12, -0.29, 0.31, (60, 160, 255)), (10, 24, 0.33, -0.27, (255, 220, 40))]
    frames = []
    for i in range(n):
        img = Image.new("RGB", (SIZE, SIZE))
        for x0, y0, vx, vy, col in balls:
            # bounce = triangle wave, so the loop seam is clean when speeds divide evenly
            x = 3 + abs(((x0 + vx * i * 4) % 52) - 26)
            y = 3 + abs(((y0 + vy * i * 4) % 52) - 26)
            img = Image.composite(Image.new("RGB", (SIZE, SIZE), col), img,
                                  shade(lambda px_, py_: (int(255 * disc(px_, py_, x, y, 3)),) * 3).convert("L"))
        frames.append(img)
    return frames


def chomper(n=64):
    """
    <summary>Fun: a yellow muncher chasing dots, pursued by a ghost.</summary>
    <remarks>The mouth opens and shuts twice per loop.</remarks>
    """
    frames = []
    track = 25  # pixels travelled per loop
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (SIZE, SIZE))
        d = ImageDraw.Draw(img)
        d.line([(0, 23), (31, 23)], fill=(30, 30, 160))
        d.line([(0, 9), (31, 9)], fill=(30, 30, 160))
        mx = 4 + track * t
        for k in range(1, 5):
            dx = 6 + 6 * k
            if dx > mx + 2:
                d.point((dx, 16), fill=(255, 190, 150))
        mouth = 50 * abs(math.sin(TAU * 2 * t))  # half-angle in degrees
        d.pieslice([mx - 5, 11, mx + 5, 21], mouth, 360 - mouth, fill=(255, 220, 0))
        gx = mx - 11
        d.rounded_rectangle([gx - 4, 11, gx + 4, 21], radius=4, fill=(255, 70, 70))
        d.rectangle([gx - 4, 17, gx + 4, 21], fill=(255, 70, 70))
        for ex in (gx - 2, gx + 2):
            d.rectangle([ex - 1, 13, ex, 14], fill=WHITE)
            d.point((ex, 14), fill=(20, 20, 200))
        frames.append(img)
    return frames


def fireworks(n=48):
    """
    <summary>Fun: three rockets rising, bursting and falling away, staggered round the loop.</summary>
    """
    rng = random.Random(8)
    shells = [(rng.uniform(6, 26), rng.uniform(5, 13), rng.random(), rng.choice([(255, 80, 80), (80, 255, 120), (255, 220, 60), (120, 160, 255)]))
              for _ in range(3)]
    frames = []
    for i in range(n):
        t = i / n
        img = Image.new("RGB", (SIZE, SIZE))
        for cx, cy, ph, col in shells:
            p = (t + ph) % 1
            if p < 0.3:  # rising
                glow_dot(img, cx, 31 - (31 - cy) * (p / 0.3), (200, 170, 120), halo=0.2)
            else:  # bursting
                q = (p - 0.3) / 0.7
                r, fade = 1 + 11 * (1 - (1 - q) ** 2), (1 - q) ** 1.5
                for k in range(12):
                    a = TAU * k / 12
                    glow_dot(img, cx + r * math.cos(a), cy + r * math.sin(a) + 6 * q * q, scale(col, fade), halo=0.3)
        frames.append(img)
    return frames


def spinning_cube(n=48):
    """
    <summary>Fun: a wireframe cube tumbling in 3D, drawn large and shrunk for smooth lines.</summary>
    """
    verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if sum(verts[a][k] != verts[b][k] for k in range(3)) == 1]
    frames = []
    for i in range(n):
        a, b = TAU * i / n, TAU * i / n * 0.5 + 0.6
        pts = []
        for x, y, z in verts:
            x, z = x * math.cos(a) - z * math.sin(a), x * math.sin(a) + z * math.cos(a)
            y, z = y * math.cos(b) - z * math.sin(b), y * math.sin(b) + z * math.cos(b)
            f = 9 / (3.2 + z)
            pts.append((C + x * f * 1.9, C + y * f * 1.9))
        big = Image.new("RGB", (SIZE * 4, SIZE * 4))
        d = ImageDraw.Draw(big)
        for p, q in edges:
            d.line([tuple(v * 4 for v in pts[p]), tuple(v * 4 for v in pts[q])], fill=(80, 220, 255), width=5)
        frames.append(big.resize((SIZE, SIZE), Image.Resampling.LANCZOS))
    return frames


# --------------------------------------------------------------------------- catalogue


@dataclass(frozen=True)
class Preset:
    """
    <summary>One built-in animation: its catalogue entry plus the function that draws it.</summary>
    <remarks>Frames are built lazily and cached per key (see <see cref="_build"/>), so the
    first preview of a preset costs a moment and every later one is free. Keys must be
    unique across <see cref="PRESETS"/>: they name the cache entry, the exported GIF and
    the "On connect, send" setting saved in QSettings.</remarks>
    """

    key: str
    name: str
    category: str  # one of CATEGORIES: BABY_0_3, "Soothing", "Fun" or "Development"
    description: str
    fps: int
    brightness: int  # suggested panel brightness, %
    builder: Callable[..., list[Image.Image]]
    sleepy: bool = False  # dim, slow, warm: suitable for settling to sleep
    max_frames: int = MAX_FRAMES  # LONG_STORY_FRAMES for a long story whose GIF stays small

    def frames(self) -> list[Image.Image]:
        """<summary>The animation's RGB frames, built once and cached.</summary>"""
        return _build(self.key)

    @property
    def seconds(self) -> float:
        """<summary>How long one loop lasts at the preset's own fps.</summary>"""
        return len(self.frames()) / self.fps


PRESETS = [
    Preset("bold", "Bold shapes", BABY_0_3,
           "One huge shape at a time: white circle, red square, white triangle, red heart. Each stays for 5 seconds.",
           2, 35, bold_shapes),
    Preset("redball", "Slow red ball", BABY_0_3,
           "A big red ball drifting very slowly from side to side, for a baby's first goes at following with the eyes.",
           6, 35, slow_red_ball),
    Preset("checks", "Big checks", BABY_0_3,
           "Four giant black-and-white squares that slowly swap over every 6 seconds.",
           2, 35, big_checks),
    Preset("target", "Red target", BABY_0_3,
           "Black-and-white rings with a gently glowing red centre.",
           4, 35, red_target),
    Preset("face", "Big face", BABY_0_3,
           "A huge, simple face that slowly blinks and smiles. Faces are what babies look at longest.",
           4, 35, big_face),
    Preset("ballround", "Ball round and round", BABY_0_3,
           "The red ball on a slow circle, 16 seconds a lap. Circular tracking comes in around 3 months, "
           "so this one is for a little later.",
           4, 35, ball_round_and_round),
    Preset("redglow", "Red glow", BABY_0_3,
           "A soft red light breathing slowly, about 8 seconds per breath. Red light is the gentlest at night.",
           6, 15, red_glow, sleepy=True),
    Preset("breathe", "Deep breathing", BABY_0_3,
           "The whole panel glows and dims at 5 breaths a minute, slower than sleeping breathing, to slow a baby's down.",
           5, 12, deep_breathing, sleepy=True),
    Preset("nightlight", "Nightlight", BABY_0_3,
           "A steady dim red, nothing moving at all.", 1, 10, nightlight, sleepy=True),
    Preset("ripple", "Slow ripple", BABY_0_3,
           "Faint red rings spreading outward every 10 seconds, like a stone dropped in still water.",
           4, 12, slow_ripple, sleepy=True),
    Preset("sleepystars", "Sleepy stars", BABY_0_3,
           "A few dim amber stars fading in and out very slowly on a dark sky.", 4, 12, sleepy_stars, sleepy=True),
    Preset("sunset", "Setting sun", BABY_0_3,
           "A warm sun sinks slowly below the horizon and the sky fades to dark. 30 seconds.",
           2, 12, setting_sun, sleepy=True),
    Preset("tide", "Red tide", BABY_0_3,
           "A dim red glow slowly washing down the panel, 12 seconds a pass.", 4, 12, red_tide, sleepy=True),
    Preset("embers", "Embers", BABY_0_3,
           "Dull red embers slowly brightening and fading, like a fire dying down.", 4, 12, embers, sleepy=True),
    Preset("breathing", "Breathing glow", "Soothing",
           "A soft amber light that slowly swells and fades, about 12 breaths a minute. Calm breathing to settle with.",
           8, 20, breathing_glow),
    Preset("stars", "Starry night", "Soothing",
           "A crescent moon over dark hills while stars twinkle gently.", 6, 20, starry_night),
    Preset("moon", "Sleepy moon", "Soothing",
           "A sleeping moon face bobbing gently, with little z's drifting up.", 6, 20, sleepy_moon),
    Preset("waves", "Ocean waves", "Soothing",
           "Slow rolling waves under a night sky.", 8, 25, ocean_waves),
    Preset("lava", "Lava lamp", "Soothing",
           "Warm blobs that slowly merge and drift apart.", 5, 25, lava_lamp),
    Preset("fireflies", "Fireflies", "Soothing",
           "Fireflies drifting over the grass, each glowing softly in its own time.", 8, 20, fireflies),
    Preset("garden", "Night garden", "Soothing",
           "Starry night, Fireflies and Fireworks in one 16 second story: the fireflies arrive, scatter "
           "when the fireworks go up, then slowly drift back.", 4, 20, night_garden),
    Preset("fwflies", "Fireworks then fireflies", "Soothing",
           "A few fireworks burst over the garden, then the fireflies rise out of the grass and glow "
           "softly until they doze off. 30 seconds.", 4, 20, fireworks_to_fireflies,
           max_frames=LONG_STORY_FRAMES),
    Preset("fish", "Fish tank", "Soothing",
           "Two fish swim past, with swaying seaweed and rising bubbles.", 8, 25, fish_tank),
    Preset("drift", "Colour drift", "Soothing",
           "Dim rainbow colours that slowly shift, with no movement to follow.", 5, 20, colour_drift),
    Preset("chomper", "Chomper", "Fun",
           "A yellow muncher chomps along a trail of dots with a ghost in pursuit.", 8, 60, chomper),
    Preset("fire", "Fire", "Fun", "Flames licking up from the bottom of the panel.", 10, 60, fire),
    Preset("plasma", "Plasma", "Fun", "Classic demo-scene plasma, swirling through every colour.", 8, 50, plasma),
    Preset("rain", "Code rain", "Fun", "Green glyph trails falling down the screen.", 10, 60, code_rain),
    Preset("starfield", "Starfield", "Fun", "Flying through space, stars streaming past.", 10, 60, starfield),
    Preset("spiral", "Rainbow spiral", "Fun", "A hypnotic rainbow spiral slowly turning.", 8, 50, rainbow_spiral),
    Preset("life", "Game of Life", "Fun", "Conway's cellular automaton, evolving from a random soup.", 3, 60, game_of_life),
    Preset("balls", "Bouncing balls", "Fun", "Three coloured balls bouncing round the panel.", 10, 60, bouncing_balls),
    Preset("fireworks", "Fireworks", "Fun", "Rockets rise, burst and fall away.", 10, 60, fireworks),
    Preset("cube", "Spinning cube", "Fun", "A wireframe cube tumbling in 3D.", 5, 60, spinning_cube),
    Preset("contrast", "Contrast cards", "Development",
           "Black-and-white bullseye, checkerboard, stripes and squares. Newborns see high contrast best (0 to 3 months).",
           4, 40, contrast_cards),
    Preset("happy", "Happy face", "Development",
           "A simple high-contrast face that blinks and says “oh!”. Babies are drawn to faces from birth.",
           4, 40, happy_face),
    Preset("tracking", "Tracking ball", "Development",
           "A red ball gliding slowly from side to side, to practise following with the eyes (from about 2 months).",
           8, 40, tracking_ball),
    Preset("peekaboo", "Peekaboo ball", "Development",
           "A ball rolls behind a wall and comes out the other side, which helps with object permanence (from about 4 months).",
           8, 40, peekaboo_ball),
    Preset("shapes", "Shapes & colours", "Development",
           "A red circle, blue square, yellow triangle and green star in turn. Name them together.",
           4, 40, shapes_and_colours),
    Preset("counting", "Counting 1 to 5", "Development",
           "A digit and that many coloured dots, adding one at a time. Count along out loud (toddlers).",
           3, 40, counting),
]
BY_KEY = {p.key: p for p in PRESETS}
CATEGORIES = list(dict.fromkeys(p.category for p in PRESETS))


@lru_cache(maxsize=None)
def _build(key: str) -> list[Image.Image]:
    """
    <summary>Build a preset's frames once; later calls return the cached list.</summary>
    <param name="key">A key from <see cref="BY_KEY"/>.</param>
    <remarks>The cached frames are shared, so callers must copy before changing them.</remarks>
    """
    return BY_KEY[key].builder()


# --------------------------------------------------------------------------- output & safety


def warm_tint(frames: list[Image.Image]) -> list[Image.Image]:
    """
    <summary>Cut blue and some green light for bedtime use.</summary>
    <param name="frames">RGB frames; they are not changed.</param>
    <returns>New frames with green at 70 percent and blue at 15 percent.</returns>
    """
    out = []
    for f in frames:
        r, g, b = f.split()
        out.append(Image.merge("RGB", (r, g.point(lambda v: int(v * 0.7)), b.point(lambda v: int(v * 0.15)))))
    return out


ORIENTATIONS: list[tuple[str, int]] = [
    ("Normal", 0),
    ("Upside down", 180),
    ("Turned clockwise (top edge on the right)", 90),
    ("Turned anticlockwise (top edge on the left)", 270),
]
"""<summary>How the panel is physically mounted, as (label, degrees turned clockwise).</summary>"""


def rotate_frames(frames: list[Image.Image], turned_cw: int) -> list[Image.Image]:
    """
    <summary>Counter-rotate frames so they read upright on a panel that is mounted turned.</summary>
    <param name="frames">Square RGB frames.</param>
    <param name="turned_cw">How far the panel itself has been turned clockwise: 0, 90, 180 or 270.</param>
    <returns>New frames rotated anticlockwise by the same amount; the originals untouched.
    0 returns the same list object so the common case costs nothing.</returns>
    <exception cref="ValueError">For any other angle.</exception>
    <remarks>Only images the app renders can be turned this way. Text and the built-in
    clock are drawn by the panel's own firmware and stay as the panel draws them.</remarks>
    """
    if turned_cw not in (0, 90, 180, 270):
        raise ValueError(f"orientation must be 0, 90, 180 or 270, not {turned_cw}")
    if turned_cw == 0:
        return frames
    return [f.rotate(turned_cw, expand=False) for f in frames]


def to_gif(frames: list[Image.Image], fps: float) -> bytes:
    """
    <summary>Looping GIF in the plainest form the panel's decoder can take.</summary>
    <param name="frames">RGB 32x32 frames in order.</param>
    <param name="fps">Playback rate; every frame gets the same delay.</param>
    <returns>GIF bytes with one global palette, full frames and no local colour tables,
    written by <see cref="pixelate.gifenc.encode_gif"/>.</returns>
    <remarks>Colours are quantised without dithering so the LEDs do not shimmer. Pillow's
    own writer was replaced on 30th October 2026: its per-frame colour tables made the real
    panel stall after a frame or two on several presets.</remarks>
    """
    from .gifenc import encode_gif

    return encode_gif(frames, [round(1000 / fps)] * len(frames))


def _linear(v: int) -> float:
    """
    <summary>Convert an sRGB channel value from 0 to 255 to linear light from 0 to 1.</summary>
    """
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


_LIN = [_linear(v) for v in range(256)]


def flashing_fraction(frames: list[Image.Image], fps: float, threshold: float = 0.1,
                      max_transitions: int = 4) -> float:
    """
    <summary>The fraction of pixels that flash too often.</summary>
    <param name="frames">The loop's frames, in order.</param>
    <param name="fps">Playback rate, which sets how many frames make one second.</param>
    <param name="threshold">The smallest swing in relative luminance that counts as a transition.</param>
    <param name="max_transitions">The most transitions allowed in any one second.</param>
    <returns>From 0 to 1. The app treats anything over MAX_FLASHING_AREA as flashing.</returns>
    <remarks>A transition is a luminance swing of at least threshold against the previous
    swing's direction. A flash is two transitions. A pixel counts as flashing when some one
    second window holds more than max_transitions (more than two flashes). The loop wraps
    round, so the seam between the last frame and the first is checked as well.</remarks>
    """
    if len(frames) < 2:
        return 0.0
    lum = []
    for f in frames:
        raw = f.convert("RGB").tobytes()
        lum.append([0.2126 * _LIN[raw[i]] + 0.7152 * _LIN[raw[i + 1]] + 0.0722 * _LIN[raw[i + 2]]
                    for i in range(0, len(raw), 3)])
    window = max(1, round(fps))
    flashing = 0
    for p in range(SIZE * SIZE):
        # Repeat the loop so every one-second window, including ones that wrap, is covered.
        seq = [frame[p] for frame in lum] * (2 + window // len(lum))
        events, state, hi, lo = [], None, seq[0], seq[0]
        for i, v in enumerate(seq):
            hi, lo = max(hi, v), min(lo, v)
            if state != "up" and v - lo >= threshold:
                events.append(i)
                state, hi = "up", v
            elif state != "down" and hi - v >= threshold:
                events.append(i)
                state, lo = "down", v
        if any(sum(1 for e in events[j:] if e < events[j] + window) > max_transitions for j in range(len(events))):
            flashing += 1
    return flashing / (SIZE * SIZE)


MAX_FLASHING_AREA = 0.10


def export_all(out_dir: Path):
    """
    <summary>Write every preset as a GIF and print its size and flashing area.</summary>
    <param name="out_dir">Created if missing; existing GIFs of the same name are overwritten.</param>
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in PRESETS:
        data = to_gif(p.frames(), p.fps)
        (out_dir / f"{p.key}.gif").write_bytes(data)
        flash = flashing_fraction(p.frames(), p.fps)
        print(f"{p.name:18} {len(p.frames()):3} frames  {p.seconds:4.1f}s  {len(data) / 1024:5.1f} KB  "
              f"flashing area {flash:.0%}")


if __name__ == "__main__":
    export_all(Path(sys.argv[1] if len(sys.argv) > 1 else "presets-out"))

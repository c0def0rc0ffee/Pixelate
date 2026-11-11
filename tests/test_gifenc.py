"""
<summary>Tests for the in-house GIF writer: structure the panel needs, and exact pixels back.</summary>
"""

import io

import pytest
from PIL import Image

from pixelate import gifenc
from pixelate.presets import PRESETS, to_gif


def _parse(data: bytes):
    """<summary>Walk the GIF and return (global table bits, [(local bits, (w, h), delay ms)]).</summary>"""
    assert data[:6] == b"GIF89a"
    pos = 13
    flags = data[10]
    gct_bits = (flags & 7) + 1 if flags & 0x80 else None
    if gct_bits:
        pos += 3 * (1 << gct_bits)
    frames, delay = [], None
    while pos < len(data):
        b = data[pos]
        if b == 0x21:
            label = data[pos + 1]
            pos += 2
            if label == 0xF9:
                delay = int.from_bytes(data[pos + 2 : pos + 4], "little") * 10
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        elif b == 0x2C:
            w = int.from_bytes(data[pos + 5 : pos + 7], "little")
            h = int.from_bytes(data[pos + 7 : pos + 9], "little")
            f = data[pos + 9]
            pos += 10
            lct = (f & 7) + 1 if f & 0x80 else None
            if lct:
                pos += 3 * (1 << lct)
            assert data[pos] == 8, "LZW minimum code size must be 8"
            pos += 1
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
            frames.append((lct, (w, h), delay))
        elif b == 0x3B:
            break
        else:
            raise AssertionError(f"unexpected byte {b:#x} at {pos}")
    return gct_bits, frames


def test_lzw_round_trips_through_pillow():
    """<summary>Our LZW stream, wrapped in a GIF with an identity palette, decodes to the exact indices.</summary>
    <remarks>Covers long runs, noise, and enough data to fill the 4096-entry table and force clears.</remarks>"""
    import random
    import struct

    def wrap(px: bytes, w: int, h: int) -> bytes:
        """
        <summary>Wrap a raw LZW stream in the smallest valid single frame GIF around it.</summary>
        """
        return (b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF7, 0, 0) + bytes(range(256)) * 3
                + b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x08"
                + gifenc._sub_blocks(gifenc.lzw_encode(px)) + b"\x3b")

    cases = [
        (bytes(random.Random(1).randrange(256) for _ in range(32 * 32)), 32, 32),
        (bytes([7] * 600 + [random.Random(2).randrange(256) for _ in range(424)]), 32, 32),
        (bytes(random.Random(3).randrange(256) for _ in range(256 * 256)), 256, 256),
    ]
    for px, w, h in cases:
        with Image.open(io.BytesIO(wrap(px, w, h))) as im:
            assert im.tobytes() == px


def test_structure_is_plain():
    """<summary>One 256-entry global table, no local tables, full frames, our delays, loop forever.</summary>"""
    frames = [Image.new("RGB", (32, 32), c) for c in [(255, 0, 0), (0, 255, 0), (0, 255, 0), (0, 0, 255)]]
    data = gifenc.encode_gif(frames, [500, 4000, 4000, 50])
    gct, parsed = _parse(data)
    assert gct == 8
    assert [l for l, _, _ in parsed] == [None] * 4, "no local colour tables"
    assert [s for _, s, _ in parsed] == [(32, 32)] * 4, "every frame is full size, identical ones kept"
    assert [d for _, _, d in parsed] == [500, 4000, 4000, 50]
    assert b"NETSCAPE2.0" in data
    with Image.open(io.BytesIO(data)) as im:
        assert im.info["loop"] == 0
        assert im.n_frames == 4
        im.seek(3)
        assert im.convert("RGB").getpixel((0, 0)) == (0, 0, 255)


def test_rejects_bad_input():
    """<summary>Mismatched sizes or counts fail loudly rather than producing a bad file.</summary>"""
    a = Image.new("RGB", (32, 32))
    with pytest.raises(ValueError):
        gifenc.encode_gif([], [])
    with pytest.raises(ValueError):
        gifenc.encode_gif([a, a], [100])
    with pytest.raises(ValueError):
        gifenc.encode_gif([a, Image.new("RGB", (16, 16))], [100, 100])


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_every_preset_has_no_local_tables(preset):
    """<summary>Each shipped preset's GIF has the plain structure, frame for frame.</summary>"""
    frames = preset.frames()
    gct, parsed = _parse(to_gif(frames, preset.fps))
    assert gct == 8
    assert len(parsed) == len(frames)
    assert all(l is None for l, _, _ in parsed)
    assert all(s == (32, 32) for _, s, _ in parsed)

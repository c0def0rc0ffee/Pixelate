"""
<summary>A minimal GIF89a writer that produces the plainest possible animated GIF.</summary>
<remarks>
Written after the first session with the real panel (30th October 2026). Its firmware
stalled part way through some presets, and the one structural difference between
the presets that played and the ones that stalled was in the per-frame local colour
tables that Pillow emits (and that the idotmatrix library re-emits). Pillow cannot
be told not to write them, so this module writes the file itself:

* one global colour table of exactly 256 entries, never a local one;
* every frame a full 32x32 image, no cropping, no transparency, no interlace;
* LZW minimum code size always 8;
* one Graphic Control Extension per frame carrying that frame's own delay;
* Netscape loop extension set to loop forever.

Frames are quantised to a single shared palette with median cut and no dithering,
so the LEDs do not shimmer. The output is meant to be sent to the panel byte for
byte (see <see cref="pixelate.devices.IDotMatrixDevice.send_image"/>).
</remarks>
"""

from __future__ import annotations

import struct
from typing import Iterable, Sequence

from PIL import Image

SIZE = 32


def shared_palette(frames: Sequence[Image.Image]) -> tuple[list[Image.Image], bytes]:
    """
    <summary>Quantise all frames to one 256-colour palette.</summary>
    <param name="frames">RGB images, all the same size.</param>
    <returns>The frames as mode "P" images sharing one palette, and that palette as
    exactly 768 bytes (256 RGB triples, zero padded).</returns>
    <remarks>The palette is chosen over a strip of every frame, so colours that only
    appear late in the animation still get entries. No dithering anywhere.</remarks>
    """
    w, h = frames[0].size
    strip = Image.new("RGB", (w, h * len(frames)))
    for i, f in enumerate(frames):
        strip.paste(f.convert("RGB"), (0, h * i))
    pal_img = strip.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    palette = bytes(pal_img.getpalette()[:768])
    palette += bytes(768 - len(palette))
    indexed = [f.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE) for f in frames]
    return indexed, palette


def lzw_encode(indices: bytes, min_code_size: int = 8) -> bytes:
    """
    <summary>GIF flavoured LZW compression of one frame's pixel indices.</summary>
    <param name="indices">One byte per pixel, each below 2**min_code_size.</param>
    <param name="min_code_size">Always 8 here: the table is always 256 entries.</param>
    <returns>The compressed stream, without the sub-block framing.</returns>
    <remarks>Variable code width from min_code_size+1 up to 12 bits, clear code emitted
    when the table fills, as every decoder expects. Plain dictionary based, which is
    plenty for 1024 pixels a frame.</remarks>
    """
    clear = 1 << min_code_size
    end = clear + 1
    out = bytearray()
    acc = 0
    nbits = 0

    def emit(code: int, width: int) -> None:
        """
        <summary>Appends one code of the given bit width to the output, least significant bit first.</summary>
        """
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += width
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8

    table: dict[bytes, int] = {bytes([i]): i for i in range(clear)}
    next_code = end + 1
    width = min_code_size + 1
    emit(clear, width)
    prefix = b""
    for byte in indices:
        candidate = prefix + bytes([byte])
        if candidate in table:
            prefix = candidate
            continue
        emit(table[prefix], width)
        if next_code < 4096:
            table[candidate] = next_code
            next_code += 1
            if next_code - 1 == (1 << width) and width < 12:
                width += 1
        else:
            emit(clear, width)
            table = {bytes([i]): i for i in range(clear)}
            next_code = end + 1
            width = min_code_size + 1
        prefix = bytes([byte])
    if prefix:
        emit(table[prefix], width)
    emit(end, width)
    if nbits:
        out.append(acc & 0xFF)
    return bytes(out)


def _sub_blocks(data: bytes) -> bytes:
    """<summary>Wrap a byte string in GIF data sub-blocks of at most 255 bytes.</summary>"""
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i : i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def encode_gif(frames: Sequence[Image.Image], durations_ms: Iterable[int], loop: int = 0) -> bytes:
    """
    <summary>Write an animated GIF with one global palette and full, plain frames.</summary>
    <param name="frames">RGB images of identical size, in display order. Identical
    consecutive frames are kept as separate frames; nothing is merged or cropped.</param>
    <param name="durations_ms">One delay per frame in milliseconds. GIF stores
    hundredths of a second, so values are rounded to 10 ms; anything under 20 ms is
    raised to 20 ms, which is the shortest delay most decoders honour.</param>
    <param name="loop">Netscape loop count; 0 means forever.</param>
    <returns>The complete GIF file as bytes.</returns>
    <exception cref="ValueError">No frames, or frame sizes differ, or the duration count
    does not match the frame count.</exception>
    """
    frames = list(frames)
    durations = list(durations_ms)
    if not frames:
        raise ValueError("no frames")
    if len(durations) != len(frames):
        raise ValueError("one duration per frame, please")
    w, h = frames[0].size
    if any(f.size != (w, h) for f in frames):
        raise ValueError("all frames must be the same size")
    indexed, palette = shared_palette(frames)

    out = bytearray(b"GIF89a")
    # Logical screen: size, flags = global table present | 8 bit colour | 256 entry table
    out += struct.pack("<HHBBB", w, h, 0x80 | 0x70 | 0x07, 0, 0)
    out += palette
    # Netscape looping extension
    out += b"\x21\xff\x0bNETSCAPE2.0" + b"\x03\x01" + struct.pack("<H", loop) + b"\x00"
    for img, ms in zip(indexed, durations):
        delay = max(2, round(ms / 10))
        # Graphic control: disposal 1 (leave in place), no user input, no transparency
        out += b"\x21\xf9\x04" + bytes([0x04]) + struct.pack("<H", delay) + b"\x00\x00"
        # Image descriptor: full frame, no local table, not interlaced
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0)
        out += b"\x08" + _sub_blocks(lzw_encode(img.tobytes(), 8))
    out += b"\x3b"
    return bytes(out)

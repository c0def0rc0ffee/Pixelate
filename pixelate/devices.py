"""
<summary>Display backends: one class per panel protocol, plus a simulator.</summary>
<remarks>
The REYLAX panel is a rebadged Bluetooth LE matrix. Two protocols are common for
these panels, so both are supported:

* iPixel Color: advertises as "LED_BLE_..." (via the pypixelcolor library)
* iDotMatrix: advertises as "IDM-..." (via the idotmatrix library)

A Simulator backend lets the app be used with no hardware at all.
Every backend method is a coroutine and runs on the BLE event loop thread, never the
GUI thread; submit calls through <c>pixelate.ble.BleRunner</c>. Only the iDotMatrix
backend has been tried on a real panel; the iPixel one follows its library's documentation.
</remarks>
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SIZE = 32

# Text animations, in the order shown in the GUI. Both protocols use the same codes.
TEXT_ANIMATIONS = [
    "Static",
    "Scroll left",
    "Scroll right",
    "Scroll up",
    "Scroll down",
    "Blink",
    "Fade",
    "Snowflake",
]


@dataclass
class FoundDevice:
    """
    <summary>One Bluetooth device seen by <see cref="scan"/>.</summary>
    <remarks>protocol is "ipixel", "idotmatrix" or "unknown", guessed from the advertised
    name by <see cref="classify"/>. rssi is None when the adapter did not report it.</remarks>
    """
    name: str
    address: str
    protocol: str  # "ipixel", "idotmatrix" or "unknown"
    rssi: Optional[int] = None

    @property
    def label(self) -> str:
        """
        <summary>The line shown in the device list: name, address, protocol and signal strength.</summary>
        """
        proto = {"ipixel": "iPixel Color", "idotmatrix": "iDotMatrix"}.get(self.protocol, "unknown protocol")
        rssi = f", {self.rssi} dBm" if self.rssi is not None else ""
        return f"{self.name}  [{self.address}]  ({proto}{rssi})"


def classify(name: str) -> str:
    """
    <summary>Guess a panel's protocol from its advertised Bluetooth name.</summary>
    <param name="name">The advertised local name.</param>
    <returns>"idotmatrix" for names starting IDM-, "ipixel" for any name containing LED,
    otherwise "unknown".</returns>
    <remarks>The IDM- test comes first, so an iDotMatrix panel whose name also contains LED
    is still classed correctly.</remarks>
    """
    if name.startswith("IDM-"):
        return "idotmatrix"
    if "LED" in name.upper():
        return "ipixel"
    return "unknown"


async def scan(timeout: float = 6.0, show_all: bool = False) -> list[FoundDevice]:
    """
    <summary>Scan for nearby panels.</summary>
    <param name="timeout">Seconds to listen for advertisements.</param>
    <param name="show_all">Include every named device, not only ones with a known protocol.</param>
    <returns>Known panels first, then strongest signal first. Unnamed devices are never listed.</returns>
    <remarks>Only listens; nothing is sent to any device. A panel already connected to the
    phone app usually stops advertising, so close the app first.</remarks>
    """
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    results = []
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if not name:
            continue
        protocol = classify(name)
        if protocol != "unknown" or show_all:
            results.append(FoundDevice(name, device.address, protocol, adv.rssi))
    results.sort(key=lambda d: (d.protocol == "unknown", -(d.rssi or -999)))
    return results


def _hex(rgb: tuple[int, int, int]) -> str:
    """
    <summary>Formats an RGB triple as six lower case hex digits, as pypixelcolor expects.</summary>
    """
    return "%02x%02x%02x" % rgb


class Device:
    """
    <summary>Common interface for all backends.</summary>
    <remarks>
    <c>on_disconnect</c> is set by the GUI after construction. A backend that can
    tell the link has gone (only iDotMatrix today) calls it, on the BLE loop thread,
    when the panel drops the connection without being asked to. The GUI uses it to
    flip to "Not connected" instead of carrying on against a dead link.
    </remarks>
    """

    name = "device"
    on_disconnect: Optional[Callable[[], None]] = None
    link_info: str = ""  # filled in after connect where the backend can tell, e.g. the MTU

    async def connect(self) -> None:
        """
        <summary>Open the Bluetooth link. The base class does nothing.</summary>
        """
        ...

    async def disconnect(self) -> None:
        """
        <summary>Close the Bluetooth link. The base class does nothing.</summary>
        """
        ...

    async def send_image(self, path: Path) -> None:
        """
        <summary>Show a 32x32 PNG or animated GIF.</summary>
        <param name="path">The image file on disk.</param>
        """
        raise NotImplementedError

    async def send_text(self, text: str, color: tuple, bg: Optional[tuple], animation: int, speed: int) -> None:
        """
        <summary>Show text on the panel with one of the <see cref="TEXT_ANIMATIONS"/>.</summary>
        <param name="text">The text to show.</param>
        <param name="color">Text colour as an RGB triple.</param>
        <param name="bg">Background colour, or None for no background.</param>
        <param name="animation">Index into <see cref="TEXT_ANIMATIONS"/>.</param>
        <param name="speed">Animation speed as the protocol's own number.</param>
        """
        raise NotImplementedError

    async def set_brightness(self, percent: int) -> None:
        """
        <summary>Set the panel brightness.</summary>
        <param name="percent">0 to 100. Backends clamp it to what their panel accepts.</param>
        """
        raise NotImplementedError

    async def set_power(self, on: bool) -> None:
        """
        <summary>Turn the screen on or off. The link stays up either way.</summary>
        <param name="on">True for on.</param>
        """
        raise NotImplementedError

    async def show_clock(self, style: int, show_date: bool, hour24: bool) -> None:
        """
        <summary>Switch the panel to its own built in clock.</summary>
        <param name="style">The clock style number; backends clamp it.</param>
        <param name="show_date">Show the date as well as the time.</param>
        <param name="hour24">24 hour time when True.</param>
        """
        raise NotImplementedError

    async def sync_time(self) -> None:
        """
        <summary>Set the panel's clock to this computer's local time.</summary>
        <remarks>The panel keeps time by itself afterwards, so this is only needed once per connect.</remarks>
        """
        raise NotImplementedError


class IPixelDevice(Device):
    """
    <summary>Backend for iPixel Color panels, through the pypixelcolor library.</summary>
    <remarks>Not yet tried on a real panel: the one in use speaks iDotMatrix. Failures come
    back as exceptions from the library.</remarks>
    """
    def __init__(self, address: str):
        """
        <summary>Creates the library client; nothing is sent until connect.</summary>
        <param name="address">The panel's Bluetooth address from <see cref="scan"/>.</param>
        """
        from pypixelcolor import AsyncClient

        self.name = f"iPixel {address}"
        self.client = AsyncClient(address)

    async def connect(self):
        """
        <summary>Opens the link through pypixelcolor.</summary>
        """
        await self.client.connect()

    async def disconnect(self):
        """
        <summary>Closes the link through pypixelcolor.</summary>
        """
        await self.client.disconnect()

    async def send_image(self, path):
        """
        <summary>Sends the image, letting the library fit it to the panel.</summary>
        """
        await self.client.send_image(str(path), resize_method="fit")

    async def send_text(self, text, color, bg, animation, speed):
        """
        <summary>Sends text, converting the colours to the hex strings the library takes.</summary>
        """
        await self.client.send_text(
            text, color=_hex(color), bg_color=_hex(bg) if bg else None, animation=animation, speed=speed
        )

    async def set_brightness(self, percent):
        """
        <summary>Sets brightness, clamped to 0 to 100.</summary>
        """
        await self.client.set_brightness(max(0, min(100, percent)))

    async def set_power(self, on):
        """
        <summary>Turns the screen on or off.</summary>
        """
        await self.client.set_power(on)

    async def show_clock(self, style, show_date, hour24):
        """
        <summary>Switches to the panel's built in clock.</summary>
        """
        await self.client.set_clock_mode(style=style, show_date=show_date, format_24=hour24)

    async def sync_time(self):
        """
        <summary>Sends the local hour, minute and second; this protocol takes no date.</summary>
        """
        now = dt.datetime.now()
        await self.client.set_time(now.hour, now.minute, now.second)


class _LastErrorHandler(logging.Handler):
    """
    <summary>Remembers the most recent ERROR the idotmatrix library logged.</summary>
    <remarks>The library catches every exception, logs it and returns False, so the
    only way to tell the user *why* a send failed is to catch that log record. One
    instance is attached to the "idotmatrix" logger the first time an
    <see cref="IDotMatrixDevice"/> is created.</remarks>
    """

    last: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        """
        <summary>Keeps the text of ERROR and worse records; anything quieter is ignored.</summary>
        """
        if record.levelno >= logging.ERROR:
            self.last = record.getMessage()


_last_error = _LastErrorHandler()


def _install_error_capture() -> None:
    """<summary>Attach <see cref="_last_error"/> to the library's logger once.</summary>"""
    lib = logging.getLogger("idotmatrix")
    if _last_error not in lib.handlers:
        lib.addHandler(_last_error)


def _check(result):
    """
    <summary>Turn the idotmatrix library's silent False into an exception.</summary>
    <param name="result">Whatever the library call returned.</param>
    <returns>The result unchanged when it is not False.</returns>
    <exception cref="RuntimeError">The library reported a failure. The message carries
    the library's own error text (for example the GATT error code the panel sent back)
    when it logged one, so the GUI log box shows the real reason.</exception>
    """
    if result is False:
        detail = _last_error.last or "no detail logged"
        _last_error.last = None
        raise RuntimeError(f"iDotMatrix: {detail}")
    return result


# Bold sans fonts, best first. The idotmatrix library renders each glyph into a
# 16x32 one-bit cell with Pillow, and its own default is a relative path to a font
# it does not ship, so without one of these every text send fails.
TEXT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def find_text_font() -> str:
    """
    <summary>Pick an installed TrueType font for the iDotMatrix text renderer.</summary>
    <returns>Absolute path of the first candidate in <see cref="TEXT_FONT_CANDIDATES"/>
    that exists on this machine.</returns>
    <exception cref="FileNotFoundError">None of the candidates is installed; the message
    names the package to install rather than letting the library fail with Pillow's
    bare "cannot open resource".</exception>
    <remarks>Only the iDotMatrix protocol needs this. iPixel panels render text in
    firmware and the pypixelcolor library sends the string as is.</remarks>
    """
    for candidate in TEXT_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError(
        "no TrueType font found for panel text; install fonts-dejavu-core (or add a path to "
        "TEXT_FONT_CANDIDATES in pixelate/devices.py)"
    )


class IDotMatrixDevice(Device):
    """
    <summary>Backend for iDotMatrix panels, through the idotmatrix library.</summary>
    <remarks>This is the protocol the real panel speaks. The library returns False instead of
    raising, so every call goes through <see cref="_check"/>. Its ConnectionManager is a
    process wide singleton, so only one iDotMatrix panel can be connected at a time.</remarks>
    """
    def __init__(self, address: str):
        """
        <summary>Prepares the shared connection manager for this address without connecting.</summary>
        <param name="address">The panel's Bluetooth address from <see cref="scan"/>.</param>
        <remarks>Importing idotmatrix switches the root logger to DEBUG, so the level is put
        back straight after the import. A client left over for a different address is dropped.</remarks>
        """
        root = logging.getLogger()
        level = root.level
        from idotmatrix import ConnectionManager

        root.setLevel(level)  # importing idotmatrix turns on DEBUG logging for everything
        _install_error_capture()
        self.name = f"iDotMatrix {address}"
        self.address = address
        self.conn = ConnectionManager()  # a process-wide singleton
        self._closing = False
        if self.conn.address != address:
            self.conn.client = None

    def _dropped(self, _client) -> None:
        """
        <summary>Bleak's disconnected callback: tell the GUI if we did not ask for it.</summary>
        <param name="_client">The BleakClient that lost its link (unused).</param>
        <remarks>Runs on the BLE loop thread. Seen on the real panel on 30th October 2026:
        after rejecting a GIF chunk with GATT error 0x0E the panel closes the link.</remarks>
        """
        if not self._closing and self.on_disconnect:
            self.on_disconnect()

    async def connect(self):
        """
        <summary>Connect, supplying our own BleakClient so the disconnect callback is wired.</summary>
        <remarks>The library's ConnectionManager only builds a client when it has none,
        so handing it one here is the supported way to get a callback in.</remarks>
        """
        from bleak import BleakClient

        self._closing = False
        self.conn.address = self.address
        self.conn.client = BleakClient(self.address, disconnected_callback=self._dropped)
        await self.conn.connect()
        # The MTU decides how many write round trips a GIF takes, so it is worth seeing
        # in the log: a 23-byte MTU means 20-byte writes and about 9 KB per 15 s.
        # This is information only, so a panel whose service table looks different must
        # not turn a successful connect into a failure.
        try:
            from idotmatrix.const import UUID_WRITE_DATA

            client = self.conn.client
            chunk = client.services.get_characteristic(UUID_WRITE_DATA).max_write_without_response_size
            self.link_info = f"MTU {client.mtu_size}, {chunk}-byte writes"
        except Exception as e:  # noqa: BLE001
            self.link_info = f"link details unavailable: {e}"

    async def disconnect(self):
        """
        <summary>Closes the link, marking it as asked for so <see cref="_dropped"/> stays quiet.</summary>
        """
        self._closing = True
        await self.conn.disconnect()

    async def send_image(self, path):
        """
        <summary>Send a GIF byte for byte, or a PNG through the library's image path.</summary>
        <param name="path">A 32x32 GIF from <see cref="pixelate.presets.to_gif"/> or a 32x32 PNG.</param>
        <remarks>GIFs deliberately skip the library's uploadProcessed: it re-encodes with
        Pillow, which flattens every frame delay to the last frame's, sets loop=1, and
        adds per-frame colour tables the panel stalls on. Our GIFs are already 32x32.</remarks>
        """
        from idotmatrix import Gif, Image

        if Path(path).suffix.lower() == ".gif":
            _check(await Gif().uploadUnprocessed(str(path)))
        else:
            _check(await Image().setMode(1))
            _check(await Image().uploadProcessed(str(path), pixel_size=SIZE))

    async def send_text(self, text, color, bg, animation, speed):
        """
        <summary>Render the text to glyph bitmaps and send it with the chosen animation.</summary>
        <remarks>The font is passed explicitly via <see cref="find_text_font"/>: the
        library's default font path is relative to its own missing fonts folder, which
        was the first failure seen on the real panel (30th October 2026).</remarks>
        """
        from idotmatrix import Text

        _check(await Text().setMode(
            text,
            font_path=find_text_font(),
            text_mode=animation,
            speed=speed,
            text_color=color,
            text_bg_mode=1 if bg else 0,
            text_bg_color=bg or (0, 0, 0),
        ))

    async def set_brightness(self, percent):
        """
        <summary>Sets brightness, clamped to 5 to 100, the range the library accepts.</summary>
        """
        from idotmatrix import Common

        _check(await Common().setBrightness(max(5, min(100, percent))))

    async def set_power(self, on):
        """
        <summary>Turns the screen on or off.</summary>
        """
        from idotmatrix import Common

        _check(await (Common().screenOn() if on else Common().screenOff()))

    async def show_clock(self, style, show_date, hour24):
        """
        <summary>Switches to the panel's built in clock; styles above 7 become 7.</summary>
        """
        from idotmatrix import Clock

        _check(await Clock().setMode(min(style, 7), visibleDate=show_date, hour24=hour24))

    async def sync_time(self):
        """
        <summary>Sends the full local date and time.</summary>
        """
        from idotmatrix import Common

        n = dt.datetime.now()
        _check(await Common().setTime(n.year, n.month, n.day, n.hour, n.minute, n.second))


class SimulatorDevice(Device):
    """
    <summary>Pretends to be a panel so the app can be used with no hardware.</summary>
    <remarks>Each call waits briefly, like a small Bluetooth write, then reports what it was
    sent through on_event(kind, payload). The GUI draws image payloads in its preview.</remarks>
    """

    name = "Simulator"

    def __init__(self, on_event: Callable[[str, object], None]):
        """
        <summary>Stores the callback that receives every pretend command.</summary>
        <param name="on_event">Called with a kind string and its payload, on the BLE loop thread.</param>
        """
        self.on_event = on_event

    async def _pretend(self, kind, payload=None):
        """
        <summary>Waits like a real write would, then reports the command.</summary>
        <param name="kind">What was sent, such as "image" or "brightness".</param>
        <param name="payload">The data sent, or None.</param>
        """
        await asyncio.sleep(0.15)  # roughly what a small BLE write costs
        self.on_event(kind, payload)

    async def connect(self):
        """
        <summary>Reports a pretend connect.</summary>
        """
        await self._pretend("connected")

    async def disconnect(self):
        """
        <summary>Reports a pretend disconnect.</summary>
        """
        await self._pretend("disconnected")

    async def send_image(self, path):
        """
        <summary>Reports the image file's bytes.</summary>
        """
        await self._pretend("image", Path(path).read_bytes())

    async def send_text(self, text, color, bg, animation, speed):
        """
        <summary>Reports the text and its settings as a dict.</summary>
        """
        await self._pretend("text", dict(text=text, color=color, bg=bg, animation=animation, speed=speed))

    async def set_brightness(self, percent):
        """
        <summary>Reports the brightness.</summary>
        """
        await self._pretend("brightness", percent)

    async def set_power(self, on):
        """
        <summary>Reports the power state.</summary>
        """
        await self._pretend("power", on)

    async def show_clock(self, style, show_date, hour24):
        """
        <summary>Reports the clock settings as a dict.</summary>
        """
        await self._pretend("clock", dict(style=style, show_date=show_date, hour24=hour24))

    async def sync_time(self):
        """
        <summary>Reports the local time it would have sent.</summary>
        """
        await self._pretend("time", dt.datetime.now())


def make_device(found: FoundDevice) -> Device:
    """
    <summary>Build the right backend for a scanned device.</summary>
    <param name="found">A device from <see cref="scan"/>.</param>
    <returns>An unconnected backend.</returns>
    <remarks>Unknown names get the iPixel backend, the protocol most REYLAX style panels use.
    The simulator is not made here; the GUI builds it directly.</remarks>
    """
    if found.protocol == "idotmatrix":
        return IDotMatrixDevice(found.address)
    # Unknown names get the iPixel backend: it's the protocol most REYLAX-style panels use.
    return IPixelDevice(found.address)

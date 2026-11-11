"""
<summary>The preset playlist: an ordered rotation of presets with a change interval.</summary>
<remarks>
Pure logic with no Qt in it, so it is tested directly. The GUI owns the timer and the
Bluetooth calls; this class only decides what comes next and how the list is saved.
The panel keeps playing whatever it has until a new GIF has fully arrived, so a rotation
never blanks the screen between items. The interval is held to at least one minute:
the panel has dropped the link after transfers arriving straight after one another.
</remarks>
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_MINUTES = 1
MAX_MINUTES = 240
DEFAULT_MINUTES = 10
RECONNECT_SECONDS = 30
"""<summary>How long the GUI waits before trying to reconnect after a drop mid-playlist.</summary>"""


@dataclass
class Playlist:
    """
    <summary>An ordered list of preset keys and the number of minutes each one shows for.</summary>
    <remarks>Keys are preset keys (see <c>pixelate.presets.BY_KEY</c>); the same key may appear
    more than once. <see cref="position"/> is the index of the item sent last, or -1 before
    anything has been sent, so <see cref="next_key"/> starts at the first item.</remarks>
    """

    keys: list[str] = field(default_factory=list)
    minutes: int = DEFAULT_MINUTES
    position: int = -1

    def __post_init__(self):
        """
        <summary>Clamps the minutes into the allowed range, whatever was stored in the settings.</summary>
        """
        self.minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(self.minutes)))

    @property
    def interval_ms(self) -> int:
        """<summary>The change interval as a timer period in milliseconds.</summary>"""
        return self.minutes * 60 * 1000

    def next_key(self) -> str | None:
        """
        <summary>Advance to the next item and return its key, wrapping round at the end.</summary>
        <returns>The key to send now, or None when the list is empty.</returns>
        """
        if not self.keys:
            self.position = -1
            return None
        self.position = (self.position + 1) % len(self.keys)
        return self.keys[self.position]

    def add(self, key: str) -> None:
        """<summary>Append a preset to the end of the rotation.</summary>"""
        self.keys.append(key)

    def remove(self, index: int) -> None:
        """
        <summary>Take one item out by position, keeping the rotation pointed at the same item.</summary>
        <param name="index">Position in <see cref="keys"/>; out of range does nothing.</param>
        """
        if not 0 <= index < len(self.keys):
            return
        del self.keys[index]
        if index <= self.position:
            self.position -= 1
        if self.position >= len(self.keys):
            self.position = len(self.keys) - 1

    def prune(self, known: set[str]) -> None:
        """<summary>Drop keys that no longer name a preset, as happens after a rename.</summary>"""
        self.keys = [k for k in self.keys if k in known]
        self.position = min(self.position, len(self.keys) - 1)

    def encode(self) -> str:
        """<summary>The key list as one settings string: keys joined by commas.</summary>"""
        return ",".join(self.keys)

    @classmethod
    def decode(cls, keys: str | None, minutes: object, known: set[str]) -> "Playlist":
        """
        <summary>Rebuild a playlist from the two saved settings values.</summary>
        <param name="keys">The string from <see cref="encode"/>, or None or empty.</param>
        <param name="minutes">Whatever was saved: QSettings may hand back a string.</param>
        <param name="known">Preset keys that exist now; anything else is dropped.</param>
        """
        try:
            mins = int(minutes) if minutes not in (None, "") else DEFAULT_MINUTES
        except (TypeError, ValueError):
            mins = DEFAULT_MINUTES
        pl = cls([k for k in (keys or "").split(",") if k], mins)
        pl.prune(known)
        return pl

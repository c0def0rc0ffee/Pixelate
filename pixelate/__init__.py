"""
<summary>Pixelate: a desktop controller for 32x32 Bluetooth LED pixel panels.</summary>
<remarks>
The package exposes only the version here. The window is in <c>gui</c>, the panel
backends in <c>devices</c>, the Bluetooth thread in <c>ble</c>, the built-in
animations in <c>presets</c>, and the GIF writer the panel needs in <c>gifenc</c>.
Run it with <c>python -m pixelate</c>.
</remarks>
"""

from pathlib import Path


def _read_version() -> str:
    """
    <summary>Read the version from the VERSION file at the project root.</summary>
    <returns>The trimmed file contents, or "dev" when the file is missing, for example
    when the package is run from somewhere the file was not copied to.</returns>
    <remarks>VERSION is the single source of truth for the project's version, so nothing
    here is hard coded. Only the BUILD part moves without being asked.</remarks>
    """
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


__version__ = _read_version()

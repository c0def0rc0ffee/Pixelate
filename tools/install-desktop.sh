#!/usr/bin/env bash
# <summary>Puts Pixelate in the Linux application menu for the current user.</summary>
# <remarks>Writes a freedesktop .desktop entry under ~/.local/share/applications that
# runs this checkout's .venv, installs the icon set from pixelate/assets into the
# user's hicolor icon theme, and refreshes the desktop and icon caches. Nothing is
# copied system wide and nothing needs root. Run it again after moving the project
# or recreating the .venv; pass --uninstall to take the entry and icons out again.
# The generated entry contains this machine's paths, which is why it lives outside
# the repository rather than being committed.</remarks>

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT/.venv/bin/python"
ASSETS="$PROJECT/pixelate/assets"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
ENTRY="$APPS/pixelate.desktop"

# <summary>Removes the desktop entry and every icon size this script installed.</summary>
uninstall() {
    rm -f "$ENTRY"
    for png in "$ASSETS"/pixelate-*.png; do
        size="$(basename "$png" .png)"; size="${size#pixelate-}"
        rm -f "$ICONS/${size}x${size}/apps/pixelate.png"
    done
    rm -f "$ICONS/scalable/apps/pixelate.svg"
    refresh
    echo "Pixelate removed from the application menu."
}

# <summary>Tells the desktop about changed entries and icons, where the tools exist.</summary>
refresh() {
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

[[ -x "$PYTHON" ]] || { echo "No .venv found at $PROJECT/.venv; create it first (see README.md)." >&2; exit 1; }
ls "$ASSETS"/pixelate-*.png >/dev/null 2>&1 || "$PYTHON" "$PROJECT/tools/make_icon.py"

for png in "$ASSETS"/pixelate-*.png; do
    size="$(basename "$png" .png)"; size="${size#pixelate-}"
    install -Dm644 "$png" "$ICONS/${size}x${size}/apps/pixelate.png"
done
install -Dm644 "$ASSETS/pixelate.svg" "$ICONS/scalable/apps/pixelate.svg"

mkdir -p "$APPS"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.5
Name=Pixelate
GenericName=LED Pixel Panel Controller
Comment=Draw pixel art and animations and send them to a 32x32 Bluetooth LED panel
Exec="$PYTHON" -m pixelate
Path=$PROJECT
Icon=pixelate
Terminal=false
Categories=Graphics;Qt;
Keywords=LED;pixel;panel;Bluetooth;iDotMatrix;iPixel;
StartupNotify=true
StartupWMClass=pixelate
DESKTOP
chmod 644 "$ENTRY"
refresh

echo "Installed: $ENTRY"
echo "Pixelate should now appear in the application menu under Graphics."

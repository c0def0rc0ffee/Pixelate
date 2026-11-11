# Pixelate user guide

The longer companion to the README: every tab, every preset collection and the safety
checks. The README covers installation and the first connection.

## Connecting

Click **Scan**, pick the panel, then click **Connect**. The app remembers the panel for
next time. Choose **Simulator (no hardware)** to try everything without the panel; the
preview box shows exactly what the panel would.

Two protocols are supported, because panels like this usually speak one of them:

| Protocol      | Bluetooth name | Library        |
|---------------|----------------|----------------|
| iPixel Color  | `LED_BLE_…`    | `pypixelcolor` |
| iDotMatrix    | `IDM-…`        | `idotmatrix`   |

The scanner works out which from the Bluetooth name. If the panel shows up under a name
that matches neither, tick **Show all Bluetooth devices**, pick it, and the app tries the
iPixel protocol.

## System tray

While Pixelate runs it puts its pixel heart in the system tray. Closing the window only
hides it there, so a playlist keeps going and the panel stays connected. Click the tray
icon, or right click it and choose **Open Pixelate**, to bring the window back; **Quit
Pixelate** in the same menu disconnects the panel and exits. Starting Pixelate again from
the menu while it is already running brings the running window to the front rather than
opening a second copy, because the panel only takes one connection. On a desktop with no
tray, closing the window quits as before.

## Drawing

- Left mouse button paints and right mouse button erases.
- Tool shortcuts: P pencil, E eraser, F fill, I colour picker.
- Ctrl+Z undoes, Ctrl+Shift+Z redoes, and Ctrl+Enter sends to the panel.
- With more than one frame, the drawing is sent as an animated GIF at the fps you set.
- Tick **Live** to send automatically after every stroke.
- **Open image/GIF** loads any picture, scaled to fit the panel; **Save as** writes a PNG
  or an animated GIF.

## Presets

The **Presets** tab holds built-in animations. Click one to preview it, then click **Send
to panel**, or **Edit a copy** to change it in the editor.

**Panel mounted** (Display tab) tells the app which way round the panel is fixed: normal,
upside down, or turned a quarter either way. Everything the app draws (presets, drawings,
the round clock) is turned to match so it reads upright. The panel draws text and its
built-in clock itself, so those two stay as the panel draws them. Files you save are never
turned.

**On connect, send** picks a preset that goes to the panel automatically every time the
app connects, right after the clock sync, so the panel always starts on something you
chose. The panel itself has no setting for what it shows at power on, so this is the app
standing in for one. Warm tint and the brightness box apply to it just as they do to the
Send button. "Nothing" turns it off.

**Playlist** rotates through presets for as long as the app stays open. Select a preset,
press **Add selected** to put it at the end of the list (the same one can go in more than
once), set **Change every** to how many minutes each should show for, and press **Start
playlist**. The first item goes to the panel at once and the next follows when the
interval is up. The panel keeps showing the old animation until the new one has fully
arrived, so there is no blank between them. If the panel drops the Bluetooth link, which
it does from time to time, the app reconnects on its own every 30 seconds until it is
back and then carries on from the next item; "On connect, send" is skipped while a
playlist is running. Pressing Disconnect or Stop playlist ends it. The list and the
interval are remembered between runs, but the playlist never starts by itself. The
shortest interval is one minute, because the panel has dropped the link when transfers
arrived straight after one another.

### Baby, 0 to 3 months

The default collection, tuned for a newborn's eyes: one huge shape at a time, in bold
black-and-white and red, with long holds and very slow movement. It is the only age range
so far; others will follow.

- Awake: Bold shapes, Slow red ball, Big checks, Red target, Big face, Ball round and round.
- Sleepy (marked 🌙): Red glow, Deep breathing, Nightlight, Slow ripple, Sleepy stars,
  Setting sun, Red tide, Embers. These are all dim red or amber with no blue light,
  nothing to track, and nothing faster than one slow breath. Sending one sets the
  panel to 10 to 15% brightness.

To add a collection for an older age range, add a category in `pixelate/presets.py`. Each
preset's `category` decides which collection it appears in, and `sleepy=True` gets it the
moon and the dimness checks.

### Soothing

Slow (5 to 30 s loops), dim and mostly warm-coloured: Breathing glow, Starry night, Sleepy
moon, Ocean waves, Lava lamp, Fireflies, Night garden, Fireworks then fireflies, Fish tank
and Colour drift. Tick **Warm tint** at bedtime to cut blue light.

### Development

High contrast with slow movement:

| Preset | What it's for | Rough age |
|---|---|---|
| Contrast cards | Black-and-white patterns for newborn eyes | 0 to 3 months |
| Happy face | Babies are drawn to faces | from birth |
| Tracking ball | Following movement with the eyes | 2 months + |
| Peekaboo ball | Object permanence: the ball still exists behind the wall | 4 months + |
| Shapes & colours | Naming shapes and colours together | 12 months + |
| Counting 1 to 5 | Counting out loud, one more each time | toddlers |

### Fun

For everyone else: Chomper, Fire, Plasma, Code rain, Starfield, Rainbow spiral, Game of
Life, Bouncing balls, Fireworks and Spinning cube.

### Flash safety

Every preset is checked automatically. No meaningful area of the panel changes brightness
more than twice a second, which is stricter than the WCAG three-flashes rule. If one of
your own animations flashes faster than that, the app warns you before sending it. Export
every preset as a GIF, with its stats, using:

```bash
.venv/bin/python -m pixelate.presets presets-out
```

## Text

The **Text** tab sends a line of text in a chosen colour, with an optional background colour
and a choice of animation. The panel renders the
text itself, so it is not affected by the orientation setting.

## Clock

The panel's own clock is digital: **Show clock** turns it on, and the app syncs the panel
to the computer's time on every connect (**Sync time from PC** does it again by hand). For an analogue face, open the **Clock** tab, pick
the hand and accent colours, and click **Start round clock**. The app draws the face itself
and re-sends it just after every minute. Sending anything else stops it.

## Display

Brightness, screen on and off, and the **Panel mounted** orientation.

## About

**Help → About Pixelate** (or F1) shows the version, the libraries in use and the licence.
The version comes from the `VERSION` file.

## Command line

```bash
.venv/bin/python -m pixelate.scan          # find the panel and say which protocol it speaks
.venv/bin/python -m pixelate.scan --all    # list every named Bluetooth device nearby
.venv/bin/python -m pixelate.scan --test   # scroll "Hi!" across the first panel found
.venv/bin/python -m pixelate.scan --gif FILE   # send a GIF byte for byte
```

## Files

- `pixelate/devices.py`: one backend per protocol, plus the simulator
- `pixelate/ble.py`: background asyncio thread for Bluetooth
- `pixelate/gui.py`: the PySide6 window and the About dialog
- `pixelate/tray.py`: the system tray icon and the one running copy guard
- `pixelate/scan.py`: command-line scanner and connection test
- `pixelate/presets.py`: built-in animations and the flash check
- `pixelate/gifenc.py`: the plain GIF writer the panel's decoder needs
- `pixelate/playlist.py`: the preset rotation behind the Playlist box
- `pixelate/clockface.py`: the analogue clock face
- `tools/install-desktop.sh`: Linux application menu entry; `tools/make_icon.py` draws the icon
- `build-zip.sh`: the release build
- `HANDOVER.md`: project state, first-connection procedure and what the real panel taught us

## Building releases

`./build-zip.sh` runs the tests and a house rules check, then writes the release zips into
`Pixelate Dist/` and `Pixelate Git/` and mirrors the release into `Pixelate App/`. Each
build moves the last number of `VERSION` on; `--no-bump` keeps it as it is. Nothing is
published by the script.

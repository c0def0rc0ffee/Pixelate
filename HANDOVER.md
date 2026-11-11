# Handover notes

Written 30th October 2026 on the machine where the app was built. Read this
first if you are picking the project up on another machine.

## What this is

Pixelate is a Linux desktop app (Python + PySide6) for the **REYLAX 32×32 LED
pixel panel** Rob bought from Amazon. It's a dedicated display for his baby
daughter, to calm her and give her
simple shapes and colours to look at. As she grows it'll keep being her panel,
with a new collection added for each age range. Rob also wants fun presets for
himself.

Don't attach tablet/phone screen-time warnings to this. Rob has pointed out
that guidance is about tablets and phones, not a dim panel of simple shapes,
which is closer to a cot mobile. Flash and brightness safety notes are fine.

## State of play

- The whole app is built and tested against a **built-in simulator**. Every
  test in `tests/` passes (the count is in README.md).
- **First connected to the real panel on 30th October 2026.** Everything in the
  procedure below has been exercised against it; see Findings for what was
  learned and fixed. Only the iDotMatrix backend has been tried for real.
- **Answered 30th October 2026: the panel is iDotMatrix** (see Findings). Two protocols
  are common for these panels and both are implemented in `pixelate/devices.py`:
  - **iPixel Color**, advertises as `LED_BLE_…`, library `pypixelcolor`
  - **iDotMatrix**, advertises as `IDM-…`, library `idotmatrix`

  The scanner works out which from the Bluetooth name. If the name matches
  neither, it defaults to iPixel.
- **Answered 30th October 2026: size is not the limit.** Presets are 0.5 to 95 KB and the
  69 KB Ocean waves went through fine. Some GIFs are still rejected; see Findings.
- The project is a git working tree. The folder is also synced between
  machines, which is why a stale `.venv` can turn up (step 1 below).

## Procedure for the first real connection

1. Set up on the new machine (the `.venv` from the build machine won't work,
   it has absolute paths baked in; delete it if it synced across):

   ```bash
   cd Pixelate                              # wherever the folder lives
   rm -rf .venv
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt pytest
   .venv/bin/python -m pytest -q tests      # every test should pass
   ```

   Needs Python 3.12+ and BlueZ (`bluetoothctl`). PySide6 is a ~300 MB wheel.

2. Plug the panel in. **Force-close the phone app** (REYLAX's app) on any
   phone; the panel only accepts one Bluetooth connection at a time and the
   app will hold it.

3. Bluetooth on:

   ```bash
   bluetoothctl power on
   ```

4. Find the panel and identify the protocol:

   ```bash
   .venv/bin/python -m pixelate.scan
   ```

   Lists LED-looking devices with name, address and which protocol they match.
   If nothing: `--all` lists every named BLE device nearby. Note down the name
   and address; put them in this file.

5. Send a test message to the first panel found:

   ```bash
   .venv/bin/python -m pixelate.scan --test
   ```

   "Hi!" should scroll across it in yellow.

6. Run the app:

   ```bash
   .venv/bin/python -m pixelate
   ```

   Scan → pick the panel → Connect. Then try, in this order, because each
   exercises a different code path:
   - **Display tab**: brightness slider, Screen off / Screen on
   - **Text tab**: Send text
   - **Presets → Baby, 0 to 3 months → Nightlight** (a 2-frame GIF, smallest)
   - **Presets → Bold shapes** (a 48-frame GIF)
   - **Presets → Soothing → Ocean waves** (the biggest GIF, ≈70 KB)
   - **Drawing**: draw something, Send drawing to panel; tick Live
   - **Clock tab**: Show clock, then Start round clock (re-sends a PNG each
     minute)

   The log box at the bottom right shows every result and error.

## If things go wrong

- **Scan finds nothing**: Bluetooth off, panel off, or the phone app is still
  connected. Try `--all`. If the panel shows up under a name that matches
  neither `LED` nor `IDM-`, tick "Show all Bluetooth devices" in the app, pick
  it, and it'll try the iPixel protocol.
- **Connects but nothing shows / errors on send**: wrong protocol, or the
  library's packet format doesn't match this panel's firmware. Check what
  `pypixelcolor` says the device reports (`client.get_device_info()`); the
  libraries read width/height from the device. Last resort is capturing a
  Bluetooth HCI log from the phone app (Android developer options → Bluetooth
  HCI snoop log) and comparing packets.
- **Big GIFs fail but small ones work**: the panel's memory limit. Reduce
  `MAX_FRAMES` in `pixelate/presets.py` or the fps of the offending preset,
  and lower the size assertion in `tests/test_presets.py::test_gif_round_trips`.
- **iDotMatrix library swallows errors**: it logs and returns `False` instead
  of raising. `devices.py` wraps calls in `_check()` so they raise.
- **Colours look wrong / washed out**: the panel probably applies its own
  gamma. `warm_tint()` and the presets assume linear-ish output.

Record what actually happened in the "Findings" section below.

## Findings from the real panel

First tried 30th October 2026 on the second machine with a CSR USB dongle (hci0).

- Bluetooth name: `IDM-` followed by six hex digits (the panel's own identifier;
  not recorded here, the scanner finds it in a few seconds)
- Address: a random static address the panel advertises; the app remembers it
  in QSettings after the first connect
- Protocol: iDotMatrix (picked up by the scanner from the `IDM-` prefix; about -55 dBm from the next room)
- Largest GIF that worked: Ocean waves, 69 KB on disk (the library re-encodes it
  to 75 KB, 19 chunks of 4 KB) sent fine three times. Size is not the limit.
- Longest GIF that worked: Fireworks then fireflies, 120 frames (30 s at 4 fps,
  21 KB), played through to the end and looped on 8th November 2026. More than 64 frames
  is fine on this panel; the true frame limit, if any, is still unknown. Only
  presets with `max_frames=LONG_STORY_FRAMES` (128) may go past `MAX_FRAMES` (64).
- Confirmed working in the app (30th October 2026): connect, clock sync, brightness,
  screen off/on, text, single-frame drawing (PNG path), Nightlight, Bold shapes,
  Ocean waves.
- Seen failing: four GIF uploads were rejected by the panel with ATT error 0x0E
  ("GATT Protocol Error: Unlikely Error") part way through the chunked write,
  and each time the panel then dropped the connection, so the following
  brightness call failed with "device disconnected" while the app still showed
  Connected. Which presets those were is not in the log because the app only
  logged the preset name on success at the time, so this is unresolved. Not a
  size problem. The app now logs each attempt, the panel's own error text and
  the drop itself; the second run confirmed the drop detection works.
- Second run, same evening: a 9 KB GIF took 15 to 24 s to send, so the link is
  slow (write with response per chunk; the MTU is now logged on connect to
  confirm the cause). The panel also dropped the link once right after a burst
  of live-mode drawing sends with no error reported at all, which points at
  the panel disliking a new transfer arriving straight after the last one
  rather than at any one GIF. Then later the same evening it dropped while idle
  (29 s after the last send) and once 2 s after a fresh connect, and the next
  connect failed with "failed to discover services, device disconnected". So
  the drops are not tied to sends. Suspects, none proven: KDE Connect's daemon
  runs a Bluetooth discovery scan every two minutes (seen in the journal, it
  probes the panel's address) on the same cheap CSR dongle; the dongle itself
  (0a12:0001 clones are known for flaky BLE links); the phone app reclaiming
  the panel; the panel overheating. Test in that order: stop kdeconnectd, then
  swap the dongle.
- With kdeconnectd stopped (18:42 to 18:53): the panel still dropped the link
  three times, always idle (5 s after a connect, 23 s after a send, 2.5 min
  after a send). So KDE Connect is not the cause of the drops. On the other
  hand every one of nine GIF sends in that run succeeded, including Embers at
  83 KB (the biggest tried so far, 59 s to send), so the earlier GIF rejections
  may have been the scan interfering mid-transfer. Unproven.
- Link details from the dongle: bleak reports MTU 23 and 514-byte writes. 23 is
  bleak's "not negotiated" default on BlueZ, so the real MTU is unknown; BlueZ
  is turning each 514-byte write into a long write and the panel accepts it.
  Throughput was about 1.4 KB/s. Next test for the drops: a different dongle.
- Counting 1 to 5 showed 1, then 2, then stopped (fixed, see below). Note that the
  idotmatrix library re-encodes every GIF before sending: it applies the LAST
  frame's duration to every frame (Bold shapes' 4 s holds become 0.5 s), sets
  loop=1, and writes full frames with disposal 2. `python -m pixelate.scan
  --gif FILE` now sends a GIF byte for byte so variants can be tested on the
  panel without that re-encode.
- Spinning cube and Bouncing balls also stop after two frames. The one structural
  difference between presets that play and ones that stall was in the per-frame
  local colour tables Pillow writes. So `pixelate/gifenc.py` now writes the GIF
  itself: one 256-entry global palette, no local tables, full frames, real
  per-frame delays, loop forever; and `IDotMatrixDevice.send_image` sends GIFs
  with `uploadUnprocessed` so the library cannot re-encode them. Files also got
  smaller (Plasma 95 KB to 54 KB). Confirmed on the panel: Counting, Spinning cube
  and Bouncing balls all play through now.
- The panel keeps its own clock: with the built-in clock showing, closing the
  app (and so dropping the link) left the time on screen and still running.
  So a clock sync at connect is enough; nothing has to stay connected for
  the built-in clock. The live round clock is different: that is re-sent as
  a PNG every tick, so it freezes on whatever frame was last sent.
- There is no command in the iDotMatrix protocol (as the idotmatrix library knows
  it) for choosing what the panel shows at power on. The Presets tab therefore
  has "On connect, send" (31st October 2026), stored as `preset/on_connect` in
  QSettings, which sends the chosen preset after every successful connect. Not
  yet tested: whether the panel brings back the last sent GIF by itself after a
  power cycle. Try it: send a preset, pull the power, plug it back in.
- Orientation (31st October 2026): "Panel mounted" in the Display tab, stored as
  `display/orientation` (degrees the panel is turned clockwise). Images are
  counter-rotated in software by `rotate_frames` before encoding; nothing is
  sent to the panel about it. The library does have `Common.flipScreen` (180
  only, and it would also flip text and the built-in clock); untested on this
  panel and not wired up.
- Power on (31st October 2026): every time the mains is pulled and restored the panel
  comes back showing "Hi!". No preset or GIF sent from the app survives a power
  cycle. "Hi!" is also exactly the text `--test` sent the day before, so it is
  still open whether the panel keeps its last TEXT across power cycles or whether
  "Hi!" is the firmware's own greeting. Untested as of 31st October 2026: send a
  different text from the Text tab, power cycle, see which comes back. Until
  then the only reliable way to control what the panel shows is to send it
  after power on, which the "On connect, send" setting does while the app is
  running. No protocol command for a power-on default is known.
- Anything else: the scanner found it first time with the phone app closed.
  The first `--test` connected fine but failed inside the idotmatrix library with
  "cannot open resource": its text renderer defaults to a font file it does not
  ship. `devices.py` now passes an installed font (`find_text_font()`, DejaVu
  Sans Bold first). After that `--test` sent "Hi!" without error (visual check
  on the panel pending at the time of writing). The app itself is the next step.
- Playlist (5th November 2026): the Presets tab rotates through chosen presets every N
  minutes (`playlist/keys` and `playlist/minutes` in QSettings), and while it runs
  a dropped link is reconnected every 30 s and the rotation carries on from the
  next item, skipping "On connect, send". Exercised against the simulator only;
  not yet run overnight on the real panel.

## Layout

| File | What |
|---|---|
| `pixelate/gui.py` | The PySide6 window. Editor, presets tab (with the playlist and its auto reconnect), text, clock, display, simulator preview. Dark theme always. |
| `pixelate/playlist.py` | `Playlist`: the ordered rotation, its interval and the settings round trip. No Qt, so it is unit tested. |
| `pixelate/devices.py` | `Device` interface, `IPixelDevice`, `IDotMatrixDevice`, `SimulatorDevice`, `scan()`. |
| `pixelate/ble.py` | Background asyncio loop; all Bluetooth runs there, results delivered to the GUI thread. |
| `pixelate/presets.py` | All built-in animations, generated in code. `Preset` dataclass, `PRESETS` list, `to_gif()`, `flashing_fraction()` safety check, `warm_tint()`, `rotate_frames()`. |
| `pixelate/gifenc.py` | The in-house GIF89a writer: one global palette, full frames, no local colour tables. The panel stalls on anything else. |
| `pixelate/tray.py` | System tray icon (open, hide, quit) and the local socket guard that keeps the app to one running copy. |
| `pixelate/clockface.py` | Analogue clock face renderer for the live round clock. |
| `pixelate/scan.py` | CLI: find the panel, `--test` sends text, `--gif` sends a file byte for byte. |
| `pixelate/__init__.py` | Reads `VERSION` into `__version__` for the About dialog. |
| `VERSION` | Single source of truth for the version. Only the last number moves without asking. |
| `tests/test_presets.py` | Every preset: well-formed, no flashing, GIF round-trips, unique keys. Sleepy ones: dim, red, slow. Soothing ones: dim. |
| `tests/test_gifenc.py` | GIF structure and exact LZW round trips. |
| `tests/test_devices.py` | Name classification, font lookup, error capture. Nothing opens Bluetooth. |
| `tests/test_playlist.py` | Rotation order, wrap round, removal keeping its place, settings round trip. |
| `tests/test_tray.py` | Tray open and hide, hide on close, Quit shutting down, the one copy guard, plus undo, simulator and brightness fixes. Offscreen Qt, no Bluetooth. |
| `tests/test_house_rules.py` | No dash characters as punctuation anywhere in the tree, VERSION well-formed. |
| `build-zip.sh` | The release build: tests, house rules gate, Dist and Git zips, App mirror, verification. |

## Preset collections

Shown in the app in this order; "Baby, 0 to 3 months" is the default. It is the
only age range so far; later ranges get their own category constant in
`presets.py` (next to `BABY_0_3`) when they are added.

- **Baby, 0 to 3 months** (bold black/white/red, one huge shape,
  slow): Bold shapes, Slow red ball, Big checks, Red target, Big face,
  Ball round and round. Sleepy 🌙: Red glow, Deep breathing, Nightlight,
  Slow ripple, Sleepy stars, Setting sun, Red tide, Embers.
- **Soothing** (dim, slow, mostly warm): Breathing glow, Starry night,
  Sleepy moon, Ocean waves, Lava lamp, Fireflies, Night garden,
  Fireworks then fireflies, Fish tank, Colour drift.
- **Development** (older babies): Contrast cards, Happy face, Tracking ball,
  Peekaboo ball, Shapes & colours, Counting 1 to 5.
- **Fun** (for Rob): Chomper, Fire, Plasma, Code rain, Starfield, Rainbow
  spiral, Game of Life, Bouncing balls, Fireworks, Spinning cube.

Adding a preset: write a builder returning a list of 32×32 RGB PIL images
(≤ 64 frames), add a `Preset(...)` to `PRESETS` with a category, fps and
suggested brightness, and run the tests. `sleepy=True` adds the moon and the
dimness checks. Export everything as GIFs to look at with
`.venv/bin/python -m pixelate.presets OUT_DIR`.

## Safety rules baked in

- `flashing_fraction()`: no more than 10% of the panel may change luminance
  more than twice a second (stricter than WCAG's three). Every preset is
  tested; the app warns before sending a user drawing that fails.
- Sleepy presets: no blue, mean brightness low, loops ≥ 8 s, suggested
  brightness ≤ 15%.
- GIFs are quantised without dithering so the LEDs don't shimmer.

## Ideas not done yet

- Collections for later stages (3 to 4 months: more colour, circular tracking;
  6 months+: cause and effect, peekaboo).
- Schedules (e.g. switch to Nightlight at a set time). The panel supports
  on/off schedules via `set_schedule` in pypixelcolor; not wired up.
- Import of sprite sheets / Piskel files.

`build-zip.sh` and the local packaging notes were written on 8th November 2026; see README.md
for how to run the build. `CHANGELOG.md` gets a line per version, and `docs/recovery.md`
says how to get the panel back to a known state.

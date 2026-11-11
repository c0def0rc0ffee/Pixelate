# Getting the panel back to a known state

This covers the iDotMatrix 32x32 panel, the only one Pixelate has been tried on. Everything
here comes from the Findings in `HANDOVER.md`. Where something has not been tested, it says so.

## What Pixelate can and cannot change on the panel

Pixelate only sends display commands over Bluetooth: images, GIFs, text, brightness, screen
on and off, the clock and the time. It never writes firmware, and it has no command that
changes what the panel does at power on. Nothing it sends survives a power cycle, so a
power cycle is always the way back.

## The panel shows something wrong or stops responding

1. Pull the mains lead, wait a few seconds, and plug it back in.
2. The panel comes back showing "Hi!". That is the known starting state.
3. Connect from Pixelate again. If "On connect, send" is set in the Presets tab, the chosen
   preset is sent straight after the connect.

Still open: whether "Hi!" is the firmware's own greeting or the last text the panel was
sent. To check, send a different text from the Text tab and then power cycle.

## The link keeps dropping or the panel is not found

- Close the phone app. The scanner finds the panel first time when the phone app is closed,
  and the phone may otherwise hold or reclaim the connection.
- A drop shows in the log, and the app shows it as disconnected. Connect again from the
  app.
- If the connect fails with "failed to discover services", power cycle the panel as above
  and try again.
- Drops while idle were seen with a cheap CSR USB dongle. Stopping KDE Connect did not stop
  them. A different dongle is the next thing to try; that is untested.

## Not known

- Whether the panel has a factory reset. None is documented, and Pixelate has never needed one.

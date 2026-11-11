"""
<summary>Find the panel and work out which protocol it speaks.</summary>
<remarks>
    python -m pixelate.scan            # LED panels only
    python -m pixelate.scan --all      # every named BLE device nearby
    python -m pixelate.scan --test     # also connect to the best match and flash some text
    python -m pixelate.scan --gif F    # connect and send GIF file F exactly as it is on disk

--gif bypasses the idotmatrix library's re-encode (which flattens every frame to
one duration and sets loop=1) so a GIF can be tested on the panel byte for byte. The app
must be disconnected first: the panel accepts one connection at a time.
</remarks>
"""

import argparse
import asyncio
from pathlib import Path

from .devices import make_device, scan


async def main(args):
    """
    <summary>Scans, prints what it found and optionally tests the best match.</summary>
    <param name="args">The parsed command line: timeout, all, test and gif.</param>
    <remarks>Talks to the real panel when --test or --gif is given. The best match is the first device
    with a known protocol; it is always disconnected afterwards, even on failure.</remarks>
    """
    print(f"Scanning for {args.timeout:.0f}s… (close the phone app so it isn't holding the connection)")
    found = await scan(args.timeout, show_all=args.all)
    if not found:
        print("Nothing found. Check the panel is powered, Bluetooth is on (bluetoothctl power on),")
        print("then retry with --all to list every device nearby.")
        return
    for d in found:
        print("  " + d.label)
    best = next((d for d in found if d.protocol != "unknown"), None)
    if not (args.test or args.gif):
        return
    if best is None:
        print("\nNone of these matches a known panel protocol, so there is nothing to test.")
        print("If one of them is the panel, start the app, tick 'Show all Bluetooth devices' and pick it.")
        return
    print(f"\nTesting {best.name} with the {best.protocol} protocol…")
    dev = make_device(best)
    await dev.connect()
    try:
        if args.gif:
            await send_raw_gif(dev, args.gif)
        else:
            await dev.send_text("Hi!", (255, 255, 0), None, 1, 80)
            print("Sent 'Hi!'. If it's scrolling on the panel, you're all set: run `python -m pixelate`.")
    finally:
        await dev.disconnect()


async def send_raw_gif(dev, path: str) -> None:
    """
    <summary>Send a GIF file to the panel without any re-encoding.</summary>
    <param name="dev">A connected device from <see cref="make_device"/>.</param>
    <param name="path">The GIF on disk, already 32x32.</param>
    <exception cref="NotImplementedError">The device is not an iDotMatrix panel; only that
    library exposes an unprocessed upload.</exception>
    <exception cref="RuntimeError">The library reported a failure; the message has its reason.</exception>
    """
    from .devices import IDotMatrixDevice, _check

    if not isinstance(dev, IDotMatrixDevice):
        raise NotImplementedError("--gif is only implemented for iDotMatrix panels")
    from idotmatrix import Gif

    size = Path(path).stat().st_size
    print(f"Sending {path} ({size} bytes) unprocessed…")
    _check(await Gif().uploadUnprocessed(path))
    print("Sent. Watch the panel and note how many frames play.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--all", action="store_true", help="list every named BLE device")
    p.add_argument("--test", action="store_true", help="connect to the first panel found and send text")
    p.add_argument("--gif", metavar="FILE", help="connect and send this GIF file exactly as it is")
    p.add_argument("--timeout", type=float, default=8.0)
    asyncio.run(main(p.parse_args()))

"""
<summary>Runs an asyncio loop on a background thread so Bluetooth never blocks the GUI.</summary>
<remarks>
Every backend coroutine is submitted here and runs on the loop thread. Results and
errors are delivered back on the GUI thread through a queued Qt signal, so callers
never need to think about threads. Commands are serialised with one lock because the
panels choke on interleaved writes.
</remarks>
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Coroutine, Optional

from PySide6.QtCore import QObject, Signal


class BleRunner(QObject):
    """
    <summary>Owns the Bluetooth event loop thread and marshals results to the GUI.</summary>
    <remarks>Construct once per window. The thread is a daemon, so a window that never
    calls <see cref="shutdown"/> still lets the process exit; shutdown only matters for
    letting a final disconnect reach the panel.</remarks>
    """

    # (callback, value): emitted on the loop thread, delivered on the GUI thread
    _deliver = Signal(object, object)

    def __init__(self):
        """
        <summary>Creates the event loop and starts its daemon thread straight away.</summary>
        <remarks>The command lock is made lazily inside the loop, on first use, because an
        asyncio.Lock must belong to the loop that awaits it.</remarks>
        """
        super().__init__()
        self._deliver.connect(lambda cb, value: cb(value))
        self.loop = asyncio.new_event_loop()
        # Commands are serialised: panels choke on interleaved writes.
        self._lock: Optional[asyncio.Lock] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="ble-loop")
        self._thread.start()

    def _run(self):
        """
        <summary>Thread body: binds the loop to this thread and runs it until shutdown stops it.</summary>
        """
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def shutdown(self, final: Optional[Coroutine[Any, Any, Any]] = None, timeout: float = 3.0) -> None:
        """
        <summary>Run one last coroutine, wait for it briefly, then stop the loop thread.</summary>
        <param name="final">Usually the device's disconnect. None just stops the loop.</param>
        <param name="timeout">Seconds to wait for <paramref name="final"/>; a slow panel
        must not hold the window open, so after this the loop is stopped regardless.</param>
        <remarks>Call from the GUI thread when the window closes. Before this existed the
        disconnect was queued and the process exited before it was sent, so the panel kept
        the link open and the next connect sometimes failed.</remarks>
        """
        if final is not None:
            future = asyncio.run_coroutine_threadsafe(final, self.loop)
            try:
                future.result(timeout)
            except BaseException:
                pass  # best effort: the process is going away either way
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        on_done: Callable[[Any], None] = lambda _: None,
        on_error: Callable[[BaseException], None] = lambda _: None,
    ) -> None:
        """
        <summary>Queue a coroutine on the loop thread and report its outcome on the GUI thread.</summary>
        <param name="coro">The device call to run. It waits for any command already running.</param>
        <param name="on_done">Called on the GUI thread with the coroutine's result.</param>
        <param name="on_error">Called on the GUI thread with the exception, timeouts and
        cancellations included.</param>
        <remarks>Returns at once. Commands run strictly one at a time in the order submitted.</remarks>
        """
        async def wrapped():
            """
            <summary>Runs <paramref name="coro"/> under the command lock and emits the outcome.</summary>
            """
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:
                try:
                    result = await coro
                except BaseException as e:  # report everything, including timeouts
                    self._deliver.emit(on_error, e)
                else:
                    self._deliver.emit(on_done, result)

        asyncio.run_coroutine_threadsafe(wrapped(), self.loop)

    def to_gui(self, callback: Callable[[Any], None], value: Any = None) -> None:
        """
        <summary>Run <paramref name="callback"/> on the GUI thread from any other thread.</summary>
        <param name="callback">Called with <paramref name="value"/> on the GUI thread.</param>
        <param name="value">Passed through unchanged.</param>
        <remarks>Used for events that do not come from a submitted coroutine, such as the
        panel dropping the connection on its own.</remarks>
        """
        self._deliver.emit(callback, value)

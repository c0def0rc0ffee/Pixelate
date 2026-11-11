"""
<summary>The system tray icon, and the guard that keeps Pixelate to one running copy.</summary>
<remarks>
With a tray, closing the window only hides it, so a running playlist keeps going and the
panel stays connected. The tray menu opens the window again or quits for real, and only
quitting disconnects the panel. Because a hidden window is easy to forget, launching
Pixelate a second time from the application menu does not start another copy (the panel
accepts one Bluetooth connection, so two copies would fight over it); it asks the copy
already running to show its window instead. Where the desktop has no tray, none of this
applies and closing the window quits as it always did.
</remarks>
"""

from __future__ import annotations

import getpass
from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

SERVER_NAME = f"pixelate-single-instance-{getpass.getuser()}"
"""
<summary>The local socket name the running copy listens on.</summary>
<remarks>On Linux QLocalServer makes this a socket file in the shared temporary folder,
so the user name is part of it; without that, two people logged in at once would find
each other's copy.</remarks>
"""


class TrayIcon(QSystemTrayIcon):
    """
    <summary>Tray icon whose menu opens or hides the window and quits the app.</summary>
    <remarks>A left click toggles the window too. The first menu entry reads "Open
    Pixelate" or "Hide Pixelate" to match the window's state, refreshed each time the
    menu is about to show, so it is never stale after the window manager hides or
    shows the window by other means.</remarks>
    """

    def __init__(self, window: QWidget, icon: QIcon, on_quit: Callable[[], None]):
        """
        <summary>Builds the tray menu; the icon is not shown until the caller calls show().</summary>
        <param name="window">The main window to show and hide.</param>
        <param name="icon">The icon drawn in the tray.</param>
        <param name="on_quit">Called by the Quit entry; it must close the window for real
        and end the application.</param>
        """
        super().__init__(icon, window)
        self.window = window
        self.setToolTip("Pixelate")
        self.menu = QMenu()
        self.toggle_action = QAction("Open Pixelate", self.menu)
        self.toggle_action.triggered.connect(self.toggle_window)
        self.quit_action = QAction("Quit Pixelate", self.menu)
        self.quit_action.triggered.connect(on_quit)
        self.menu.addAction(self.toggle_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.menu.aboutToShow.connect(self._refresh_label)
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _refresh_label(self):
        """<summary>Make the first entry say what clicking it will do now.</summary>"""
        self.toggle_action.setText("Hide Pixelate" if self.window.isVisible() else "Open Pixelate")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """<summary>A plain left click on the icon toggles the window; the menu handles the rest.</summary>"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def toggle_window(self):
        """<summary>Hide the window if it is showing, otherwise bring it back.</summary>"""
        if self.window.isVisible() and not self.window.isMinimized():
            self.window.hide()
        else:
            show_window(self.window)


def show_window(window: QWidget) -> None:
    """
    <summary>Show, restore and raise the window, from hidden or minimised.</summary>
    <remarks>On Wayland the compositor decides focus, so activateWindow may only flash the
    taskbar entry rather than move focus; the window is still shown and raised.</remarks>
    """
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


class SingleInstance(QObject):
    """
    <summary>Lets the first copy of Pixelate claim a local socket, and later copies find it.</summary>
    <remarks>Call <see cref="hand_over"/> before building any window. When it returns
    True another copy is running and has been asked to show itself, so this one should
    exit. Otherwise this copy listens and runs <c>on_show</c> whenever a later launch
    knocks. A socket left behind by a copy that crashed is cleared, because connecting
    to it fails and the name is then removed before listening.</remarks>
    """

    def __init__(self, on_show: Callable[[], None], name: str = SERVER_NAME):
        """
        <summary>Stores the callback; nothing listens until <see cref="hand_over"/> is called.</summary>
        <param name="on_show">Run on the GUI thread each time another launch asks to be shown.</param>
        <param name="name">The socket name; tests pass their own so they never touch a real copy.</param>
        """
        super().__init__()
        self.name = name
        self.on_show = on_show
        self.server: QLocalServer | None = None

    def hand_over(self, timeout_ms: int = 500) -> bool:
        """
        <summary>Ask a running copy to show itself, or start listening if there is none.</summary>
        <param name="timeout_ms">How long to wait for a running copy to answer.</param>
        <returns>True if another copy took the request and this one should exit.</returns>
        """
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if sock.waitForConnected(timeout_ms):
            sock.write(b"show\n")
            sock.waitForBytesWritten(timeout_ms)
            sock.disconnectFromServer()
            return True
        # Nobody answered: any socket file with this name is stale.
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._on_connection)
        self.server.listen(self.name)
        return False

    def _on_connection(self):
        """<summary>Another launch knocked: show the window and drop the connection.</summary>"""
        while self.server is not None and self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            conn.close()
            self.on_show()

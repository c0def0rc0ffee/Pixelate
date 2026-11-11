"""
<summary>Tests for the tray icon, hide to tray on close, and the one running copy guard.</summary>
<remarks>Runs Qt on the offscreen platform, so no window appears and no tray is needed.
Settings are pointed at a temporary folder so the real app's saved panel and playlist
are never read or changed. Nothing here talks to Bluetooth: the window starts its BLE
thread but no device is ever connected.</remarks>
"""

import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from pixelate.tray import SingleInstance, TrayIcon


@pytest.fixture(scope="module")
def app():
    """<summary>One QApplication for the module, as Qt allows only one per process.</summary>"""
    return QApplication.instance() or QApplication([])


def _pump(app, rounds: int = 50):
    """<summary>Let queued socket and signal events run.</summary>"""
    for _ in range(rounds):
        app.processEvents()


def test_second_copy_hands_over_to_the_first(app):
    """<summary>A later launch finds the running copy, asks it to show its window, and is told to exit.</summary>"""
    name = f"pixelate-test-{uuid.uuid4().hex}"
    shown = []
    first = SingleInstance(lambda: shown.append(1), name)
    assert first.hand_over() is False
    second = SingleInstance(lambda: None, name)
    assert second.hand_over() is True
    _pump(app)
    assert shown == [1]
    first.server.close()


def test_stale_socket_does_not_block_a_new_copy(app):
    """<summary>A socket left by a copy that has gone is cleared, so the next launch starts normally.</summary>"""
    name = f"pixelate-test-{uuid.uuid4().hex}"
    old = SingleInstance(lambda: None, name)
    assert old.hand_over() is False
    old.server.close()  # the copy that held it is gone
    fresh = SingleInstance(lambda: None, name)
    assert fresh.hand_over() is False
    assert fresh.server.isListening()
    fresh.server.close()


def test_tray_toggles_the_window(app):
    """<summary>The first menu entry opens and hides the window and its label follows; Quit calls back.</summary>"""
    win = QWidget()
    quits = []
    tray = TrayIcon(win, QIcon(), lambda: quits.append(1))
    tray.toggle_window()
    assert win.isVisible()
    tray._refresh_label()
    assert tray.toggle_action.text() == "Hide Pixelate"
    tray.toggle_window()
    assert not win.isVisible()
    tray._refresh_label()
    assert tray.toggle_action.text() == "Open Pixelate"
    tray.quit_action.trigger()
    assert quits == [1]


@pytest.fixture
def window(app, tmp_path):
    """<summary>A MainWindow whose settings live in a temporary folder.</summary>"""
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path))
    from pixelate.gui import MainWindow

    win = MainWindow()
    yield win
    win.shutdown()


def test_close_hides_to_tray_and_keeps_running(window):
    """<summary>With a tray, closing only hides the window: nothing is shut down or cleaned up.</summary>"""
    window.tray = TrayIcon(window, QIcon(), window.quit_app)
    window.show()
    window.close()
    assert not window.isVisible()
    assert window._shut_down is False
    assert window.tmpdir.exists()


def test_close_without_tray_shuts_down(window):
    """<summary>With no tray, closing shuts down as it always did, removing the temporary folder.</summary>"""
    window.show()
    window.close()
    assert window._shut_down is True
    assert not window.tmpdir.exists()


def test_quit_from_tray_shuts_down(window):
    """<summary>Quit from the tray shuts a hidden window down for real.</summary>"""
    window.tray = TrayIcon(window, QIcon(), window.quit_app)
    window.show()
    window.close()  # hidden to the tray first, as a user would
    window.quit_app()
    assert window._shut_down is True
    assert not window.tmpdir.exists()


def test_shutdown_twice_is_harmless(window):
    """<summary>Shutdown runs from both the close and aboutToQuit, so a second call must do nothing.</summary>"""
    window.shutdown()
    window.shutdown()
    assert window._shut_down is True


def test_undo_after_inserting_a_frame_restores_the_right_frame(window):
    """<summary>Undo entries move with their frame when a frame is inserted before it.</summary>"""
    from PySide6.QtGui import QColor

    window.add_frame()
    window.add_frame()  # three blank frames, the third selected
    window._push_undo()
    window.frames[2].fill(QColor("#ff0000"))  # a "stroke" on frame 3
    window.current = 0
    window.add_frame()  # insert before it: frame 3 is now at index 3
    window.undo()
    assert window.frames[3].pixelColor(0, 0) == QColor("#000000")
    assert all(f.pixelColor(0, 0) == QColor("#000000") for f in window.frames)


def test_simulated_brightness_does_not_freeze_the_preview(window):
    """<summary>A brightness event after an image keeps the simulated animation playing.</summary>"""
    from pixelate.gui import new_frame

    window.preview.show_frames([new_frame(), new_frame()], [100, 100])
    window._on_sim_event("brightness", 40)
    assert window.preview.timer.isActive()
    assert window.preview.brightness == 40


def test_brightness_keys_reach_the_panel(window):
    """<summary>A keyboard or wheel step on the slider queues a send; dragging does not.</summary>"""
    from PySide6.QtWidgets import QSlider

    window.brightness.triggerAction(QSlider.SliderAction.SliderSingleStepAdd)
    assert window.brightness_timer.isActive()
    window.brightness_timer.stop()
    window.brightness.triggerAction(QSlider.SliderAction.SliderMove)
    assert not window.brightness_timer.isActive()
    window.set_brightness()  # no panel connected: must do nothing rather than fail

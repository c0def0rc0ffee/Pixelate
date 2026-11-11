"""
<summary>Pixelate: the PySide6 desktop window for 32x32 Bluetooth LED pixel panels.</summary>
<remarks>
Everything the user sees lives here: the pixel editor, the preset browser, the text,
clock and display tabs, the simulator preview and the log box. All Bluetooth work is
handed to <see cref="pixelate.ble.BleRunner"/> and comes back on the GUI thread, so
nothing in this file blocks on the panel. The theme is always dark; the panel is used
in a dim room.
</remarks>
"""

from __future__ import annotations

import datetime as dt
import io
import sys
import tempfile
import traceback
from collections import deque
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageSequence
from PySide6.QtCore import QBuffer, QByteArray, QObject, QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter, QPalette,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .ble import BleRunner
from .clockface import render_round_clock
from .devices import SIZE, TEXT_ANIMATIONS, FoundDevice, SimulatorDevice, make_device, scan
from .playlist import MAX_MINUTES, MIN_MINUTES, RECONNECT_SECONDS, Playlist
from .presets import (BY_KEY, CATEGORIES, MAX_FLASHING_AREA, ORIENTATIONS, PRESETS, flashing_fraction,
                      rotate_frames, to_gif, warm_tint)
from .tray import SingleInstance, TrayIcon, show_window

BLACK = QColor(0, 0, 0)
PALETTE = [
    "#ffffff", "#9e9e9e", "#4a4a4a", "#000000",
    "#ff0000", "#ff7a00", "#ffd800", "#7aff00",
    "#00c800", "#00ffb4", "#00b4ff", "#0040ff",
    "#7a00ff", "#ff00d8", "#ff6f9c", "#8b4513",
]
UNDO_LIMIT = 100
MAX_OPEN_FRAMES = 200
"""<summary>Frames kept when opening a GIF; reading stops there rather than decoding the rest to throw away.</summary>"""


def new_frame() -> QImage:
    """
    <summary>A blank 32x32 frame, filled black.</summary>
    <returns>A new RGB32 image the caller owns.</returns>
    """
    img = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
    img.fill(BLACK)
    return img


def qimage_to_pil(img: QImage) -> PILImage.Image:
    """
    <summary>Convert a Qt frame to a Pillow RGB image.</summary>
    <param name="img">The frame to convert; it is not changed.</param>
    <returns>A new RGB image.</returns>
    <remarks>Goes through an in memory PNG. Slower than copying bytes, but it sidesteps Qt's
    row padding and byte order, and frames are only 32x32.</remarks>
    """
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return PILImage.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def pil_to_qimage(img: PILImage.Image) -> QImage:
    """
    <summary>Convert a Pillow image to a Qt RGB32 frame.</summary>
    <param name="img">Any mode; it is converted to RGB first.</param>
    <returns>A QImage that owns its own pixels.</returns>
    <remarks>The final conversion copies, which matters: a QImage made straight from a bytes
    object only borrows it, and the bytes go away when this function returns.</remarks>
    """
    data = img.convert("RGB").tobytes()
    q = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return q.convertToFormat(QImage.Format.Format_RGB32)  # copies, so `data` may be freed


def fit_to_panel(img: PILImage.Image) -> PILImage.Image:
    """
    <summary>Scale an image to fit 32x32, keeping its aspect ratio, centred on black.</summary>
    <param name="img">Any size and mode; transparency is respected.</param>
    <returns>A 32x32 RGB image.</returns>
    <remarks>Pixel art (anything scaled up, or no wider than 64) uses NEAREST so its edges stay
    hard; larger pictures use LANCZOS.</remarks>
    """
    img = img.convert("RGBA")
    scale = min(SIZE / img.width, SIZE / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    # Pixel art scales cleanly with NEAREST; photos look better with LANCZOS.
    method = PILImage.Resampling.NEAREST if scale >= 1 or img.width <= 64 else PILImage.Resampling.LANCZOS
    img = img.resize((w, h), method)
    out = PILImage.new("RGB", (SIZE, SIZE), (0, 0, 0))
    out.paste(img, ((SIZE - w) // 2, (SIZE - h) // 2), img)
    return out


# --------------------------------------------------------------------------- editor


class PixelCanvas(QWidget):
    """
    <summary>The zoomed 32x32 editor. Left button paints, right button erases.</summary>
    <remarks>It edits <c>self.image</c> in place, the QImage of the current frame owned by
    the window, so the window's frame list sees every change without copying. stroke_started
    fires before the first pixel changes, which is when the window takes its undo copy, and
    stroke_finished fires once the stroke ends.</remarks>
    """

    stroke_started = Signal()
    stroke_finished = Signal()
    color_picked = Signal(QColor)

    def __init__(self):
        """
        <summary>Starts on a blank frame with the pencil in red and the grid on.</summary>
        """
        super().__init__()
        self.image = new_frame()
        self.onion: QImage | None = None
        self.color = QColor("#ff0000")
        self.tool = "pencil"
        self.grid = True
        self._drawing = False
        self._erase = False
        self._last = QPoint(0, 0)
        self.setMinimumSize(SIZE * 12, SIZE * 12)
        self.setMouseTracking(True)
        self._hover: QPoint | None = None

    def set_image(self, img: QImage, onion: QImage | None = None):
        """
        <summary>Show and edit a different frame.</summary>
        <param name="img">The frame to edit in place.</param>
        <param name="onion">The previous frame to draw faintly underneath, or None.</param>
        """
        self.image, self.onion = img, onion
        self.update()

    def _cell(self) -> float:
        """
        <summary>The size of one panel pixel on screen; the canvas stays square inside the widget.</summary>
        """
        return min(self.width(), self.height()) / SIZE

    def _origin(self) -> QPoint:
        """
        <summary>Top left of the drawn grid, centring it in the widget.</summary>
        """
        c = self._cell()
        return QPoint(int((self.width() - c * SIZE) / 2), int((self.height() - c * SIZE) / 2))

    def _to_pixel(self, pos) -> QPoint | None:
        """
        <summary>Map a mouse position to a panel pixel.</summary>
        <param name="pos">Widget coordinates.</param>
        <returns>The pixel, or None when the pointer is outside the grid.</returns>
        """
        c, o = self._cell(), self._origin()
        x, y = int((pos.x() - o.x()) // c), int((pos.y() - o.y()) // c)
        return QPoint(x, y) if 0 <= x < SIZE and 0 <= y < SIZE else None

    def paintEvent(self, _):
        """
        <summary>Draws the frame, the onion skin, the grid (when cells are big enough) and the hover box.</summary>
        """
        p = QPainter(self)
        c, o = self._cell(), self._origin()
        p.fillRect(self.rect(), QColor("#1b1b1f"))
        target = (o.x(), o.y(), int(c * SIZE), int(c * SIZE))
        p.drawImage(*target[:2], self.image.scaled(target[2], target[3]))
        if self.onion is not None:
            p.setOpacity(0.25)
            p.drawImage(*target[:2], self.onion.scaled(target[2], target[3]))
            p.setOpacity(1)
        if self.grid and c >= 6:
            p.setPen(QColor(255, 255, 255, 28))
            for i in range(SIZE + 1):
                p.drawLine(int(o.x() + i * c), o.y(), int(o.x() + i * c), int(o.y() + SIZE * c))
                p.drawLine(o.x(), int(o.y() + i * c), int(o.x() + SIZE * c), int(o.y() + i * c))
        if self._hover is not None:
            p.setPen(QColor(255, 255, 255, 160))
            p.drawRect(int(o.x() + self._hover.x() * c), int(o.y() + self._hover.y() * c), int(c), int(c))

    def mousePressEvent(self, e):
        """
        <summary>Starts a stroke, a fill or a colour pick, depending on the tool.</summary>
        <remarks>The right button erases with any tool except the picker; with the fill tool it
        fills with black.</remarks>
        """
        px = self._to_pixel(e.position())
        if px is None:
            return
        erase = e.button() == Qt.MouseButton.RightButton
        if self.tool == "picker":
            self.color_picked.emit(self.image.pixelColor(px))
            return
        self.stroke_started.emit()
        if self.tool == "fill":
            self._fill(px, BLACK if erase else self.color)
            self.stroke_finished.emit()
            return
        self._drawing = True
        self._erase = erase or self.tool == "eraser"
        self._last = px
        self._plot(px)

    def mouseMoveEvent(self, e):
        """
        <summary>Tracks the hover box and continues a stroke with a line from the last pixel.</summary>
        """
        px = self._to_pixel(e.position())
        if px != self._hover:
            self._hover = px
            self.update()
        if self._drawing and px is not None:
            self._line(self._last, px)
            self._last = px

    def mouseReleaseEvent(self, _):
        """
        <summary>Ends a stroke and reports it.</summary>
        """
        if self._drawing:
            self._drawing = False
            self.stroke_finished.emit()

    def leaveEvent(self, _):
        """
        <summary>Hides the hover box when the pointer leaves the canvas.</summary>
        """
        self._hover = None
        self.update()

    def _plot(self, px: QPoint):
        """
        <summary>Set one pixel to the current colour, or black when erasing.</summary>
        """
        self.image.setPixelColor(px, BLACK if self._erase else self.color)
        self.update()

    def _line(self, a: QPoint, b: QPoint):
        """
        <summary>Plot every pixel from a to b with Bresenham's line, so fast drags leave no gaps.</summary>
        """
        # Bresenham, so fast drags don't leave gaps
        x0, y0, x1, y1 = a.x(), a.y(), b.x(), b.y()
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self._plot(QPoint(x0, y0))
            if (x0, y0) == (x1, y1):
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def _fill(self, start: QPoint, color: QColor):
        """
        <summary>Flood fill the four connected region around start.</summary>
        <param name="start">The pixel clicked.</param>
        <param name="color">The fill colour. Filling a region with its own colour does nothing.</param>
        """
        target = self.image.pixel(start)
        if target == color.rgb():
            return
        todo = deque([(start.x(), start.y())])
        while todo:
            x, y = todo.pop()
            if 0 <= x < SIZE and 0 <= y < SIZE and self.image.pixel(x, y) == target:
                self.image.setPixelColor(x, y, color)
                todo.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
        self.update()


class LedPreview(QWidget):
    """
    <summary>Draws frames as round LEDs, like the real panel.</summary>
    <remarks>Shows the selected preset, the round clock and everything sent to the simulator.
    Brightness and power scale the colours, with a faint floor so unlit LEDs stay visible
    as they do on the panel.</remarks>
    """

    def __init__(self):
        """
        <summary>Starts on one blank frame at full brightness with the animation timer stopped.</summary>
        """
        super().__init__()
        self.frames: list[QImage] = [new_frame()]
        self.durations = [1000]
        self.index = 0
        self.brightness = 100
        self.power = True
        self.timer = QTimer(self, timeout=self._next)
        self.setMinimumSize(SIZE * 6, SIZE * 6)

    def show_frames(self, frames: list[QImage], durations: list[int]):
        """
        <summary>Replace what is shown and restart the animation from the first frame.</summary>
        <param name="frames">Frames to show; an empty list shows a blank frame.</param>
        <param name="durations">Milliseconds per frame, reused cyclically if shorter than frames.
        Anything under 20 ms is treated as 20.</param>
        """
        self.frames, self.durations, self.index = frames or [new_frame()], durations or [1000], 0
        self.timer.stop()
        if len(self.frames) > 1:
            self.timer.start(max(20, self.durations[0]))
        self.update()

    def _next(self):
        """
        <summary>Timer tick: advance one frame and set the timer to that frame's duration.</summary>
        """
        self.index = (self.index + 1) % len(self.frames)
        self.timer.setInterval(max(20, self.durations[self.index % len(self.durations)]))
        self.update()

    def paintEvent(self, _):
        """
        <summary>Draws one ellipse per pixel of the current frame.</summary>
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#050505"))
        c = min(self.width(), self.height()) / SIZE
        ox, oy = (self.width() - c * SIZE) / 2, (self.height() - c * SIZE) / 2
        img = self.frames[self.index]
        k = self.brightness / 100 if self.power else 0
        p.setPen(Qt.PenStyle.NoPen)
        for y in range(SIZE):
            for x in range(SIZE):
                col = img.pixelColor(x, y)
                r, g, b = int(col.red() * k), int(col.green() * k), int(col.blue() * k)
                p.setBrush(QColor(max(r, 18), max(g, 18), max(b, 18)))
                p.drawEllipse(int(ox + x * c + c * 0.12), int(oy + y * c + c * 0.12), int(c * 0.76), int(c * 0.76))


class ColorButton(QPushButton):
    """
    <summary>A swatch button that opens the colour picker and emits changed with the new colour.</summary>
    """
    changed = Signal(QColor)

    def __init__(self, color: str):
        """
        <summary>Creates the swatch showing the given colour.</summary>
        <param name="color">Any colour string QColor accepts, such as "#ff0000".</param>
        """
        super().__init__()
        self.setFixedSize(28, 22)
        self.clicked.connect(self._choose)
        self.set_color(QColor(color))

    def set_color(self, c: QColor):
        """
        <summary>Change the colour, repaint the swatch and emit changed.</summary>
        """
        self.color = QColor(c)
        self.setStyleSheet(f"background:{c.name()}; border:1px solid #888;")
        self.changed.emit(self.color)

    def _choose(self):
        """
        <summary>Opens the colour picker; cancelling leaves the colour as it was.</summary>
        """
        c = QColorDialog.getColor(self.color, self)
        if c.isValid():
            self.set_color(c)

    def rgb(self) -> tuple[int, int, int]:
        """
        <summary>The colour as an (r, g, b) tuple, the form the backends take.</summary>
        """
        return self.color.red(), self.color.green(), self.color.blue()


class SimBridge(QObject):
    """
    <summary>Carries simulator events from the BLE thread to the GUI thread.</summary>
    <remarks>The simulator reports on the loop thread; a Qt signal connected here is queued,
    so the slot runs on the GUI thread where widgets may be touched.</remarks>
    """

    event = Signal(str, object)


# --------------------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    """
    <summary>The one application window: editor on the left, panel controls on the right.</summary>
    <remarks>Files sent to the panel are written to a private temporary folder first,
    because both backend libraries take a path rather than bytes. That folder is removed
    when the window closes. Device state is a single <c>self.device</c>; None means not
    connected, and every send path checks it because the panel can drop the link at any
    moment (see <see cref="_panel_dropped"/>).</remarks>
    """

    def __init__(self):
        """
        <summary>Builds the whole window, restores saved settings and starts disconnected.</summary>
        <remarks>Builds the editor before the controls because the shortcuts and preset list
        refer to widgets made by both. Nothing is sent to any panel here; the remembered device
        is only selected in the list.</remarks>
        """
        super().__init__()
        self.setWindowTitle("Pixelate")
        self.settings = QSettings("Pixelate", "Pixelate")
        self.ble = BleRunner()
        self.sim_bridge = SimBridge()
        self.sim_bridge.event.connect(self._on_sim_event)
        self.device = None
        self.found: list[FoundDevice] = []
        self.frames: list[QImage] = [new_frame()]
        self.current = 0
        self.undo_stack: list[tuple[int, QImage]] = []
        self.redo_stack: list[tuple[int, QImage]] = []
        self._tmp = tempfile.TemporaryDirectory(prefix="pixelate-")
        self.tmpdir = Path(self._tmp.name)
        self.live_timer = QTimer(self, singleShot=True, interval=350, timeout=self.send_drawing)
        self._clock_timer: QTimer | None = None
        self._icon_pass = 0
        self.playlist = Playlist.decode(self.settings.value("playlist/keys"), self.settings.value("playlist/minutes"),
                                        set(BY_KEY))
        self.playlist_running = False
        self.playlist_timer = QTimer(self, timeout=self._playlist_tick)
        self.reconnect_timer = QTimer(self, singleShot=True, interval=RECONNECT_SECONDS * 1000,
                                      timeout=self._auto_reconnect)
        # Set by main() when the desktop has a system tray; see closeEvent.
        self.tray = None
        self.quitting = False
        self._told_about_tray = False
        self._shut_down = False

        root = QSplitter()
        root.addWidget(self._build_editor())
        root.addWidget(self._build_controls())
        root.setStretchFactor(0, 3)
        root.setStretchFactor(1, 2)
        self.setCentralWidget(root)
        self._build_menu()
        self._shortcuts()
        self._load_remembered_device()
        self._refresh_frames()
        self._set_connected(False)
        self.resize(1240, 900)

    # ---- layout

    def _build_menu(self):
        """<summary>A minimal menu bar: Help, with the About dialog on F1.</summary>"""
        help_menu = self.menuBar().addMenu("&Help")
        about = help_menu.addAction("&About Pixelate")
        about.setShortcut("F1")
        about.triggered.connect(self.show_about)

    def show_about(self):
        """<summary>Open the About dialog (see <see cref="AboutDialog"/>).</summary>"""
        AboutDialog(self).exec()

    def _build_editor(self) -> QWidget:
        """
        <summary>The left half: tools, palette, canvas, frame strip, speed and file buttons.</summary>
        <returns>The editor widget.</returns>
        """
        w = QWidget()
        lay = QVBoxLayout(w)

        tools = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        for key, label, tip in [
            ("pencil", "✏ Pencil", "P"),
            ("eraser", "⌫ Eraser", "E"),
            ("fill", "▧ Fill", "F"),
            ("picker", "⊙ Pick", "I"),
        ]:
            b = QToolButton(text=label, checkable=True, toolTip=f"{label} ({tip})")
            b.setProperty("tool", key)
            self.tool_group.addButton(b)
            tools.addWidget(b)
            if key == "pencil":
                b.setChecked(True)
        self.tool_group.buttonClicked.connect(lambda b: setattr(self.canvas, "tool", b.property("tool")))
        tools.addSpacing(12)
        self.color_btn = ColorButton("#ff0000")
        tools.addWidget(QLabel("Colour"))
        tools.addWidget(self.color_btn)
        tools.addStretch()
        self.grid_cb = QCheckBox("Grid", checked=True)
        self.onion_cb = QCheckBox("Onion skin", toolTip="Faintly show the previous frame")
        tools.addWidget(self.grid_cb)
        tools.addWidget(self.onion_cb)
        lay.addLayout(tools)

        pal = QGridLayout()
        pal.setSpacing(3)
        for i, hexc in enumerate(PALETTE):
            b = QPushButton()
            b.setFixedSize(22, 22)
            b.setStyleSheet(f"background:{hexc}; border:1px solid #555;")
            b.clicked.connect(lambda _=False, h=hexc: self.color_btn.set_color(QColor(h)))
            pal.addWidget(b, 0, i)
        pal.setColumnStretch(len(PALETTE), 1)
        lay.addLayout(pal)

        self.canvas = PixelCanvas()
        self.canvas.stroke_started.connect(self._push_undo)
        self.canvas.stroke_finished.connect(self._after_edit)
        self.canvas.color_picked.connect(self.color_btn.set_color)
        self.color_btn.changed.connect(lambda c: setattr(self.canvas, "color", c))
        self.grid_cb.toggled.connect(lambda v: (setattr(self.canvas, "grid", v), self.canvas.update()))
        self.onion_cb.toggled.connect(lambda _: self._show_frame())
        lay.addWidget(self.canvas, 1)

        fr = QHBoxLayout()
        self.frame_list = QListWidget()
        self.frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self.frame_list.setFixedHeight(76)
        self.frame_list.setIconSize(QPixmap(48, 48).size())
        self.frame_list.currentRowChanged.connect(self._select_frame)
        fr.addWidget(self.frame_list, 1)
        col = QVBoxLayout()
        for label, slot in [("+ Frame", self.add_frame), ("Duplicate", self.dup_frame), ("Delete", self.del_frame)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            col.addWidget(b)
        fr.addLayout(col)
        lay.addLayout(fr)

        bottom = QHBoxLayout()
        self.fps = QSpinBox(minimum=1, maximum=30, value=6, suffix=" fps")
        bottom.addWidget(QLabel("Animation speed"))
        bottom.addWidget(self.fps)
        bottom.addStretch()
        for label, slot in [
            ("Clear", self.clear_frame),
            ("Open image/GIF…", self.open_image),
            ("Save as…", self.save_image),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        lay.addLayout(bottom)
        return w

    def _build_controls(self) -> QWidget:
        """
        <summary>The right half: connection box, send drawing, the tabs, the preview and the log.</summary>
        <returns>The controls widget.</returns>
        <remarks>Fills <c>self.device_controls</c>, the widgets that are only enabled while a
        panel is connected.</remarks>
        """
        w = QWidget()
        lay = QVBoxLayout(w)

        conn = QGroupBox("Panel")
        cl = QVBoxLayout(conn)
        row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(260)
        row.addWidget(self.device_combo, 1)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self.do_scan)
        row.addWidget(self.scan_btn)
        cl.addLayout(row)
        row = QHBoxLayout()
        self.show_all_cb = QCheckBox("Show all Bluetooth devices", toolTip="Use if your panel has an unusual name")
        row.addWidget(self.show_all_cb)
        row.addStretch()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connect)
        row.addWidget(self.connect_btn)
        cl.addLayout(row)
        self.status = QLabel()
        cl.addWidget(self.status)
        lay.addWidget(conn)

        send = QGroupBox("Drawing")
        sl = QHBoxLayout(send)
        self.send_btn = QPushButton("Send drawing to panel")
        self.send_btn.clicked.connect(self.send_drawing)
        self.live_cb = QCheckBox("Live", toolTip="Send automatically after every stroke")
        sl.addWidget(self.send_btn, 1)
        sl.addWidget(self.live_cb)
        lay.addWidget(send)

        tabs = QTabWidget()
        tabs.addTab(self._build_presets_tab(), "Presets")
        self.text_tab, self.clock_tab, self.display_tab = (
            self._build_text_tab(), self._build_clock_tab(), self._build_display_tab())
        tabs.addTab(self.text_tab, "Text")
        tabs.addTab(self.clock_tab, "Clock")
        tabs.addTab(self.display_tab, "Display")
        lay.addWidget(tabs, 2)

        self.preview_box = QGroupBox("Preview")
        pl = QVBoxLayout(self.preview_box)
        self.preview = LedPreview()
        pl.addWidget(self.preview)
        lay.addWidget(self.preview_box, 1)

        self.log_view = QPlainTextEdit(readOnly=True, maximumBlockCount=500)
        self.log_view.setFixedHeight(110)
        lay.addWidget(self.log_view)
        self.device_controls = [send, self.text_tab, self.clock_tab, self.display_tab, self.preset_send_btn]
        return w

    def _build_presets_tab(self) -> QWidget:
        """
        <summary>The Presets tab: category, list, description, options, send buttons and playlist.</summary>
        <returns>The tab widget.</returns>
        <remarks>The "On connect, send" choice is saved in QSettings as soon as it changes.</remarks>
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        self.preset_category = QComboBox()
        self.preset_category.addItems(CATEGORIES + ["All"])
        self.preset_category.currentTextChanged.connect(self._fill_preset_list)
        lay.addWidget(self.preset_category)
        self.preset_list = QListWidget()
        self.preset_list.setIconSize(QPixmap(32, 32).size())
        self.preset_list.setMinimumHeight(190)
        self.preset_list.currentItemChanged.connect(self._preset_selected)
        self.preset_list.itemDoubleClicked.connect(lambda _: self.send_preset())
        lay.addWidget(self.preset_list, 1)
        self.preset_desc = QLabel(wordWrap=True)
        self.preset_desc.setMinimumHeight(48)
        lay.addWidget(self.preset_desc)
        self.warm_cb = QCheckBox("Warm tint for bedtime (cuts blue light)")
        self.warm_cb.toggled.connect(lambda _: self._preset_selected(self.preset_list.currentItem()))
        self.preset_bright_cb = QCheckBox("Also set suggested brightness", checked=True)
        lay.addWidget(self.warm_cb)
        lay.addWidget(self.preset_bright_cb)
        on_connect = QHBoxLayout()
        on_connect.addWidget(QLabel("On connect, send:"))
        self.on_connect_combo = QComboBox(
            toolTip="Sent to the panel straight after every successful connect, so the panel "
                    "always starts on something you chose. Warm tint and brightness apply as above.")
        self.on_connect_combo.addItem("Nothing", "")
        for p in PRESETS:
            self.on_connect_combo.addItem(("🌙 " if p.sleepy else "") + p.name, p.key)
        saved = self.settings.value("preset/on_connect", "")
        self.on_connect_combo.setCurrentIndex(max(0, self.on_connect_combo.findData(saved)))
        self.on_connect_combo.currentIndexChanged.connect(
            lambda _: self.settings.setValue("preset/on_connect", self.on_connect_combo.currentData()))
        on_connect.addWidget(self.on_connect_combo, 1)
        lay.addLayout(on_connect)
        row = QHBoxLayout()
        load = QPushButton("Edit a copy")
        load.clicked.connect(self.load_preset)
        self.preset_send_btn = QPushButton("Send to panel")
        self.preset_send_btn.clicked.connect(self.send_preset)
        row.addWidget(load)
        row.addWidget(self.preset_send_btn, 1)
        lay.addLayout(row)
        lay.addWidget(self._build_playlist_box())
        self._fill_preset_list(CATEGORIES[0])
        return w

    def _build_playlist_box(self) -> QGroupBox:
        """
        <summary>The Playlist group: the rotation list, add and remove, the interval and Start.</summary>
        <remarks>The list is in send order; a preset may be added more than once. The whole
        rotation and its interval are saved in QSettings as they change, so the playlist is
        there again next time the app opens, though it never starts by itself.</remarks>
        """
        box = QGroupBox("Playlist", toolTip="Send these presets to the panel in turn, changing every few "
                        "minutes, for as long as the app stays open. If the panel drops the link the app "
                        "reconnects on its own and carries on.")
        lay = QVBoxLayout(box)
        self.playlist_list = QListWidget()
        self.playlist_list.setMaximumHeight(90)
        lay.addWidget(self.playlist_list)
        row = QHBoxLayout()
        add = QPushButton("Add selected")
        add.clicked.connect(self.playlist_add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.playlist_remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        row.addWidget(QLabel("Change every"))
        self.playlist_minutes = QSpinBox(minimum=MIN_MINUTES, maximum=MAX_MINUTES, value=self.playlist.minutes,
                                         suffix=" min")
        self.playlist_minutes.valueChanged.connect(self._playlist_minutes_changed)
        row.addWidget(self.playlist_minutes)
        lay.addLayout(row)
        self.playlist_btn = QPushButton("Start playlist")
        self.playlist_btn.clicked.connect(self.toggle_playlist)
        lay.addWidget(self.playlist_btn)
        self._fill_playlist_list()
        return box

    # ---- playlist

    def _fill_playlist_list(self):
        """<summary>Redraw the rotation list from <c>self.playlist</c>, marking the item sent last.</summary>"""
        self.playlist_list.clear()
        for i, key in enumerate(self.playlist.keys):
            p = BY_KEY[key]
            mark = "▶ " if self.playlist_running and i == self.playlist.position else ""
            self.playlist_list.addItem(f"{mark}{i + 1}. {('🌙 ' if p.sleepy else '')}{p.name}")

    def _save_playlist(self):
        """
        <summary>Store the rotation and its interval in QSettings and redraw the list.</summary>
        """
        self.settings.setValue("playlist/keys", self.playlist.encode())
        self.settings.setValue("playlist/minutes", self.playlist.minutes)
        self._fill_playlist_list()

    def playlist_add(self):
        """<summary>Append the preset selected in the list above to the rotation.</summary>"""
        p = self._selected_preset()
        if p:
            self.playlist.add(p.key)
            self._save_playlist()

    def playlist_remove(self):
        """<summary>Take the highlighted rotation entry out; a running playlist keeps its place.</summary>"""
        row = self.playlist_list.currentRow()
        if row >= 0:
            self.playlist.remove(row)
            self._save_playlist()

    def _playlist_minutes_changed(self, minutes: int):
        """<summary>Store a new interval; a running rotation picks it up from its next change.</summary>"""
        self.playlist.minutes = minutes
        self._save_playlist()
        if self.playlist_running:
            self.playlist_timer.setInterval(self.playlist.interval_ms)

    def toggle_playlist(self):
        """<summary>Start the rotation (sending its first item now) or stop it.</summary>"""
        if self.playlist_running:
            self._stop_playlist("Playlist stopped")
            return
        if not self.playlist.keys:
            self.log("The playlist is empty. Select a preset and press Add selected.")
            return
        if not self.device:
            self.log("Connect to the panel first, then start the playlist.")
            return
        self.playlist_running = True
        self.playlist_btn.setText("Stop playlist")
        self.log(f"Playlist started: {len(self.playlist.keys)} item(s), changing every {self.playlist.minutes} min")
        self._playlist_send_next()

    def _stop_playlist(self, why: str):
        """
        <summary>End the rotation and any pending reconnect, and say why in the log.</summary>
        <param name="why">Logged as it is; empty logs nothing.</param>
        """
        self.playlist_running = False
        self.playlist_timer.stop()
        self.reconnect_timer.stop()
        self.playlist_btn.setText("Start playlist")
        self.playlist_btn.setEnabled(self.device is not None)
        self._fill_playlist_list()
        if why:
            self.log(why)

    def _playlist_send_next(self):
        """<summary>Send the next item and (re)start the interval timer from now.</summary>"""
        key = self.playlist.next_key()
        if key is None:
            self._stop_playlist("Playlist stopped: it is empty")
            return
        self._send_preset(BY_KEY[key])
        self._fill_playlist_list()
        self.playlist_timer.start(self.playlist.interval_ms)

    def _playlist_tick(self):
        """<summary>Interval elapsed: move on, unless the link is down and a reconnect is pending.</summary>"""
        if self.playlist_running and self.device:
            self._playlist_send_next()

    def _auto_reconnect(self):
        """
        <summary>Try to get the panel back after a drop while the playlist was running.</summary>
        <remarks>Fires from <c>self.reconnect_timer</c>. A failed attempt schedules the next
        one from the connect failure path, so this keeps trying until the panel answers or
        the playlist is stopped.</remarks>
        """
        if not self.playlist_running or self.device:
            return
        self.log("Playlist: reconnecting…")
        self.toggle_connect()

    def _fill_preset_list(self, category: str):
        """
        <summary>Show the presets in one category (or all of them) and start drawing their icons.</summary>
        <param name="category">A name from CATEGORIES, or "All".</param>
        """
        self.preset_list.clear()
        for p in PRESETS:
            if category in ("All", p.category):
                label = f"{p.name}   ·  {p.category.lower()}" if category == "All" else p.name
                item = QListWidgetItem(("🌙 " if p.sleepy else "") + label)
                item.setData(Qt.ItemDataRole.UserRole, p)
                self.preset_list.addItem(item)
        # Icons are rendered one per event loop turn so startup stays instant. The pass
        # number lets a chain started for an earlier category stop instead of racing the
        # new one over the same rows.
        self._icon_pass += 1
        QTimer.singleShot(0, lambda n=self._icon_pass: self._render_icons(0, n))

    def _render_icons(self, row: int, icon_pass: int):
        """
        <summary>Render one preset's list icon, then queue the next row.</summary>
        <param name="row">Index into the preset list.</param>
        <param name="icon_pass">The pass this chain belongs to; a stale pass ends quietly.</param>
        """
        if icon_pass != self._icon_pass:
            return
        item = self.preset_list.item(row)
        if item is None:
            return
        p = item.data(Qt.ItemDataRole.UserRole)
        frames = p.frames()
        item.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(frames[len(frames) // 3]).scaled(32, 32))))
        QTimer.singleShot(0, lambda: self._render_icons(row + 1, icon_pass))

    def _preset_frames(self, p):
        """
        <summary>A preset's frames, warm tinted when the bedtime box is ticked.</summary>
        """
        frames = p.frames()
        return warm_tint(frames) if self.warm_cb.isChecked() else frames

    def _preset_selected(self, item):
        """
        <summary>Show the selected preset's description and play it in the preview.</summary>
        <param name="item">The list item, or None when the list was cleared.</param>
        """
        if item is None:
            return
        p = item.data(Qt.ItemDataRole.UserRole)
        self.preset_desc.setText(
            f"{p.description}\n{len(p.frames())} frames · {p.seconds:.0f} s loop · suggested brightness {p.brightness}%")
        frames = [pil_to_qimage(f) for f in self._preset_frames(p)]
        self.preview.show_frames(frames, [round(1000 / p.fps)] * len(frames))

    def _selected_preset(self):
        """
        <summary>The preset highlighted in the list, or None.</summary>
        """
        item = self.preset_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def load_preset(self):
        """
        <summary>Copy the selected preset into the editor as frames, replacing the drawing.</summary>
        <remarks>Clears undo and redo, since their frame indices belong to the old drawing.</remarks>
        """
        p = self._selected_preset()
        if not p:
            return
        self.frames = [pil_to_qimage(f) for f in self._preset_frames(p)]
        self.current = 0
        self.fps.setValue(p.fps)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._refresh_frames()
        self.log(f"Loaded “{p.name}” into the editor")

    def send_preset(self):
        """
        <summary>Send the selected preset to the panel.</summary>
        """
        self._send_preset(self._selected_preset())

    def on_connect_preset(self):
        """
        <summary>The preset chosen under "On connect, send", or None for nothing.</summary>
        <returns>The matching <see cref="Preset"/>, or None when the setting is "Nothing"
        or names a preset that no longer exists.</returns>
        """
        key = self.on_connect_combo.currentData()
        return next((p for p in PRESETS if p.key == key), None) if key else None

    def _send_preset(self, p):
        """
        <summary>Send a preset to the connected panel as a GIF, with its brightness if asked.</summary>
        <param name="p">The preset to send; nothing happens for None or with no panel connected.</param>
        <remarks>Shared by the Send button and the on-connect default. The warm tint and
        "Also set suggested brightness" boxes apply to both, so the panel's start-up look
        is exactly what pressing Send would give.</remarks>
        """
        if not p or not self.device:
            return
        self._stop_round_clock()
        path = self.tmpdir / f"preset-{p.key}.gif"
        path.write_bytes(to_gif(self._oriented(self._preset_frames(p)), p.fps))
        kb = max(1, round(path.stat().st_size / 1024))
        self._run(self.device.send_image(path), f"Sent “{p.name}” ({kb} KB)", f"Sending “{p.name}” ({kb} KB)…")
        if self.preset_bright_cb.isChecked():
            self.brightness.setValue(p.brightness)
            self._run(self.device.set_brightness(p.brightness), f"Brightness {p.brightness}%")

    def _build_text_tab(self) -> QWidget:
        """
        <summary>The Text tab: text, colours, effect, speed and Send.</summary>
        <returns>The tab widget.</returns>
        """
        w = QWidget()
        f = QFormLayout(w)
        self.text_edit = QLineEdit("Hello!")
        self.text_color = ColorButton("#ffffff")
        self.text_bg_cb = QCheckBox("Background")
        self.text_bg = ColorButton("#0000aa")
        bg = QHBoxLayout()
        bg.addWidget(self.text_bg_cb)
        bg.addWidget(self.text_bg)
        bg.addStretch()
        self.text_anim = QComboBox()
        self.text_anim.addItems(TEXT_ANIMATIONS)
        self.text_anim.setCurrentIndex(1)
        self.text_speed = QSlider(Qt.Orientation.Horizontal, minimum=1, maximum=100, value=80)
        b = QPushButton("Send text")
        b.clicked.connect(self.send_text)
        self.text_edit.returnPressed.connect(self.send_text)
        f.addRow("Text", self.text_edit)
        f.addRow("Colour", self.text_color)
        f.addRow("", bg)
        f.addRow("Effect", self.text_anim)
        f.addRow("Speed", self.text_speed)
        f.addRow(b)
        return w

    def _build_clock_tab(self) -> QWidget:
        """
        <summary>The Clock tab: the panel's built in clock, time sync and the app drawn round clock.</summary>
        <returns>The tab widget.</returns>
        """
        w = QWidget()
        f = QFormLayout(w)
        self.clock_style = QSpinBox(minimum=0, maximum=8, value=1)
        self.clock_date = QCheckBox(checked=True)
        self.clock_24 = QCheckBox(checked=True)
        show = QPushButton("Show clock")
        show.clicked.connect(self.show_clock)
        sync = QPushButton("Sync time from PC")
        sync.clicked.connect(lambda: self._run(self.device.sync_time(), "Time synced"))
        f.addRow("Style", self.clock_style)
        f.addRow("Show date", self.clock_date)
        f.addRow("24-hour", self.clock_24)
        f.addRow(show)
        f.addRow(sync)
        f.addRow(QLabel("<b>Round clock</b>  (drawn by the app, re-sent every minute)"))
        self.round_hands = ColorButton("#ffffff")
        self.round_accent = ColorButton("#dc2828")
        colours = QHBoxLayout()
        colours.addWidget(QLabel("Hands"))
        colours.addWidget(self.round_hands)
        colours.addWidget(QLabel("  Accent"))
        colours.addWidget(self.round_accent)
        colours.addStretch()
        self.round_btn = QPushButton("Start round clock")
        self.round_btn.setCheckable(True)
        self.round_btn.toggled.connect(self._toggle_round_clock)
        self.round_timer = QTimer(self, timeout=self._send_round_clock)
        f.addRow(colours)
        f.addRow(self.round_btn)
        return w

    def _toggle_round_clock(self, on: bool):
        """
        <summary>Start the round clock (sending a face now) or stop it.</summary>
        """
        self.round_btn.setText("Stop round clock" if on else "Start round clock")
        if on:
            self._send_round_clock()
        else:
            self.round_timer.stop()

    def _stop_round_clock(self):
        """
        <summary>Stop the round clock if it is running, so another send is not overwritten a minute later.</summary>
        """
        if self.round_btn.isChecked():
            self.round_btn.setChecked(False)

    def _send_round_clock(self):
        """
        <summary>Render and send the round clock face, then time the next send for just after the minute.</summary>
        <remarks>Stops itself when no panel is connected.</remarks>
        """
        if not self.device:
            self._stop_round_clock()
            return
        now = dt.datetime.now()
        img = render_round_clock(now, self.round_hands.rgb(), self.round_accent.rgb())
        path = self.tmpdir / "round-clock.png"
        self._oriented([img])[0].save(path)
        self.preview.show_frames([pil_to_qimage(img)], [1000])
        self._run(self.device.send_image(path), f"Round clock {now:%H:%M}")
        # fire again just after the next minute boundary
        self.round_timer.start((60 - now.second) * 1000 + 500)

    def _build_display_tab(self) -> QWidget:
        """
        <summary>The Display tab: brightness, screen power and how the panel is mounted.</summary>
        <returns>The tab widget.</returns>
        <remarks>The orientation is saved in QSettings and applied to everything the app
        draws; nothing about it is sent to the panel.</remarks>
        """
        w = QWidget()
        f = QFormLayout(w)
        self.brightness = QSlider(Qt.Orientation.Horizontal, minimum=5, maximum=100, value=80)
        self.brightness_label = QLabel("80%")
        self.brightness.valueChanged.connect(lambda v: self.brightness_label.setText(f"{v}%"))
        self.brightness.sliderReleased.connect(self.set_brightness)
        # Arrow keys, Page Up and Down, the mouse wheel and clicks on the groove never fire
        # sliderReleased, so they used to move the label without reaching the panel. They
        # are sent once the slider has been still briefly, so holding a key sends one value.
        self.brightness_timer = QTimer(self, singleShot=True, interval=400, timeout=self.set_brightness)
        self.brightness.actionTriggered.connect(
            lambda action: QSlider.SliderAction(action) != QSlider.SliderAction.SliderMove
            and self.brightness_timer.start())
        br = QHBoxLayout()
        br.addWidget(self.brightness, 1)
        br.addWidget(self.brightness_label)
        on, off = QPushButton("Screen on"), QPushButton("Screen off")
        on.clicked.connect(lambda: self._run(self.device.set_power(True), "Screen on"))
        off.clicked.connect(lambda: self._run(self.device.set_power(False), "Screen off"))
        pw = QHBoxLayout()
        pw.addWidget(on)
        pw.addWidget(off)
        self.orientation = QComboBox(
            toolTip="How the panel is mounted. Everything the app draws (presets, drawings, the round "
                    "clock) is turned to match so it reads upright. Text and the built-in clock are drawn "
                    "by the panel itself and cannot be turned from here.")
        for label, degrees in ORIENTATIONS:
            self.orientation.addItem(label, degrees)
        self.orientation.setCurrentIndex(
            max(0, self.orientation.findData(int(self.settings.value("display/orientation", 0)))))
        self.orientation.currentIndexChanged.connect(
            lambda _: self.settings.setValue("display/orientation", self.orientation.currentData()))
        f.addRow("Brightness", br)
        f.addRow("Power", pw)
        f.addRow("Panel mounted", self.orientation)
        return w

    def _oriented(self, frames):
        """
        <summary>Turn rendered frames to match the "Panel mounted" setting.</summary>
        <param name="frames">PIL frames as drawn, upright.</param>
        <returns>Frames rotated for the panel; the same list when the panel is mounted normally.</returns>
        """
        return rotate_frames(frames, int(self.orientation.currentData() or 0))

    def _shortcuts(self):
        """
        <summary>Undo, redo, Ctrl+Return to send, and single letter keys for the tools.</summary>
        """
        for keys, slot in [
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            ("Ctrl+Return", self.send_drawing),
        ]:
            QShortcut(keys, self, activated=slot)
        for key, tool in [("P", "pencil"), ("E", "eraser"), ("F", "fill"), ("I", "picker")]:
            QShortcut(key, self.canvas, activated=lambda t=tool: self._pick_tool(t))

    def _pick_tool(self, tool):
        """
        <summary>Select a tool and check its button to match.</summary>
        """
        for b in self.tool_group.buttons():
            if b.property("tool") == tool:
                b.setChecked(True)
        self.canvas.tool = tool

    # ---- frames & editing

    def _refresh_frames(self):
        """
        <summary>Rebuild the frame strip from <c>self.frames</c> and show the current frame.</summary>
        <remarks>Signals are blocked while rebuilding so clearing the list does not change the
        current frame.</remarks>
        """
        self.frame_list.blockSignals(True)
        self.frame_list.clear()
        for i, img in enumerate(self.frames):
            self.frame_list.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(img.scaled(48, 48))), str(i + 1)))
        self.frame_list.setCurrentRow(self.current)
        self.frame_list.blockSignals(False)
        self._show_frame()

    def _show_frame(self):
        """
        <summary>Put the current frame on the canvas, with the previous one as onion skin if ticked.</summary>
        """
        onion = self.frames[self.current - 1] if self.onion_cb.isChecked() and self.current > 0 else None
        self.canvas.set_image(self.frames[self.current], onion)

    def _select_frame(self, row):
        """
        <summary>The frame strip selection changed; ignores rows that no longer exist.</summary>
        """
        if 0 <= row < len(self.frames):
            self.current = row
            self._show_frame()

    def _update_thumb(self):
        """
        <summary>Redraw the current frame's thumbnail after an edit.</summary>
        """
        item = self.frame_list.item(self.current)
        if item:
            item.setIcon(QIcon(QPixmap.fromImage(self.frames[self.current].scaled(48, 48))))

    def _push_undo(self):
        """
        <summary>Save a copy of the current frame before it changes, keeping the last UNDO_LIMIT.</summary>
        <remarks>Any new edit empties the redo stack.</remarks>
        """
        self.undo_stack.append((self.current, self.frames[self.current].copy()))
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def _after_edit(self):
        """
        <summary>Refresh the thumbnail and, in Live mode, schedule a send.</summary>
        <remarks>Live sends wait for <c>live_timer</c> so a quick run of strokes sends once.</remarks>
        """
        self._update_thumb()
        if self.live_cb.isChecked() and self.device:
            self.live_timer.start()

    def _swap(self, src, dst):
        """
        <summary>Shared body of undo and redo: restore one saved frame and save the current one.</summary>
        <param name="src">The stack to take from.</param>
        <param name="dst">The stack the replaced frame goes on.</param>
        <remarks>An entry whose frame has since been deleted is dropped.</remarks>
        """
        if not src:
            return
        idx, img = src.pop()
        if idx >= len(self.frames):
            return
        dst.append((idx, self.frames[idx].copy()))
        self.frames[idx] = img
        self.current = idx
        self._refresh_frames()
        self._after_edit()

    def undo(self):
        """
        <summary>Undo the last change to any frame.</summary>
        """
        self._swap(self.undo_stack, self.redo_stack)

    def redo(self):
        """
        <summary>Redo the last undone change.</summary>
        """
        self._swap(self.redo_stack, self.undo_stack)

    def _insert_frame(self, img: QImage):
        """
        <summary>Put a frame after the current one and select it, keeping undo pointed right.</summary>
        <remarks>Undo and redo entries hold frame indices, so every entry at or after the new
        position moves up one. Without this, undoing a stroke made on a later frame wrote it
        over the wrong frame once a frame had been inserted before it.</remarks>
        """
        at = self.current + 1
        self.frames.insert(at, img)
        for stack in (self.undo_stack, self.redo_stack):
            stack[:] = [(i + 1 if i >= at else i, saved) for i, saved in stack]
        self.current = at
        self._refresh_frames()

    def add_frame(self):
        """
        <summary>Insert a blank frame after the current one.</summary>
        """
        self._insert_frame(new_frame())

    def dup_frame(self):
        """
        <summary>Insert a copy of the current frame after it.</summary>
        """
        self._insert_frame(self.frames[self.current].copy())

    def del_frame(self):
        """
        <summary>Delete the current frame; the last frame left is cleared instead.</summary>
        <remarks>Clears undo and redo, since their frame indices would point at the wrong frames.</remarks>
        """
        if len(self.frames) == 1:
            self.clear_frame()
            return
        del self.frames[self.current]
        self.current = min(self.current, len(self.frames) - 1)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._refresh_frames()

    def clear_frame(self):
        """
        <summary>Fill the current frame with black, as an undoable edit.</summary>
        """
        self._push_undo()
        self.frames[self.current].fill(BLACK)
        self.canvas.update()
        self._after_edit()

    def open_image(self):
        """
        <summary>Open an image or animated GIF into the editor, fitted to 32x32.</summary>
        <remarks>Replaces the drawing and clears undo. Only the first MAX_OPEN_FRAMES frames
        of a GIF are read. The animation speed is set from the GIF's average frame delay.</remarks>
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Open image", self.settings.value("last_dir", str(Path.home())),
            "Images (*.png *.gif *.jpg *.jpeg *.bmp *.webp)",
        )
        if not path:
            return
        self.settings.setValue("last_dir", str(Path(path).parent))
        try:
            with PILImage.open(path) as im:
                frames, durations = [], []
                for fr in ImageSequence.Iterator(im):
                    frames.append(pil_to_qimage(fit_to_panel(fr)))
                    durations.append(fr.info.get("duration", 100) or 100)
                    if len(frames) == MAX_OPEN_FRAMES:
                        break
        except Exception as e:
            self.log(f"Couldn't open {path}: {e}")
            return
        self.frames, self.current = frames, 0
        if len(frames) > 1:
            self.fps.setValue(max(1, min(30, round(1000 / (sum(durations) / len(durations))))))
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._refresh_frames()
        cut = f", the first {MAX_OPEN_FRAMES} only" if len(frames) == MAX_OPEN_FRAMES else ""
        self.log(f"Opened {Path(path).name} ({len(frames)} frame{'s' if len(frames) > 1 else ''}{cut})")

    def _export(self, path: Path, for_panel: bool = False) -> Path:
        """
        <summary>Write the drawing as PNG (one frame) or looping GIF (several).</summary>
        <param name="path">Where to write; the suffix is replaced to match the format.</param>
        <param name="for_panel">True when the file is about to be sent, so it is turned to
        match the "Panel mounted" setting. Files saved for the user stay upright.</param>
        <returns>The path actually written.</returns>
        """
        images = [qimage_to_pil(f) for f in self.frames]
        if for_panel:
            images = self._oriented(images)
        if len(images) == 1:
            path = path.with_suffix(".png")
            images[0].save(path)
        else:
            path = path.with_suffix(".gif")
            path.write_bytes(to_gif(images, self.fps.value()))
        return path

    def save_image(self):
        """
        <summary>Save the drawing as PNG or GIF where the user chooses, upright.</summary>
        """
        default = "drawing.gif" if len(self.frames) > 1 else "drawing.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save drawing", str(Path(self.settings.value("last_dir", str(Path.home()))) / default),
            "PNG or GIF (*.png *.gif)",
        )
        if path:
            saved = self._export(Path(path))
            self.log(f"Saved {saved}")

    # ---- device

    def log(self, msg: str):
        """
        <summary>Add a timestamped line to the log box, which keeps the last 500 lines.</summary>
        """
        self.log_view.appendPlainText(f"{dt.datetime.now():%H:%M:%S}  {msg}")

    def _set_connected(self, connected: bool):
        """
        <summary>Enable or disable everything that needs a panel and update the status line.</summary>
        <param name="connected">True once <c>self.device</c> is set.</param>
        """
        for wdg in self.device_controls:
            wdg.setEnabled(connected)
        self.playlist_btn.setEnabled(connected or self.playlist_running)
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.device_combo.setEnabled(not connected)
        self.scan_btn.setEnabled(not connected)
        if connected:
            self.status.setText(f"● Connected to {self.device.name}")
        else:
            self.status.setText("○ Not connected")

    def _populate_devices(self):
        """
        <summary>Refill the device list from <c>self.found</c> plus the Simulator, keeping the selection.</summary>
        <remarks>A device is matched by address, so the remembered panel stays selected after a
        scan finds it again.</remarks>
        """
        current = self.device_combo.currentData()
        self.device_combo.clear()
        for d in self.found:
            self.device_combo.addItem(d.label, d)
        self.device_combo.addItem("Simulator (no hardware)", "sim")
        if current is not None:
            for i in range(self.device_combo.count()):
                data = self.device_combo.itemData(i)
                if data == current or (isinstance(data, FoundDevice) and isinstance(current, FoundDevice)
                                       and data.address == current.address):
                    self.device_combo.setCurrentIndex(i)

    def _load_remembered_device(self):
        """
        <summary>Put the panel used last (from QSettings) in the device list so no scan is needed.</summary>
        """
        addr = self.settings.value("device/address")
        if addr:
            self.found = [FoundDevice(self.settings.value("device/name", "Last panel"), addr,
                                      self.settings.value("device/protocol", "ipixel"))]
        self._populate_devices()

    def do_scan(self):
        """
        <summary>Scan for panels on the BLE thread and add what is found to the device list.</summary>
        <remarks>Panels found earlier but missing this time stay in the list.</remarks>
        """
        self.scan_btn.setEnabled(False)
        self.status.setText("Scanning… (close the phone app first)")

        def done(results):
            """
            <summary>Merge the scan results into the list and log them.</summary>
            """
            self.scan_btn.setEnabled(True)
            known = {d.address for d in results}
            self.found = results + [d for d in self.found if d.address not in known]
            self._populate_devices()
            self.status.setText(f"Found {len(results)} device(s)")
            self.log(f"Scan found: {', '.join(d.label for d in results) or 'nothing'}")

        def failed(e):
            """
            <summary>Log a failed scan with the usual cause.</summary>
            """
            self.scan_btn.setEnabled(True)
            self.status.setText("Scan failed")
            self.log(f"Scan failed: {e}. Is Bluetooth switched on? (bluetoothctl power on)")

        self.ble.submit(scan(show_all=self.show_all_cb.isChecked()), done, failed)

    def toggle_connect(self):
        """
        <summary>Disconnect when connected; otherwise connect to the device selected in the list.</summary>
        <remarks>A disconnect stops the playlist and the round clock first. A connect syncs the
        clock, then either carries on a running playlist or sends the "On connect, send" preset.
        A successful connect to a real panel is remembered in QSettings.</remarks>
        """
        if self.device:
            if self.playlist_running:
                self._stop_playlist("Playlist stopped")
            self._stop_round_clock()
            dev, self.device = self.device, None
            self._set_connected(False)
            self.ble.submit(dev.disconnect(), lambda _: self.log("Disconnected"), lambda e: self.log(f"Disconnect: {e}"))
            return
        choice = self.device_combo.currentData()
        if choice == "sim":
            dev = SimulatorDevice(self.sim_bridge.event.emit)
        elif isinstance(choice, FoundDevice):
            try:
                dev = make_device(choice)
            except Exception as e:
                self.log(f"Can't create device: {e}")
                return
        else:
            return
        # A manual Connect while a playlist reconnect is pending would otherwise race it:
        # the second attempt replaced the first's link on the shared iDotMatrix connection.
        self.reconnect_timer.stop()
        self.connect_btn.setEnabled(False)
        self.status.setText(f"Connecting to {dev.name}…")

        def done(_):
            """
            <summary>Connected: wire up drop detection, remember the panel and send the start up content.</summary>
            """
            self.connect_btn.setEnabled(True)
            self.device = dev
            dev.on_disconnect = lambda: self.ble.to_gui(self._panel_dropped, dev)
            self._set_connected(True)
            self.log(f"Connected to {dev.name}" + (f" ({dev.link_info})" if dev.link_info else ""))
            if isinstance(choice, FoundDevice):
                self.settings.setValue("device/address", choice.address)
                self.settings.setValue("device/name", choice.name)
                self.settings.setValue("device/protocol", choice.protocol)
            self._run(dev.sync_time(), "Clock synced to PC time")
            if self.playlist_running:
                self.log("Playlist: carrying on")
                self._playlist_send_next()
                return
            start = self.on_connect_preset()
            if start:
                self.log(f"Start-up preset: “{start.name}”")
                self._send_preset(start)

        def failed(e):
            """
            <summary>Connect failed: log it and, if a playlist is running, schedule another attempt.</summary>
            """
            self.connect_btn.setEnabled(True)
            self._set_connected(False)
            self.log(f"Connect failed: {e!r}. Make sure the phone app is closed and the panel is on.")
            if self.playlist_running:
                self.log(f"Playlist: trying again in {RECONNECT_SECONDS} s")
                self.reconnect_timer.start()

        self.ble.submit(dev.connect(), done, failed)

    def _run(self, coro, ok_msg: str, start_msg: str = ""):
        """
        <summary>Queue a device coroutine on the BLE thread and log its outcome.</summary>
        <param name="coro">The backend call to run.</param>
        <param name="ok_msg">Logged when it succeeds.</param>
        <param name="start_msg">Logged before it starts, for long transfers, so a failed
        send still shows what was being sent.</param>
        <remarks>A failure is logged as "Failed: ok_msg: reason", naming the operation,
        because the panel's rejections leave no other trace of what was attempted.</remarks>
        """
        if not self.device:
            coro.close()
            return
        if start_msg:
            self.log(start_msg)
        self.ble.submit(coro, lambda _: self.log(ok_msg), lambda e: self._on_error(e, ok_msg))

    def _on_error(self, e: BaseException, what: str = ""):
        """
        <summary>Log a failed device call with the operation and the real reason.</summary>
        <param name="e">The exception from the BLE thread.</param>
        <param name="what">The success message of the call that failed, used as its name.</param>
        """
        label = f" {what}" if what else ""
        self.log(f"Failed:{label}: {e}")
        traceback.print_exception(e)

    def _panel_dropped(self, dev):
        """
        <summary>The panel closed the link itself: reflect that in the GUI.</summary>
        <param name="dev">The device that reported the drop; ignored if no longer current.</param>
        <remarks>Called on the GUI thread via <c>BleRunner.to_gui</c>. The real panel does
        this after rejecting a GIF, and until this existed the app kept showing
        Connected while every further call failed.</remarks>
        """
        if self.device is not dev:
            return
        self._stop_round_clock()
        self.device = None
        self._set_connected(False)
        if self.playlist_running:
            self.playlist_timer.stop()
            self.log(f"Panel dropped the connection. Playlist will reconnect in {RECONNECT_SECONDS} s.")
            self.reconnect_timer.start()
        else:
            self.log("Panel dropped the connection. Press Connect to carry on.")

    def send_drawing(self):
        """
        <summary>Send the drawing to the panel as PNG or GIF, turned to match the mounting.</summary>
        <remarks>Warns in the log when an animation fails the flash check, but still sends it:
        the drawing is the user's own.</remarks>
        """
        if not self.device:
            return
        self._stop_round_clock()
        path = self._export(self.tmpdir / "send", for_panel=True)
        n = len(self.frames)
        if n > 1 and flashing_fraction([qimage_to_pil(f) for f in self.frames], self.fps.value()) > MAX_FLASHING_AREA:
            self.log("⚠ This animation flashes quickly. Slow it down before showing it to a baby "
                     "or anyone sensitive to flashing light.")
        frames = f"{n} frame{'s' if n > 1 else ''}"
        # Live mode sends after every stroke; a start line per stroke would drown the log.
        start = "" if self.live_cb.isChecked() else f"Sending drawing ({frames})…"
        self._run(self.device.send_image(path), f"Sent drawing ({frames})", start)

    def send_text(self):
        """
        <summary>Send the text box's text with the chosen colours and effect.</summary>
        """
        self._stop_round_clock()
        text = self.text_edit.text()
        if text and self.device:
            bg = self.text_bg.rgb() if self.text_bg_cb.isChecked() else None
            self._run(
                self.device.send_text(text, self.text_color.rgb(), bg, self.text_anim.currentIndex(),
                                      self.text_speed.value()),
                f"Sent text “{text}”",
            )

    def show_clock(self):
        """
        <summary>Switch the panel to its built in clock with the chosen style.</summary>
        """
        self._stop_round_clock()
        self._run(
            self.device.show_clock(self.clock_style.value(), self.clock_date.isChecked(), self.clock_24.isChecked()),
            "Clock mode",
        )

    def set_brightness(self):
        """<summary>Send the slider's value; the delayed send may land after a disconnect, so it checks.</summary>"""
        if not self.device:
            return
        v = self.brightness.value()
        self._run(self.device.set_brightness(v), f"Brightness {v}%")

    # ---- simulator rendering

    def _on_sim_event(self, kind: str, payload):
        """
        <summary>Mirror what the simulator was sent onto the LED preview.</summary>
        <param name="kind">"image", "text", "clock", "brightness", "power", "connected" or similar.</param>
        <param name="payload">Whatever <see cref="SimulatorDevice"/> attached to the event.</param>
        """
        # Only events that replace what the panel shows stop the running animation. Brightness
        # follows every preset send, and stopping here froze the simulated GIF on its first frame.
        if kind in ("image", "text", "clock"):
            self.preview.timer.stop()
            if self._clock_timer:
                self._clock_timer.stop()
        if kind == "image":
            with PILImage.open(io.BytesIO(payload)) as im:
                frames, durations = [], []
                for fr in ImageSequence.Iterator(im):
                    frames.append(pil_to_qimage(fit_to_panel(fr)))
                    durations.append(fr.info.get("duration", 100) or 100)
            self.preview.show_frames(frames, durations)
        elif kind == "text":
            self.preview.show_frames(*render_text_frames(**payload))
        elif kind == "clock":
            def tick():
                """
                <summary>Redraw the simulated clock every second.</summary>
                """
                self.preview.show_frames([render_clock(**payload)], [1000])
            self._clock_timer = QTimer(self, timeout=tick, interval=1000)
            self._clock_timer.start()
            tick()
        elif kind == "brightness":
            self.preview.brightness = payload
        elif kind == "power":
            self.preview.power = payload
        self.preview.update()

    def closeEvent(self, e):
        """
        <summary>Hide to the tray when there is one; otherwise shut down and close.</summary>
        <remarks>With a tray icon set (see <c>pixelate.tray</c>) closing the window only hides
        it, so the playlist and the connection carry on; the tray's Quit entry sets
        <c>self.quitting</c> first and the close then goes through <see cref="shutdown"/>.</remarks>
        """
        if self.tray is not None and not self.quitting:
            e.ignore()
            self.hide()
            if not self._told_about_tray:
                self._told_about_tray = True
                self.tray.showMessage("Pixelate is still running",
                                      "It stays in the tray. Use the tray icon to open it again or quit.",
                                      self.windowIcon(), 5000)
            return
        self.shutdown()
        super().closeEvent(e)

    def quit_app(self):
        """<summary>The tray's Quit entry: close for real, disconnecting the panel, and end the app.</summary>"""
        self.quitting = True
        self.close()
        QApplication.quit()

    def shutdown(self):
        """
        <summary>Stop the timers, disconnect cleanly and remove the temporary folder.</summary>
        <remarks>The disconnect is waited for (bounded, see <c>BleRunner.shutdown</c>) so the
        panel sees the link close; it only accepts one connection, and a link left dangling
        made the next connect fail. Safe to call twice: it also runs on
        <c>QApplication.aboutToQuit</c>, which is how a logout ends the app while the window
        is hidden in the tray and no close event arrives.</remarks>
        """
        if self._shut_down:
            return
        self._shut_down = True
        self.round_timer.stop()
        self.live_timer.stop()
        self.brightness_timer.stop()
        self.playlist_timer.stop()
        self.reconnect_timer.stop()
        if self._clock_timer:
            self._clock_timer.stop()
        dev, self.device = self.device, None
        self.ble.shutdown(dev.disconnect() if dev else None)
        self._tmp.cleanup()


class AboutDialog(QDialog):
    """
    <summary>The About box: what Pixelate is, its version, and the libraries it stands on.</summary>
    <remarks>The version comes from the VERSION file at the project root via
    <c>pixelate.__version__</c>; library versions are read from the installed packages at
    run time so the dialog never goes stale when requirements.txt moves.</remarks>
    """

    LIBRARIES = [
        ("PySide6", "the Qt user interface"),
        ("Pillow", "image handling and the preset renderers"),
        ("bleak", "Bluetooth Low Energy"),
        ("idotmatrix", "the iDotMatrix panel protocol"),
        ("pypixelcolor", "the iPixel Color panel protocol"),
    ]

    def __init__(self, parent=None):
        """
        <summary>Builds the dialog's labels from the version and the installed libraries.</summary>
        <param name="parent">The window the dialog is centred on.</param>
        """
        super().__init__(parent)
        self.setWindowTitle("About Pixelate")
        lay = QVBoxLayout(self)
        title = QLabel(f"<h2 style='margin:0'>Pixelate</h2><span style='color:#8a8a90'>version {__version__}</span>")
        lay.addWidget(title)
        body = QLabel(
            "<p>A desktop controller for 32×32 Bluetooth LED pixel panels, such as the REYLAX panel "
            "and other iDotMatrix or iPixel Color boards.</p>"
            "<p>Draw pixel art and animations, open images and GIFs, send scrolling text, show the "
            "clock, and set brightness and power. The built-in presets were made for a baby's room: "
            "bold shapes to look at while awake, and dim red light to settle to sleep. Every preset "
            "passes a flash safety check stricter than the WCAG three-flashes rule.</p>"
            "<p>Everything runs on this machine. Nothing is sent anywhere except to the panel over "
            "Bluetooth.</p>"
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        lay.addWidget(body)
        libs = "".join(f"<li><b>{name}</b> {self._installed(name)}: {what}</li>" for name, what in self.LIBRARIES)
        lib_label = QLabel(f"<p>Built with Python {sys.version.split()[0]} and:</p><ul>{libs}</ul>")
        lib_label.setWordWrap(True)
        lay.addWidget(lib_label)
        licence = QLabel("Released under the MIT Licence. See the LICENSE file alongside the source.")
        licence.setWordWrap(True)
        lay.addWidget(licence)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.setMinimumWidth(460)

    @staticmethod
    def _installed(package: str) -> str:
        """
        <summary>The installed version of a package, or "not installed".</summary>
        <param name="package">The distribution name as pip knows it.</param>
        """
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(package)
        except PackageNotFoundError:
            return "not installed"


def _text_strip(text: str, color, bg) -> QImage:
    """<summary>Render text once as a wide strip for the simulator's scrolling effects.</summary>"""
    font = QFont("DejaVu Sans", 1)
    font.setPixelSize(14)
    font.setBold(True)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)

    width = max(SIZE, QFontMetrics(font).horizontalAdvance(text) + 2)
    img = QImage(width, SIZE, QImage.Format.Format_RGB32)
    img.fill(QColor(*bg) if bg else BLACK)
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor(*color))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    p.end()
    return img


def render_text_frames(text, color, bg, animation, speed):
    """
    <summary>A rough copy of the panel's text effects, for the simulator only.</summary>
    <param name="text">The text.</param>
    <param name="color">Text colour as an RGB triple.</param>
    <param name="bg">Background colour, or None for black.</param>
    <param name="animation">Index into TEXT_ANIMATIONS.</param>
    <param name="speed">1 to 100; higher means shorter frames.</param>
    <returns>Frames and their durations in milliseconds.</returns>
    <remarks>The four scrolls are imitated; blink, fade and snowflake all show as a blink.</remarks>
    """
    strip = _text_strip(text, color, bg)
    step_ms = int(160 - speed * 1.4)  # faster speed -> shorter frames
    blank = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
    blank.fill(QColor(*bg) if bg else BLACK)
    if animation in (1, 2, 3, 4):  # scrolling
        frames = []
        for off in range(-SIZE, strip.width()):
            f = blank.copy()
            p = QPainter(f)
            if animation == 1:
                p.drawImage(-off, 0, strip)
            elif animation == 2:
                p.drawImage(off - strip.width() + SIZE, 0, strip)
            else:
                dy = off % (SIZE * 2) - SIZE
                p.drawImage(0, -dy if animation == 3 else dy, strip)
            p.end()
            frames.append(f)
        return frames, [step_ms] * len(frames)
    first = strip.copy(0, 0, SIZE, SIZE)
    if animation in (5, 6, 7):  # blink / fade / snow -> blink
        return [first, blank], [400, 250]
    return [first], [1000]


def render_clock(style, show_date, hour24):
    """
    <summary>A stand in for the panel's built in clock, for the simulator only.</summary>
    <param name="style">Changes only the colour here; the real styles are the panel's own.</param>
    <param name="show_date">Add the day and month under the time.</param>
    <param name="hour24">24 hour time when True.</param>
    <returns>One 32x32 frame for the current time.</returns>
    """
    now = dt.datetime.now()
    img = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
    img.fill(BLACK)
    p = QPainter(img)
    font = QFont("DejaVu Sans Mono")
    font.setPixelSize(11)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    p.setFont(font)
    p.setPen(QColor.fromHsv((style * 40) % 360, 200, 255))
    p.drawText(0, 0, SIZE, 16, Qt.AlignmentFlag.AlignCenter, now.strftime("%H:%M" if hour24 else "%I:%M"))
    if show_date:
        p.setPen(QColor(150, 150, 150))
        p.drawText(0, 16, SIZE, 16, Qt.AlignmentFlag.AlignCenter, now.strftime("%d/%m"))
    p.end()
    return img


def apply_dark_theme(app: QApplication):
    """
    <summary>Switch the application to the Fusion style with a dark palette.</summary>
    <param name="app">The running application.</param>
    <remarks>Always dark, because the panel is used in a dim room. Disabled text and
    buttons get their own colours so disabled controls look greyed out.</remarks>
    """
    app.setStyle("Fusion")
    pal = QPalette()
    R = QPalette.ColorRole
    for role, hexc in [
        (R.Window, "#202024"), (R.WindowText, "#e6e6e6"), (R.Base, "#18181b"),
        (R.AlternateBase, "#26262b"), (R.Text, "#e6e6e6"), (R.Button, "#2c2c31"),
        (R.ButtonText, "#e6e6e6"), (R.ToolTipBase, "#2c2c31"), (R.ToolTipText, "#e6e6e6"),
        (R.Highlight, "#3d7bd9"), (R.HighlightedText, "#ffffff"), (R.Link, "#6aa5ff"),
        (R.PlaceholderText, "#8a8a90"), (R.Mid, "#3a3a40"), (R.Dark, "#141417"),
    ]:
        pal.setColor(role, QColor(hexc))
    for role in (R.WindowText, R.Text, R.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6c6c72"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Button, QColor("#25252a"))
    app.setPalette(pal)


def app_icon() -> QIcon:
    """
    <summary>Loads the pixel heart icon drawn by <c>tools/make_icon.py</c> at every size it ships in.</summary>
    <returns>An icon with all the PNG sizes added, or an empty icon if the assets folder is missing.</returns>
    """
    icon = QIcon()
    for png in sorted((Path(__file__).parent / "assets").glob("pixelate-*.png")):
        icon.addFile(str(png))
    return icon


def main():
    """
    <summary>Start Pixelate, or bring the copy already running to the front.</summary>
    <remarks>When the desktop has a system tray the app keeps running with the window
    closed, so it no longer quits on the last window closing; the tray's Quit entry ends
    it. Without a tray it behaves as it always has. See <c>pixelate.tray</c>.</remarks>
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Pixelate")
    # Lets the Wayland compositor pair the window with the pixelate.desktop entry,
    # so the taskbar shows our icon rather than a generic Python one.
    app.setDesktopFileName("pixelate")
    app.setWindowIcon(app_icon())
    holder: list[MainWindow] = []
    instance = SingleInstance(lambda: holder and show_window(holder[0]))
    if instance.hand_over():
        print("Pixelate is already running; its window has been brought to the front.")
        return
    apply_dark_theme(app)
    win = MainWindow()
    holder.append(win)
    app.aboutToQuit.connect(win.shutdown)
    if QSystemTrayIcon.isSystemTrayAvailable():
        win.tray = TrayIcon(win, app.windowIcon(), win.quit_app)
        win.tray.show()
        app.setQuitOnLastWindowClosed(False)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

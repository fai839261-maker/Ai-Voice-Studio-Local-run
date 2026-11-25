"""Studio-style level meters and the ON AIR light."""
from __future__ import annotations
import time

from PySide6.QtCore import Property, QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget

GREEN, AMBER, RED = "#34d399", "#fbbf24", "#ef4444"
HOT = 70  # percent: segments above this are red and the meter glows


class StudioMeter(QWidget):
    """Segmented level meter with peak hold. Green, then amber, then red above 70 %.

    Works as both the caption and the bar for the older code: setText('GPU 67%'), value(), setToolTip().
    """
    SEGMENTS = 24

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self._text = f"{name} —"
        self._level = 0
        self._peak = 0
        self._peak_time = 0.0
        self._idle = True
        self.track = QColor("#243649")
        self.text_color = QColor("#e9f0f6")
        self.muted = QColor("#8fa4b6")
        self.setFixedSize(212, 42)
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)

    # -- API used by the older code
    def setText(self, text: str) -> None:
        self._text = text
        self._idle = text.rstrip().endswith("—")
        self.update()

    def value(self) -> int:
        return self._level

    def _get_level(self) -> int:
        return self._level

    def _set_level(self, v: int) -> None:
        self._level = max(0, min(100, int(v)))
        if self._level >= self._peak:
            self._peak, self._peak_time = self._level, time.monotonic()
        elif not self._timer.isActive():
            self._timer.start()      # the peak marker needs to fall; otherwise no timer runs
        self.update()

    level = Property(int, _get_level, _set_level)

    def set_colors(self, track: str, text: str, muted: str) -> None:
        self.track, self.text_color, self.muted = QColor(track), QColor(text), QColor(muted)
        self.update()

    def is_hot(self) -> bool:
        return self._level > HOT

    def _tick(self) -> None:
        if self._peak > self._level and time.monotonic() - self._peak_time > 1.6:
            self._peak = max(self._level, self._peak - 6)
            self.update()
        if self._peak <= self._level:
            self._timer.stop()

    @staticmethod
    def segment_color(index: int, count: int) -> str:
        fraction = (index + 1) / count
        return GREEN if fraction <= 0.5 else AMBER if fraction <= HOT / 100 else RED

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hot = self.is_hot() and not self._idle
        if hot:
            glow = QColor(RED)
            glow.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 8, 8)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(RED) if hot else self.text_color)
        p.drawText(QRectF(8, 2, self.width() - 16, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        n = self.SEGMENTS
        gap, left, top, height = 2.0, 8.0, 24.0, 10.0
        width = (self.width() - 2 * left - gap * (n - 1)) / n
        lit = 0 if self._idle else round(self._level / 100 * n)
        peak_i = -1 if self._idle else min(n - 1, round(self._peak / 100 * n) - 1)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            color = QColor(self.segment_color(i, n))
            if i >= lit and i != peak_i:
                color = QColor(self.track)
            elif i >= lit and i == peak_i:
                color.setAlpha(230)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(left + i * (width + gap), top, width, height), 2, 2)
        p.end()


class OnAir(QWidget):
    """Red 'ON AIR' light that blinks while the software is making audio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self._on = False
        self.setFixedSize(92, 28)
        self.timer = QTimer(self)
        self.timer.setInterval(650)
        self.timer.timeout.connect(self._blink)

    def set_active(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self._on = True
            if active:
                self.timer.start()
            else:
                self.timer.stop()
            self.update()

    def _blink(self) -> None:
        if self.active:
            self._on = not self._on
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.active:
            fill = QColor(RED if self._on else "#7f1d1d")
            text = QColor("#ffffff")
        else:
            fill = QColor("#3b2a2a")
            text = QColor("#7a6a6a")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 8, 8)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QPen(text))
        p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, "● ON AIR")
        p.end()


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to a new row when the space runs out."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, test_only):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and row_height > 0:
                x = rect.x()
                y += row_height + self._spacing
                row_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y()


class ChipGroup(QWidget):
    """A row of one-click choices. Click a choice to select it, click it again to clear it."""
    changed = Signal()

    def __init__(self, options, parent=None):
        super().__init__(parent)
        lay = FlowLayout(self)
        self.buttons = {}
        for label in options:
            b = QPushButton(label)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=label: self._clicked(name))
            lay.addWidget(b)
            self.buttons[label] = b

    def _clicked(self, name: str) -> None:
        on = self.buttons[name].isChecked()
        for other, b in self.buttons.items():
            b.setChecked(on and other == name)
        self.changed.emit()

    def value(self) -> str:
        return next((n for n, b in self.buttons.items() if b.isChecked()), "")

    def set_value(self, name: str) -> None:
        for other, b in self.buttons.items():
            b.setChecked(other == name)


class _EnabledFollower(QObject):
    def __init__(self, spin, slider):
        super().__init__(slider)
        self.slider = slider

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.EnabledChange:
            self.slider.setEnabled(obj.isEnabled())
        return False


class SliderRow(QWidget):
    """A slider that edits a spin box (the spin box stays the real value holder, the slider is the easy way to change it)."""

    def __init__(self, spin, left: str = "", right: str = "", scale: float = 100, parent=None):
        super().__init__(parent)
        self.spin, self.scale = spin, scale
        spin.setReadOnly(True)                      # the number is chosen with the slider, never typed
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(spin.minimum() * scale), round(spin.maximum() * scale))
        self.slider.setValue(round(spin.value() * scale))
        # The spin box stays as the hidden value holder; a plain label shows the number.
        spin.setFixedSize(1, 1)
        spin.setStyleSheet("border:0;background:transparent;padding:0;min-width:0;min-height:0;")
        self.value_label = QLabel(spin.text())
        self.value_label.setObjectName("valuebox")
        self.value_label.setFixedWidth(104)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.valueChanged.connect(lambda _v: self.value_label.setText(spin.text()))
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        row.addWidget(spin)
        lay.addLayout(row)
        if left or right:
            caps = QHBoxLayout()
            caps.setContentsMargins(0, 0, 114, 0)
            a, b = QLabel(left), QLabel(right)
            for x in (a, b):
                x.setObjectName("muted")
            caps.addWidget(a)
            caps.addStretch(1)
            caps.addWidget(b)
            lay.addLayout(caps)
        self.slider.valueChanged.connect(self._from_slider)
        spin.valueChanged.connect(self._from_spin)
        self._follow = _EnabledFollower(spin, self.slider)
        spin.installEventFilter(self._follow)
        self.slider.setEnabled(spin.isEnabled())

    def refresh_range(self) -> None:
        """Call after the spin box range changed (for example when another model is chosen)."""
        self.slider.blockSignals(True)
        self.slider.setRange(round(self.spin.minimum() * self.scale), round(self.spin.maximum() * self.scale))
        self.slider.setValue(round(self.spin.value() * self.scale))
        self.slider.blockSignals(False)
        self.value_label.setText(self.spin.text())

    def _from_slider(self, v: int) -> None:
        value = v / self.scale
        self.spin.blockSignals(True)
        self.spin.setValue(round(value) if isinstance(self.spin, QSpinBox) else value)
        self.spin.blockSignals(False)
        self.spin.valueChanged.emit(self.spin.value())

    def _from_spin(self, _v) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.spin.value() * self.scale))
        self.slider.blockSignals(False)


class DropZone(QFrame):
    """Click or drop an audio file. Emits the chosen path."""
    chosen = Signal(str)
    EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".mpeg", ".mp2", ".wma")

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 22, 18, 22)
        lay.setSpacing(6)
        icon = QLabel("⬆")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size:34px;")
        self.title = QLabel(title)
        self.title.setObjectName("sectiontitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel(hint)
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        for x in (icon, self.title, self.hint):
            lay.addWidget(x)

    @classmethod
    def audio_paths(cls, event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(cls.EXTENSIONS)]

    def dragEnterEvent(self, event) -> None:
        if self.audio_paths(event):
            event.acceptProposedAction()
            self.setProperty("hot", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, _event) -> None:
        self.setProperty("hot", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event) -> None:
        paths = self.audio_paths(event)
        self.dragLeaveEvent(event)
        if paths:
            self.chosen.emit(paths[0])


class CardGrid(QWidget):
    """Cards of one fixed width, placed in as many columns as fit. Re-flows when the window is resized."""

    def __init__(self, card_width: int = 310, parent=None, max_cols: int = 0):
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout
        self.card_width = card_width       # with max_cols this is the narrowest a card may get
        self.max_cols = max_cols
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.cards: list[QWidget] = []
        self.cols = 0

    def set_cards(self, widgets: list[QWidget]) -> None:
        for old in self.cards:
            self.grid.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self.cards = list(widgets)
        self._arrange(force=True)

    def columns(self) -> int:
        cols = max(1, (self.width() + self.grid.spacing()) // (self.card_width + self.grid.spacing()))
        return min(cols, self.max_cols) if self.max_cols else cols

    def _one_width(self, cols: int) -> int:
        if not self.max_cols:
            return self.card_width
        return max(self.card_width, (self.width() - 8 - (cols - 1) * self.grid.spacing()) // max(1, cols))

    def _arrange(self, force: bool = False) -> None:
        cols = self.columns()
        width = self._one_width(cols)
        if cols == self.cols and not force and width == getattr(self, "_last_width", -1):
            return
        self.cols = cols
        self._last_width = width
        for w in self.cards:
            w.setFixedWidth(width)
            self.grid.removeWidget(w)
        for i, w in enumerate(self.cards):
            self.grid.addWidget(w, i // cols, i % cols)
            w.show()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange()


class ChoiceBox(QComboBox):
    """A drop-down list (arrow on the side) for a small whole number. The real value stays in a hidden spin box."""

    def __init__(self, spin, values, fmt=str, parent=None):
        super().__init__(parent)
        self.spin = spin
        # The spin box must NOT be a child of the drop-down: a disabled parent would keep it disabled for good.
        self._holder = QWidget()
        spin.setParent(self._holder)
        spin.choice_box = self
        spin.setReadOnly(True)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedSize(1, 1)
        spin.hide()
        options = sorted(set(values) | {spin.value()})
        for v in options:
            self.addItem(fmt(v), v)
        self.setCurrentIndex(max(0, self.findData(spin.value())))
        self.currentIndexChanged.connect(self._from_box)
        spin.valueChanged.connect(self._from_spin)
        self._follow = _EnabledFollower(spin, self)
        spin.installEventFilter(self._follow)
        self.setEnabled(spin.isEnabled())

    def _from_box(self, _i) -> None:
        if self.currentData() is not None and self.spin.value() != self.currentData():
            self.spin.setValue(self.currentData())

    def _from_spin(self, value) -> None:
        index = self.findData(value)
        if index >= 0 and index != self.currentIndex():
            self.blockSignals(True)
            self.setCurrentIndex(index)
            self.blockSignals(False)


class NeonBar(QWidget):
    """Modern futuristic progress bar: glowing gradient fill, a moving light while it works, a sweeping
    segment when the length is not known. Draws only while something is changing: when it is idle,
    finished or hidden, no timer runs and it costs no processor time.

    Has the same calls as QProgressBar that the older code uses (setRange, setValue, value, ...).
    """

    def __init__(self, parent=None, height: int = 10):
        super().__init__(parent)
        self._min, self._max, self._value = 0, 100, 0
        self._phase = 0.0
        self._shown = 0.0            # the fill that is drawn; eases towards the real value
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # 25 pictures a second, only while working
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(height)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- QProgressBar-compatible calls
    def setRange(self, low: int, high: int) -> None:
        self._min, self._max = int(low), int(high)
        self._value = max(self._min, min(self._max, self._value)) if self._max > self._min else 0
        if not self.isVisible():
            self._shown = self.fraction()
        self._sync()
        self.update()

    def setValue(self, value: int) -> None:
        value = int(value)
        self._value = max(self._min, min(self._max, value)) if self._max > self._min else 0
        if not self.isVisible():
            self._shown = self.fraction()
        self._sync()
        self.update()

    def value(self) -> int:
        return self._value

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._max

    def reset(self) -> None:
        self.setValue(self._min)

    def setTextVisible(self, _visible: bool) -> None:
        pass

    def setFormat(self, _text: str) -> None:
        pass

    def text(self) -> str:
        return ""

    # -- state
    def indeterminate(self) -> bool:
        return self._max <= self._min

    def fraction(self) -> float:
        return 0.0 if self.indeterminate() else (self._value - self._min) / (self._max - self._min)

    def _working(self) -> bool:
        return self.indeterminate() or 0 < self.fraction() < 1 or abs(self._shown - self.fraction()) > 0.002

    def _sync(self) -> None:
        if self.isVisible() and self._working():
            if not self._timer.isActive():
                self._timer.start()
        elif self._timer.isActive() and not self.isVisible():
            self._timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        window = self.window()
        if window is not None and window.isMinimized():
            return                                   # nobody can see it
        self._phase = (self._phase + 0.022) % 1.0
        goal = self.fraction()
        self._shown += (goal - self._shown) * 0.25 if abs(goal - self._shown) > 0.002 else goal - self._shown
        if not self._working():
            self._timer.stop()
        self.update()

    # -- drawing
    def _colors(self):
        pal = self.palette()
        accent = pal.color(QPalette.ColorRole.Highlight)
        track = pal.color(QPalette.ColorRole.Button).darker(135)
        h, s, v, _a = accent.getHsv()
        other = QColor.fromHsv((h + 60) % 360 if h >= 0 else 260, max(120, s), 255)
        return accent, other, track

    def paintEvent(self, _event) -> None:
        accent, other, track = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        r = h / 2
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, r, r)
        edge = QColor(accent)
        edge.setAlpha(60)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, r, r)
        p.setPen(Qt.PenStyle.NoPen)

        if self.indeterminate():
            seg = w * 0.34
            x = -seg + (w + seg) * self._phase
            fill = QRectF(max(0.5, x), 0.5, min(seg, w - max(0.5, x)) if x < w else 0, h - 1)
            if x < 0:
                fill = QRectF(0.5, 0.5, max(0.0, x + seg), h - 1)
            self._draw_fill(p, fill, r, accent, other, shine=False, left=x)
        else:
            width = (w - 1) * max(0.0, min(1.0, self._shown))
            if width > 0.5:
                self._draw_fill(p, QRectF(0.5, 0.5, max(width, h - 1), h - 1), r, accent, other, shine=self._working() and not self.indeterminate())
        p.end()

    def _draw_fill(self, p: QPainter, fill: QRectF, r: float, a: QColor, b: QColor, shine: bool, left: float = 0.0) -> None:
        if fill.width() <= 0:
            return
        glow = QColor(a)
        glow.setAlpha(55)
        p.setBrush(glow)
        p.drawRoundedRect(fill.adjusted(-1, -1, 1, 1), r + 1, r + 1)
        grad = QLinearGradient(fill.left(), 0, fill.right(), 0)
        grad.setColorAt(0.0, a)
        grad.setColorAt(1.0, b)
        p.setBrush(grad)
        p.drawRoundedRect(fill, r, r)
        top = QColor(255, 255, 255, 46)
        p.setBrush(top)
        p.drawRoundedRect(QRectF(fill.left() + 1, fill.top() + 1, max(0.0, fill.width() - 2), fill.height() * 0.42), r, r)
        if shine:
            band = fill.width() * 0.28
            x = fill.left() - band + (fill.width() + band) * self._phase
            light = QLinearGradient(x, 0, x + band, 0)
            clear = QColor(255, 255, 255, 0)
            bright = QColor(255, 255, 255, 120)
            light.setColorAt(0.0, clear)
            light.setColorAt(0.5, bright)
            light.setColorAt(1.0, clear)
            p.save()
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.addRoundedRect(fill, r, r)
            p.setClipPath(path)
            p.setBrush(light)
            p.drawRect(QRectF(x, fill.top(), band, fill.height()))
            p.restore()


class DropPick(QComboBox):
    """One choice from a drop-down list, with the same calls as ChipGroup (changed, value, set_value).
    The first entry means 'no choice'. Used where a long row of buttons took too much room."""
    changed = Signal()

    def __init__(self, options, any_text="Any", parent=None):
        super().__init__(parent)
        self.addItem(any_text, "")
        for label in options:
            self.addItem(label, label)
        self.currentIndexChanged.connect(lambda _i: self.changed.emit())

    def value(self) -> str:
        return self.currentData() or ""

    def set_value(self, name: str) -> None:
        self.blockSignals(True)
        self.setCurrentIndex(max(0, self.findData(name)))
        self.blockSignals(False)
